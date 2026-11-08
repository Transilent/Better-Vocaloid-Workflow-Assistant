"""Timeline interaction and asynchronous seek ownership, without audio devices."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget
import audio_mixdown_ui as ui

app = QApplication.instance() or QApplication([])


class Player(QObject):
    positionChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(int)
    available = True
    transitioning = False

    def __init__(self):
        super().__init__()
        self.value = 1000
        self.path = 'old.wav'
        self.playing = False
        self.seeks = []
        self.plays = 0
        self.loads = []

    def position(self): return self.value
    def source(self): return self.path
    def has_media(self): return True
    def state(self):
        return ui.PlaybackState.PlayingState if self.playing else ui.PlaybackState.PausedState

    def seek(self, value):
        self.seeks.append(value)
        self.value = value
        self.positionChanged.emit(value)
        return True

    def pause(self):
        self.playing = False
        self.stateChanged.emit(self.state())

    def play(self):
        self.plays += 1
        self.playing = True
        self.stateChanged.emit(self.state())

    def stop(self): self.playing = False
    def set_loop(self, checked): pass
    def load(self, path, position):
        self.loads.append((path, position))
        return True

    def load_crossfade(self, path, position, *args, **kwargs):
        return self.load(path, position)

    def finish_load(self):
        self.path, self.value = self.loads[-1]
        self.positionChanged.emit(self.value)


def fresh():
    owner = QWidget()
    with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
            patch.object(ui.AudioMixdownDialog, '_start_component_support_check'), \
            patch.object(ui.AudioMixdownDialog, '_init_preview_player'):
        d = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
    p = Player()
    d._player = p
    d._connect_preview_player(p)
    d._support_ready = True
    d._sources_ready = lambda: True
    d._preview_result = {'after': {'source_duration_seconds': 60.0}}
    d._preview_segment_start_seconds = 0.0
    d._preview_segment_duration_seconds = 12.0
    d._preview_dirty = False
    d._sync_preview_analysis = Mock()
    d._maybe_continue_preview = Mock()
    d._schedule_preview_refresh = Mock(return_value=True)
    d._choose_preview_player = Mock()
    d.player_position.setRange(0, 60000)
    d.player_position.setEnabled(True)
    d._player_position_changed(1000)
    return owner, d, p


def dispose(owner, d):
    d._stop_preview_player()
    d.deleteLater()
    owner.deleteLater()
    app.processEvents()


# Hundreds of movement events paint locally, with exactly one seek on release.
owner, d, p = fresh()
d._begin_player_seek()
for value in range(2000, 10000, 20):
    d.player_position.setValue(value)
    d._seek_player_preview(value)
    p.positionChanged.emit(1000)
assert not p.seeks and d.player_position.value() == 9980
assert d.player_time.text().startswith('00:09')
d._finish_player_seek()
assert p.seeks == [9980] and d._pending_player_seek is None
assert d._last_player_seek['clock_quality'] == 'player_position_estimate'
# A delayed signal carries the old value, while the actual player is current.
p.positionChanged.emit(1000)
assert d.player_position.value() == 9980
dispose(owner, d)

owner, d, p = fresh()
d._begin_player_seek()
d.player_position.setValue(9000)
d._seek_player_preview(9000)
d._cancel_player_seek_gesture()
assert not p.seeks and not d._player_seeking and d.player_position.value() == 1000
dispose(owner, d)

# Real slider events: a groove click uses absolute time, not page stepping;
# grabbing near the handle edge does not make its position jump.
slider = ui._SeekSlider(Qt.Horizontal)
slider.resize(420, 30)
slider.setRange(0, 10000)
slider.show()
app.processEvents()
released = []
slider.sliderReleased.connect(lambda: released.append(slider.value()))
QTest.mouseClick(slider, Qt.LeftButton, pos=QPoint(315, 15))
assert 7000 < slider.value() < 8000, slider.value()
assert len(released) == 1
option = ui.QStyleOptionSlider()
slider.initStyleOption(option)
handle = slider.style().subControlRect(
    ui.QStyle.CC_Slider, option, ui.QStyle.SC_SliderHandle, slider)
before = slider.value()
QTest.mousePress(slider, Qt.LeftButton, pos=handle.center() + QPoint(2, 0))
assert abs(slider.value() - before) < 30
QTest.mouseRelease(slider, Qt.LeftButton, pos=QPoint(419, 15))
assert slider.value() == 10000
slider.setLayoutDirection(Qt.RightToLeft)
QTest.mouseClick(slider, Qt.LeftButton, pos=QPoint(1, 15))
assert slider.value() == 10000
cancelled = []
slider.seekCancelled.connect(lambda: cancelled.append(True))
QTest.mousePress(slider, Qt.LeftButton, pos=QPoint(100, 15))
QTest.keyClick(slider, Qt.Key_Escape)
assert cancelled == [True] and not slider.isSliderDown()
slider.close()

# Keyboard/wheel bursts retain their target until one coalesced commit.
owner, d, p = fresh()
for value in (2000, 3000, 4000):
    d.player_position.setValue(value)
    p.positionChanged.emit(1000)
assert not p.seeks and d.player_position.value() == 4000
QTest.qWait(ui._PLAYER_SEEK_COALESCE_MS + 30)
assert p.seeks == [4000] and d._pending_player_seek is None
assert d._last_player_seek['elapsed_ms'] >= ui._PLAYER_SEEK_COALESCE_MS - 10
dispose(owner, d)

# Play/pause during the keyboard coalescing interval changes final intent and
# never starts old-position audio. Stop also cancels the queued commit.
owner, d, p = fresh()
d.player_position.setValue(2000)
d.toggle_preview_playback()
assert p.plays == 0
QTest.qWait(ui._PLAYER_SEEK_COALESCE_MS + 30)
assert p.seeks == [2000] and p.plays == 1 and p.playing
d.player_position.setValue(3000)
d.toggle_preview_playback()
assert not p.playing
QTest.qWait(ui._PLAYER_SEEK_COALESCE_MS + 30)
assert p.seeks == [2000, 3000] and not p.playing
d.player_position.setValue(4000)
d._stop_preview_player()
QTest.qWait(ui._PLAYER_SEEK_COALESCE_MS + 30)
assert p.seeks == [2000, 3000]
dispose(owner, d)

# An explicit A/B play action during a queued or paused pending seek updates
# the same intent; it must not silently remain paused or lose the new side.
owner, d, p = fresh()
d.player_position.setValue(2000)
d._select_preview = Mock(return_value=True)
d._preview_result['before'] = {'source_duration_seconds': 60.0}
d.play_preview('before')
assert p.seeks == [2000] and p.playing
d._select_preview.assert_called_once_with('before', autoplay=True, position_ms=2000)
d._seek_to_song_position(21000, commit=True)
d.toggle_preview_playback()
assert not d._preview_play_intent
d._request_preview_playback = Mock()
d.play_preview('before')
assert d._pending_player_seek['autoplay'] and d._preview_play_intent
dispose(owner, d)

# An out-of-window seek keeps ownership through old position/duration packets,
# a second seek and delayed media loading. Pause affects intent, not old media.
owner, d, p = fresh()
p.playing = True
d._preview_play_intent = True
d._seek_to_song_position(21000, commit=True)
assert not p.playing and d.player_position.value() == 21000
first_id = d._pending_player_seek['seek_id']
p.positionChanged.emit(1000)
d._player_duration_changed(12000)
assert d.player_position.value() == 21000 and d.player_position.maximum() == 60000
d._seek_to_song_position(31000, commit=True)
assert d._pending_player_seek['seek_id'] > first_id
assert d._pending_player_seek['autoplay']
d.toggle_preview_playback()  # pause while waiting
assert not d._preview_play_intent and p.plays == 0
with tempfile.TemporaryDirectory(prefix='vwa-seek-') as folder:
    target = Path(folder) / 'target.wav'
    target.write_bytes(b'fixture')  # the fake player never decodes this file
    d._preview_result['after'] = {'output_path': str(target),
        'preview_start_seconds': 31.0, 'duration_seconds': 12.0,
        'source_duration_seconds': 60.0}
    d._preview_result['before'] = {'output_path': str(target),
        'preview_start_seconds': 0.0, 'duration_seconds': 12.0}
    assert not d._select_preview('before', autoplay=True) and not p.loads
    assert d._select_preview('after', autoplay=True)
    assert d._pending_player_seek['phase'] == 'loading' and p.plays == 0
    p.positionChanged.emit(1000)
    assert d.player_position.value() == 31000
    p.finish_load()
    assert d._pending_player_seek is None and d.player_position.value() == 31000
assert not p.playing
dispose(owner, d)

# Exact EOF is a stable selection, including when a lead-in is needed to
# render it. Do not silently move the user's cursor backwards by 250 ms.
owner, d, p = fresh()
d._seek_to_song_position(60000, commit=True)
assert d.player_position.value() == 60000 and d._preview_resume_seconds == 59.75
with tempfile.TemporaryDirectory(prefix='vwa-seek-eof-') as folder:
    target = Path(folder) / 'eof.wav'
    target.write_bytes(b'fixture')
    d._preview_result['after'] = {'output_path': str(target),
        'preview_start_seconds': 59.75, 'duration_seconds': .25,
        'source_duration_seconds': 60.0}
    assert d._select_preview('after', autoplay=False)
    assert p.loads[-1][1] == 250
    p.finish_load()
    assert d.player_position.value() == 60000
    assert d.player_time.text().startswith('01:00')
dispose(owner, d)

# A late render/error from the first seek cannot override a newer target.
owner, d, p = fresh()
d._seek_to_song_position(21000, commit=True)
stale_serial = d._player_seek_serial
d._seek_to_song_position(41000, commit=True)
d._audition_request_context = {'seek_serial': stale_serial, 'signature': 'same'}
d._preview_parameter_signature = lambda: 'same'
d._audition_roundtrip_ms = lambda: 42.0
d._apply_audition_result = Mock()
d._audition_result_received({'report': {'after': {}}})
d._apply_audition_result.assert_not_called()
assert d.player_position.value() == 41000
d._audition_request_context = {'seek_serial': stale_serial}
d._audition_error_received({'message': 'old failure'})
assert d._pending_player_seek['phase'] != 'failed'
d._preview_pending = False
d._audition_request_context = {'seek_serial': d._player_seek_serial}
d._audition_error_received({'message': 'current failure'})
assert d._pending_player_seek['phase'] == 'failed'
p.positionChanged.emit(1000)
assert d.player_position.value() == 41000
dispose(owner, d)
assert not d._player_seek_timer.isActive() and d._pending_player_seek is None

print('PASS: timeline drag/click/RTL, coalesced seek, latest target, pause, stale result/error and stop')
