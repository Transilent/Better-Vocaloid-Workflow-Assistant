"""Exercise the real mix dialog, isolated subprocess and explicit task binding."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
component = os.environ.get('VWA_MIX_TEST_PEDALBOARD_PATH', '')
if not component:
    candidate = APP / 'dependencies/optional/pedalboard/current/packages'
    component = str(candidate) if candidate.is_dir() else ''
if not component:
    print('SKIP: No isolated Pedalboard component selected for the mixdown GUI test.')
    raise SystemExit(0)
component = Path(component).resolve()
if not (component / 'pedalboard').is_dir() and (component / 'unpacked/pedalboard').is_dir():
    component = component / 'unpacked'
if not (component / 'pedalboard').is_dir():
    raise ValueError('Pedalboard component directory does not contain the package')

import numpy as np
import soundfile as sf
from PyQt5.QtWidgets import QApplication, QWidget
import audio_mixdown_ui as ui
import task_contract as contract

app = QApplication.instance() or QApplication([])
owner = QWidget()
ui._OPTIONAL_PACKAGES = str(component)


def wait(dialog):
    deadline = time.monotonic() + 90
    while dialog.busy() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.02)
    app.processEvents()
    if dialog.busy():
        raise AssertionError('The mixdown background operation timed out')


with tempfile.TemporaryDirectory(prefix='audio-mix-gui-', dir=APP / 'work') as temp:
    root = Path(temp)
    job = root / 'job'
    job.mkdir()
    (job / 'request.json').write_text(json.dumps({'language': 'zh', 'voice_mode': 'single', 'tools': {}}), encoding='utf-8')
    (job / 'status.json').write_text(json.dumps({'state': 'done', 'steps': {}}), encoding='utf-8')
    rate = 44100
    t = np.arange(rate * 2, dtype=np.float32) / rate
    vocal, backing, output = root/'vocal.wav', root/'backing.wav', root/'mix.wav'
    sf.write(str(vocal), .15 * np.sin(2 * np.pi * 440 * t), rate, subtype='PCM_24')
    sf.write(str(backing), .1 * np.sin(2 * np.pi * 220 * t), rate, subtype='PCM_24')
    vocal_before, backing_before = vocal.read_bytes(), backing.read_bytes()
    dialog = ui.AudioMixdownDialog(owner, job, owner)
    dialog.show()
    app.processEvents()
    assert dialog._support_ready, dialog._support_error
    dialog.fields['vocal'].setText(str(vocal))
    dialog.fields['instrumental'].setText(str(backing))
    dialog.fields['output'].setText(str(output))
    dialog.reverb_enabled.setChecked(True)
    dialog.reverb_profile.setCurrentIndex(dialog.reverb_profile.findData('wide'))
    dialog.preview_duration.setValue(1)
    dialog.preview()
    wait(dialog)
    assert dialog._preview_result, dialog.status.text()
    assert all(Path(dialog._preview_result[key]['output_path']).is_file() for key in ('before', 'after'))
    dialog.export()
    wait(dialog)
    assert output.is_file(), dialog.status.text()
    assert vocal.read_bytes() == vocal_before and backing.read_bytes() == backing_before
    dialog.register()
    wait(dialog)
    assert dialog.registered, dialog.status.text()
    assert contract.selected(job, 'mixdown') == output.resolve()
    metadata = json.loads((job / 'task.json').read_text(encoding='utf-8-sig'))
    assert metadata['audio_mixdown']['parameters']['vocal_path'] == str(vocal.resolve())
    assert metadata['audio_mixdown']['parameters']['instrumental_path'] == str(backing.resolve())
    assert metadata['audio_mixdown']['parameters']['reverb_profile'] == 'wide'
    assert metadata['bindings']['mixdown']['entry']['dependencies'] == []
    dialog.close()
    app.processEvents()
print('PASS: real mix dialog previews, renders via child process, and explicitly registers the WAV')
