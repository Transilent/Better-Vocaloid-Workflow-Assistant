"""Select registered tests impacted by modified files via static Python AST import graph."""
from __future__ import annotations

import ast
from collections import defaultdict, deque
import json
from pathlib import Path
import re
import sys


def _imports_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    return found


def registered_tests(repo: Path) -> list[str]:
    text = (repo / 'tests/run_tests.py').read_text(encoding='utf-8')
    return sorted(set(re.findall(r"'(test_[A-Za-z0-9_]+\.py)'", text)))


def impacted_tests(repo: Path, changed_paths: list[str]) -> list[str]:
    repo = Path(repo).resolve()
    known = set(registered_tests(repo))
    src_dir = repo / 'src'
    src_modules = {p.stem for p in src_dir.glob('*.py')}

    # Reverse import graph among src/*.py
    rev_graph: dict[str, set[str]] = defaultdict(set)
    for py in src_dir.glob('*.py'):
        for imp in _imports_of(py):
            if imp in src_modules and imp != py.stem:
                rev_graph[imp].add(py.stem)

    # Test -> imported src modules
    test_imports: dict[str, set[str]] = {}
    for name in known:
        p = repo / 'tests' / name
        if p.is_file():
            test_imports[name] = _imports_of(p) & src_modules

    seed_modules: set[str] = set()
    direct_tests: set[str] = set()
    for raw in changed_paths:
        rel = raw.replace('\\', '/').strip()
        if not rel:
            continue
        if rel.startswith('tests/') and Path(rel).name in known:
            direct_tests.add(Path(rel).name)
        elif rel.startswith('src/') and rel.endswith('.py'):
            seed_modules.add(Path(rel).stem)
        elif rel.startswith('src/quick_ui/') and rel.endswith('.qml'):
            name = Path(rel).stem.lower()
            if 'chorus' in name:
                seed_modules.add('quick_chorus')
                direct_tests.add('test_chorus_workbench.py')
            elif 'tuning' in name or 'workshop' in name:
                seed_modules.update({'quick_tuning', 'quick_workshop'})
            elif 'mixdown' in name:
                seed_modules.add('quick_mixdown')
                direct_tests.add('test_quick_mixdown_state.py')
            else:
                direct_tests.add('test_workspace_routes.py')
        elif rel.startswith('assets/i18n/'):
            direct_tests.add('test_i18n.py')
        elif rel.startswith('packaging/') or rel.startswith('tools/Prepare-Source'):
            direct_tests.add('test_source_layout.py')
        elif rel.startswith('src/native/vwa_chorus_pitch'):
            seed_modules.add('chorus_pitch_dsp')

    # Transitive closure over src modules (bounded depth=2, stopping fan-out
    # through app-wide hub modules unless the hub itself was directly edited).
    hub_modules = {'common', 'quick_bridge', 'quick_app', 'workspace_controller', 'i18n', 'storage'}
    affected = set(seed_modules)
    queue = deque((m, 0) for m in seed_modules)
    while queue:
        mod, depth = queue.popleft()
        if depth >= 2:
            continue
        for dependent in rev_graph.get(mod, ()):
            if dependent in hub_modules and dependent not in seed_modules:
                continue
            if dependent not in affected:
                affected.add(dependent)
                queue.append((dependent, depth + 1))

    selected = set(direct_tests)
    for tname, imps in test_imports.items():
        if imps & affected:
            selected.add(tname)
    return sorted(selected & known)


if __name__ == '__main__':
    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    paths = sys.argv[2:]
    print(json.dumps(impacted_tests(repo_root, paths)))
