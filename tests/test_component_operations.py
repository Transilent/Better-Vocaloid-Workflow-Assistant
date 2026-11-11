"""Pure, isolated contracts for the shared component operation layer."""
from pathlib import Path
import sys
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))

import component_operations as operations


BASE_MANIFEST = {
    'weights': [{'bytes': 100}],
    'shared_wheels': [{'bytes': 20}],
    'torch_wheels': {'cpu': {'bytes': 30}, 'cuda': {'bytes': 300}},
    'install_bytes': {'cpu': 200, 'cuda': 2000},
}
READY = {
    'ready': True,
    'runtime': 'cuda',
    'verification': {'level': 'metadata', 'content_verified': False,
                     'sha256_checked': False, 'state': 'metadata_ready'},
}
CORE = {'core': {'ready': True, 'state': 'ready', 'bytes': 40,
                 'installed_bytes': 40}}
PASSED = {'state': 'passed', 'errors': [], 'mode': 'startup'}
CHINESE_PARTIAL = {
    'ready': False, 'state': 'partial', 'bytes': 1000,
    'complete_bytes': 250, 'complete_files': 1, 'files': 4,
    'partial_bytes': 125, 'installed': False,
}
EXPRESSION_MISSING = {'ready': False, 'bytes': 0, 'version': '0.3.5'}


def catalog(runtime='cuda', *, core=CORE, check_result=PASSED,
            roformer=None, chinese=CHINESE_PARTIAL,
            expression=EXPRESSION_MISSING, manifest=BASE_MANIFEST,
            roformer_root=Path('missing-roformer'),
            expression_root=Path('missing-expression')):
    roformer = READY if roformer is None else roformer
    with patch.object(operations.dependency_profiles, 'status', return_value=core), \
         patch.object(operations.check_dependencies, 'check', return_value=check_result), \
         patch.object(operations.optional_components, 'status', return_value=roformer), \
         patch.object(operations.optional_components, 'manifest', return_value=manifest), \
         patch.object(operations.optional_components, 'COMPONENT', roformer_root), \
         patch.object(operations.feature_components, 'status', return_value=chinese), \
         patch.object(operations.expression_component, 'status', return_value=expression), \
         patch.object(operations.expression_component, 'SPEC', {'bytes': 70}), \
         patch.object(operations.expression_component, 'COMPONENT', expression_root):
        return operations.catalog(runtime)


rows = catalog('cuda')
assert [row['id'] for row in rows] == list(operations.COMPONENT_IDS)
assert all(tuple(row) == operations.ROW_KEYS for row in rows)
assert all(set(row) == set(operations.ROW_KEYS) for row in rows)
assert all(row['status'] in operations.STATUSES for row in rows)
by_id = {row['id']: row for row in rows}
assert by_id['builtin']['status'] == 'ready' and by_id['builtin']['can_use']
assert by_id['builtin']['download_bytes'] == 0
assert by_id['builtin']['required_bytes'] == 40
assert by_id['roformer']['status'] == 'ready' and by_id['roformer']['can_use']
assert by_id['roformer']['download_bytes'] == 420
assert by_id['roformer']['installed_bytes'] == 2000
assert by_id['roformer']['required_bytes'] == 2420 + 512 * 1024**2
assert by_id['chinese']['status'] == 'partial'
assert by_id['chinese']['installed_bytes'] == 250
assert by_id['chinese']['can_verify']
assert by_id['expression']['status'] == 'missing'

# Basic dependencies are checked by both the project tools and manifest scan.
failed_core = {'core': {'ready': False, 'state': 'missing', 'bytes': 40,
                        'installed_bytes': 0}}
builtin_missing = catalog(check_result={'state': 'failed', 'errors': []},
                          core=failed_core)[0]
assert builtin_missing['status'] == 'missing' and not builtin_missing['can_use']

# The installed CPU runtime must not make the selected CUDA target look usable.
cpu_ready = dict(READY, runtime='cpu')
roformer_cpu = catalog('cuda', roformer=cpu_ready)[1]
assert roformer_cpu['status'] == 'missing'
assert roformer_cpu['status_text'] == '当前目标未安装'
assert roformer_cpu['installed_bytes'] == 0
assert roformer_cpu['can_install'] and not roformer_cpu['can_verify']
assert not roformer_cpu['can_use'] and 'CPU' in roformer_cpu['detail']

# Dispatch preserves the existing service call shapes and callbacks.
progress = lambda done, total, text: None
cancel = lambda: False
sentinel = object()
with patch.object(operations.optional_components, 'install', return_value=sentinel) as install:
    assert operations.install('roformer', runtime='cuda', source='mirror',
                              progress=progress, check=cancel, force=True) is sentinel
    install.assert_called_once_with('cuda', progress, cancel, force=True, source='mirror')
with patch.object(operations.feature_components, 'install', return_value=sentinel) as install:
    assert operations.install('chinese', source='direct', progress=progress,
                              check=cancel, force=True) is sentinel
    install.assert_called_once_with('chinese', progress, cancel, source='direct')
with patch.object(operations.expression_component, 'install', return_value=sentinel) as install:
    assert operations.install('expression', source='auto', progress=progress,
                              check=cancel) is sentinel
    install.assert_called_once_with(progress, cancel, source='auto')

report = {'verified': True, 'state': 'verified'}
with patch.object(operations.optional_components, 'full_verify', return_value=report) as verify:
    assert operations.verify('roformer', progress=progress, check=cancel) is report
    verify.assert_called_once_with(progress=progress, check=cancel)
with patch.object(operations.feature_components, 'verify', return_value=report) as verify:
    assert operations.verify('chinese', progress=progress, check=cancel) is report
    verify.assert_called_once_with('chinese', progress=progress, check=cancel)
with patch.object(operations.expression_component, 'verify', return_value=True):
    world = operations.verify('expression')
assert world['verified'] is True and world['state'] == 'verified'
assert 'SHA-256' in world['scope'] and '不加载扩展' in world['scope']

with patch.object(operations.download_sources, 'SOURCE_OPTIONS', (
        ('官方', 'direct'), ('自动', 'auto'), ('镜像', 'mirror'),
        ('GitHub', 'gitproxy'), ('自定义', 'custom'), ('重复', 'custom'))):
    options = operations.download_source_options()
assert [value for _, value in options] == ['auto', 'direct', 'mirror', 'gitproxy', 'custom']

print('PASS: shared component catalog, runtime isolation, dispatch and verification contracts')
