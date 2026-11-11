"""Coordinate one resident mix session and the user's unsubmitted seek gesture.

The transport owns playback intent, media position, duration and acknowledged
controls. This controller owns source replacement, requested A/B processing,
dirty edits and the local timeline gesture. It never reads Qt widgets or files.
"""
from __future__ import annotations

import time


class MixdownPreviewController:
    def __init__(self, player, *, clock=time.monotonic):
        self.player = player
        self.clock = clock
        self.source_key = None
        self.requested_key = 'after'
        self.dirty = True
        self.dragging = False
        self.pending_seek = None
        self.last_seek = None
        self._seek_serial = 0
        self._play_after_refresh = False

    @property
    def play_intent(self):
        return self.player.play_intent

    @property
    def position_ms(self):
        return (self.pending_seek['target_ms'] if self.pending_seek is not None
                else self.player.position())

    def select(self, key):
        if key not in ('before', 'after'):
            raise ValueError('Unknown mixdown preview selection')
        if key != self.requested_key:
            self.requested_key = key
            self.dirty = True

    def set_playing(self, enabled):
        """Preserve one intent while dirty edits are waiting for GUI debounce."""
        if not enabled:
            self._play_after_refresh = False
            return self.player.pause()
        if (self.player.ready or self.player.loading) and not self.dirty:
            self._play_after_refresh = False
            return self.player.play()
        self._play_after_refresh = True
        self.player.set_play_intent(True)
        return True

    def refresh(self, request, source_key, *, start_ms=0, loop_range=(0, 0)):
        """Submit an edit or replace the session after an actual source change."""
        player = self.player
        if not source_key or not player.available:
            self.release()
            return False
        # A background parameter edit must not retry a failed physical device.
        if player.failed and not self.play_intent:
            return False
        changed_source = source_key != self.source_key
        player.set_loop_range(*loop_range)
        opening = changed_source or (not player.ready and not player.loading)
        if opening:
            position = max(0, int(start_ms)) if changed_source else self.position_ms
            self.pending_seek = None
            self.dragging = False
            accepted = player.open(request, position_ms=position,
                                   autoplay=self.play_intent, key=self.requested_key)
        else:
            accepted = player.parameters(request, key=self.requested_key)
            if accepted and self._play_after_refresh and self.play_intent:
                accepted = player.play()
        if accepted:
            self._play_after_refresh = False
            self.source_key = source_key
            self.dirty = False
        return bool(accepted)

    def begin_seek(self):
        self.dragging = True

    def stage_seek(self, position, *, phase='queued'):
        self._seek_serial += 1
        target = max(0, min(int(position), self.player.duration()))
        self.pending_seek = {
            'seek_id': self._seek_serial, 'target_ms': target, 'phase': phase,
            'requested_at': self.clock(),
        }
        return target

    def commit_seek(self, position):
        self.dragging = False
        if not self.player.ready:
            return False
        target = max(0, min(int(position), self.player.duration()))
        pending = self.pending_seek
        if pending is None or pending['target_ms'] != target:
            self.stage_seek(target, phase='seeking')
            pending = self.pending_seek
        pending.update(phase='seeking', submitted_at=self.clock())
        if not self.player.seek(target, autoplay=self.play_intent):
            self.fail_seek()
            return False
        return True

    def follow_position(self):
        """A device snapshot may paint the cursor only after the latest seek."""
        if self.dragging or self.player.seek_pending:
            return False
        pending = self.pending_seek
        if pending is not None:
            if pending['phase'] != 'seeking':
                return False
            pending.update(phase='following', confirmed_at=self.clock(),
                           clock_quality='native_callback_submitted_media_not_acoustic')
            pending['elapsed_ms'] = round((pending['confirmed_at'] - pending['requested_at']) * 1000, 3)
            self.last_seek = dict(pending)
            self.pending_seek = None
        return True

    def fail_seek(self):
        if self.pending_seek is not None:
            self.pending_seek.update(phase='failed', failed_at=self.clock())
            self.last_seek = dict(self.pending_seek)
            self.pending_seek = None
        self.dragging = False

    def failed(self):
        self._play_after_refresh = False
        self.player.pause()
        self.dirty = True
        self.fail_seek()

    def release(self):
        self._play_after_refresh = False
        self.player.release()
        self.source_key = None
        self.pending_seek = None
        self.dragging = False
        self.dirty = True
