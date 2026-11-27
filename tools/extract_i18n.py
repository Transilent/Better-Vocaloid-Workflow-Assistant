#!/usr/bin/env python3
"""Extract, audit, and validate VWA i18n strings across Python & QML sources.

Validates:
  1. JSON syntax & duplicate-key absence in assets/i18n/{en,ja,additions}.json.
  2. Exact key parity between en and ja catalogs (both base and additions).
  3. Placeholder parity ({0}, {1}, %1, %s, ...) between Chinese source keys and
     English/Japanese translations.
  4. Coverage of tr(...) / vwaTr(...) CJK display strings in src/**/*.py and
     src/quick_ui/**/*.qml, with optional CI ratchet (--max-missing-en / -ja).
  5. Detection of fragment-concatenation i18n anti-patterns in QML.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_FORMAT_PLACEHOLDER_RE = re.compile(r'\{(\d+)(?::[^}]*|![^}]*)?\}')
_PRINTF_PLACEHOLDER_RE = re.compile(r'%(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?[sdgfr]')
_QT_ARG_PLACEHOLDER_RE = re.compile(r'%(\d+)')

# Matches vwaTr("..."), page.vwaTr("..."), tr("..."), dialog.tr("..."),
# quickTranslation.text("..."), controller.text("...") in QML files.
_QML_TR_CALL_RE = re.compile(
    r"""(?<![A-Za-z0-9_])(?:vwaTr|tr|quickTranslation\.text|controller\.text)\s*\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""",
    re.DOTALL,
)
# Detects fragment concatenation like vwaTr("A") + x + vwaTr("B")
_QML_CONCAT_ANTIPATTERN_RE = re.compile(
    r"""(?:vwaTr|tr)\s*\([^)\n]+\)\s*\+\s*[^;\n]+\+\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?:vwaTr|tr)\s*\(""",
)


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    dups: list[str] = []
    for key, _ in pairs:
        if key in seen:
            dups.append(key)
        seen.add(key)
    if dups:
        raise ValueError(f"Duplicate JSON keys: {', '.join(sorted(set(dups))[:10])}")
    return dict(pairs)


_JS_ESCAPE_RE = re.compile(r'\\(u[0-9a-fA-F]{4}|[nrt\\\'"])')
_JS_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"'}


def _unescape_js_string(literal: str) -> str:
    body = literal[1:-1]

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.startswith("u") and len(token) == 5:
            return chr(int(token[1:], 16))
        return _JS_SIMPLE_ESCAPES.get(token, match.group(0))

    return _JS_ESCAPE_RE.sub(_replace, body)


def _placeholder_shape(text: str) -> tuple[list[str], list[str], list[str]]:
    return (
        sorted(_FORMAT_PLACEHOLDER_RE.findall(text)),
        sorted(_PRINTF_PLACEHOLDER_RE.findall(text)),
        sorted(_QT_ARG_PLACEHOLDER_RE.findall(text)),
    )


def extract_python_strings(src_dir: Path) -> dict[str, set[str]]:
    """Extract literal string first arguments to tr(...) / i18n.tr(...) in src/*.py."""
    by_file: dict[str, set[str]] = defaultdict(set)
    for py_path in sorted(src_dir.rglob("*.py")):
        rel = py_path.relative_to(src_dir.parent).as_posix()
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            is_tr = (
                (isinstance(func, ast.Name) and func.id in ("tr", "vwaTr"))
                or (isinstance(func, ast.Attribute) and func.attr in ("tr", "vwaTr"))
            )
            if not is_tr:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                text = first.value
                if _CJK_RE.search(text):
                    by_file[rel].add(text)
    return dict(by_file)


def extract_qml_strings(src_dir: Path) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    """Extract literal string first arguments to tr(...) / vwaTr(...) in src/**/*.qml."""
    by_file: dict[str, set[str]] = defaultdict(set)
    concat_hits: list[dict[str, Any]] = []
    for qml_path in sorted(src_dir.rglob("*.qml")):
        rel = qml_path.relative_to(src_dir.parent).as_posix()
        try:
            content = qml_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _QML_TR_CALL_RE.finditer(content):
            text = _unescape_js_string(match.group(1))
            if _CJK_RE.search(text):
                by_file[rel].add(text)
        for lineno, line in enumerate(content.splitlines(), start=1):
            if _QML_CONCAT_ANTIPATTERN_RE.search(line):
                concat_hits.append({"file": rel, "line": lineno, "snippet": line.strip()[:120]})
    return dict(by_file), concat_hits


def load_catalogs(root: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Load and validate base + additions catalogs, returning (merged, errors)."""
    i18n_dir = root / "assets" / "i18n"
    errors: list[str] = []
    base: dict[str, dict[str, str]] = {}
    for lang in ("en", "ja"):
        path = i18n_dir / f"{lang}.json"
        if not path.is_file():
            errors.append(f"Missing catalog file: {path}")
            base[lang] = {}
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_pairs)
            if not isinstance(data, dict):
                errors.append(f"Catalog {lang}.json is not a JSON object")
                data = {}
            base[lang] = data
        except Exception as exc:
            errors.append(f"Failed to load {lang}.json: {exc}")
            base[lang] = {}

    additions_path = i18n_dir / "additions.json"
    additions: dict[str, dict[str, str]] = {"en": {}, "ja": {}}
    if additions_path.is_file():
        try:
            raw_add = json.loads(additions_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_pairs)
            if set(raw_add.keys()) != {"en", "ja"}:
                errors.append(f"additions.json top-level keys must be {{'en','ja'}}, got {sorted(raw_add.keys())}")
            for lang in ("en", "ja"):
                additions[lang] = dict(raw_add.get(lang, {}))
        except Exception as exc:
            errors.append(f"Failed to load additions.json: {exc}")

    # Parity checks
    en_base_only = sorted(set(base["en"]) - set(base["ja"]))
    ja_base_only = sorted(set(base["ja"]) - set(base["en"]))
    if en_base_only:
        errors.append(f"en.json has {len(en_base_only)} keys missing from ja.json (e.g. {en_base_only[:3]})")
    if ja_base_only:
        errors.append(f"ja.json has {len(ja_base_only)} keys missing from en.json (e.g. {ja_base_only[:3]})")

    en_add_only = sorted(set(additions["en"]) - set(additions["ja"]))
    ja_add_only = sorted(set(additions["ja"]) - set(additions["en"]))
    if en_add_only:
        errors.append(f"additions.json en has {len(en_add_only)} keys missing from ja (e.g. {en_add_only[:3]})")
    if ja_add_only:
        errors.append(f"additions.json ja has {len(ja_add_only)} keys missing from en (e.g. {ja_add_only[:3]})")

    merged: dict[str, dict[str, str]] = {}
    for lang in ("en", "ja"):
        combined = dict(base[lang])
        combined.update(additions[lang])
        merged[lang] = combined
        for src, dst in combined.items():
            if not isinstance(dst, str) or not dst.strip():
                errors.append(f"[{lang}] Empty translation for key: {src!r}")
                continue
            if _placeholder_shape(src) != _placeholder_shape(dst):
                errors.append(
                    f"[{lang}] Placeholder mismatch for {src!r} -> {dst!r}: "
                    f"{_placeholder_shape(src)} != {_placeholder_shape(dst)}"
                )
    return merged, errors


