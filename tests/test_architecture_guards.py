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

# Ratchet ceiling for circular imports across flat src/*.py.
# Both direct 2-way pairs AND multi-hop Tarjan SCCs are locked at 0.
MAX_CIRCULAR_IMPORT_PAIRS = 0
MAX_SCC_COUNT = 0

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

    # Full-graph Tarjan SCC check catches arbitrary multi-hop import cycles (A -> B -> C -> A).
    index_counter = [0]
    stack = []
    on_stack = set()
    indices = {}
    lowlink = {}
    sccs = []

    def strongconnect(v):
        indices[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for mod in sorted(local_mods):
        if mod not in indices:
            strongconnect(mod)

    assert len(sccs) <= MAX_SCC_COUNT, (
        f'Multi-hop circular import SCCs detected ({len(sccs)} > {MAX_SCC_COUNT}): {sccs}'
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


NEW_FILE_MAX_LINES = 800
# Per-file ratchet ceilings for existing >800-line legacy files (only allowed to shrink).
LEGACY_FILE_LINE_CEILINGS = {
    'agent_control.py': 1223,
    'agent_delivery.py': 857,
    'agent_export_audit.py': 1483,
    'agent_exports.py': 1393,
    'agent_lyric_timeline.py': 933,
    'agent_lyrics.py': 1401,
    'agent_mcp_standard_flow.py': 1168,
    'agent_pitch.py': 1380,
    'agent_preparation.py': 1719,
    'agent_pronunciation.py': 1624,
    'agent_reference.py': 1494,
    'agent_score.py': 1355,
    'agent_separation_repair.py': 1003,
    'agent_structure.py': 897,
    'agent_style.py': 1728,
    'agent_views.py': 1309,
    'agent_workflow.py': 1639,
    'audio_mixdown.py': 1751,
    'chorus_analysis.py': 980,
    'chorus_audio.py': 958,
    'chorus_pitch_dsp.py': 814,
    'chorus_score_match.py': 962,
    'context_planner.py': 1033,
    'mixdown_realtime_renderer.py': 1481,
    'mixdown_realtime_service.py': 1212,
    'mixdown_workspace_state.py': 1202,
    'pipeline.py': 1350,
    'pitch_accuracy.py': 1226,
    'preset_library.py': 1597,
    'project_exports.py': 848,
    'publication_draft_service.py': 1005,
    'quick_bridge.py': 1853,
    'quick_chorus.py': 2361,
    'quick_mixdown.py': 1806,
    'quick_publication.py': 2046,
    'quick_support.py': 1322,
    'quick_tuning.py': 2320,
    'quick_ui/ChorusPage.qml': 2128,
    'quick_ui/ChorusPitchLane.qml': 1017,
    'quick_ui/Main.qml': 895,
    'quick_ui/MixdownEq.qml': 880,
    'quick_ui/MixdownPage.qml': 3055,
    'quick_ui/PreparationPage.qml': 2249,
    'quick_ui/ProjectConvertPage.qml': 854,
    'quick_ui/PublicationPage.qml': 2330,
    'quick_ui/ResultsPage.qml': 2537,
    'quick_ui/StartupDialogs.qml': 868,
    'quick_ui/TuningPage.qml': 1850,
    'quick_ui/TuningTimeline.qml': 1783,
    'quick_ui/VideoPage.qml': 1562,
    'quick_ui/WorkshopStylePane.qml': 888,
    'quick_video.py': 1314,
    'quick_workshop.py': 829,
    'style_drafts.py': 838,
    'task_contract.py': 972,
    'transcription_quality.py': 1175,
}


def test_file_size_ratchet():
    offenders = []
    targets = list(_iter_source_py())
    qml_dir = SRC / 'quick_ui'
    if qml_dir.is_dir():
        targets.extend(sorted(qml_dir.glob('*.qml')))
    for path in targets:
        rel = path.relative_to(SRC).as_posix()
        lines = len(path.read_text(encoding='utf-8').splitlines())
        ceiling = LEGACY_FILE_LINE_CEILINGS.get(rel, NEW_FILE_MAX_LINES)
        if lines > ceiling:
            offenders.append(f'{rel}: {lines} lines > ceiling {ceiling}')
    assert not offenders, (
        'File-size ratchet violated (split new/modified code into smaller modules): '
        + ', '.join(offenders)
    )


def main():
    test_no_qt_in_domain_modules()
    test_no_silent_except_exception()
    test_circular_import_ratchet()
    test_no_eager_heavy_imports_in_ui_and_mdx()
    test_no_exact_duplicate_agent_helpers()
    test_chorus_cache_never_inside_src()
    test_agent_primitives_contract()
    test_browser_runtime_edge_fallback_and_cache_prune()
    test_file_size_ratchet()
    print('PASS test_architecture_guards')


if __name__ == '__main__':
    main()
