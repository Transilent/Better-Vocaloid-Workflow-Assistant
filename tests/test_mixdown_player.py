"""Offscreen contracts for the dual-player mixdown playback abstraction."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtTest import QTest

import mixdown_player as mp


app = QCoreApplication.instance() or QCoreApplication([])


def pump():
    app.processEvents()


def near(left, right, tolerance=1):
    return abs(float(left) - float(right)) <= tolerance


class FakeAdapter(mp.BaseMediaAdapter):
    """Data-only media player used to drive deterministic lifecycle tests."""

    def __init__(self, label, parent=None, duration_ms=10_000):
        super().__init__(parent)
        self.label = label
        self._duration_ms = int(duration_ms)
        self._position_ms = 0
        self._state = mp.PlaybackState.StoppedState
        self._volume = 100
        self.log = []
        self.play_count = 0
        self.pause_count = 0
        self.stop_count = 0

    def set_media(self, path, request_id):
        self._request_id = int(request_id)
        self._source = str(path) if path else None
        self._position_ms = 0
        self._state = mp.PlaybackState.StoppedState
        self.log.append((self.label, 'set_media', self._source, self._request_id))
        self.durationChanged.emit(self._duration_ms, self._request_id)
        self.mediaStatusChanged.emit(
            mp.MediaStatus.LoadingMedia if self._source else mp.MediaStatus.NoMedia,
            self._request_id)

    def play(self):
        self.play_count += 1
        self.log.append((self.label, 'play', self._position_ms, self._request_id))
        self._state = mp.PlaybackState.PlayingState
        self.stateChanged.emit(self._state, self._request_id)

    def pause(self):
        self.pause_count += 1
        self.log.append((self.label, 'pause', self._position_ms, self._request_id))
        self._state = mp.PlaybackState.PausedState
        self.stateChanged.emit(self._state, self._request_id)

    def stop(self):
        self.stop_count += 1
        self.log.append((self.label, 'stop', self._position_ms, self._request_id))
        self._state = mp.PlaybackState.StoppedState
        self.stateChanged.emit(self._state, self._request_id)

    def set_position(self, position_ms):
        self._position_ms = max(0, min(self._duration_ms, int(position_ms)))
        self.log.append((self.label, 'seek', self._position_ms, self._request_id))
        self.positionChanged.emit(self._position_ms, self._request_id)

    def position(self):
        return self._position_ms

    def duration(self):
        return self._duration_ms

    def state(self):
        return self._state

    def set_volume(self, volume):
        volume = max(0, min(100, int(volume)))
        if volume != self._volume:
            self._volume = volume
            self.log.append((self.label, 'volume', volume, self._request_id))

    def emit_loaded(self, request_id=None):
        request_id = self._request_id if request_id is None else int(request_id)
        self.log.append((self.label, 'loaded', self._source, request_id))
        self.mediaStatusChanged.emit(mp.MediaStatus.LoadedMedia, request_id)

    def emit_end(self, request_id=None):
        request_id = self._request_id if request_id is None else int(request_id)
        self._state = mp.PlaybackState.StoppedState
        self.stateChanged.emit(self._state, request_id)
        self.mediaStatusChanged.emit(mp.MediaStatus.EndOfMedia, request_id)

    def emit_invalid(self, request_id=None):
        request_id = self._request_id if request_id is None else int(request_id)
        self.mediaStatusChanged.emit(mp.MediaStatus.InvalidMedia, request_id)

def make_player(clock=None, candidate_duration_ms=10_000):
    created = []

    def factory(parent):
        duration_ms = 10_000 if not created else candidate_duration_ms
        adapter = FakeAdapter(
            'player-' + str(len(created)), parent, duration_ms=duration_ms)
        created.append(adapter)
        return adapter

    player = mp.MixdownPlayer(
        adapter_factory=factory,
        clock=clock or time.monotonic,
        volume=72)
    assert player.available, player.unavailable_reason
    assert len(created) == 2
    return player, created


def prime_direct(player, adapters, path, position_ms=100):
    assert player.load(path, position_ms)
    pump()
    adapters[0].emit_loaded()
    pump()
    assert adapters[0].position() == position_ms
    assert player.play()
    pump()
    assert player.state() == mp.PlaybackState.PlayingState
    assert player.source() == str(Path(path).resolve())


def test_windows_multimedia_preference_preserves_override():
    key = 'QT_MULTIMEDIA_PREFERRED_PLUGINS'
    had_value = key in os.environ
    previous = os.environ.get(key)
    try:
        os.environ.pop(key, None)
        mp._prefer_windows_media_foundation('nt')
        assert os.environ[key] == 'windowsmediafoundation'

        os.environ[key] = 'user-selected-plugin'
        mp._prefer_windows_media_foundation('nt')
        assert os.environ[key] == 'user-selected-plugin'

        os.environ.pop(key, None)
        mp._prefer_windows_media_foundation('darwin')
        assert key not in os.environ
    finally:
        if had_value:
            os.environ[key] = previous
        else:
            os.environ.pop(key, None)


def test_loaded_signal_requires_loaded_media(root):
    player, adapters = make_player()
    loaded, errors = [], []
    player.loaded.connect(loaded.append)
    player.errorOccurred.connect(errors.append)
    assert player.load(root / 'bad.wav')
    pump()  # Loading is not a usable media source.
    assert loaded == []
    adapters[0].emit_invalid()
    pump()
    assert loaded == []
    assert errors

    good = root / 'good.wav'
    assert player.load(good)
    pump()
    assert loaded == []
    adapters[0].emit_loaded()
    pump()
    assert loaded == [str(good.resolve())]
    adapters[0].mediaStatusChanged.emit(mp.MediaStatus.BufferedMedia,
                                        adapters[0].request_id)
    pump()
    assert loaded == [str(good.resolve())]


def test_first_crossfade_and_invalid_candidate(root):
    clock = [50.0]
    player, adapters = make_player(lambda: clock[0])
    first = root / 'first-crossfade.wav'
    bad = root / 'invalid-crossfade.wav'
    assert player.load_crossfade(first, 120, 80)
    pump()
    assert adapters[0].state() == mp.PlaybackState.StoppedState
    assert adapters[1].source == str(first.resolve())
    assert player.source() == ''
    adapters[1].emit_loaded()
    pump()
    assert player.source() == str(first.resolve())
    assert player.position() == 120
    assert player.state() == mp.PlaybackState.PlayingState
    assert adapters[1]._volume == 72
    assert adapters[0].stop_count >= 1

    # A decode failure in standby restores the active fade volume and keeps
    # the audible source alive.
    assert player.load_crossfade(bad, 0, 80)
    pump()
    old_volume = player.volume()
    errors = []
    cancelled = []
    player.errorOccurred.connect(errors.append)
    player.crossfadeCancelled.connect(cancelled.append)
    adapters[0].emit_invalid()
    pump()
    assert player.source() == str(first.resolve())
    assert player.state() == mp.PlaybackState.PlayingState
    assert adapters[1]._volume == old_volume
    assert not player.transitioning
    assert cancelled[-1] == str(bad.resolve())
    assert errors


def test_crossfade_order_curve_and_single_lifecycle(root):
    clock = [100.0]
    player, adapters = make_player(lambda: clock[0])
    old, new = root / 'old.wav', root / 'new.wav'
    prime_direct(player, adapters, old)
    active, standby = adapters
    old_stops = active.stop_count
    old_pauses = active.pause_count
    started, finished, progress = [], [], []
    statuses = []
    player.crossfadeStarted.connect(started.append)
    player.crossfadeFinished.connect(finished.append)
    player.crossfadeProgress.connect(progress.append)
    player.mediaStatusChanged.connect(statuses.append)

    assert player.load_crossfade(new, 250, 80)
    pump()
    # Loading the standby must not touch the audible source.
    assert active.stop_count == old_stops
    assert active.pause_count == old_pauses
    assert active.state() == mp.PlaybackState.PlayingState
    assert active._volume == 72
    assert standby.source == str(new.resolve())
    assert standby.play_count == 0
    assert not started and player.transitioning

    standby.emit_loaded()
    pump()
    player._crossfade_timer.stop()
    assert standby.position() == 250
    assert standby.play_count == 1
    assert active.stop_count == old_stops
    assert active._volume == 72
    assert standby._volume == 0
    assert started == [str(new.resolve())]
    assert player._crossfade_timer.isSingleShot()
    assert player._crossfade_timer.interval() <= 16

    clock[0] += 0.020
    player._advance_crossfade()
    middle_old, middle_new = active._volume, standby._volume
    assert 0 < middle_old < 72
    assert 0 < middle_new < 72
    assert near(middle_old + middle_new, 72)
    assert active.stop_count == old_stops

    clock[0] += 0.020
    player._advance_crossfade()
    assert active._volume <= middle_old
    assert standby._volume >= middle_new
    assert active.stop_count == old_stops

    clock[0] += 0.039
    player._advance_crossfade()
    assert active.stop_count == old_stops, 'old source stopped before fade completed'

    clock[0] += 0.002
    player._advance_crossfade()
    assert finished == [str(new.resolve())]
    assert not player.transitioning
    assert player.source() == str(new.resolve())
    assert active.stop_count == old_stops + 1
    assert active._volume == 0
    assert standby._volume == 72
    assert standby.state() == mp.PlaybackState.PlayingState
    assert mp.MediaStatus.NoMedia not in statuses
    assert progress[0] == 0.0 and progress[-1] == 1.0
    old_volume_at = max(i for i, row in enumerate(active.log) if row[1] == 'volume')
    old_stop_at = max(i for i, row in enumerate(active.log) if row[1] == 'stop')
    new_play_at = max(i for i, row in enumerate(standby.log) if row[1] == 'play')
    new_seek_at = max(i for i, row in enumerate(standby.log) if row[1] == 'seek')
    assert new_seek_at < new_play_at
    assert old_volume_at < old_stop_at
    assert old_stop_at == max(i for i, row in enumerate(active.log) if row[1] == 'stop')

    # A duplicate LoadedMedia for the now-active source is one harmless event,
    # not a second transition or lifecycle.
    standby.emit_loaded()
    pump()
    assert finished == [str(new.resolve())]
    assert player.source() == str(new.resolve())


def test_crossfade_compensates_for_active_playback_during_load(root):
    player, adapters = make_player(lambda: 100.0)
    prime_direct(player, adapters, root / 'old-sync.wav', position_ms=1000)
    active, standby = adapters
    assert player.load_crossfade(root / 'new-sync.wav', 500, 80)
    active.set_position(1230)  # Loading took 230 ms while old audio continued.
    standby.emit_loaded()
    pump()
    assert standby.position() == 730
    player.stop()


def test_loop_crossfade_wraps_target_by_candidate_duration(root):
    player, adapters = make_player(
        lambda: 100.0, candidate_duration_ms=6_000)
    old, new = root / 'loop-wrap-old.wav', root / 'loop-wrap-new.wav'
    prime_direct(player, adapters, old, position_ms=9_500)
    active, standby = adapters
    player.loop = True

    assert player.load_crossfade(new, 5_500, 80)
    pump()
    active.emit_end()
    pump()
    QTest.qWait(5)
    pump()
    assert active.state() == mp.PlaybackState.PlayingState
    assert active.position() == 0

    # Loading spans the old source's loop boundary. At LoadedMedia its
    # position is already 200 ms into the next pass.
    active.set_position(200)
    pump()
    standby.emit_loaded()
    pump()
    player._crossfade_timer.stop()

    # 5,500 + (200 + 10,000 - 9,500) = 6,200, which wraps to 200 ms in the
    # 6,000 ms candidate instead of pinning the candidate at EndOfMedia.
    assert standby.position() == 200
    assert player.transitioning
    player.stop()


def test_pause_while_crossfade_candidate_loads_cancels_without_autoplay(root):
    player, adapters = make_player()
    old, new = root / 'pause-load-old.wav', root / 'pause-load-new.wav'
    prime_direct(player, adapters, old)
    active, standby = adapters
    cancelled, started, loaded = [], [], []
    player.crossfadeCancelled.connect(cancelled.append)
    player.crossfadeStarted.connect(started.append)
    player.loaded.connect(loaded.append)

    assert player.load_crossfade(new, 600, 80)
    pump()
    request_id = standby.request_id
    assert player.transitioning
    assert standby.play_count == 0

    assert player.pause()
    pump()
    assert not player.transitioning
    assert active.state() == mp.PlaybackState.PausedState
    assert active._volume == 72
    assert cancelled == [str(new.resolve())]
    assert standby.source is None

    # A late decoder event from the cancelled request must not restart either
    # player or silently commit the discarded source.
    standby.emit_loaded(request_id)
    pump()
    assert standby.play_count == 0
    assert started == []
    assert loaded == []
    assert player.source() == str(old.resolve())
    assert player.state() == mp.PlaybackState.PausedState

    active_plays = active.play_count
    assert player.play()
    pump()
    assert active.play_count == active_plays + 1
    assert standby.play_count == 0
    assert player.state() == mp.PlaybackState.PlayingState
    player.stop()


def test_seek_during_crossfade_keeps_new_user_position(root):
    player, adapters = make_player(lambda: 100.0)
    prime_direct(player, adapters, root / 'old-seek.wav', position_ms=1000)
    active, standby = adapters
    assert player.load_crossfade(root / 'new-seek.wav', 1000, 80)
    standby.emit_loaded(); pump()
    assert player.transitioning
    assert player.seek(4000)
    assert not player.transitioning
    assert active.position() == 4000 and player.position() == 4000
    standby.emit_loaded(); pump()  # A late status from the retired candidate.
    assert player.position() == 4000
    player.stop()


def test_cancel_pending_and_midfade_keeps_active_until_loaded(root):
    clock = [200.0]
    player, adapters = make_player(lambda: clock[0])
    old = root / 'cancel-old.wav'
    first_new = root / 'cancel-first.wav'
    second_new = root / 'cancel-second.wav'
    third_new = root / 'cancel-third.wav'
    prime_direct(player, adapters, old)
    active, standby = adapters
    old_stops, old_pauses = active.stop_count, active.pause_count
    cancelled = []
    started = []
    player.crossfadeCancelled.connect(cancelled.append)
    player.crossfadeStarted.connect(started.append)

    assert player.load_crossfade(first_new, 100)
    pump()
    first_request = standby.request_id
    assert active.stop_count == old_stops
    assert active.pause_count == old_pauses
    assert active.state() == mp.PlaybackState.PlayingState

    # Replace a still-loading candidate.  Only standby may be stopped/cleared.
    assert player.load_crossfade(second_new, 200)
    pump()
    assert active.stop_count == old_stops
    assert active.pause_count == old_pauses
    assert active.state() == mp.PlaybackState.PlayingState
    assert active._volume == 72
    assert standby.source == str(second_new.resolve())
    assert cancelled[-1] == str(first_new.resolve())

    # A late status from the canceled request is ignored.
    standby.emit_loaded(first_request)
    pump()
    assert not started
    assert player.source() == str(old.resolve())
    assert player.transitioning

    standby.emit_loaded()
    pump()
    player._crossfade_timer.stop()
    assert started == [str(second_new.resolve())]
    clock[0] += 0.040
    player._advance_crossfade()
    assert active.state() == mp.PlaybackState.PlayingState
    assert 0 < active._volume < 72
    assert active.stop_count == old_stops

    # Replace during the active fade.  Restore old to full volume, discard the
    # in-flight candidate, and leave old playing until third is LoadedMedia.
    assert player.load_crossfade(third_new, 300)
    pump()
    assert active.stop_count == old_stops
    assert active.pause_count == old_pauses
    assert active.state() == mp.PlaybackState.PlayingState
    assert active._volume == 72
    assert standby.source == str(third_new.resolve())
    assert cancelled[-1] == str(second_new.resolve())
    assert player.source() == str(old.resolve())

    standby.emit_loaded()
    pump()
    player._crossfade_timer.stop()
    assert player.source() == str(old.resolve())
    assert active.stop_count == old_stops
    clock[0] += 0.081
    player._advance_crossfade()
    assert player.source() == str(third_new.resolve())
    assert active.stop_count == old_stops + 1
    assert standby._volume == 72


def test_seek_ab_position_pause_and_loop(root):
    clock = [300.0]
    player, adapters = make_player(lambda: clock[0])
    old, new = root / 'loop-old.wav', root / 'loop-new.wav'
    prime_direct(player, adapters, old)
    assert player.seek(420)
    pump()
    assert player.position() == 420
    assert adapters[0].position() == 420
    assert player.pause()
    pump()
    assert player.state() == mp.PlaybackState.PausedState
    assert player.play()
    pump()
    assert player.state() == mp.PlaybackState.PlayingState

    # A/B loads at the explicitly supplied relative position, before play.
    assert player.load_crossfade(new, 350, 80)
    pump()
    adapters[1].emit_loaded()
    pump()
    player._crossfade_timer.stop()
    assert adapters[1].position() == 350
    play_index = min(i for i, row in enumerate(adapters[1].log)
                     if row[1] == 'play')
    seek_index = max(i for i, row in enumerate(adapters[1].log)
                     if row[1] == 'seek')
    assert seek_index < play_index
    clock[0] += 0.081
    player._advance_crossfade()
    assert player.position() == 350
    assert player.loop is False
    player.loop = True
    assert player.loop is True
    current = adapters[1]
    plays = current.play_count
    current.emit_end()
    pump()
    QTest.qWait(5)
    pump()
    assert current.position() == 0
    assert current.play_count == plays + 1

    player.set_loop(False)
    plays = current.play_count
    current.emit_end()
    pump()
    QTest.qWait(5)
    pump()
    assert current.play_count == plays


def test_queued_cross_thread_and_safe_fallbacks(root):
    player, adapters = make_player()
    marker = root / 'threaded.wav'
    assert player.load(marker)
    pump()
    events = []
    main_ident = threading.get_ident()
    player.loaded.connect(
        lambda path: events.append((path, threading.get_ident())))

    worker = threading.Thread(target=adapters[0].emit_loaded)
    worker.start()
    worker.join()
    assert events == [], 'adapter callback was not marshalled to the player thread'
    pump()
    assert len(events) == 1
    assert events[0][0] == str(marker.resolve())
    assert events[0][1] == main_ident

    # The test suite is intentionally offscreen; default construction must
    # become a non-throwing no-audio player without even importing the media DLL.
    assert not mp._QT_MULTIMEDIA_ATTEMPTED
    fallback = mp.MixdownPlayer()
    assert not mp._QT_MULTIMEDIA_ATTEMPTED
    assert not fallback.available
    assert 'offscreen' in fallback.unavailable_reason.lower()
    errors = []
    fallback.errorOccurred.connect(errors.append)
    assert not fallback.load_crossfade(marker, 0)
    assert not fallback.play()
    assert fallback.position() == 0
    assert fallback.duration() == 0
    assert fallback.state() == mp.PlaybackState.StoppedState
    assert fallback.volume(55) == 55
    assert fallback.loop is False
    fallback.loop = True
    assert fallback.loop is True
    fallback.stop()
    assert len(errors) >= 3

    def broken_factory(parent):
        raise RuntimeError('multimedia backend failed to initialise')

    broken = mp.MixdownPlayer(adapter_factory=broken_factory)
    assert not broken.available
    assert 'failed to initialise' in broken.unavailable_reason
    assert not broken.load(marker)

    # Full outward transport surface and a clean single stop.
    assert player.duration() == 10_000
    assert player.volume() == 72
    assert player.volume(64) == 64
    assert player.stop()
    pump()
    assert player.source() == ''
    assert player.position() == 0
    assert player.duration() == 0
    assert player.state() == mp.PlaybackState.StoppedState


def main():
    # Fake adapters never open these paths; no filesystem writes are needed.
    root = Path(tempfile.gettempdir()) / 'vwa-mixdown-player-tests'
    test_windows_multimedia_preference_preserves_override()
    test_loaded_signal_requires_loaded_media(root)
    test_first_crossfade_and_invalid_candidate(root)
    test_crossfade_order_curve_and_single_lifecycle(root)
    test_crossfade_compensates_for_active_playback_during_load(root)
    test_loop_crossfade_wraps_target_by_candidate_duration(root)
    test_pause_while_crossfade_candidate_loads_cancels_without_autoplay(root)
    test_seek_during_crossfade_keeps_new_user_position(root)
    test_cancel_pending_and_midfade_keeps_active_until_loaded(root)
    test_seek_ab_position_pause_and_loop(root)
    test_queued_cross_thread_and_safe_fallbacks(root)
    print('test_mixdown_player: PASS')


if __name__ == '__main__':
    main()