def audit_i18n(root: Path) -> dict[str, Any]:
    src_dir = root / "src"
    py_by_file = extract_python_strings(src_dir)
    qml_by_file, concat_hits = extract_qml_strings(src_dir)
    merged, errors = load_catalogs(root)

    all_by_file: dict[str, set[str]] = {}
    all_by_file.update(py_by_file)
    all_by_file.update(qml_by_file)

    all_keys: set[str] = set()
    for keys in all_by_file.values():
        all_keys.update(keys)

    missing_by_lang: dict[str, list[str]] = {}
    missing_by_file: dict[str, dict[str, list[str]]] = {"en": {}, "ja": {}}
    for lang in ("en", "ja"):
        cat = merged[lang]
        missing_by_lang[lang] = sorted(k for k in all_keys if k not in cat)
        for rel, keys in sorted(all_by_file.items()):
            miss = sorted(k for k in keys if k not in cat)
            if miss:
                missing_by_file[lang][rel] = miss

    return {
        "root": str(root),
        "catalog_counts": {lang: len(merged[lang]) for lang in ("en", "ja")},
        "extracted_unique_cjk_keys": len(all_keys),
        "extracted_python_files": len(py_by_file),
        "extracted_qml_files": len(qml_by_file),
        "missing_counts": {lang: len(missing_by_lang[lang]) for lang in ("en", "ja")},
        "missing_by_file": missing_by_file,
        "concat_antipattern_count": len(concat_hits),
        "concat_antipatterns": concat_hits,
        "catalog_errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract and audit VWA i18n strings and catalogs.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="Print full machine-readable JSON report.")
    parser.add_argument("--missing", nargs="?", const="*", default=None,
                        help="List missing strings grouped by file (optional substring filter).")
    parser.add_argument("--check", action="store_true",
                        help="Exit non-zero on catalog integrity errors or ratchet exceedance.")
    parser.add_argument("--max-missing-en", type=int, default=None,
                        help="Fail --check if missing English keys exceed this threshold.")
    parser.add_argument("--max-missing-ja", type=int, default=None,
                        help="Fail --check if missing Japanese keys exceed this threshold.")
    args = parser.parse_args(argv)

    report = audit_i18n(args.root.resolve())

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"VWA i18n audit: catalog(en={report['catalog_counts']['en']}, ja={report['catalog_counts']['ja']}) | "
            f"extracted={report['extracted_unique_cjk_keys']} unique CJK keys "
            f"({report['extracted_python_files']} py, {report['extracted_qml_files']} qml) | "
            f"missing(en={report['missing_counts']['en']}, ja={report['missing_counts']['ja']}) | "
            f"concat_warnings={report['concat_antipattern_count']} | "
            f"catalog_errors={len(report['catalog_errors'])}"
        )
        if report["catalog_errors"]:
            print("Catalog errors:")
            for err in report["catalog_errors"][:30]:
                print(f"  - {err}")
        if args.missing is not None:
            filt = "" if args.missing == "*" else args.missing.lower()
            for rel, keys in report["missing_by_file"]["en"].items():
                if filt and filt not in rel.lower():
                    continue
                print(f"\n[{rel}] ({len(keys)} missing):")
                for k in keys:
                    print(f"  {k!r}")

    if args.check:
        failed = False
        if report["catalog_errors"]:
            failed = True
        if args.max_missing_en is not None and report["missing_counts"]["en"] > args.max_missing_en:
            print(
                f"FAIL: missing en keys ({report['missing_counts']['en']}) > max ({args.max_missing_en})",
                file=sys.stderr,
            )
            failed = True
        if args.max_missing_ja is not None and report["missing_counts"]["ja"] > args.max_missing_ja:
            print(
                f"FAIL: missing ja keys ({report['missing_counts']['ja']}) > max ({args.max_missing_ja})",
                file=sys.stderr,
            )
            failed = True
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
