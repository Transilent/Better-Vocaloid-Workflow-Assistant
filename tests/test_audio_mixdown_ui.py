"""Exercise the real multi-vocal dialog, safe export and optional task binding."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

import numpy as np
import soundfile as sf
from PyQt5.QtCore import QObject, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPalette
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QWidget
import audio_mixdown_ui as ui
import task_contract as contract


app = QApplication.instance() or QApplication([])

silence_detail = ui._report_detail({'duration_seconds': .1, 'peak_dbfs': -144.0})
assert '静音' in silence_detail and '-144.00 dBFS' not in silence_detail
assert ui._db_to_fraction(0.0) > .75
assert abs(ui._fraction_to_db(ui._db_to_fraction(-6.0)) + 6.0) < .01

# A long song advances to an overlapping segment before the current one ends;
# a selected loop and the true final segment must not start another render.
continuation = SimpleNamespace(
    _player_loop=False, _player_seeking=False, _preview_is_playing=lambda: True,
    _preview_dirty=False, preview_busy=lambda: False,
    _preview_debounce=SimpleNamespace(isActive=lambda: False),
    _player=SimpleNamespace(transitioning=False),
    _preview_result={'after': {'source_duration_seconds': 36.0}},
    _preview_segment_start_seconds=0.0, _preview_segment_duration_seconds=12.0,
    _preview_resume_seconds=None, _preview_force_both=False,
    _schedule_preview_refresh=Mock())
ui.AudioMixdownDialog._maybe_continue_preview(continuation, 9500)
continuation._schedule_preview_refresh.assert_not_called()
ui.AudioMixdownDialog._maybe_continue_preview(continuation, 10200)
continuation._schedule_preview_refresh.assert_called_once_with(autoplay=True, immediate=True)
assert continuation._preview_resume_seconds == 10.2 and continuation._preview_force_both
continuation._schedule_preview_refresh.reset_mock()
continuation._player_loop = True
ui.AudioMixdownDialog._maybe_continue_preview(continuation, 11000)
continuation._schedule_preview_refresh.assert_not_called()

# A late rerender can finish after the source segment has reached EndOfMedia.
# Preserve an earlier play intent, clamp into the rendered window, but never
# override an explicit pause that happened while the request was in flight.
late_result = SimpleNamespace(
    _preview_is_playing=lambda: False,
    _preview_play_intent=True,
    _preview_resume_seconds=8.0,
    _absolute_player_seconds=lambda: 8.0,
    preview_duration=SimpleNamespace(value=lambda: 2.0),
)
late_context = {'start': 6.0, 'was_playing': True, 'play_intent': True}
late_after = {
    'preview_start_seconds': 6.0, 'duration_seconds': 2.0,
    'source_duration_seconds': 8.0,
    'maximum_source_duration_seconds': 14.0,
}
autoplay, relative = ui.AudioMixdownDialog._preview_transport_after_result(
    late_result, late_context, late_after)
assert autoplay is True and relative == 1750, (autoplay, relative)
late_result._preview_play_intent = False
autoplay, relative = ui.AudioMixdownDialog._preview_transport_after_result(
    late_result, late_context, late_after)
assert autoplay is False and relative == 1750, (autoplay, relative)

# An after-only tail update may reuse A only if both sides describe the same
# rendered timeline. maximum_source_duration_seconds is a render bound, not an
# actual ending and must not be used as the alignment length.
assert ui.AudioMixdownDialog._preview_reports_aligned(
    {'preview_start_seconds': 6.0, 'duration_seconds': 2.0,
     'source_duration_seconds': 8.0},
    {'preview_start_seconds': 6.0, 'duration_seconds': 2.0,
     'source_duration_seconds': 8.0,
     'maximum_source_duration_seconds': 14.0},
)
assert not ui.AudioMixdownDialog._preview_reports_aligned(
    {'preview_start_seconds': 6.0, 'duration_seconds': 1.75,
     'source_duration_seconds': 7.75},
    {'preview_start_seconds': 6.0, 'duration_seconds': 2.25,
     'source_duration_seconds': 8.25,
     'source_duration_measured': True},
)

# One close click during component probing must survive the handoff into the
# resident service startup instead of disappearing when the probe finishes.
close_timer = QTimer()
close_probe = SimpleNamespace(
    _close_pending=False,
    _close_wait_timer=close_timer,
    busy=lambda: False,
    support_checking=lambda: True,
    preview_busy=lambda: False,
    reject=Mock(),
)
close_probe._try_finish_pending_close = (
    lambda: ui.AudioMixdownDialog._try_finish_pending_close(close_probe))
close_timer.timeout.connect(
    lambda: ui.AudioMixdownDialog._try_finish_pending_close(close_probe))
assert ui.AudioMixdownDialog._defer_pending_close(close_probe) is False
QTest.qWait(45)
assert close_probe._close_pending and close_timer.isActive()
close_probe.reject.assert_not_called()
close_probe.support_checking = lambda: False
close_probe.preview_busy = lambda: True
QTest.qWait(45)
assert close_probe._close_pending and close_timer.isActive()
close_probe.reject.assert_not_called()
close_probe.preview_busy = lambda: False
QTest.qWait(60)
assert not close_probe._close_pending and not close_timer.isActive()
close_probe.reject.assert_called_once_with()
continuation._player_loop = False
continuation._preview_result['after']['source_duration_seconds'] = 12.0
ui.AudioMixdownDialog._maybe_continue_preview(continuation, 11000)
continuation._schedule_preview_refresh.assert_not_called()


class FakeMediaContent:
    def __init__(self, url=None):
        self.url = url

    def isNull(self):
        return self.url is None


class FakeMediaPlayer(QObject):
    PlayingState = 1
    PausedState = 2
    StoppedState = 0
    EndOfMedia = 6
    InvalidMedia = 7
    NoMedia = 0
    LoadedMedia = 2
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(int)
    mediaStatusChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.media_value = FakeMediaContent()
        self.position_value = 0
        self.state_value = self.StoppedState
        self.volume_value = 72
        self.play_count = 0

    def setVolume(self, value):
        self.volume_value = value

    def volume(self):
        return self.volume_value

    def setMedia(self, media):
        self.media_value = media
        self.position_value = 0
        self.mediaStatusChanged.emit(self.LoadedMedia)

    def media(self):
        return self.media_value

    def source(self):
        if self.media_value.url is None:
            return None
        return self.media_value.url.toLocalFile()

    def play(self):
        self.play_count += 1
        self.state_value = self.PlayingState
        self.stateChanged.emit(self.state_value)

    def pause(self):
        self.state_value = self.PausedState
        self.stateChanged.emit(self.state_value)

    def stop(self):
        self.state_value = self.StoppedState
        self.stateChanged.emit(self.state_value)

    def state(self):
        return self.state_value

    def position(self):
        return self.position_value

    def setPosition(self, position):
        self.position_value = max(0, int(position))
        self.positionChanged.emit(self.position_value)


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
    while (dialog.busy() or dialog.support_checking() or dialog.preview_busy()
           or dialog._preview_debounce.isActive()) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    if (dialog.busy() or dialog.support_checking() or dialog.preview_busy()
            or dialog._preview_debounce.isActive()):
        raise AssertionError('The mixdown background operation timed out')


def wait_cleanup(dialog):
    deadline = time.monotonic() + 5
    while not dialog._audition_cleanup_complete and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    thread = dialog._audition_close_thread
    if thread is not None:
        thread.join(.2)
    if not dialog._audition_cleanup_complete:
        raise AssertionError('The resident audition service did not close')


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fake_audition(payload, check):
    if check and check():
        raise ui._Cancelled('test audition cancellation')
    request = payload['request']
    vocals = request['vocals']
    rate = 8000
    variant = str(payload.get('variant') or 'both')
    for key in (('before_output', 'after_output') if variant == 'both'
                else (('after_output',) if variant == 'after' else ('before_output',))):
        target = Path(payload[key])
        target.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(target), np.zeros(rate // 10, dtype=np.float32), rate,
                 subtype='PCM_24')
    any_solo = any(row['solo'] and not row['mute'] for row in vocals)
    rows = [
        {'role': 'vocal', 'index': index, 'peak_dbfs': -12.0 - index * 3.0,
         'mute': row['mute'], 'solo': row['solo'], 'pan': row['pan'],
         'gain_db': row['gain_db'], 'offset_seconds': row['offset_seconds'],
         'reverb_send': row['reverb_send'],
         'audible': not row['mute'] and (not any_solo or row['solo'])}
        for index, row in enumerate(vocals)
    ] + [{'role': 'backing', 'index': 0, 'peak_dbfs': -8.0,
          'mute': request.get('instrumental_mute', False),
          'solo': request.get('instrumental_solo', False),
          'pan': request.get('instrumental_pan', 0.0),
          'gain_db': request.get('instrumental_db', 0.0),
          'audible': not request.get('instrumental_mute', False) and not any_solo}]
    start = float(request.get('preview_start_seconds', 0.0))
    duration = float(request.get('preview_duration_seconds', .1))

    def report(path, bypass):
        return {
            'output_path': str(Path(path).resolve()),
            'duration_seconds': duration,
            'preview_start_seconds': start,
            'peak_dbfs': -6.0,
            'tracks': rows,
            'meters': {'tracks': rows, 'bus_peak_dbfs': -6.0},
            'peak_envelope': {'hz': 10.0, 'bus': [-8.0, -6.0, -12.0]},
            'gr_envelope': {'hz': 10.0, 'vocal:0': [-1.0, -3.0, -2.0],
                            'bus': [-2.0, -4.0, -3.0]},
            'gr_db_p95': -3.2,
            'reverb_edc': {
                'schema': 'vwa.audio_mixdown.reverb-edc/v1',
                'measured': True,
                'profile': 'natural',
                'predelay_ms': 0.0,
                'times_ms': [0.0, 20.0, 40.0, 80.0],
                'db': [0.0, -6.0, -12.0, -24.0],
                'duration_ms': 80.0,
                'cache_hit': False,
            },
            'bypass_processing': bypass,
        }
    result = {
        'cache_hit': False,
        'schema': 'vwa.audio_mixdown.audition/v1',
        'requested_variant': variant,
        'rendered_variants': (
            ['before', 'after'] if variant == 'both'
            else [variant]
        ),
    }
    if variant in ('before', 'both'):
        result['before'] = report(payload['before_output'], True)
    if variant in ('after', 'both'):
        result['after'] = report(payload['after_output'], False)
        result['peak_envelope'] = result['after']['peak_envelope']
        result['gr_envelope'] = result['after']['gr_envelope']
        result['gr_db_p95'] = result['after']['gr_db_p95']
        result['reverb_edc'] = result['after']['reverb_edc']
    return result


class FakeResidentAuditionService:
    """Small polling double with the production service's async contract."""

    instances = []
    render_delay = .03

    def __init__(self, cache_dir=None, **kwargs):
        self.cache_dir = cache_dir
        self.lock = threading.Lock()
        self.alive = False
        self.closed = False
        self.close_count = 0
        self.start_count = 0
        self.requests = []
        self.events = []
        self.active = None
        self._counter = 0
        type(self).instances.append(self)

    @property
    def is_alive(self):
        return self.alive

    @property
    def pid(self):
        return 4242 if self.alive else None

    def start(self):
        self.start_count += 1
        self.alive = True
        return {'type': 'ready', 'pid': self.pid, 'tick_hz': 30.0}

    def request(self, request, before_output=None, after_output=None,
                cache_dir=None, variant='after', *, merge=True):
        self._counter += 1
        request_id = 'fake-%d' % self._counter
        payload = {
            'request': dict(request),
            'before_output': str(before_output) if before_output else None,
            'after_output': str(after_output) if after_output else None,
            'cache_dir': str(cache_dir or self.cache_dir),
            'variant': str(variant),
        }
        with self.lock:
            self.requests.append({
                'request_id': request_id, 'variant': variant, 'merge': bool(merge),
                'request': dict(request), 'payload': payload,
            })
            self.active = {'id': request_id, 'payload': payload,
                           'started': time.monotonic(), 'cancelled': False}
        return request_id

    def cancel(self, request_id=None):
        with self.lock:
            if self.active is not None and (request_id is None or self.active['id'] == request_id):
                self.active['cancelled'] = True

    def poll(self, *, timeout=0.0, max_events=None):
        del timeout, max_events
        finished = None
        with self.lock:
            active = self.active
            if active is not None:
                if active['cancelled']:
                    finished = {'type': 'cancelled', 'request_id': active['id'],
                                'reason': 'cancel_requested'}
                elif time.monotonic() - active['started'] >= self.render_delay:
                    try:
                        report = ui._audition_once(active['payload'], None)
                    except Exception as error:
                        finished = {'type': 'error', 'request_id': active['id'],
                                    'message': str(error)}
                    else:
                        finished = {'type': 'result', 'request_id': active['id'],
                                    'report': report}
                if finished is not None:
                    self.active = None
        return [finished] if finished is not None else []

    def close(self, timeout=None):
        del timeout
        self.close_count += 1
        self.closed = True
        self.alive = False
        return 0


