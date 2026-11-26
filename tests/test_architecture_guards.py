"""AST-based architectural guardrails to prevent backend regressions."""
from __future__ import annotations

import ast
import collections
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' if (ROOT / 'src').is_dir() else ROOT
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Ratchet ceiling for 2-way circular imports across flat src/*.py.
# Whenever a cycle is eliminated, lower this number so cycles can only shrink.
MAX_CIRCULAR_IMPORT_PAIRS = 0

HEAVY_FORBIDDEN_IN_STARTUP = frozenset({'torch', 'onnxruntime', 'scipy', 'librosa', 'playwright'})
QT_MODULES = frozenset({'PySide6', 'PyQt6', 'PyQt5'})


def _iter_source_py():
    return sorted(SRC.glob('*.py'))


def test_no_qt_in_domain_modules():
    offenders = []
    for path in _iter_source_py():
        mod = path.stem
        if mod.startswith('quick_') or mod.startswith('qt_') or mod in ('launch', 'main', 'gui', 'app', 'desktop'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split('.')[0] in QT_MODULES:
                        offenders.append(path.name)
                        break
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split('.')[0] in QT_MODULES:
                    offenders.append(path.name)
                    break
    assert not offenders, f'Domain/DSP modules must not import Qt: {sorted(set(offenders))}'


def test_no_silent_except_exception():
    offenders = []
    for path in _iter_source_py():
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    offenders.append(f'{path.name}:{node.lineno} (bare except)')
                elif isinstance(node.type, ast.Name) and node.type.id == 'Exception':
                    if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue)):
                        offenders.append(f'{path.name}:{node.lineno}')
    assert not offenders, f'Silent except Exception: pass/continue forbidden: {offenders}'


def test_circular_import_ratchet():
    py_files = _iter_source_py()
    local_mods = {p.stem for p in py_files}
    graph = {}
    for path in py_files:
        mod = path.stem
        deps = set()
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split('.')[0]
                    if root in local_mods and root != mod:
                        deps.add(root)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split('.')[0]
                if root in local_mods and root != mod:
                    deps.add(root)
        graph[mod] = deps
    cycles = set()
    for a, deps in graph.items():
        for b in deps:
            if a in graph.get(b, ()):
                cycles.add(tuple(sorted((a, b))))
    assert len(cycles) <= MAX_CIRCULAR_IMPORT_PAIRS, (
        f'Circular import count increased to {len(cycles)} (max {MAX_CIRCULAR_IMPORT_PAIRS}): '
        f'{sorted(cycles)}'
    )


def test_no_eager_heavy_imports_in_ui_and_mdx():
    offenders = []
    for path in _iter_source_py():
        mod = path.stem
        if not (mod.startswith('quick_') or mod in ('launch', 'mdx')):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split('.')[0]
                    if root in HEAVY_FORBIDDEN_IN_STARTUP:
                        offenders.append(f'{path.name}:{node.lineno} ({root})')
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split('.')[0]
                if root in HEAVY_FORBIDDEN_IN_STARTUP:
                    offenders.append(f'{path.name}:{node.lineno} ({root})')
    assert not offenders, f'Heavy AI/scientific libraries must be lazy-imported: {offenders}'


def test_no_exact_duplicate_agent_helpers():
    hashes = collections.defaultdict(list)
    for path in sorted(SRC.glob('agent_*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                span = (node.end_lineno or node.lineno) - node.lineno + 1
                if span >= 12:
                    dump = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
                    h = hashlib.md5(dump.encode()).hexdigest()[:12]
                    hashes[h].append((path.name, node.name, span))
    dups = [items for items in hashes.values() if len({r for r, _, _ in items}) >= 2]
    assert not dups, f'Duplicate helper functions across agent_*.py (move to agent_primitives.py): {dups}'


def test_chorus_cache_never_inside_src():
    if (ROOT / 'src').is_dir():
        assert not (ROOT / 'src' / 'cache').exists(), 'Cache directory leaked into src/cache/'
    import chorus_cache
    import os

    prev_test = os.environ.pop('VWA_TESTING', None)
    prev_btest = os.environ.pop('BVWA_TESTING', None)
    prev_dir = os.environ.pop('VWA_CAS_CACHE_DIR', None)
    try:
        resolved = chorus_cache._cache_root()
        assert resolved is not None
        assert 'src' not in resolved.parts[:-1] or resolved.parent.name != 'src', (
            f'chorus_cache root must not resolve inside src/: {resolved}'
        )
    finally:
        if prev_test is not None:
            os.environ['VWA_TESTING'] = prev_test
        if prev_btest is not None:
            os.environ['BVWA_TESTING'] = prev_btest
        if prev_dir is not None:
            os.environ['VWA_CAS_CACHE_DIR'] = prev_dir


def test_agent_primitives_contract():
    import agent_primitives as ap

    payload = {'b': 1, 'a': [2, 3]}
    assert ap.canonical_json(payload) == '{"a":[2,3],"b":1}'
    assert ap.canonical_json_bytes(payload, pretty=True).endswith(b'\n')
    assert len(ap.json_digest(payload)) == 64
    assert ap.finite_number(3, 'x', preserve_int=True) == 3
    assert type(ap.finite_number(3, 'x', preserve_int=True)) is int
    try:
        ap.finite_number(True, 'x')
        raise AssertionError('bool must be rejected')
    except ValueError:
        pass
    try:
        ap.finite_number(float('nan'), 'x')
        raise AssertionError('NaN must be rejected')
    except ValueError:
        pass


def test_browser_runtime_edge_fallback_and_cache_prune():
    import tempfile
    from unittest.mock import patch
    import browser_runtime as br

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        missing_bundled = str(root / 'dependencies/browser/chrome/chrome.exe')
        with patch.object(br, 'find_system_edge', return_value=Path('C:/Edge/msedge.exe')):
            opts = br.browser_engine_options(
                headless=True,
                root=root,
                config={'publish_browser_executable': missing_bundled},
            )
            assert opts['channel'] == 'msedge' and opts['headless'] is True

        # Verify cache pruner removes disposable cache dirs while preserving Cookies & Local Storage
        profile = root / 'profile'
        (profile / 'Default/Cache').mkdir(parents=True)
        (profile / 'Default/Cache/blob.bin').write_bytes(b'x' * 4096)
        (profile / 'Default/Local Storage').mkdir(parents=True)
        (profile / 'Default/Local Storage/keep.db').write_bytes(b'login-token')
        (profile / 'Default/Cookies').write_bytes(b'cookie-jar')
        reclaimed = br.prune_browser_profile_cache(profile)
        assert reclaimed >= 4096
        assert not (profile / 'Default/Cache').exists()
        assert (profile / 'Default/Cookies').read_bytes() == b'cookie-jar'
        assert (profile / 'Default/Local Storage/keep.db').read_bytes() == b'login-token'


def main():
    test_no_qt_in_domain_modules()
    test_no_silent_except_exception()
    test_circular_import_ratchet()
    test_no_eager_heavy_imports_in_ui_and_mdx()
    test_no_exact_duplicate_agent_helpers()
    test_chorus_cache_never_inside_src()
    test_agent_primitives_contract()
    test_browser_runtime_edge_fallback_and_cache_prune()
    print('PASS test_architecture_guards')


if __name__ == '__main__':
    main()
