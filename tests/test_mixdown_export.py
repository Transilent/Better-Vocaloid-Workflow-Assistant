"""Qt-free safety checks for the frozen simple-mixdown export transaction."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import mixdown_export as export
import mixdown_runtime as runtime
from audio_mixdown import MixdownCancelled


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expect(exception, function):
    try:
        function()
    except exception:
        return
    raise AssertionError('%s was not raised' % exception.__name__)


def make_request(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    vocal = Path(root) / 'vocal.wav'
    backing = Path(root) / 'backing.wav'
    output = Path(root) / 'mix.wav'
    vocal.write_bytes(b'vocal-source')
    backing.write_bytes(b'backing-source')
    return {
        'vocals': [{'path': str(vocal), 'gain_db': -3.0,
                    'offset_seconds': .1, 'mute': False, 'solo': False,
                    'pan': .2, 'reverb_send': .3}],
        'instrumental_path': str(backing),
        'output_path': str(output),
    }


def report_for(request, payload=b'encoded-wav'):
    output = Path(request['output_path'])
    output.write_bytes(payload)
    return {
        'output_path': str(output),
        'source_sha256': {
            str(Path(row['path']).resolve()): digest(row['path'])
            for row in request['vocals']
        } | {str(Path(request['instrumental_path']).resolve()):
             digest(request['instrumental_path'])},
        'output_sha256': digest(output),
        'duration_seconds': .25,
        'peak_dbfs': -9.0,
        'warnings': [],
    }


assert not any(name.startswith('PyQt5') for name in sys.modules)

work = APP / 'work'
work.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(
        prefix='mixdown-export-test-', dir=work,
        ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    parameters = make_request(root / 'success')
    plan = export.plan_export(parameters, source_job=root / 'job')
    parameters['vocals'][0]['offset_seconds'] = 99.0
    assert plan.request['vocals'][0]['offset_seconds'] == .1
    assert plan.parameters['vocals'][0]['offset_seconds'] == .1
    assert Path(plan.request['output_path']) == plan.temporary
    assert plan.destination.suffix == '.wav'
    assert plan.temporary.parent == plan.destination.parent

    request_copy = plan.request
    request_copy['vocals'][0]['offset_seconds'] = 77.0
    parameters_copy = plan.parameters
    parameters_copy['vocals'][0]['offset_seconds'] = 66.0
    receipt_copy = plan.receipt_context()
    receipt_copy['parameters']['vocals'][0]['offset_seconds'] = 55.0
    assert plan.request['vocals'][0]['offset_seconds'] == .1
    assert plan.parameters['vocals'][0]['offset_seconds'] == .1
    assert plan.receipt_context()['parameters']['vocals'][0]['offset_seconds'] == .1

    events = []
    executed_requests = []
    original_request = copy.deepcopy(plan.request)
    def successful_render(request, check, progress=None):
        executed_requests.append(copy.deepcopy(request))
        if progress:
            progress({'event': 'progress', 'phase': 'mix', 'fraction': .5})
        return report_for(request)
    with patch.object(export, 'render_once', side_effect=successful_render):
        report = export.execute(
            plan, lambda: False,
            lambda event: events.append(dict(event)))
    assert report['output_path'] == str(plan.destination)
    assert report['source_files_unchanged'] is True
    assert plan.destination.read_bytes() == b'encoded-wav'
    assert not plan.temporary.exists()
    assert plan.request == original_request
    assert executed_requests[0]['vocals'][0]['offset_seconds'] == .1
    assert events == [{'event': 'progress', 'phase': 'mix', 'fraction': .5}]

    # A source mutation invalidates the frozen signature and leaves no output.
    parameters = make_request(root / 'source-change')
    plan = export.plan_export(parameters)
    def mutate_source(request, check, progress=None):
        result = report_for(request)
        Path(request['vocals'][0]['path']).write_bytes(b'changed-source')
        return result
    with patch.object(export, 'render_once', side_effect=mutate_source):
        expect(ValueError, lambda: export.execute(plan, lambda: False))
    assert not plan.destination.exists()
    assert not plan.temporary.exists()

    # Missing backend source fingerprints are rejected, including cleanup.
    parameters = make_request(root / 'missing-hashes')
    plan = export.plan_export(parameters)
    def no_hashes(request, check, progress=None):
        result = report_for(request)
        result.pop('source_sha256')
        return result
    with patch.object(export, 'render_once', side_effect=no_hashes):
        expect(ValueError, lambda: export.execute(plan, lambda: False))
    assert not plan.destination.exists()
    assert not plan.temporary.exists()

    # Cancellation after render is distinct and uses the shared transaction.
    parameters = make_request(root / 'cancel')
    plan = export.plan_export(parameters)
    cancel = {'requested': False}
    def cancelled(request, check, progress=None):
        result = report_for(request)
        cancel['requested'] = True
        return result
    with patch.object(export, 'render_once', side_effect=cancelled):
        expect(MixdownCancelled,
               lambda: export.execute(plan, lambda: cancel['requested']))
    assert not plan.destination.exists()
    assert not plan.temporary.exists()

    # A destination created during render wins; its bytes are never replaced.
    parameters = make_request(root / 'race')
    plan = export.plan_export(parameters)
    def race_destination(request, check, progress=None):
        result = report_for(request)
        plan.destination.write_bytes(b'created-by-someone-else')
        return result
    with patch.object(export, 'render_once', side_effect=race_destination):
        expect(FileExistsError, lambda: export.execute(plan, lambda: False))
    assert plan.destination.read_bytes() == b'created-by-someone-else'
    assert not plan.temporary.exists()

    # Only an explicit module double without __file__ bypasses the worker.
    injected = types.ModuleType('audio_mixdown')
    def injected_render(request, cancel=None, progress=None):
        if progress:
            progress({'phase': 'mix', 'fraction': .5})
        return report_for(request)
    injected.render = injected_render
    with patch.dict(sys.modules, {'audio_mixdown': injected}):
        injected_parameters = make_request(root / 'injected')
        preview_request = dict(injected_parameters, output_path=str(Path(root / 'injected') / 'injected.wav'))
        result = export.render_once(preview_request, lambda: False)
    assert Path(result['output_path']).is_file()

    # Runtime package caching happens only after a successful import check.
    package = root / 'pedalboard-packages'
    package.mkdir()
    fake = types.ModuleType('pedalboard')
    fake.__version__ = '0.9.26'
    with patch.dict(sys.modules, {'pedalboard': fake}):
        runtime.set_cached_optional_packages(None)
        assert runtime.activate_pedalboard(package)[0]
        assert runtime.cached_optional_packages() == str(package.resolve())
        paths = runtime.worker_environment()['VWA_MIXDOWN_EXTRA_PATHS'].split(os.pathsep)
        assert str(package.resolve()) in paths
        fake.__version__ = '0.9.25'
        runtime.set_cached_optional_packages(None)
        assert not runtime.activate_pedalboard(package)[0]
        assert runtime.cached_optional_packages() is None

print('PASS: frozen export, injected backend, cleanup/cancellation/no-overwrite, and runtime cache verified')