def fake_render(request, check, progress=None):
    if check and check():
        raise ui._Cancelled('test cancellation')
    vocals = request.get('vocals')
    assert isinstance(vocals, list) and 1 <= len(vocals) <= 3
    assert all(set(row) == {'path', 'gain_db', 'offset_seconds', 'mute', 'solo', 'pan', 'reverb_send'}
               for row in vocals)
    assert not {'vocal_path', 'vocal_db', 'offset_seconds'} & set(request)
    assert Path(request['instrumental_path']).is_file()
    output = Path(request['output_path'])
    output.parent.mkdir(parents=True, exist_ok=True)
    rate = 8000
    samples = np.zeros(rate // 10, dtype=np.float32)
    sf.write(str(output), samples, rate, subtype='PCM_24')
    sources = [row['path'] for row in vocals] + [request['instrumental_path']]
    any_solo = any(row['solo'] and not row['mute'] for row in vocals)
    backing_audible = not request.get('instrumental_mute', False) and not any_solo
    meter_tracks = [
        {'role': 'vocal', 'index': index, 'peak_dbfs': -12.0 - index * 3.0,
         'mute': row['mute'], 'solo': row['solo'], 'pan': row['pan'],
         'audible': not row['mute'] and (not any_solo or row['solo'])}
        for index, row in enumerate(vocals)
    ] + [{'role': 'backing', 'index': 0, 'peak_dbfs': -8.0,
          'mute': request.get('instrumental_mute', False),
          'solo': request.get('instrumental_solo', False),
          'pan': request.get('instrumental_pan', 0.0),
          'audible': backing_audible}]
    if progress:
        progress({'event': 'progress', 'phase': 'mix', 'fraction': 0.5,
                  'tracks': meter_tracks, 'bus_peak': -6.0})
    return {
        'output_path': str(output.resolve()),
        'duration_seconds': .1,
        'peak_dbfs': -144.0,
        'meters': {'tracks': meter_tracks, 'bus_peak_dbfs': -6.0},
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

    FakeResidentAuditionService.instances = []
    with patch.object(ui, '_render_once', side_effect=fake_render), \
            patch.object(ui, '_audition_once', side_effect=fake_audition), \
            patch.object(ui, 'MixdownAuditionService', FakeResidentAuditionService), \
            patch.object(ui, '_activate_pedalboard', return_value=(True, '0.9.26')):
        dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
        owner.show()
        dialog.show()
        app.processEvents()
        wait(dialog)

        assert dialog._audition_service_ready
        assert dialog._audition_service is not None
        assert dialog._audition_poll_timer.isActive()
        assert len(dialog._mixdown_shortcuts) == 6
        dialog._shortcut_select_analysis_tab(1)
        assert dialog.analysis_tabs.currentIndex() == 1
        dialog._shortcut_select_analysis_tab(2)
        assert dialog.analysis_tabs.currentIndex() == 2
        dialog._shortcut_select_analysis_tab(0)
        assert dialog.analysis_tabs.currentIndex() == 0
        assert dialog.embedded
        assert not dialog.isWindow()
        assert dialog.close_button.isHidden()
        assert dialog._support_ready, dialog._support_error
        assert dialog.analysis_tabs.objectName() == 'mixdownAnalysisTabs'
        assert dialog.analysis_tabs.count() == 3
        assert [dialog.analysis_tabs.tabText(i) for i in range(3)] == ['EQ', 'GR', 'EDC']
        assert 190 <= dialog.analysis_tabs.minimumHeight()
        assert dialog.analysis_tabs.sizePolicy().verticalPolicy() == ui.QSizePolicy.Expanding
        assert all(not page.autoFillBackground()
                   for page in (dialog.eq_page, dialog.gr_page, dialog.edc_page))
        assert dialog.job is None
        assert len(dialog.active_vocal_tracks()) == 1
        assert dialog.vocal_tracks[1]['panel'].isHidden()
        assert isinstance(dialog.vocal_slider, ui._DbFader)
        assert dialog.vocal_slider.value() == 0
        dialog.vocal_slider.setFocus()
        QTest.keyClick(dialog.vocal_slider, Qt.Key_Up)
        assert dialog.vocal_slider.value() == 5
        assert dialog.vocal_db.value() == .5
        QTest.keyClick(dialog.vocal_slider, Qt.Key_Up, Qt.ShiftModifier)
        assert dialog.vocal_slider.value() == 6
        QTest.mouseDClick(dialog.vocal_slider, Qt.LeftButton)
        assert dialog.vocal_slider.value() == 0
        assert dialog.vocal_db.value() == 0.0
        console = dialog.findChild(ui._ResponsiveMixerConsole)
        assert console is not None
        scroll = dialog.findChild(QScrollArea)
        assert scroll is not None
        assert console.column_count() == 2, console.column_count()
        assert dialog.workspace.column_count() == 1
        dialog.resize(1600, 900)
        app.processEvents()
        assert dialog.workspace.column_count() == 3
        assert not dialog.workspace._right_stack.isHidden()
        dialog.resize(1920, 1080)
        app.processEvents()
        assert dialog.workspace.column_count() == 3
        assert scroll.verticalScrollBar().maximum() == 0
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.analysis_tabs.height() >= 300
        assert dialog.eq_curve.height() >= 300
        assert dialog.processing_card.minimumSizeHint().height() <= dialog.processing_card.height()
        dialog.resize(2512, 1352)
        app.processEvents()
        assert scroll.verticalScrollBar().maximum() == 0
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.workspace.height() >= 1100
        assert dialog.analysis_tabs.height() >= 800
        assert dialog.eq_curve.height() >= 700
        assert dialog.mixer_card.height() >= dialog.workspace.height() - 1
        assert dialog.processing_card.height() >= dialog.workspace.height() - 1
        assert (dialog.preview_card.height() + dialog.export_card.height()
                >= dialog.workspace.height() - 20)
        dialog.resize(1100, 800)
        app.processEvents()
        assert dialog.workspace.column_count() == 2
        assert dialog.workspace._right_stack.isHidden()
        dialog.resize(640, 560)
        app.processEvents()
        assert console.column_count() == 2, console.column_count()
        assert scroll.horizontalScrollBar().maximum() == 0
        dialog.resize(920, 840)
        app.processEvents()
        assert dialog.workspace.column_count() == 1
        assert dialog.eq_enabled.isChecked() is False
        assert len(dialog.eq_curve.bands()) == 4
        dialog.eq_enabled.setChecked(True)
        dialog.presence.setValue(2.5)
        app.processEvents()
        bell = next(b for b in dialog.eq_curve.bands() if b['type'] == 'bell')
        assert bell['gain_db'] == 2.5

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
            'path': str(vocals[0].resolve()), 'gain_db': -1.5,
            'offset_seconds': .125, 'mute': False, 'solo': False, 'pan': 0.0,
            'reverb_send': 1.0,
        }
        assert not {'vocal_path', 'vocal_db', 'offset_seconds'} & set(single)

        dialog.add_vocal_track()
        dialog.add_vocal_track()
        app.processEvents()
        assert len(dialog.active_vocal_tracks()) == 3
        pan_control = dialog.vocal_tracks[0]['pan']
        pan_control.setValue(25)
        QTest.mouseDClick(pan_control, Qt.LeftButton)
        assert pan_control.value() == 0
        assert dialog.vocal_tracks[0]['pan_value'].text() == 'C'
        expected_gain = (0.0, -4.0, 3.5)
        expected_offset = (0.0, .25, -.125)
        expected_pan = (-.25, .30, 0.0)
        expected_mute = (False, False, True)
        expected_solo = (False, True, False)
        for index, track in enumerate(dialog.vocal_tracks):
            assert not track['panel'].isHidden()
            track['path'].setText(str(vocals[index]))
            track['gain_db'].setValue(expected_gain[index])
            track['offset'].setValue(expected_offset[index])
            track['pan'].setValue(round(expected_pan[index] * 100))
            track['mute'].setChecked(expected_mute[index])
            track['solo'].setChecked(expected_solo[index])
        dialog.instrumental_pan.setValue(10)
        app.processEvents()
        assert dialog.vocal_tracks[0]['panel'].property('channelFiltered') is True
        assert dialog.vocal_tracks[1]['panel'].property('channelSolo') is True
        assert dialog.vocal_tracks[2]['panel'].property('channelMuted') is True
        assert dialog.backing_panel.property('channelFiltered') is True
        assert console.column_count() == 2
        dialog.resize(1600, 1000)
        app.processEvents()
        assert dialog.workspace.column_count() == 3
        assert console.column_count() == 4
        strip_tops = [strip.mapTo(console, QPoint(0, 0)).y()
                      for strip in console.active_strips()]
        assert len(strip_tops) == 4 and len(set(strip_tops)) == 1
        assert scroll.verticalScrollBar().maximum() <= 8
        assert scroll.horizontalScrollBar().maximum() == 0
        dialog.resize(round(1920 / 1.30), round(1080 / 1.30))
        app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.workspace.column_count() == 3
        screenshot_dir = APP / 'work/test-results'
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        for width, height, name in (
                (1920, 1080, 'mixdown-1920x1080.png'),
                (1600, 1000, 'mixdown-1600x1000.png'),
                (2512, 1352, 'mixdown-2512x1352.png'),
                (1477, 831, 'mixdown-1920x1080-at-130pct.png')):
            dialog.resize(width, height)
            app.processEvents()
            screenshot = screenshot_dir / name
            assert dialog.grab().save(str(screenshot))
            assert screenshot.is_file() and screenshot.stat().st_size > 0
        if hasattr(ui._desktop_theme, 'apply_mixdown_analysis_palette'):
            for mode in ('light', 'dark'):
                dialog._vwa_appearance = {'material': 'solid'}
                ui._desktop_theme.apply_theme(dialog, mode)
                dialog._refresh_analysis_palette()
                palette = dialog.eq_curve.palette()
                contrast = abs(palette.color(QPalette.Text).lightness()
                               - palette.color(QPalette.Base).lightness())
                assert contrast >= 40, (mode, contrast)
                screenshot = screenshot_dir / ('mixdown-eq-%s.png' % mode)
                assert dialog.grab().save(str(screenshot))
                assert screenshot.is_file() and screenshot.stat().st_size > 0
        dialog.resize(640, 560)
        app.processEvents()
        assert console.column_count() == 2
        rects = []
        for strip in console.active_strips():
            origin = strip.mapTo(console, QPoint(0, 0))
            rects.append(QRect(origin, strip.size()))
        tops = sorted({rect.top() for rect in rects})
        assert len(tops) == 2
        assert [sum(rect.top() == top for rect in rects) for top in tops] == [2, 2]
        assert scroll.horizontalScrollBar().maximum() == 0
        dialog.resize(920, 840)
        app.processEvents()
        assert console.column_count() == 2
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

        wait(dialog)
        assert len(FakeResidentAuditionService.instances) == 1
        resident = FakeResidentAuditionService.instances[0]
        assert resident.start_count == 1
        assert dialog._audition_service is resident
        assert resident.requests
        assert resident.requests[0]['variant'] == 'both'
        assert resident.requests[0]['merge'] is True
        first_payload = resident.requests[0]['payload']
        assert first_payload['before_output'] != first_payload['after_output']
        assert dialog._preview_result, (dialog.status.text(), dialog.preview_report.text())
        assert dialog._player_key == 'after'
        assert dialog.preview_after_button.isChecked()
        assert not hasattr(dialog, 'preview_button')
        assert all(Path(dialog._preview_result[key]['output_path']).is_file()
                   for key in ('before', 'after'))
        assert ui._audition_once.call_count >= 1
        request = ui._audition_once.call_args_list[-1].args[0]['request']
        assert len(request['vocals']) == 3
        assert request['reverb_profile'] == 'wide'
        assert [row['mute'] for row in request['vocals']] == list(expected_mute)
        assert [row['solo'] for row in request['vocals']] == list(expected_solo)
        assert [row['pan'] for row in request['vocals']] == list(expected_pan)
        assert request['instrumental_pan'] == .1
        assert request['preview_start_seconds'] == 0.0
        assert request['preview_duration_seconds'] == 2.0
        assert len(request['eq_bands']) == 4
        assert request['width'] == 1.0
        assert request['reverb_predelay_ms'] == 0.0
        assert [round(row['reverb_send'], 6) for row in request['vocals']] == [1.0, 1.0, 1.0]
        assert [shortcut.key().toString() for shortcut in dialog._mixdown_shortcuts] == [
            'Alt+1', 'Alt+2', 'Alt+3', 'Ctrl+Shift+A', 'Ctrl+Shift+B', 'Ctrl+Shift+L']
        dialog.player_loop_button.setEnabled(True)
        dialog._shortcut_toggle_loop()
        assert dialog.player_loop_button.isChecked()
        dialog._shortcut_toggle_loop()
        assert not dialog.player_loop_button.isChecked()

        screenshot_dir = APP / 'work/test-results'
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        nav_panel = QWidget()
        nav_panel.setFixedWidth(300)
        from workspace_ui import AudioMixdownPage
        real_page = AudioMixdownPage(owner)
        real_window_dialog = real_page.mixer
        real_window_dialog._schedule_preview_refresh = lambda *args, **kwargs: False
        shell_layout.addWidget(nav_panel)
        shell_layout.addWidget(real_page, 1)
        shell.resize(1600, 1000)
        shell.show()
        wait(real_window_dialog)
        real_window_dialog.add_vocal_track()
        real_window_dialog.add_vocal_track()
        for index, track in enumerate(real_window_dialog.active_vocal_tracks()):
            track['path'].setText(str(vocals[index]))
        real_window_dialog.fields['instrumental'].setText(str(backing))
        real_window_dialog.fields['output'].setText(str(root / 'window-output.wav'))
        app.processEvents()
        real_scroll = real_window_dialog.findChild(QScrollArea)
        assert real_window_dialog.workspace.column_count() == 3
        assert 520 <= real_window_dialog.mixer_card.width() <= 660
        assert real_window_dialog.fields['output'].width() >= 160
        assert (real_window_dialog.processing_card.width()
                >= ui._MixerWorkspace.SIDE_CARD_MIN_WIDTH)
        viewport = real_scroll.viewport()
        eq_view = real_window_dialog.eq_curve
        eq_rect = QRect(eq_view.mapTo(viewport, QPoint(0, 0)), eq_view.size())
        bypass_local = eq_view.bypass_rect().toRect()
        bypass_rect = QRect(
            eq_view.mapTo(viewport, bypass_local.topLeft()), bypass_local.size())
        assert eq_view.width() >= ui._ResponsiveEQCurveView.COMPACT_MINIMUM_WIDTH
        assert eq_view.bypass_rect().width() >= 94.0
        assert eq_rect.left() >= viewport.rect().left()
        assert eq_rect.right() <= viewport.rect().right()
        assert bypass_rect.left() >= viewport.rect().left()
        assert bypass_rect.right() <= viewport.rect().right()
        assert viewport.rect().contains(bypass_rect)
        assert real_scroll.horizontalScrollBar().maximum() == 0
        assert real_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        shell.resize(1100, 800)
        app.processEvents(); app.processEvents()
        assert real_window_dialog.workspace.column_count() == 1
        narrow_viewport = real_scroll.viewport()
        narrow_eq = QRect(
            eq_view.mapTo(narrow_viewport, QPoint(0, 0)), eq_view.size())
        assert narrow_eq.left() >= narrow_viewport.rect().left()
        assert narrow_eq.right() <= narrow_viewport.rect().right()
        assert real_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        shell.resize(1600, 1000)
        app.processEvents(); app.processEvents()
        assert real_window_dialog.workspace.column_count() == 3
        assert real_window_dialog.mixer_console.column_count() == 4
        assert real_window_dialog.vocal_tracks[0]['slider'].minimumHeight() == 124
        assert real_window_dialog.vocal_tracks[2]['remove'].width() <= 26
        assert real_window_dialog.vocal_tracks[2]['remove'].text() == '×'
        compact_title = real_window_dialog.vocal_tracks[2]['panel'].findChild(
            QLabel, 'mixer-channel-title')
        assert compact_title is not None and compact_title.isHidden()
        real_tops = [strip.mapTo(real_window_dialog.mixer_console, QPoint(0, 0)).y()
                     for strip in real_window_dialog.mixer_console.active_strips()]
        assert len(real_tops) == 4 and len(set(real_tops)) == 1
        assert real_scroll.verticalScrollBar().maximum() <= 8
        assert real_scroll.horizontalScrollBar().maximum() == 0
        window_shot = screenshot_dir / 'mixdown-window-1600x1000-with-nav.png'
        assert shell.grab().save(str(window_shot)) and window_shot.stat().st_size > 0
        real_window_dialog.close()
        wait_cleanup(real_window_dialog)
        shell.close()
        app.processEvents()

        previous_after = copy.deepcopy(dialog._preview_result['after'])
        previous_before_path = dialog._preview_result['before']['output_path']
        single_side = {
            'duration_seconds': .1,
            'peak_envelope': {'hz': 10.0, 'bus': [-7.0, -5.0]},
            'meters': {'tracks': previous_after['tracks'], 'bus_peak_dbfs': -5.0},
        }
        merged_single = dialog._merge_single_side_report(single_side, previous_after)
        assert merged_single['gr_envelope'] == previous_after['gr_envelope']
        assert merged_single['reverb_edc'] == previous_after['reverb_edc']
        dialog._apply_report_meters(single_side)
        assert dialog._preview_result['before']['output_path'] == previous_before_path
        assert dialog._preview_meter_report['gr_envelope'] == previous_after['gr_envelope']
        assert dialog._preview_meter_report['reverb_edc'] == previous_after['reverb_edc']
        assert dialog._preview_meter_report['peak_envelope']['bus'] == [-7.0, -5.0]

        def wait_for_variant(expected):
            wait(dialog)
            assert resident.requests[-1]['variant'] == expected, resident.requests[-1]
            return resident.requests[-1]

        dialog.vocal_tracks[0]['gain_db'].setValue(-1.0)
        changed = wait_for_variant('both')
        assert changed['payload']['before_output'] is not None
        assert dialog._preview_result['before']['tracks'][0]['gain_db'] == -1.0
        dialog.vocal_tracks[0]['pan'].setValue(-20)
        wait_for_variant('both')
        assert dialog._preview_result['before']['tracks'][0]['pan'] == -.2
        dialog.vocal_tracks[0]['mute'].setChecked(True)
        wait_for_variant('both')
        assert dialog._preview_result['before']['tracks'][0]['mute'] is True
        dialog.instrumental_db.setValue(-2.0)
        wait_for_variant('both')
        assert dialog._preview_result['before']['tracks'][-1]['gain_db'] == -2.0
        dialog.width_control.set_value(1.5)
        wait_for_variant('both')
        current_before = dialog._request(
            root / 'before-signature.wav', preview=True, bypass_processing=True)
        current_before.update(preview_start_seconds=dialog.preview_start.value(),
                              preview_duration_seconds=2.0)
        assert dialog._preview_before_signature == dialog._before_parameter_signature(current_before)
        dialog._select_preview('before', autoplay=False)
        assert dialog._player_key == 'before'
        assert dialog.preview_before_button.isChecked()
        assert dialog._preview_result['before']['tracks'][0]['pan'] == -.2

        # A/B selection follows the source that is actually audible. A failed
        # or cancelled candidate must roll back to the still-playing side.
        actual_after_path = Path(dialog._preview_result['after']['output_path'])
        actual_before_path = Path(dialog._preview_result['before']['output_path'])
        previous_player = dialog._player
        previous_fallback = dialog._system_player_fallback_available

        tail_copy_dir = root / 'tail-alignment-copy'
        tail_copy_dir.mkdir()
        stale_before = dict(dialog._preview_result['before'])
        stale_before['duration_seconds'] = float(stale_before['duration_seconds']) + 0.75
        assert dialog._carry_forward_before(
            tail_copy_dir, stale_before) is None
        assert not (tail_copy_dir / 'before.wav').exists()
        carried_before = dialog._carry_forward_before(
            tail_copy_dir, dialog._preview_result['before'])
        assert carried_before and Path(carried_before['output_path']).is_file()

        class CrossfadeProbe:
            available = True
            transitioning = False

            def set_loop(self, value):
                del value

            def state(self):
                return ui.PlaybackState.PlayingState

            def source(self):
                return str(actual_after_path)

            def position(self):
                return 250

            def load_crossfade(self, *args):
                del args
                return True

        dialog._player = CrossfadeProbe()
        assert dialog._select_preview('before', autoplay=True)
        assert dialog.preview_before_button.isChecked()
        dialog._preview_crossfade_cancelled(str(actual_before_path))
        assert dialog.preview_after_button.isChecked()
        assert dialog._player_key == 'after'

        class RejectingLoadProbe:
            available = True

            def set_loop(self, value):
                del value

            def source(self):
                return str(actual_after_path)

            def load(self, *args):
                del args
                return False

        dialog._player = RejectingLoadProbe()
        assert not dialog._select_preview('before', autoplay=False)
        assert dialog.preview_after_button.isChecked()
        assert dialog._player_key == 'after'

        class UnavailableProbe:
            available = False

        dialog._player = UnavailableProbe()
        dialog._system_player_fallback_available = False
        with patch.object(ui.QDesktopServices, 'openUrl', return_value=True) as open_url:
            assert dialog._select_preview('before', autoplay=True)
            open_url.assert_not_called()
            dialog._sync_controls()
            assert not dialog.system_player_button.isHidden()
            assert '仅播放当前' in dialog.system_player_button.toolTip()
            assert dialog._open_current_preview_in_system_player()
            open_url.assert_called_once()
            assert Path(open_url.call_args.args[0].toLocalFile()) == actual_before_path

        class AsyncInvalidProbe:
            available = True

            def set_loop(self, value):
                del value

            def source(self):
                return str(actual_after_path)

            def load_crossfade(self, *args):
                del args
                return True

        dialog._player = AsyncInvalidProbe()
        dialog._system_player_fallback_available = False
        with patch.object(ui.QDesktopServices, 'openUrl', return_value=True) as open_url:
            dialog._player_media_status_changed(ui.MediaStatus.NoMedia)
            assert not dialog._system_player_fallback_available
            dialog._player_media_status_changed(ui.MediaStatus.InvalidMedia)
            open_url.assert_not_called()
        assert dialog._system_player_fallback_available
        assert dialog.preview_after_button.isChecked()

        dialog._player = previous_player
        dialog._system_player_fallback_available = previous_fallback
        dialog._set_preview_selection('after')
        dialog._preview_actual_key = 'after'
        dialog._sync_controls()

        dialog.vocal_tracks[0]['gain_db'].setValue(expected_gain[0])
        dialog.vocal_tracks[0]['pan'].setValue(round(expected_pan[0] * 100))
        dialog.vocal_tracks[0]['mute'].setChecked(expected_mute[0])
        dialog.instrumental_db.setValue(0.0)
        dialog.width_control.set_value(1.0)
        wait_for_variant('both')

        resident.render_delay = .80
        request_count = len(resident.requests)
        old_after_path = Path(dialog._preview_result['after']['output_path'])
        previous_meter_report = copy.deepcopy(dialog._preview_meter_report)
        dialog.presence.setValue(3.0)
        assert dialog._preview_debounce.interval() == 90
        app.processEvents()
        assert len(resident.requests) == request_count
        QTest.qWait(120)
        app.processEvents()
        assert len(resident.requests) == request_count + 1
        changed = resident.requests[-1]
        assert changed['variant'] == 'after' and changed['merge'] is True
        assert changed['request']['eq_presence_db'] == 3.0
        assert Path(dialog._preview_result['after']['output_path']) == old_after_path
        assert dialog._preview_meter_report == previous_meter_report

        dialog.presence.setValue(3.5)
        app.processEvents()
        dialog.presence.setValue(4.0)
        QTest.qWait(120)
        app.processEvents()
        assert len(resident.requests) == request_count + 1
        wait(dialog)
        assert len(resident.requests) == request_count + 2
        assert resident.requests[-1]['request']['eq_presence_db'] == 4.0
        assert resident.requests[-1]['variant'] == 'after'
        assert resident.requests[-1]['merge'] is True
        resident.render_delay = .03
        dialog.presence.setValue(2.5)
        wait(dialog)
        # The production MixdownPlayer exposes seek(), not Qt's setPosition().
        # Keep this independent of the legacy FakeMediaPlayer below.
        class SeekOnly:
            def __init__(self): self.value = 0
            def position(self): return self.value
            def source(self): return 'already-loaded.wav'
            def seek(self, value): self.value = value; return True
        previous_player = dialog._player
        seek_player = SeekOnly()
        dialog._player = seek_player
        dialog._preview_segment_start_seconds = 0.0
        dialog._preview_segment_duration_seconds = 1.0
        dialog.player_position.setRange(0, 1000)
        dialog._begin_player_seek()
        dialog.player_position.setValue(410)
        dialog._seek_player_preview(410)
        assert seek_player.value == 410
        dialog._finish_player_seek()
        assert seek_player.value == 410 and not dialog._player_seeking
        dialog.player_position.setValue(420)  # keyboard/wheel path
        assert seek_player.value == 420
        seek_player.value = 500
        dialog._player_position_changed(500)  # player update must not seek again
        assert seek_player.value == 500
        dialog._preview_result['after']['source_duration_seconds'] = 30.0
        with patch.object(dialog, '_schedule_preview_refresh', return_value=True) as schedule:
            dialog._seek_to_song_position(21000, commit=True)
            assert dialog._preview_resume_seconds == 21.0 and dialog._preview_force_both
            schedule.assert_called_once_with(autoplay=False, immediate=True)
        dialog._preview_result['after'].pop('source_duration_seconds')
        dialog._player = previous_player
        with patch.object(ui, 'QMediaPlayer', FakeMediaPlayer), \
                patch.object(ui, 'QMediaContent', FakeMediaContent):
            fake_player = FakeMediaPlayer(dialog)
            dialog._player = fake_player
            dialog._select_preview('after', autoplay=False)
            fake_player.position_value = 300
            fake_player.state_value = FakeMediaPlayer.PlayingState
            dialog._player_position_changed(321)
            QTest.qWait(40)
            app.processEvents()
            dialog.meter_feed.flush()
            assert abs(dialog.meter_feed.current_position_ms - 321) < .1
            assert abs(dialog._edc_playback_position_ms - 321) < .1
            if hasattr(dialog.reverb_decay, 'feed'):
                assert dialog.reverb_decay.feed is dialog.meter_feed
            old_path = Path(dialog._preview_result['after']['output_path'])
            automatic_calls = ui._audition_once.call_count
            dialog.vocal_db.setValue(-2.0)
            app.processEvents()
            assert Path(dialog._preview_result['after']['output_path']) == old_path
            assert fake_player.media_value.url is not None
            wait(dialog)
            assert ui._audition_once.call_count > automatic_calls
            updated = ui._audition_once.call_args_list[-1].args[0]['request']
            assert updated['vocals'][0]['gain_db'] == -2.0
            assert dialog._preview_result is not None
            new_path = Path(dialog._preview_result['after']['output_path'])
            assert new_path != old_path
            assert Path(fake_player.media_value.url.toLocalFile()) == new_path
            assert fake_player.play_count >= 1
            assert fake_player.media_value.url is not None
            dialog.vocal_db.setValue(expected_gain[0])
            wait(dialog)
            fake_player.stop()
            dialog._player = None

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
        assert [row['mute'] for row in receipt['parameters']['vocals']] == list(expected_mute)
        assert [row['solo'] for row in receipt['parameters']['vocals']] == list(expected_solo)
        assert [row['pan'] for row in receipt['parameters']['vocals']] == list(expected_pan)
        assert receipt['parameters']['instrumental_pan'] == .1
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
        assert dialog.bus_meter._peak_db == -6.0
        assert '静音' in dialog.result_text.text()
        assert '-144.00 dBFS' not in dialog.result_text.text()

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

        # A task's automatically filled backing must not leak into another
        # task, nor may an export made under A be registered under B.
        other_job = root / 'other-job'
        other_job.mkdir()
        (other_job / 'request.json').write_text('{}', encoding='utf-8')
        (other_job / 'status.json').write_text('{"state":"done"}', encoding='utf-8')
        automatic_backing = job / 'audio/instrumental.wav'
        automatic_backing.parent.mkdir()
        automatic_backing.write_bytes(backing.read_bytes())
        dialog.fields['instrumental'].clear()
        assert dialog.set_job(job)
        assert Path(dialog.fields['instrumental'].text()) == automatic_backing.resolve()
        bound_output = root / 'task-a-mix.wav'
        dialog.fields['output'].setText(str(bound_output))
        wait(dialog)
        dialog.export(); wait(dialog)
        assert bound_output.is_file() and dialog._export_receipt['source_job'] == str(job.resolve())
        assert dialog.set_job(other_job)
        assert not dialog.fields['instrumental'].text()
        assert dialog._result is None and not dialog.register_button.isEnabled()
        assert bound_output.is_file()

        # Explicitly chosen backing is preserved, but a task-bound result is
        # still invalidated when the associated task changes.
        dialog.fields['instrumental'].setText(str(backing))
        assert dialog.set_job(job)
        second_output = root / 'task-a-manual-backing.wav'
        dialog.fields['output'].setText(str(second_output))
        wait(dialog)
        dialog.export(); wait(dialog)
        assert second_output.is_file()
        assert dialog.set_job(other_job)
        assert Path(dialog.fields['instrumental'].text()) == backing.resolve()
        assert dialog._result is None and not dialog.register_button.isEnabled()

        dialog.vocal_tracks[2]['mute'].setChecked(True)
        dialog.vocal_tracks[2]['solo'].setChecked(True)
        dialog.vocal_tracks[2]['pan'].setValue(-40)
        dialog.remove_vocal_track(2)
        assert not dialog.vocal_tracks[2]['mute'].isChecked()
        assert not dialog.vocal_tracks[2]['solo'].isChecked()
        assert dialog.vocal_tracks[2]['pan'].value() == 0
        dialog.add_vocal_track()
        assert not dialog.vocal_tracks[2]['mute'].isChecked()
        assert not dialog.vocal_tracks[2]['solo'].isChecked()
        assert dialog.vocal_tracks[2]['pan'].value() == 0

        starts_before_crash = resident.start_count
        preserved_gr = copy.deepcopy(dialog._preview_meter_report.get('gr_envelope'))
        preserved_edc = copy.deepcopy(dialog._preview_meter_report.get('reverb_edc'))
        dialog._audition_service_lost('simulated crash')
        dialog._audition_service_lost('duplicate crash event')
        assert resident.start_count == starts_before_crash
        assert not dialog._audition_poll_timer.isActive()
        assert dialog._preview_meter_report.get('gr_envelope') == preserved_gr
        assert dialog._preview_meter_report.get('reverb_edc') == preserved_edc

        final_preview_dir = Path(dialog._preview_directory) if dialog._preview_directory else None
        dialog.close()
        wait_cleanup(dialog)
        assert resident.close_count == 1
        assert not dialog._audition_poll_timer.isActive()
        assert resident.pid is None
        assert final_preview_dir is None or not final_preview_dir.exists()
        standard = ui.AudioMixdownDialog(owner, job, owner)
        app.processEvents()
        wait(standard)
        assert not standard.embedded
        assert standard.isWindow()
        assert not standard.close_button.isHidden()
        standard.close()
        wait_cleanup(standard)
        assert FakeResidentAuditionService.instances[-1].close_count == 1
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
            app.processEvents()
            wait(dialog)
            assert dialog._support_ready, dialog._support_error
            dialog.add_vocal_track(); dialog.add_vocal_track()
            for index, track in enumerate(dialog.vocal_tracks):
                track['path'].setText(str(paths[index]))
                track['offset'].setValue(index * .05)
            dialog.fields['instrumental'].setText(str(backing))
            dialog.fields['output'].setText(str(output))
            dialog.preview_duration.setValue(.3)
            dialog.preview(); wait(dialog)
            assert dialog._preview_result, (dialog.status.text(), dialog.preview_report.text())
            dialog.export(); wait(dialog)
            assert output.is_file(), dialog.status.text()
            assert len(dialog._export_receipt['inputs']['vocals']) == 3
            preview_dir = Path(dialog._preview_directory) if dialog._preview_directory else None
            dialog.close(); wait_cleanup(dialog); owner.close(); app.processEvents()
            assert not dialog._audition_poll_timer.isActive()
            assert preview_dir is None or not preview_dir.exists()
else:
    print('SKIP: actual GUI renderer requires the optional Pedalboard test component')

print('PASS: embedded mixdown UI handles 3 vocal tracks, shared effects, safe export, video handoff and optional registration')
