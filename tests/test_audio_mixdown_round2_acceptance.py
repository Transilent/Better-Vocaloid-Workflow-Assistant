"""Independent offscreen acceptance for the round-2 mix console.

This test intentionally uses the public widget surface plus the dialog's
request builder. It stubs the optional Pedalboard probe and never renders audio.
The retired right-stack/card geometry contract was removed with that workspace;
the responsive rack, fader/pan input and canonical multi-track request remain.
"""
import atexit
import os
import sys
import tempfile
import time
import wave
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(0, str(APP / 'tests'))

from PyQt5.QtCore import QPoint, QPointF, QRect, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QScrollArea, QWidget

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


def fixture_player_factory(dll_path, *, cache_dir, parent=None,
                           service_factory=None, service_options=None):
    del dll_path, service_factory, service_options
    return RealtimePreviewPlayer('fixture.dll', cache_dir=cache_dir, parent=parent,
                                 service_factory=Service)


def settle(rounds=6):
    for _ in range(rounds):
        app.processEvents()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_support(dialog, timeout=10.0):
    deadline = time.monotonic() + timeout
    while dialog.support_checking() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    settle()
    check(not dialog.support_checking(), 'stubbed component probe did not finish')
    check(dialog._support_ready, dialog._support_error)
    check(dialog.support_panel.isHidden(), 'ready component panel remains visible')


def rect_in(widget, ancestor):
    return QRect(widget.mapTo(ancestor, QPoint(0, 0)), widget.size())


def row_rects(rects):
    rows = []
    for rect in sorted(rects, key=lambda item: (item.top(), item.left())):
        if not rows or abs(rect.top() - rows[-1][0].top()) > 1:
            rows.append([])
        rows[-1].append(rect)
    return [sorted(row, key=lambda item: item.left()) for row in rows]


