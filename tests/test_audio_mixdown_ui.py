"""Exercise the real multi-vocal dialog, safe export and optional task binding."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

import numpy as np
import soundfile as sf
from PyQt5.QtWidgets import QApplication, QLineEdit, QWidget
import audio_mixdown_ui as ui
import task_contract as contract


app = QApplication.instance() or QApplication([])


class VideoHandoff(QWidget):
    def __init__(self):
        super().__init__()
        self.fields = {'mixdown': QLineEdit(), 'video': QLineEdit(), 'output': QLineEdit()}
        self.loaded = None
        self.busy_state = False
        self.preview_busy_state = False
        self.load_result = True
        self.source_job = None

    def busy(self):
        return self.busy_state

    def preview_busy(self):
        return self.preview_busy_state

    def use_source_job(self, job, mixdown=None):
        if not self.load_result:
            return False
        self.loaded = (Path(job).resolve() if job else None, Path(mixdown).resolve())
        self.fields['mixdown'].setText(str(mixdown))
        return True


def wait(dialog):
    deadline = time.monotonic() + 30
    while dialog.busy() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    if dialog.busy():
        raise AssertionError('The mixdown background operation timed out')


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fake_render(request, check):
    if check and check():
        raise ui._Cancelled('test cancellation')
    vocals = request.get('vocals')
    assert isinstance(vocals, list) and 1 <= len(vocals) <= 3
    assert all(set(row) == {'path', 'gain_db', 'offset_seconds'} for row in vocals)
    assert not {'vocal_path', 'vocal_db', 'offset_seconds'} & set(request)
    assert Path(request['instrumental_path']).is_file()
    output = Path(request['output_path'])
    output.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    samples = np.zeros(rate // 10, dtype=np.float32)
    sf.write(str(output), samples, rate, subtype='PCM_24')
    sources = [row['path'] for row in vocals] + [request['instrumental_path']]
    return {
        'output_path': str(output.resolve()),
        'duration_seconds': .1,
        'peak_dbfs': -144.0,
        'source_sha256': {str(Path(path).resolve()): source_hash(path) for path in sources},
        'output_sha256': source_hash(output),
    }


with tempfile.TemporaryDirectory(prefix='audio-mix-gui-', dir=APP / 'work') as temp:
    root = Path(temp)
    preview_root = root / 'previews'
    preview_root.mkdir()
    tempfile.tempdir = str(preview_root)
    job = root / 'job'
    job.mkdir()
    (job / 'request.json').write_text(
        json.dumps({'language': 'zh', 'voice_mode': 'single', 'tools': {}}), encoding='utf-8')
    (job / 'status.json').write_text(
        json.dumps({'state': 'done', 'steps': {}}), encoding='utf-8')

    rate = 8000
    t = np.arange(rate // 10, dtype=np.float32) / rate
    vocals = [root / ('vocal-{0}.wav'.format(index)) for index in range(1, 4)]
    backing, output = root / 'backing.wav', root / 'mix.wav'
    for index, path in enumerate(vocals, 1):
        sf.write(str(path), .12 * np.sin(2 * np.pi * (220 + 110 * index) * t),
                 rate, subtype='PCM_24')
    sf.write(str(backing), .08 * np.sin(2 * np.pi * 110 * t), rate, subtype='PCM_24')
    source_before = {path: path.read_bytes() for path in (*vocals, backing)}

    owner = QWidget()
    owner.video_mixdown = VideoHandoff()
    navigation = []
    owner.navigate = navigation.append

    with patch.object(ui, '_render_once', side_effect=fake_render), \
            patch.object(ui, '_activate_pedalboard', return_value=(True, '0.9.26')):
        dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
        owner.show()
        dialog.show()
        app.processEvents()

        assert dialog.embedded
        assert not dialog.isWindow()
        assert dialog.close_button.isHidden()
        assert dialog._support_ready, dialog._support_error
        assert dialog.job is None
        assert len(dialog.active_vocal_tracks()) == 1
        assert dialog.vocal_tracks[1]['panel'].isHidden()

        dialog.fields['vocal'].setText(str(vocals[0]))
        dialog.vocal_db.setValue(-1.5)
        dialog.offset.setValue(.125)
        dialog.fields['instrumental'].setText(str(backing))
        dialog.fields['output'].setText(str(output))
        assert dialog.set_job(job)
        assert dialog.fields['output'].text() == str(output)
        assert dialog.set_job(None)
        assert dialog.fields['output'].text() == str(output)
        single = dialog._request(root / 'legacy.wav')
        assert len(single['vocals']) == 1
        assert single['vocals'][0] == {
            'path': str(vocals[0].resolve()), 'gain_db': -1.5, 'offset_seconds': .125,
        }
        assert not {'vocal_path', 'vocal_db', 'offset_seconds'} & set(single)

        dialog.add_vocal_track()
        dialog.add_vocal_track()
        assert len(dialog.active_vocal_tracks()) == 3
        expected_gain = (0.0, -4.0, 3.5)
        expected_offset = (0.0, .25, -.125)
        for index, track in enumerate(dialog.vocal_tracks):
            assert not track['panel'].isHidden()
            track['path'].setText(str(vocals[index]))
            track['gain_db'].setValue(expected_gain[index])
            track['offset'].setValue(expected_offset[index])

        dialog.reverb_enabled.setChecked(True)
        dialog.reverb_profile.setCurrentIndex(dialog.reverb_profile.findData('wide'))
        dialog.preview_duration.setValue(1)

        existing = root / 'existing.wav'
        existing.write_bytes(b'keep this file')
        dialog.fields['output'].setText(str(existing))
        dialog.export()
        assert not dialog.busy()
        assert '无法开始导出' in dialog.status.text()
        assert existing.read_bytes() == b'keep this file'
        dialog.fields['output'].setText(str(output))
        app.processEvents()

        dialog.preview()
        wait(dialog)
        assert dialog._preview_result, dialog.status.text()
        assert all(Path(dialog._preview_result[key]['output_path']).is_file()
                   for key in ('before', 'after'))
        preview_requests = [row for row in ui._render_once.call_args_list[:2]]
        assert len(preview_requests) == 2
        for call, bypass in zip(preview_requests, (True, False)):
            request = call.args[0]
            assert len(request['vocals']) == 3
            assert request['reverb_profile'] == 'wide'
            assert (request['reverb_mix'] == 0.0) is bypass

        dialog.export()
        wait(dialog)
        assert output.is_file(), dialog.status.text()
        assert all(path.read_bytes() == source_before[path] for path in (*vocals, backing))
        receipt = dialog._export_receipt
        assert receipt['schema'] == 'vwa.audio_mixdown.receipt/v2'
        assert len(receipt['inputs']['vocals']) == 3
        assert all(len(entry['sha256']) == 64 for entry in receipt['inputs']['vocals'])
        assert len(receipt['inputs']['instrumental_path']['sha256']) == 64
        assert len(receipt['output']['sha256']) == 64
        assert [row['gain_db'] for row in receipt['parameters']['vocals']] == list(expected_gain)
        assert [row['offset_seconds'] for row in receipt['parameters']['vocals']] == list(expected_offset)
        assert 'vocal_path' not in receipt['parameters']
        assert dialog.register_button.isHidden()

        owner.video_mixdown.preview_busy_state = True
        assert not dialog.open_video()
        assert not owner.video_mixdown.fields['mixdown'].text()
        owner.video_mixdown.preview_busy_state = False
        owner.video_mixdown.source_job = root / 'unrelated-task'
        assert dialog.continue_to_video()
        assert Path(owner.video_mixdown.fields['mixdown'].text()).resolve() == output.resolve()
        assert owner.video_mixdown.loaded is None
        assert owner.video_mixdown.source_job is None
        assert navigation == [8]

        assert dialog.set_job(job)
        app.processEvents()
        other_mixdown = root / 'other-mixdown.wav'
        other_mixdown.write_bytes(b'do not overwrite')
        owner.video_mixdown.fields['mixdown'].setText(str(other_mixdown))
        owner.video_mixdown.load_result = False
        assert not dialog.open_video()
        assert Path(owner.video_mixdown.fields['mixdown'].text()) == other_mixdown
        owner.video_mixdown.fields['mixdown'].clear()
        owner.video_mixdown.source_job = root / 'unrelated-task'
        assert dialog.open_video()
        assert Path(owner.video_mixdown.fields['mixdown'].text()).resolve() == output.resolve()
        assert owner.video_mixdown.source_job is None
        assert not dialog.register_button.isHidden()
        assert dialog.register_button.isEnabled()
        dialog.register()
        wait(dialog)
        assert dialog.registered, dialog.status.text()
        assert dialog.result() == 0
        assert dialog.isVisible()

        assert contract.selected(job, 'mixdown') == output.resolve()
        metadata = json.loads((job / 'task.json').read_text(encoding='utf-8-sig'))
        saved = metadata['audio_mixdown']
        assert [row['path'] for row in saved['parameters']['vocals']] == [
            str(path.resolve()) for path in vocals]
        assert saved['parameters']['instrumental_path'] == str(backing.resolve())
        assert saved['parameters']['reverb_profile'] == 'wide'
        assert len(saved['inputs']['vocals']) == 3
        assert all(len(entry['sha256']) == 64 for entry in saved['inputs']['vocals'])
        assert len(saved['inputs']['instrumental_path']['sha256']) == 64
        assert len(saved['output']['sha256']) == 64
        assert metadata['bindings']['mixdown']['entry']['dependencies'] == []

        dialog.close()
        standard = ui.AudioMixdownDialog(owner, job, owner)
        assert not standard.embedded
        assert standard.isWindow()
        assert not standard.close_button.isHidden()
        standard.close()
        owner.close()
        app.processEvents()

tempfile.tempdir = None
component = os.environ.get('VWA_MIX_TEST_PEDALBOARD_PATH')
if not component:
    installed = APP / 'dependencies/optional/pedalboard/current/packages'
    component = str(installed) if installed.is_dir() else None
if component:
    # Exercise the actual Qt → child process → FFmpeg/Pedalboard path, not just
    # a renderer double. The backend's own tests verify the numerical result.
    with tempfile.TemporaryDirectory(prefix='audio-mix-real-', dir=APP / 'work') as temp:
        root = Path(temp)
        paths = [root / ('vocal-%d.wav' % i) for i in range(3)]
        backing, output = root / 'backing.wav', root / 'real-mix.wav'
        t = np.arange(8000, dtype=np.float32) / 8000
        for index, path in enumerate(paths):
            sf.write(str(path), .05 * np.sin(2*np.pi*(220+index*110)*t), 8000)
        sf.write(str(backing), .04*np.sin(2*np.pi*110*t), 8000)
        owner = QWidget()
        owner.video_mixdown = VideoHandoff()
        owner.navigate = lambda index: None
        with patch.object(ui, '_optional_packages', return_value=Path(component)):
            dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
            assert dialog._support_ready, dialog._support_error
            dialog.add_vocal_track(); dialog.add_vocal_track()
            for index, track in enumerate(dialog.vocal_tracks):
                track['path'].setText(str(paths[index]))
                track['offset'].setValue(index * .05)
            dialog.fields['instrumental'].setText(str(backing))
            dialog.fields['output'].setText(str(output))
            dialog.preview_duration.setValue(.3)
            dialog.preview(); wait(dialog)
            assert dialog._preview_result, dialog.status.text()
            dialog.export(); wait(dialog)
            assert output.is_file(), dialog.status.text()
            assert len(dialog._export_receipt['inputs']['vocals']) == 3
            dialog.close(); owner.close(); app.processEvents()
else:
    print('SKIP: actual GUI renderer requires the optional Pedalboard test component')

print('PASS: embedded mixdown UI handles 3 vocal tracks, shared effects, safe export, video handoff and optional registration')
