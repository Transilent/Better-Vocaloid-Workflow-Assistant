"""Qt bridge for debounced crash-safe autosave recovery and global toast events."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot

import draft_recovery
from i18n import tr
from vwa_paths import ROOT

_LOG = logging.getLogger(__name__)
AUTOSAVE_DEBOUNCE_MS = 15_000


class QuickRecoveryBridge(QObject):
    changed = Signal()
    toastRequested = Signal(str, str)

    def __init__(self, parent, chorus, mixdown, *, root: Path = ROOT, check_existing: bool | None = None):
        super().__init__(parent)
        self._root = Path(root).resolve()
        self._chorus = chorus
        self._mixdown = mixdown
        self._closed = False
        self._chorus_meta = None
        self._mixdown_meta = None

        should_check = (os.environ.get('VWA_TESTING') != '1') if check_existing is None else bool(check_existing)
        if should_check:
            self._refresh_existing()

        self._chorus_timer = QTimer(self)
        self._chorus_timer.setSingleShot(True)
        self._chorus_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._chorus_timer.timeout.connect(self._flush_chorus_draft)

        self._mixdown_timer = QTimer(self)
        self._mixdown_timer.setSingleShot(True)
        self._mixdown_timer.setInterval(AUTOSAVE_DEBOUNCE_MS)
        self._mixdown_timer.timeout.connect(self._flush_mixdown_draft)

        if self._chorus is not None:
            self._chorus.changed.connect(self._on_chorus_changed)
        if self._mixdown is not None:
            self._mixdown.viewChanged.connect(self._on_mixdown_changed)
            self._mixdown.tracksChanged.connect(self._on_mixdown_changed)

    def _refresh_existing(self):
        self._chorus_meta = draft_recovery.read_draft_meta('chorus', self._root)
        self._mixdown_meta = draft_recovery.read_draft_meta('mixdown', self._root)

    @Property(bool, notify=changed)
    def hasChorusDraft(self) -> bool:
        return self._chorus_meta is not None

    @Property(str, notify=changed)
    def chorusDraftLabel(self) -> str:
        return str((self._chorus_meta or {}).get('label') or '')

    @Property(bool, notify=changed)
    def hasMixdownDraft(self) -> bool:
        return self._mixdown_meta is not None

    @Property(str, notify=changed)
    def mixdownDraftLabel(self) -> str:
        return str((self._mixdown_meta or {}).get('label') or '')

    def _on_chorus_changed(self):
        if self._closed or self._chorus is None:
            return
        if self._chorus.dirty and len(getattr(self._chorus.project, 'tracks', ())) > 0:
            self._chorus_timer.start()
        elif not self._chorus.dirty:
            self._chorus_timer.stop()
            if self._chorus_meta is None:
                draft_recovery.clear_draft('chorus', self._root)

    def _on_mixdown_changed(self):
        if self._closed or self._mixdown is None:
            return
        state = getattr(self._mixdown, '_state', None)
        if state is not None and state.has_media():
            self._mixdown_timer.start()

    def _flush_chorus_draft(self):
        if self._closed or self._chorus is None:
            return
        if not self._chorus.dirty or not getattr(self._chorus.project, 'tracks', ()):
            return
        try:
            draft_recovery.write_chorus_draft(
                self._chorus.project,
                source_path=str(getattr(self._chorus, '_path', '') or ''),
                root=self._root,
            )
        except OSError as exc:
            _LOG.debug('Chorus autosave skipped: %s', exc)

    def _flush_mixdown_draft(self):
        if self._closed or self._mixdown is None:
            return
        state = getattr(self._mixdown, '_state', None)
        if state is None or not state.has_media():
            return
        try:
            payload = draft_recovery.mixdown_state_to_draft(state)
            tracks = [t for t in payload.get('tracks', ()) if t.get('path')]
            first_name = Path(tracks[0]['path']).stem if tracks else '混音草稿'
            draft_recovery.save_draft('mixdown', payload, label=first_name, root=self._root)
        except OSError as exc:
            _LOG.debug('Mixdown autosave skipped: %s', exc)

    @Slot()
    def restoreChorusDraft(self):
        if self._chorus is None:
            return
        project_file = draft_recovery.chorus_draft_project_path(self._root)
        if not project_file.is_file():
            self.discardChorusDraft()
            return
        source_path = str((self._chorus_meta or {}).get('source_path') or '')

        def _done(ok: bool, err_msg: str):
            if ok:
                draft_recovery.clear_draft('chorus', self._root)
                self._chorus_meta = None
                self.changed.emit()
                self.toastRequested.emit(tr('已恢复上次未保存的修音草稿'), 'success')
            else:
                detail = f'：{err_msg}' if err_msg else ''
                self.toastRequested.emit(tr('修音草稿恢复失败') + detail, 'error')

        if hasattr(self._chorus, 'openRecoveredDraft'):
            self._chorus.openRecoveredDraft(project_file, source_path=source_path, on_finished=_done)
        else:
            self._chorus_meta = None
            self.changed.emit()
            self._chorus.openProject(QUrl.fromLocalFile(str(project_file)), True)
            self.toastRequested.emit(tr('已恢复上次未保存的修音草稿'), 'success')

    @Slot()
    def discardChorusDraft(self):
        draft_recovery.clear_draft('chorus', self._root)
        self._chorus_meta = None
        self.changed.emit()
        self.toastRequested.emit(tr('已丢弃修音恢复草稿'), 'info')

    @Slot()
    def restoreMixdownDraft(self):
        if self._mixdown is None:
            return
        draft = draft_recovery.load_draft('mixdown', self._root)
        if draft is None:
            self.discardMixdownDraft()
            return
        state = getattr(self._mixdown, '_state', None)
        if state is None:
            return
        restored = draft_recovery.apply_mixdown_draft(state, draft['payload'])
        draft_recovery.clear_draft('mixdown', self._root)
        self._mixdown_meta = None
        self.changed.emit()
        if hasattr(self._mixdown, '_recompute_and_emit'):
            self._mixdown._recompute_and_emit()
        if restored > 0 and hasattr(self._mixdown, '_schedule_preview'):
            self._mixdown._schedule_preview(0)
        self.toastRequested.emit(tr('已恢复上次未保存的混音草稿'), 'success')

    @Slot()
    def discardMixdownDraft(self):
        draft_recovery.clear_draft('mixdown', self._root)
        self._mixdown_meta = None
        self.changed.emit()
        self.toastRequested.emit(tr('已丢弃混音恢复草稿'), 'info')

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._chorus_timer.stop()
        self._mixdown_timer.stop()
        # Clean graceful shutdown clears live autosave snapshots so the next
        # normal startup does not mistakenly offer crash recovery.
        if self._chorus_meta is None:
            draft_recovery.clear_draft('chorus', self._root)
        if self._mixdown_meta is None:
            draft_recovery.clear_draft('mixdown', self._root)