def check_rack(console, expected_columns):
    expected = console.active_strips()
    check(console.column_count() == expected_columns,
          'rack columns: expected %d, got %d' % (expected_columns, console.column_count()))
    check(len(expected) in (2, 3, 4), 'unexpected active channel count %d' % len(expected))

    rack_rect = rect_in(console._rack, console)
    left_margin = rack_rect.left()
    right_margin = console.width() - (rack_rect.left() + rack_rect.width())
    check(abs(left_margin - right_margin) <= 2,
          'rack is not horizontally centered: %d vs %d' % (left_margin, right_margin))

    rects = [rect_in(strip, console) for strip in expected]
    rows = row_rects(rects)
    check(console._grid.horizontalSpacing() == ui._ResponsiveMixerConsole.GAP,
          'rack grid spacing differs from the declared gap')
    expected_vertical_gap = 6 if console._wide_rack_hint else ui._ResponsiveMixerConsole.GAP
    check(console._grid.verticalSpacing() == expected_vertical_gap,
          'rack grid vertical spacing differs from the active density contract')
    expected_rows = (len(expected) + expected_columns - 1) // expected_columns
    check(len(rows) == expected_rows,
          'rack rows: expected %d, got %d' % (expected_rows, len(rows)))

    for row in rows:
        check(len(row) <= expected_columns, 'row contains too many channel strips')
        for before, after in zip(row, row[1:]):
            actual = after.left() - before.right() - 1
            check(actual >= 0, 'channel strips overlap horizontally')
            declared = ui._ResponsiveMixerConsole.GAP
            check(actual <= max(64, console.width() // 4),
                  'horizontal channel spacing is disproportionate to the %dpx grid gap: %d'
                  % (declared, actual))

    for upper, lower in zip(rows, rows[1:]):
        actual = min(rect.top() for rect in lower) - max(rect.bottom() for rect in upper) - 1
        check(abs(actual - ui._ResponsiveMixerConsole.GAP) <= 2,
              'vertical channel gap: expected %d, got %d'
              % (ui._ResponsiveMixerConsole.GAP, actual))



def make_wav(path, frequency):
    rate = 8000
    frames = bytearray()
    for index in range(rate // 20):
        sample = int(1800 * ((index * frequency * 2 // rate) % 2 - 1))
        frames.extend(int(sample).to_bytes(2, 'little', signed=True))
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(frames)


def send_wheel(widget, delta=-120):
    position = widget.rect().center()
    event = QWheelEvent(
        QPointF(position),
        QPointF(widget.mapToGlobal(position)),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)
    settle()
    return event


(APP / 'work').mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='mix-round2-ui-', dir=APP / 'work') as folder:
    temp = Path(folder)
    vocals = [temp / ('vocal-%d.wav' % number) for number in range(1, 4)]
    for index, path in enumerate(vocals, 1):
        make_wav(path, 180 * index)
    backing = temp / 'backing.wav'
    make_wav(backing, 110)
    output = temp / 'out.wav'

    owner = QWidget()
    owner.show()
    with patch.object(ui, '_activate_pedalboard', return_value=(True, 'offscreen-stub')), \
            patch.object(realtime, 'native_output_path',
                         return_value=str(APP / 'fixture-native-output.dll')), \
            patch.object(realtime, 'RealtimePreviewPlayer',
                         side_effect=fixture_player_factory):
        dialog = ui.AudioMixdownDialog(owner, None, owner, embedded=True)
        _ACTIVE_PLAYERS.append(dialog._player)
        dialog.show()
        settle()
        wait_support(dialog)
        # This acceptance is layout/input-only; suppress debounced preview
        # requests while exercising synthetic sources and track controls.
        dialog._preview_debounce.stop()
        dialog._preview_debounce.setInterval(600000)

        console = dialog.mixer_console
        scroll = dialog.findChild(QScrollArea)
        check(scroll is not None, 'outer scroll area is missing')

        dialog.resize(920, 840)
        settle()
        check_rack(console, 2)
        check(scroll.horizontalScrollBar().maximum() == 0,
              '2-channel rack introduced horizontal scrolling')

        dialog.add_vocal_track()
        settle()
        # P1 deliberately uses a readable 2x2 rack away from the wide card
        # layout.  Four tracks no longer force three/four very narrow columns
        # at this mid-size dialog.
        check_rack(console, 2)
        check(scroll.horizontalScrollBar().maximum() == 0,
              '3-channel rack introduced horizontal scrolling')

        dialog.add_vocal_track()
        settle()
        check_rack(console, 2)
        check(scroll.horizontalScrollBar().maximum() == 0,
              '4-channel rack introduced horizontal scrolling')

        dialog.resize(640, 560)
        settle()
        check_rack(console, 2)
        rows = row_rects([rect_in(strip, console) for strip in console.active_strips()])
        check([len(row) for row in rows] == [2, 2],
              '640px 4-channel rack is not 2x2: %s' % [len(row) for row in rows])
        check(scroll.horizontalScrollBar().maximum() == 0,
              '640px 2x2 rack introduced horizontal scrolling')

        for width in (920, 1100, 1600, 1920, 2512):
            dialog.resize(width, 840 if width < 1600 else 1000)
            settle()
            check_rack(console, console.column_count())
            check(scroll.horizontalScrollBar().maximum() == 0,
                  'mixer at %dpx introduced horizontal scrolling' % width)

        dialog.resize(920, 840)
        settle()
        fader = dialog.vocal_tracks[0]['slider']
        fader.setValue(0)
        fader.setFocus()
        settle()
        QTest.keyClick(fader, Qt.Key_Up)
        check(fader.value() == 5, 'plain Up should move the fader by 0.5 dB')
        QTest.keyClick(fader, Qt.Key_Up, Qt.ShiftModifier)
        check(fader.value() == 6, 'Shift+Up should move the fader by 0.1 dB')
        QTest.mouseDClick(fader, Qt.LeftButton)
        settle()
        check(fader.value() == 0, 'double-click should reset the fader to 0 dB')
        check(abs(dialog.vocal_tracks[0]['gain_db'].value()) < 1e-9,
              'fader reset did not update the dB spin box')

        dialog.vocal_tracks[0]['path'].setText(str(vocals[0]))
        settle()
        pan = dialog.vocal_tracks[0]['pan']
        check(pan.isEnabled(), 'pan is disabled after choosing a vocal source')
        pan.setValue(-37)
        settle()
        check(dialog.vocal_tracks[0]['pan_value'].text() == 'L37',
              'pan readout did not follow the dial: value=%d text=%r' %
              (pan.value(), dialog.vocal_tracks[0]['pan_value'].text()))
        QTest.mouseDClick(pan, Qt.LeftButton)
        settle()
        check(pan.value() == 0, 'double-click should recenter pan')
        check(dialog.vocal_tracks[0]['pan_value'].text() == 'C',
              'pan readout did not return to center')

        dialog.resize(640, 560)
        settle()
        scroll.verticalScrollBar().setValue(0)
        fader.setValue(0)
        dialog.add_vocal_button.setFocus()
        settle()
        check(not fader.hasFocus(), 'fader unexpectedly owns focus for wheel test')
        before_scroll = scroll.verticalScrollBar().value()
        event = send_wheel(fader, -120)
        check(fader.value() == 0, 'unfocused fader changed on mouse wheel')
        check(not event.isAccepted(), 'unfocused fader consumed the mouse wheel event')
        send_wheel(scroll.viewport(), -120)
        check(scroll.verticalScrollBar().value() > before_scroll,
              'page wheel did not scroll after bypassing the unfocused fader')

        while len(dialog.active_vocal_tracks()) < 3:
            dialog.add_vocal_track()
        settle()
        for index, track in enumerate(dialog.active_vocal_tracks()):
            track['path'].setText(str(vocals[index]))
        dialog.fields['instrumental'].setText(str(backing))
        dialog.fields['output'].setText(str(output))

        tracks = dialog.active_vocal_tracks()
        tracks[0]['mute'].setChecked(True)
        tracks[0]['pan'].setValue(-25)
        tracks[1]['solo'].setChecked(True)
        tracks[1]['pan'].setValue(30)
        tracks[2]['pan'].setValue(0)
        dialog.instrumental_mute.setChecked(True)
        dialog.instrumental_solo.setChecked(True)
        dialog.instrumental_pan.setValue(10)
        settle()

        request = dialog._request(output)
        expected_mute = [True, False, False]
        expected_solo = [False, True, False]
        expected_pan = [-0.25, 0.30, 0.0]
        check([row['mute'] for row in request['vocals']] == expected_mute,
              'vocal mute states did not enter the request')
        check([row['solo'] for row in request['vocals']] == expected_solo,
              'vocal solo states did not enter the request')
        check([round(row['pan'], 6) for row in request['vocals']] == expected_pan,
              'vocal pan states did not enter the request')
        check(request['instrumental_mute'] is True
              and request['instrumental_solo'] is True
              and abs(request['instrumental_pan'] - 0.10) < 1e-9,
              'backing M/S/Pan states did not enter the request')

        check(scroll.horizontalScrollBar().maximum() == 0,
              'final interaction state introduced horizontal scrolling')
        player = dialog._player
        service = player._service
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
        check(player._done.is_set() and service.closed,
              'fixture realtime actor was not closed')
        dialog.close()
        owner.close()
        dialog.deleteLater()
        owner.deleteLater()
        settle(10)
        app.processEvents()

print('PASS: responsive two/four-channel rack, fader/pan input, three-track M/S/Pan request mapping and no horizontal overflow')
