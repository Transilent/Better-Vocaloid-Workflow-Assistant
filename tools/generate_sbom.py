#!/usr/bin/env python3
"""Generate CycloneDX 1.5 SBOM (sbom.cdx.json) and SHA256SUMS.txt for VWA releases."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import uuid

_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*==\s*([^\s;#]+)")

_PINNED_NATIVE_COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "type": "library",
        "name": "miniaudio",
        "version": "0.11.26",
        "purl": "pkg:github/mackron/miniaudio@877ea4a7c2601c7b0f2a44e83dfac42e6470fea6",
        "licenses": [{"license": {"id": "MIT-0"}}],
        "hashes": [
            {
                "alg": "SHA-256",
                "content": "6b376c7fbb1352ba1e0e432ab66069b335ea9405e45cb6c30b97ef0f3c9c3e0e",
            }
        ],
    },
    {
        "type": "library",
        "name": "signalsmith-stretch",
        "version": "a670068d9aeb",
        "purl": "pkg:github/Signalsmith-Audio/signalsmith-stretch@a670068d9aeb64913331d5cc29337b19a457a7df",
        "licenses": [{"license": {"id": "MIT"}}],
    },
    {
        "type": "library",
        "name": "signalsmith-linear",
        "version": "de55e6a50ffc",
        "purl": "pkg:github/Signalsmith-Audio/linear@de55e6a50ffcf6f8f43f649692d94691c7025151",
        "licenses": [{"license": {"id": "MIT"}}],
    },
    {
        "type": "library",
        "name": "ffmpeg-alimiter",
        "version": "7.1",
        "purl": "pkg:github/FFmpeg/FFmpeg@n7.1",
        "licenses": [{"license": {"id": "LGPL-2.1-or-later"}}],
    },
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_sbom(root: Path) -> dict[str, Any]:
    """Build a CycloneDX 1.5 SBOM dictionary from VWA packaging manifests."""
    root = Path(root).resolve()
    version_file = root / "packaging/version.json"
    if not version_file.is_file():
        version_file = root / "version.json"
    version_meta = json.loads(version_file.read_text(encoding="utf-8-sig")) if version_file.is_file() else {}
    version = str(version_meta.get("version") or "0.0.0")

    components: list[dict[str, Any]] = list(_PINNED_NATIVE_COMPONENTS)

    lock_file = root / "packaging/requirements-runtime.lock.txt"
    if not lock_file.is_file():
        lock_file = root / "requirements-runtime.lock.txt"
    if lock_file.is_file():
        seen: set[tuple[str, str]] = set()
        for raw_line in lock_file.read_text(encoding="utf-8-sig").splitlines():
            match = _REQ_LINE_RE.match(raw_line)
            if not match:
                continue
            pkg, ver = match.group(1), match.group(2)
            key = (pkg.casefold(), ver)
            if key in seen:
                continue
            seen.add(key)
            components.append(
                {
                    "type": "library",
                    "name": pkg,
                    "version": ver,
                    "purl": f"pkg:pypi/{pkg.lower()}@{ver}",
                }
            )

    qt_ver = version_meta.get("qt_version")
    if isinstance(qt_ver, str) and qt_ver.strip():
        components.append(
            {
                "type": "framework",
                "name": "PySide6",
                "version": qt_ver.strip(),
                "purl": f"pkg:pypi/pyside6@{qt_ver.strip()}",
                "licenses": [{"license": {"id": "LGPL-3.0-only"}}],
            }
        )

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "component": {
                "type": "application",
                "name": "VWA",
                "version": version,
                "description": "Vocaloid Workflow Assistant",
                "licenses": [{"license": {"id": "MIT"}}],
            },
        },
        "components": components,
    }


def write_sha256sums(release_dir: Path, *, exclude: tuple[str, ...] = ("SHA256SUMS.txt",)) -> Path:
    """Write standard coreutils-compatible SHA256SUMS.txt for all release artifacts in *release_dir*."""
    release_dir = Path(release_dir).resolve()
    excluded = {name.casefold() for name in exclude}
    rows: list[str] = []
    for item in sorted(release_dir.iterdir(), key=lambda p: p.name.casefold()):
        if not item.is_file() or item.is_symlink() or item.name.casefold() in excluded:
            continue
        if item.suffix.casefold() == ".sha256":
            continue
        rows.append(f"{_sha256_file(item)}  {item.name}")
    target = release_dir / "SHA256SUMS.txt"
    target.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="ascii", newline="\n")
    return target


def write_release_supply_chain(root: Path, release_dir: Path) -> dict[str, str]:
    """Generate both sbom.cdx.json and SHA256SUMS.txt inside *release_dir*."""
    release_dir = Path(release_dir).resolve()
    release_dir.mkdir(parents=True, exist_ok=True)
    sbom_path = release_dir / "sbom.cdx.json"
    sbom_doc = build_sbom(root)
    sbom_path.write_text(json.dumps(sbom_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    sums_path = write_sha256sums(release_dir)
    return {"sbom": str(sbom_path), "sha256sums": str(sums_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate CycloneDX SBOM and SHA256SUMS.txt for VWA.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    out = write_release_supply_chain(args.root, args.release_dir)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
