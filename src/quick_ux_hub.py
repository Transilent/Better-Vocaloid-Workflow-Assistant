"""Qt UX hub for global MRU recent items and window-level smart file routing."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from i18n import tr
import recent_items
from vwa_paths import ROOT

_LOG = logging.getLogger(__name__)


def _to_local_path(value) -> str:
    if isinstance(value, QUrl):
        return value.toLocalFile() if value.isLocalFile() else ''
    text = str(value or '').strip()
    if text.startswith('file:///'):
        return QUrl(text).toLocalFile()
    return text


class QuickUxHub(QObject):
    recentChanged = Signal()

    def __init__(self, bridge, *, root: Path = ROOT):
        super().__init__(bridge)
        self._bridge = bridge
        self._root = Path(root).resolve()
        self._last_recorded: dict[str, str] = {}
        self._connect_auto_recorders()

    def _connect_auto_recorders(self):
        chorus = getattr(self._bridge, '_chorus', None)
        if chorus is not None:
            chorus.changed.connect(self._watch_chorus)
        tuning = getattr(self._bridge, '_tuning', None)
        if tuning is not None:
            tuning.changed.connect(self._watch_tuning)
        converter = getattr(self._bridge, '_converter', None)
        if converter is not None:
            converter.changed.connect(self._watch_converter)
        mixdown = getattr(self._bridge, '_mixdown', None)
        if mixdown is not None:
            mixdown.tracksChanged.connect(self._watch_mixdown)

    def _auto_record(self, slot_key: str, path: str, kind: str, title: str = '', fmt: str = ''):
        if os.environ.get('VWA_TESTING') == '1':
            return
        clean = str(path or '').strip()
        if not clean or self._last_recorded.get(slot_key) == clean:
            return
        if not Path(clean).is_file():
            return
        self._last_recorded[slot_key] = clean
        try:
            recent_items.record_recent(clean, kind, title=title, fmt=fmt, root=self._root)
            self.recentChanged.emit()
        except (OSError, ValueError) as exc:
            _LOG.debug('MRU auto-record skipped: %s', exc)

    def _watch_chorus(self):
        chorus = getattr(self._bridge, '_chorus', None)
        path = getattr(chorus, '_path', None)
        if path:
            title = getattr(getattr(chorus, 'project', None), 'title', '')
            self._auto_record('chorus', str(path), 'chorus', title=str(title or ''), fmt='VWACHORUS')

    def _watch_tuning(self):
        tuning = getattr(self._bridge, '_tuning', None)
        proj = getattr(tuning, 'project', None)
        raw = proj.project if proj is not None and hasattr(proj, 'project') else None
        if isinstance(raw, dict) and raw.get('path'):
            self._auto_record(
                'tuning',
                str(raw['path']),
                'tuning',
                title=Path(str(raw['path'])).stem,
                fmt=str(raw.get('format') or ''),
            )

    def _watch_converter(self):
        conv = getattr(self._bridge, '_converter', None)
        info = getattr(conv, '_inspection', None)
        if isinstance(info, dict) and info.get('path'):
            self._auto_record(
                'converter',
                str(info['path']),
                'project_convert',
                title=Path(str(info['path'])).stem,
                fmt=str(info.get('formatShort') or info.get('format') or ''),
            )

    def _watch_mixdown(self):
        mixdown = getattr(self._bridge, '_mixdown', None)
        state = getattr(mixdown, '_state', None)
        if state is None:
            return
        for row in state.tracks:
            if row.get('id') == 'vocal-1' and row.get('path'):
                self._auto_record('mixdown', str(row['path']), 'mixdown', title=Path(str(row['path'])).stem)
                break

    @Property('QVariantList', notify=recentChanged)
    def recentItems(self):
        rows = recent_items.list_recent(root=self._root, prune_missing=True)
        return [
            {
                **row,
                'kindLabel': tr(row['kindLabel']),
            }
            for row in rows
        ]

    @Slot(str, str)
    @Slot(str, str, str, str)
    def recordRecent(self, path, kind, title='', fmt=''):
        local = _to_local_path(path)
        if not local or not Path(local).is_file():
            return
        try:
            recent_items.record_recent(local, str(kind), title=str(title), fmt=str(fmt), root=self._root)
            self.recentChanged.emit()
        except (OSError, ValueError) as exc:
            _LOG.debug('recordRecent skipped: %s', exc)

    @Slot()
    def clearRecentItems(self):
        recent_items.clear_recent(self._root)
        self._last_recorded.clear()
        self.recentChanged.emit()
        self._bridge.pushToast('已清空最近使用记录', 'info')

    @Slot(str, str)
    def openRecentItem(self, path, kind):
        local = _to_local_path(path)
        if not local or not Path(local).is_file():
            self._bridge.pushToast('文件已不存在，已从最近列表中移除', 'warning')
            self.recentChanged.emit()
            return
        url = QUrl.fromLocalFile(local)
        k = str(kind or '').strip()
        if k == 'chorus':
            self._bridge.navigate('chorus')
            self._bridge._chorus.openProject(url, False)
        elif k == 'tuning':
            self._bridge.navigate('tuning')
            self._bridge._tuning.chooseProject(url)
        elif k == 'project_convert':
            self._bridge.navigate('project_convert')
            self._bridge._converter.loadSource(url)
        elif k == 'mixdown':
            self._bridge.navigate('audio_mixdown')
            self._bridge._mixdown.chooseSource('vocal-1', url)
        elif k == 'video':
            self._bridge.navigate('video_mixdown')
            self._bridge._video.chooseFile('sourceVideo', url)
        else:
            self._bridge.navigate('processing')
            self._bridge.chooseMusic(url)
        self._bridge.pushToast(f'已打开：{Path(local).name}', 'info')

    @Slot('QVariantList', result='QVariantMap')
    def inspectDropUrls(self, urls):
        paths = [p for p in (_to_local_path(u) for u in (urls or ())) if p]
        return recent_items.classify_dropped_files(paths)

    @Slot(str, 'QVariantList')
    def routeDroppedFiles(self, target_id, paths):
        clean = [p for p in (_to_local_path(v) for v in (paths or ())) if p and Path(p).is_file()]
        if not clean:
            return
        first_url = QUrl.fromLocalFile(clean[0])
        tid = str(target_id or '').strip()
        if tid == 'chorus_project':
            self._bridge.navigate('chorus')
            self._bridge._chorus.openProject(first_url, False)
            self.recordRecent(clean[0], 'chorus')
        elif tid == 'chorus_audio':
            self._bridge.navigate('chorus')
            self._bridge._chorus.importAudio('vocal', [QUrl.fromLocalFile(p) for p in clean])
        elif tid == 'chorus_score':
            self._bridge.navigate('chorus')
            self._bridge._chorus.importScore(first_url)
        elif tid == 'tuning_project':
            self._bridge.navigate('tuning')
            self._bridge._tuning.chooseProject(first_url)
            self.recordRecent(clean[0], 'tuning')
        elif tid == 'convert_project':
            self._bridge.navigate('project_convert')
            self._bridge._converter.loadSource(first_url)
            self.recordRecent(clean[0], 'project_convert')
        elif tid == 'prep_audio':
            self._bridge.navigate('processing')
            self._bridge.chooseMusic(first_url)
            self.recordRecent(clean[0], 'preparation')
        elif tid == 'mixdown_vocal':
            self._bridge.navigate('audio_mixdown')
            self._bridge._mixdown.chooseSource('vocal-1', first_url)
            self.recordRecent(clean[0], 'mixdown')
        elif tid == 'video_source':
            self._bridge.navigate('video_mixdown')
            self._bridge._video.chooseFile('sourceVideo', first_url)
            self.recordRecent(clean[0], 'video')
        else:
            return
        self._bridge.pushToast(f'已导入：{Path(clean[0]).name}', 'success')
