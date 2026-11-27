"""Build a verified 2.x lightweight runtime/dependency incremental patch archive."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.update_apply import (
    MAX_RUNTIME_PATCH_BYTES,
    desktop_identity,
    runtime_patch_name,
    validate_file,
    validate_quick_program,
    validate_runtime_patch_entry,
    _is_runtime_patch_dependency_path,
)


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory_path(name: str) -> Path:
    canonical = ROOT / 'packaging' / name
    return canonical if canonical.is_file() else ROOT / name


def build(output: Path, source_manifest: Path, *, target_root: Path | None = None,
          previous_inventory: Path | None = None) -> dict:
    from tools.verify_source_layout import verify

    output = Path(output).resolve()
    runtime_patch_name({'name': output.name})
    verify(ROOT)
    version_info = json.loads(inventory_path('version.json').read_text(encoding='utf-8-sig'))
    version, identity = version_info['version'], desktop_identity(version_info)
    if not identity:
        raise ValueError('Runtime patches are only supported for Qt Quick 2.x releases.')

    res_root = Path(target_root).resolve() if target_root else ROOT
    src_manifest_path = Path(source_manifest).resolve()
    dst_manifest_path = res_root / 'dependencies/manifest.json'
    src_sha = file_hash(src_manifest_path)
    dst_sha = file_hash(dst_manifest_path)
    src_entries = {e['file']: e for e in json.loads(src_manifest_path.read_text(encoding='utf-8-sig'))['entries']}
    dst_entries = {e['file']: e for e in json.loads(dst_manifest_path.read_text(encoding='utf-8-sig'))['entries']}

    changed_deps = sorted(k for k, v in dst_entries.items() if src_entries.get(k) != v)
    removed_deps = sorted(set(src_entries) - set(dst_entries))
    for dep in changed_deps + removed_deps:
        if not _is_runtime_patch_dependency_path(dep):
            raise ValueError('Dependency delta outside lightweight runtime patch boundary: ' + dep)

    portable_entries = json.loads(inventory_path('portable-files.json').read_text(encoding='utf-8-sig'))['entries']
    selected: list[tuple[dict, Path]] = []
    seen = set()
    for entry in portable_entries:
        fname = entry['file']
        if fname == 'config.json' or fname.startswith(('dependencies/', 'models/', 'vendor/')):
            continue
        src = ROOT / entry['source']
        if not src.is_file():
            src = res_root / fname
        record = {'file': fname, 'bytes': src.stat().st_size, 'sha256': file_hash(src)}
        validate_file(record)
        selected.append((record, src))
        seen.add(fname)
    validate_quick_program(seen)

    for dep in changed_deps:
        src = res_root / dep
        record = {'file': dep, 'bytes': src.stat().st_size, 'sha256': file_hash(src)}
        if record != dst_entries[dep]:
            raise ValueError('Target dependency file differs from manifest: ' + dep)
        validate_runtime_patch_entry(record)
        selected.append((record, src))
        seen.add(dep)

    manifest_record = {'file': 'dependencies/manifest.json', 'bytes': dst_manifest_path.stat().st_size, 'sha256': dst_sha}
    validate_runtime_patch_entry(manifest_record)
    selected.append((manifest_record, dst_manifest_path))
    seen.add('dependencies/manifest.json')

    removed = [dict(src_entries[dep]) for dep in removed_deps]
    if previous_inventory is not None:
        prev = json.loads(Path(previous_inventory).read_text(encoding='utf-8-sig'))
        for item in prev.get('entries', []):
            fname = item.get('file', '')
            if (fname and fname not in seen and fname != 'config.json'
                    and not fname.startswith(('dependencies/', 'models/', 'vendor/', 'runtime/'))):
                validate_file(item, allow_legacy_removal=True)
                removed.append({'file': fname, 'bytes': int(item['bytes']), 'sha256': str(item['sha256'])})

    total_uncompressed = sum(rec['bytes'] for rec, _ in selected)
    if total_uncompressed > MAX_RUNTIME_PATCH_BYTES:
        raise ValueError(f'Runtime patch uncompressed payload exceeds limit ({total_uncompressed} > {MAX_RUNTIME_PATCH_BYTES})')

    patch_doc = {
        'version': version,
        'source_runtime_sha256': src_sha,
        'target_runtime_sha256': dst_sha,
        **identity,
        'entries': [rec for rec, _ in selected],
        'removed': removed,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for rec, src in selected:
            zipped.write(src, rec['file'])
        zipped.writestr('runtime-patch.json', json.dumps(patch_doc, ensure_ascii=False, indent=2))
    out_bytes = output.stat().st_size
    if out_bytes > MAX_RUNTIME_PATCH_BYTES:
        output.unlink(missing_ok=True)
        raise ValueError('Runtime patch ZIP exceeds maximum allowed size.')
    return {
        'name': output.name,
        'bytes': out_bytes,
        'sha256': file_hash(output),
        'source_runtime_sha256': src_sha,
        'target_runtime_sha256': dst_sha,
        'changed_dependencies': len(changed_deps),
        'removed_count': len(removed),
        **identity,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--target-root', type=Path, default=None)
    parser.add_argument('--previous-inventory', type=Path, default=None)
    parser.add_argument('--user-test-confirmed', action='store_true')
    args = parser.parse_args()
    if not args.user_test_confirmed:
        parser.error('Local user testing must be confirmed before creating a distributable runtime patch.')
    print(json.dumps(build(args.output, args.source_manifest, target_root=args.target_root,
                           previous_inventory=args.previous_inventory), indent=2))
