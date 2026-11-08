"""Timeline interaction against the resident realtime player, without hardware."""
import atexit
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(0, str(APP / 'tests'))

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

import audio_mixdown_ui as ui
import mixdown_realtime_player as realtime
from mixdown_realtime_player import RealtimePreviewPlayer
from test_mixdown_realtime_player import Service


app = QApplication.instance() or QApplication([])

_ACTIVE_PLAYERS = []


def _close_active_players():
    while _ACTIVE_PLAYERS:
        player = _ACTIVE_PLAYERS.pop()
        try:
            player.close()
        except Exception:
            pass


atexit.register(_close_active_players)


def settle(rounds=4):
    for _ in range(rounds):
        app.processEvents()


def wait_for(predicate, player, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        player.poll()
        app.processEvents()
        if predicate():
            return True
        time.sleep(.002)
    player.poll()
    settle()
    return bool(predicate())


def factory(dll_path, *, cache_dir, parent=None, service_factory=None,
            service_options=None):
    del dll_path, service_factory, service_options
    return RealtimePreviewPlayer('fixture.dll', cache_dir=cache_dir, parent=parent,
                                 service_factory=Service)


def fresh():
    owner = QWidget()
    created = {}

    def make(*args, **kwargs):
        player = factory(*args, **kwargs)
        created['player'] = player
        return player

    with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
            patch.object(ui.AudioMixdownDialog, '_start_component_support_check',
                         return_value=False), \
            patch.object(realtime, 'native_output_path',
                         return_value=str(APP / 'fixture-native-output.dll')), \
            patch.object(realtime, 'RealtimePreviewPlayer', side_effect=make):
        dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
    player = created['player']
    service = player._service
    _ACTIVE_PLAYERS.append(player)
    dialog._support_ready = True
    dialog._support_error = ''
    dialog.support_panel.hide()
    dialog._sources_ready = lambda: True
    dialog._preview_dirty = False
    dialog._preview_resume_seconds = 1.0
    dialog._preview_segment_start_seconds = 0.0
    dialog._sync_preview_analysis = Mock()
    dialog._schedule_preview_refresh = Mock(return_value=True)
    dialog._preview_debounce.stop()

    player.open({'fixture': True}, position_ms=1000, autoplay=False, key='after')
    assert wait_for(lambda: any(command[0] == 'open'
                                for command in service.commands), player)
    generation = player._generation
    service.events.put({
        'type': 'live_ready', 'request_id': generation,
        'sample_rate': 48000, 'channels': 2,
        'duration_frames': 2880000,
    })
    player.poll()
    settle()
    assert player.ready and dialog.player_position.maximum() == 60000
    return owner, dialog, player, service, generation


def acknowledge(player, service, generation, *, state='paused'):
    pending = player._pending_seek
    assert pending is not None and pending['control_id'] is not None
    target = pending['target']
    frame = int(round(target * 48))
    service.events.put({
        'type': 'live_state', 'request_id': generation,
        'snapshot': {
            'state': state, 'sample_rate': 48000, 'duration_frames': 2880000,
            'native': {'media_frame': frame, 'sample_rate': 48000,
                       'queued_frames': 1024, 'underrun_frames': 0},
            'seek_control_id': pending['control_id'],
            'last_seek': {'frame': frame, 'phase': 'queued'},
        },
    })
    player.poll()
    settle()


def dispose(owner, dialog, player):
    dialog._player_seek_timer.stop()
    dialog._preview_debounce.stop()
    dialog._audition_poll_timer.stop()
    dialog._audition_closing = True
    dialog._audition_cleanup_complete = True
    dialog._audition_service = None
    dialog._native_preview_player = None
    cache = dialog._preview_cache_directory
    dialog._preview_cache_directory = None
    player.close()
    if player in _ACTIVE_PLAYERS:
        _ACTIVE_PLAYERS.remove(player)
    ui._discard_preview_directory(cache)
    try:
        dialog.deleteLater()
        owner.deleteLater()
    except RuntimeError:
        pass
    settle()


# Drag paints locally and commits one exact seek on release.  A stale state
# packet cannot pull the cursor away while the native acknowledgement is absent.
owner, dialog, player, service, generation = fresh()
dialog._begin_player_seek()
for value in range(2000, 10000, 20):
    dialog.player_position.setValue(value)
    dialog._seek_player_preview(value)
    dialog._player_position_changed(1000)
assert not any(command[0] == 'seek' for command in service.commands)
assert dialog.player_position.value() == 9980
assert dialog.player_time.text().startswith('00:09')
dialog._finish_player_seek()
assert wait_for(lambda: player._pending_seek is not None
                 and player._pending_seek['control_id'] is not None, player)
dialog._player_position_changed(1000)
assert dialog.player_position.value() == 9980
assert dialog._pending_player_seek is not None
acknowledge(player, service, generation, state='playing')
assert dialog._pending_player_seek is None
assert 9980 <= dialog.player_position.value() < 9990
assert dialog._last_player_seek['clock_quality'] == 'native_callback_submitted_media_not_acoustic'
dispose(owner, dialog, player)

# Groove click uses absolute timeline coordinates.  Grabbing near the handle
# does not jump; RTL and Escape retain the existing slider contract.
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

# Keyboard/wheel bursts coalesce to the newest target, while button intent is
# carried by that same seek command rather than starting a second player.
owner, dialog, player, service, generation = fresh()
for value in (2000, 3000, 4000):
    dialog.player_position.setValue(value)
assert not any(command[0] == 'seek' for command in service.commands)
dialog.toggle_preview_playback()
QTest.qWait(ui._PLAYER_SEEK_COALESCE_MS + 30)
assert wait_for(lambda: player._pending_seek is not None
                 and player._pending_seek['control_id'] is not None, player)
seek_command = next(command for command in reversed(service.commands)
                    if command[0] == 'seek')
assert seek_command[2] == (4000, True)
acknowledge(player, service, generation, state='playing')
assert dialog._preview_play_intent
assert dialog.player_position.value() == 4000

dialog.player_position.setValue(3000)
dialog.toggle_preview_playback()
QTest.qWait(ui._PLAYER_SEEK_COALESCE_MS + 30)
assert wait_for(lambda: player._pending_seek is not None
                 and player._pending_seek['control_id'] is not None, player)
latest = next(command for command in reversed(service.commands)
              if command[0] == 'seek')
assert latest[2] == (3000, False)
acknowledge(player, service, generation, state='paused')
assert not dialog._preview_play_intent
assert dialog.player_position.value() == 3000
dispose(owner, dialog, player)

# A second target replaces ownership of the first; the old acknowledgement is
# ignored and cannot clear the newer pending seek.
owner, dialog, player, service, generation = fresh()
dialog._seek_to_song_position(7000, commit=True)
assert wait_for(lambda: player._pending_seek is not None
                 and player._pending_seek['control_id'] is not None, player)
first = player._pending_seek['control_id']
dialog._seek_to_song_position(9000, commit=True)
assert wait_for(lambda: player._pending_seek is not None
                 and player._pending_seek['control_id'] is not None
                 and player._pending_seek['target'] == 9000, player)
latest = player._pending_seek['control_id']
service.events.put({
    'type': 'live_state', 'request_id': generation,
    'snapshot': {
        'state': 'paused', 'sample_rate': 48000, 'duration_frames': 2880000,
        'native': {'media_frame': 336000, 'sample_rate': 48000,
                   'queued_frames': 1024},
        'seek_control_id': first,
        'last_seek': {'frame': 336000, 'phase': 'queued'},
    },
})
player.poll()
settle()
assert player._pending_seek is not None
assert player._pending_seek['control_id'] == latest
assert dialog.player_position.value() == 9000
acknowledge(player, service, generation, state='paused')
assert dialog._pending_player_seek is None
assert 9000 <= dialog.player_position.value() < 9010
dispose(owner, dialog, player)

print('PASS: realtime drag/click/RTL/escape, coalesced seek, native latest-target acknowledgement and play intent')
