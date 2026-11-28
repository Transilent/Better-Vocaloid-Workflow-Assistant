"""Repository hygiene, schema validity, binary-blob, and secret guard for CI."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
TEXT_ROOTS = ('src', 'tools', 'tests', 'packaging', 'components', 'docs', '.github')
TEXT_SUFFIXES = frozenset({
    '.py', '.qml', '.js', '.json', '.yml', '.yaml', '.md', '.txt',
    '.ps1', '.bat', '.cmd', '.c', '.cc', '.cpp', '.h', '.hpp', '.cs', '.toml',
})
FORBIDDEN_LARGE_BINARY_SUFFIXES = frozenset({
    '.ckpt', '.onnx', '.pt', '.pth', '.safetensors', '.gguf',
    '.whl', '.zip', '.7z', '.rar', '.tar', '.gz',
})
MAX_TRACKED_BINARY_BYTES = 5 * 1024 * 1024  # 5 MiB

# Split marker literals so this script never trips its own conflict/credential scans.
_CONFLICT_START = '<' * 7 + ' '
_CONFLICT_END = '>' * 7 + ' '
_FORBIDDEN_PATTERNS = (
    re.compile(r'gh' + r'p_[A-Za-z0-9]{30,}'),
    re.compile(r'github_' + r'pat_[A-Za-z0-9_]{30,}'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE' + r' KEY-----'),
)


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.check_output(
            ['git', 'ls-files', '-z'],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
        paths = [ROOT / item for item in out.decode('utf-8', errors='replace').split('\0') if item]
        return [p for p in paths if p.is_file()]
    except (OSError, subprocess.SubprocessError):
        found: list[Path] = []
        for folder in TEXT_ROOTS:
            base = ROOT / folder
            if base.is_dir():
                found.extend(p for p in base.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
        for p in ROOT.iterdir():
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
                found.append(p)
        return sorted(set(found))


def check_conflict_markers(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != 'CODEOWNERS':
            continue
        if rel.startswith('dependencies/'):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.startswith(_CONFLICT_START) or line.startswith(_CONFLICT_END):
                errors.append(f'Git conflict marker in {rel}:{lineno}')
    return errors


def check_json_and_yaml() -> list[str]:
    errors: list[str] = []
    json_targets: list[Path] = []
    for pattern in (
        'packaging/*.json',
        'components/*.json',
        'assets/i18n/*.json',
        'dependencies/manifest.json',
    ):
        json_targets.extend(sorted(ROOT.glob(pattern)))
    for path in json_targets:
        try:
            json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            errors.append(f'Invalid JSON in {path.relative_to(ROOT).as_posix()}: {exc}')

    yaml_targets: list[Path] = []
    for pattern in (
        '.github/workflows/*.yml',
        '.github/workflows/*.yaml',
        '.github/ISSUE_TEMPLATE/*.yml',
        '.github/dependabot.yml',
    ):
        yaml_targets.extend(sorted(ROOT.glob(pattern)))
    for path in yaml_targets:
        try:
            data = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
            if data is None:
                errors.append(f'Empty YAML document in {path.relative_to(ROOT).as_posix()}')
        except Exception as exc:
            errors.append(f'Invalid YAML in {path.relative_to(ROOT).as_posix()}: {exc}')
    return errors


def check_oversized_binaries(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        if path.suffix.lower() not in FORBIDDEN_LARGE_BINARY_SUFFIXES:
            continue
        size = path.stat().st_size
        if size > MAX_TRACKED_BINARY_BYTES:
            rel = path.relative_to(ROOT).as_posix()
            errors.append(
                f'Oversized binary tracked in Git ({size / (1024 * 1024):.1f} MiB > 5 MiB): {rel}'
            )
    return errors


def check_forbidden_patterns(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != 'CODEOWNERS':
            continue
        if rel == 'tools/ci_hygiene_check.py':
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for idx, pattern in enumerate(_FORBIDDEN_PATTERNS, 1):
            match = pattern.search(text)
            if match:
                lineno = text.count('\n', 0, match.start()) + 1
                errors.append(f'Forbidden pattern #{idx} matched at {rel}:{lineno}')
    return errors


def main() -> int:
    files = _tracked_files()
    errors: list[str] = []
    errors.extend(check_conflict_markers(files))
    errors.extend(check_json_and_yaml())
    errors.extend(check_oversized_binaries(files))
    errors.extend(check_forbidden_patterns(files))
    if errors:
        for err in errors:
            sys.stderr.write(f'ERROR: {err}\n')
        return 1
    print(f'PASS ci_hygiene_check ({len(files)} tracked files verified)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
