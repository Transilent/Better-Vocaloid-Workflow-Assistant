"""Pure fixtures for the optional Chinese Qwen downloader; no GUI or weights."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

import check_dependencies as checks
import dependency_profiles as profiles
import download_sources
import feature_components as features
from common import native_path

REVISION = 'a' * 40
PAYLOADS = {
    'qwen_fixture_backend.onnx': b'backend fixture payload',
    'qwen_fixture_llm.gguf': b'language model fixture payload',
}


def fixture_spec(root):
    rows = []
    for name, payload in PAYLOADS.items():
        relative = 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/' + name
        rows.append({
            'file': relative,
            'bytes': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest(),
            'url': 'https://huggingface.co/fixture/Qwen3-ASR/resolve/' + REVISION + '/' + name,
        })
    spec = {
        'name': 'Chinese lyrics · Qwen3-ASR fixture',
        'files': rows,
        'bytes': sum(item['bytes'] for item in rows),
        'revision': REVISION,
        'upstream': 'https://huggingface.co/fixture/Qwen3-ASR',
    }
    (root / 'components').mkdir(parents=True)
    (root / 'components/runtime-features.json').write_text(
        json.dumps({'format': 1, 'features': {'chinese': spec}}, ensure_ascii=False), encoding='utf-8')
    return spec, rows


class FakeDownload:
    def __init__(self, *, fail=None, bad_hash=None, cancel_after=None):
        self.calls = []
        self.fail = set(fail or ())
        self.bad_hash = set(bad_hash or ())
        self.cancel_after = set(cancel_after or ())

    def __call__(self, item, folder, progress, check=None, source='auto'):
        self.calls.append((dict(item), Path(folder), source))
        assert item['url'].startswith('https://huggingface.co/')
        assert len(item['sha256']) == 64
        target = Path(folder) / item['name']
        payload = PAYLOADS[item['name']]
        if item['name'] in self.fail:
            (Path(folder) / (item['name'] + '.partial')).write_bytes(payload[:max(1, len(payload) // 2)])
            raise OSError('fixture download interrupted')
        if item['name'] in self.bad_hash:
            target.write_bytes(b'x' * len(payload))
        elif not target.is_file() or target.read_bytes() != payload:
            target.write_bytes(payload)
        progress(len(payload), len(payload), '正在下载 · 固定测试源')
        del check  # The caller must enforce the activation boundary after return.
        return target


def install_fixture(root, rows, fake, cancel=None):
    check = cancel if cancel is not None else None
    with patch.object(features, 'ROOT', root), \
         patch.object(features, 'dependency_manifest', return_value={'entries': rows}), \
         patch('optional_components.download', side_effect=fake):
        return features.install('chinese', lambda *args: None, check, source='mirror')


def status_fixture(root, rows):
    with patch.object(features, 'ROOT', root), \
         patch.object(features, 'dependency_manifest', return_value={'entries': rows}), \
         patch.object(profiles, 'PACKAGE_ROOT', root), \
         patch.object(profiles, 'dependency_manifest', return_value={'entries': rows}):
        return profiles.status(['chinese'])['chinese']


TEST_TMP = Path(native_path(APP / 'work/temp'))
TEST_TMP.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='cf-', dir=TEST_TMP) as temporary:
    base = Path(temporary)

    # A later-file failure preserves the verified target and cache, never ready.
    root = base / 'partial'
    _, rows = fixture_spec(root)
    with_json = FakeDownload(fail={'qwen_fixture_llm.gguf'})
    try:
        install_fixture(root, rows, with_json)
        raise AssertionError('Partial multi-file install reported success')
    except OSError as error:
        assert 'interrupted' in str(error)
    state = status_fixture(root, rows)
    assert not state['ready'] and state['state'] == 'partial' and state['complete_files'] == 1
    assert state['partial_bytes'] > 0 and state['downloaded_bytes'] < state['bytes']
    resumed = FakeDownload()
    result = install_fixture(root, rows, resumed)
    assert result['ready'] and result['verified']
    assert [call[0]['name'] for call in resumed.calls] == ['qwen_fixture_llm.gguf']
    assert all(call[2] == 'mirror' for call in resumed.calls)
    assert status_fixture(root, rows)['ready']

    # Cancellation after download but before activation keeps only cache state.
    root = base / 'cancel'
    _, rows = fixture_spec(root)
    cancelled = {'flag': False}
    fake = FakeDownload(cancel_after={'qwen_fixture_backend.onnx'})
    def cancel_check():
        return cancelled['flag']
    def cancel_download(item, folder, progress, check=None, source='auto'):
        result = fake(item, folder, progress, check, source)
        if item['name'] in fake.cancel_after:
            cancelled['flag'] = True
        return result
    try:
        install_fixture(root, rows, cancel_download, cancel_check)
        raise AssertionError('Cancel at activation boundary was ignored')
    except InterruptedError:
        pass
    state = status_fixture(root, rows)
    assert not state['ready'] and state['complete_files'] == 0 and state['partial_bytes'] > 0
    assert not (root / rows[0]['file']).exists()
    cancelled['flag'] = False
    resumed = FakeDownload()
    assert install_fixture(root, rows, resumed, cancel_check)['ready']

    # A downloader result with the right size but wrong hash is never activated.
    root = base / 'bad-hash'
    _, rows = fixture_spec(root)
    try:
        install_fixture(root, rows, FakeDownload(bad_hash={'qwen_fixture_backend.onnx'}))
        raise AssertionError('Bad SHA-256 was accepted')
    except ValueError as error:
        assert 'SHA-256' in str(error)
    assert not (root / rows[0]['file']).exists()
    assert not status_fixture(root, rows)['ready']

    # An intact feature is a verification/no-op; the original installer is unused.
    root = base / 'healthy'
    _, rows = fixture_spec(root)
    for row in rows:
        target = root / row['file']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PAYLOADS[Path(row['file']).name])
    no_network = FakeDownload()
    result = install_fixture(root, rows, no_network)
    assert result['ready'] and result['verified'] and not result['repaired']
    assert no_network.calls == []
    assert status_fixture(root, rows)['ready']

    # Same-size corruption stays visible as needing verification, then repairs.
    root = base / 'same-size-repair'
    _, rows = fixture_spec(root)
    for row in rows:
        target = root / row['file']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PAYLOADS[Path(row['file']).name])
    corrupt = root / rows[0]['file']
    corrupt.write_bytes(b'X' * len(PAYLOADS[Path(rows[0]['file']).name]))
    assert status_fixture(root, rows)['ready']
    with patch.object(features, 'ROOT', root), \
         patch.object(features, 'dependency_manifest', return_value={'entries': rows}):
        deep = features.status('chinese', verify=True)
    assert not deep['ready'] and deep['state'] == 'damaged'
    failed_repair = FakeDownload(fail={Path(rows[0]['file']).name})
    try:
        install_fixture(root, rows, failed_repair)
        raise AssertionError('Failed same-size repair reported success')
    except OSError:
        pass
    quick = status_fixture(root, rows)
    assert not quick['ready'] and quick['state'] == 'damaged', quick
    repair = FakeDownload()
    result = install_fixture(root, rows, repair)
    assert result['ready'] and result['repaired']
    assert [call[0]['name'] for call in repair.calls] == [Path(rows[0]['file']).name]
    assert corrupt.read_bytes() == PAYLOADS[Path(rows[0]['file']).name]

    # Manifest paths cannot escape the application root.
    root = base / 'escape'
    spec, rows = fixture_spec(root)
    escaped = [dict(rows[0], file='../outside.bin'), rows[1]]
    spec['files'][0]['file'] = '../outside.bin'
    (root / 'components/runtime-features.json').write_text(
        json.dumps({'format': 1, 'features': {'chinese': spec}}, ensure_ascii=False), encoding='utf-8')
    never = FakeDownload()
    with patch.object(features, 'ROOT', root), \
         patch.object(features, 'dependency_manifest', return_value={'entries': escaped}), \
         patch('optional_components.download', side_effect=never):
        try:
            features.install('chinese', lambda *args: None, source='mirror')
            raise AssertionError('Escaping target path was accepted')
        except ValueError as error:
            assert '路径' in str(error)
    assert never.calls == [] and not (base / 'outside.bin').exists()

from components_ui import blocking_reason, chinese_button_text, download_source_options
assert chinese_button_text({'ready': False}) == '下载中文模型'
assert chinese_button_text({'ready': True}) == '校验 / 修复'
assert chinese_button_text({'ready': False, 'installed': True}) == '校验 / 修复'
with patch.object(download_sources, 'SOURCE_OPTIONS', (
        ('官方', 'direct'), ('自动', 'auto'), ('镜像', 'mirror'),
        ('GitHub', 'github'), ('国内镜像', 'cn'), ('自定义', 'custom'), ('离线', 'offline'))):
    options = download_source_options()
assert [value for _, value in options[:3]] == ['auto', 'direct', 'mirror']
assert len(options) == 7
assert [value for _, value in download_source_options()[:3]] == ['auto', 'direct', 'mirror']
busy_owner = SimpleNamespace(
    processing_active=False, runner=None,
    storage=SimpleNamespace(busy=lambda: False),
    updates=SimpleNamespace(busy=lambda: True),
    publishing=None, video_mixdown=None, tuning=None, calibration_dialog=None,
)
assert blocking_reason(busy_owner) == '更新下载'

with tempfile.TemporaryDirectory(prefix='lf-', dir=TEST_TMP) as temporary:
    root = Path(temporary)
    chinese_file = 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/backend.bin'
    japanese_file = 'dependencies/vocal2midi/experiments/romajiASR/model.bin'
    japanese = b'Japanese fixture remains local'
    target = root / japanese_file
    target.parent.mkdir(parents=True)
    target.write_bytes(japanese)
    entries = [
        {'file': chinese_file, 'bytes': 11, 'sha256': '0' * 64},
        {'file': japanese_file, 'bytes': len(japanese), 'sha256': hashlib.sha256(japanese).hexdigest()},
    ]
    (root / 'dependencies').mkdir(exist_ok=True)
    (root / 'dependencies/manifest.json').write_text(
        json.dumps({'entries': entries}, ensure_ascii=False), encoding='utf-8')
    config = {'python': sys.executable, 'ffmpeg': sys.executable}
    with patch.object(checks, 'ROOT', root), patch.object(checks, 'load_config', return_value=config):
        japanese_status = checks.check(features={'japanese'})
        chinese_status = checks.check(features={'chinese'})
        assert checks.check(features={'core'})['state'] == 'passed'
        assert japanese_status['state'] == 'passed'
        assert japanese_status['feature_states']['japanese']['state'] == 'ready'
        assert 'chinese' not in japanese_status['feature_states']
        assert chinese_status['state'] == 'failed'
        assert chinese_status['feature_states']['chinese']['state'] == 'missing'
        with patch.object(profiles, 'PACKAGE_ROOT', root), \
             patch.object(profiles, 'dependency_manifest', return_value={'entries': entries}):
            assert profiles.status(['japanese'])['japanese']['ready']
            assert not profiles.status(['chinese'])['chinese']['ready']
        checks.validate({'japanese'})
        try:
            checks.validate({'chinese'})
            raise AssertionError('Missing Chinese model was accepted')
        except FileNotFoundError as error:
            message = str(error)
            assert '下载中文模型' in message and '模型与组件' in message
            assert '日语识别' in message
            from error_recovery import explain
            assert explain(message)['action'] == 'components'
        chinese_target = root / chinese_file
        chinese_target.parent.mkdir(parents=True, exist_ok=True)
        chinese_target.write_bytes(b'x' * 11)
        japanese_target = root / japanese_file
        japanese_target.unlink()
        with patch.object(checks, 'ROOT', root), patch.object(checks, 'load_config', return_value=config):
            japanese_missing = checks.check(features={'japanese'})
            assert japanese_missing['state'] == 'failed'
            assert japanese_missing['feature_states']['japanese']['state'] == 'missing'
            try:
                checks.validate({'japanese'})
                raise AssertionError('Missing Japanese model was accepted')
            except FileNotFoundError as error:
                message = str(error)
                assert '日语模型缺失' in message and '下载中文模型' not in message
    request_ja = {'language': 'ja', 'recognize_lyrics': True, 'ui_language': 'en',
                  'tools': {'vocal2midi': str(root / 'dependencies/vocal2midi')},
                  'project_formats': []}
    request_notes = dict(request_ja, language='zh', recognize_lyrics=False)
    with patch.object(profiles, 'dependency_manifest', return_value={'entries': entries}), \
         patch('common.resolve_tools', side_effect=lambda tools: tools):
        japanese_features = profiles.task_features(request_ja, {'midi'})
        notes_features = profiles.task_features(request_notes, {'midi'})
    assert 'japanese' in japanese_features and 'chinese' not in japanese_features
    assert 'japanese' not in notes_features and 'chinese' not in notes_features

print('PASS: Chinese Qwen resume, cancel boundary, pinned hash rejection, shared sources and language isolation')
