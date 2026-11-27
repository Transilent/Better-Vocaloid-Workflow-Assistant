"""Qt UX hub for global MRU recent items and window-level smart file routing."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot

import audio_device_recovery as _audio_rec
from i18n import tr
from preparation_queue import PrepQueueStore
import recent_items
import vwa_config
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
    audioDevicesChanged = Signal()
    prepQueueChanged = Signal()

    def __init__(self, bridge, *, root: Path = ROOT):
        super().__init__(bridge)
        self._bridge = bridge
        self._root = Path(root).resolve()
        self._last_recorded: dict[str, str] = {}
        self._prep_queue = PrepQueueStore(self._root)
        self._was_prep_busy = False
        self._reconnect_guard = _audio_rec.DeviceReconnectGuard(max_attempts=1, cooldown_seconds=2.0, backoff_ms=300)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(self._reconnect_guard.backoff_ms)
        self._reconnect_timer.timeout.connect(self._execute_mixdown_reconnect)
        self._audio_devices_cache: list[dict] | None = None
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
            mixdown.viewChanged.connect(self._watch_mixdown_device)
        if hasattr(self._bridge, 'activityChanged'):
            self._bridge.activityChanged.connect(self._watch_preparation_queue)

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
            chorus = self._bridge._chorus
            if getattr(chorus, 'dirty', False):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台有未保存修改，请先保存或放弃后再打开其他工程', 'warning')
                return
            if getattr(getattr(chorus, 'worker', None), 'kind', ''):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台正在处理，请稍候再试', 'warning')
                return
            self._bridge.navigate('chorus')
            chorus.openProject(url, False)
        elif k == 'tuning':
            self._bridge.navigate('tuning')
            self._bridge._tuning.chooseProject(url)
        elif k == 'project_convert':
            conv = self._bridge._converter
            if getattr(getattr(conv, 'worker', None), 'kind', ''):
                self._bridge.navigate('project_convert')
                self._bridge.pushToast('工程互转正在处理，请稍候再试', 'warning')
                return
            self._bridge.navigate('project_convert')
            conv.loadSource(url)
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

    def _import_chorus_audio_and_score(self, audio_paths: list[str], score_path: str):
        chorus = self._bridge._chorus
        if not audio_paths:
            if score_path:
                chorus.importScore(QUrl.fromLocalFile(score_path))
            return
        if score_path:
            pending = {'done': False}

            def _after_audio():
                if pending['done'] or getattr(chorus.worker, 'kind', ''):
                    return
                pending['done'] = True
                try:
                    chorus.worker.changed.disconnect(_after_audio)
                except (RuntimeError, TypeError):
                    pass
                if not getattr(chorus, '_closed', False):
                    chorus.importScore(QUrl.fromLocalFile(score_path))

            chorus.worker.changed.connect(_after_audio)
        chorus.importAudio('vocal', [QUrl.fromLocalFile(p) for p in audio_paths])

    @Slot(str, 'QVariantList')
    def routeDroppedFiles(self, target_id, paths):
        clean = [p for p in (_to_local_path(v) for v in (paths or ())) if p and Path(p).is_file()]
        if not clean:
            return
        first_url = QUrl.fromLocalFile(clean[0])
        tid = str(target_id or '').strip()
        if tid == 'chorus_project':
            chorus = self._bridge._chorus
            if getattr(chorus, 'dirty', False):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台有未保存修改，请先保存或放弃后再打开其他工程', 'warning')
                return
            if getattr(getattr(chorus, 'worker', None), 'kind', ''):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台正在处理，请稍候再试', 'warning')
                return
            self._bridge.navigate('chorus')
            chorus.openProject(first_url, False)
            self.recordRecent(clean[0], 'chorus')
        elif tid == 'chorus_audio':
            chorus = self._bridge._chorus
            if getattr(getattr(chorus, 'worker', None), 'kind', ''):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台正在处理，请稍候再试', 'warning')
                return
            self._bridge.navigate('chorus')
            chorus.importAudio('vocal', [QUrl.fromLocalFile(p) for p in clean])
        elif tid == 'chorus_audio_and_score':
            chorus = self._bridge._chorus
            if getattr(getattr(chorus, 'worker', None), 'kind', ''):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台正在处理，请稍候再试', 'warning')
                return
            audio_paths = [p for p in clean if Path(p).suffix.lower() in recent_items.AUDIO_EXTS]
            midi_paths = [p for p in clean if Path(p).suffix.lower() in recent_items.MIDI_EXTS]
            self._bridge.navigate('chorus')
            self._import_chorus_audio_and_score(audio_paths, midi_paths[0] if midi_paths else '')
        elif tid == 'chorus_score':
            chorus = self._bridge._chorus
            if getattr(getattr(chorus, 'worker', None), 'kind', ''):
                self._bridge.navigate('chorus')
                self._bridge.pushToast('修音工作台正在处理，请稍候再试', 'warning')
                return
            midi_or_proj = next(
                (p for p in clean if Path(p).suffix.lower() in (recent_items.MIDI_EXTS | recent_items.VOCAL_PROJECT_EXTS)),
                clean[0],
            )
            self._bridge.navigate('chorus')
            chorus.importScore(QUrl.fromLocalFile(midi_or_proj))
        elif tid == 'tuning_project':
            self._bridge.navigate('tuning')
            self._bridge._tuning.chooseProject(first_url)
            self.recordRecent(clean[0], 'tuning')
        elif tid == 'convert_project':
            conv = self._bridge._converter
            if getattr(getattr(conv, 'worker', None), 'kind', ''):
                self._bridge.navigate('project_convert')
                self._bridge.pushToast('工程互转正在处理，请稍候再试', 'warning')
                return
            self._bridge.navigate('project_convert')
            conv.loadSource(first_url)
            self.recordRecent(clean[0], 'project_convert')
        elif tid == 'prep_audio':
            self._bridge.navigate('processing')
            self._bridge.chooseMusic(first_url)
            self.recordRecent(clean[0], 'preparation')
            queued = 0
            for extra in clean[1:]:
                try:
                    self._prep_queue.enqueue(extra)
                    queued += 1
                except (OSError, ValueError):
                    pass
            if queued:
                self.prepQueueChanged.emit()
                self._bridge.pushToast(tr('已载入首曲，其余 {0} 首已加入准备队列', queued), 'success')
                return
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

    # --- T3-M5: WASAPI device enumeration, hot-swap reconnect & multi-song prep queue ---

    def _watch_mixdown_device(self):
        if os.environ.get('VWA_TESTING') == '1':
            return
        mixdown = getattr(self._bridge, '_mixdown', None)
        if mixdown is None or getattr(mixdown, '_closed', False):
            return
        player = getattr(mixdown, '_player', None)
        state = getattr(mixdown, '_state', None)
        if player is None or state is None:
            return
        if not getattr(player, 'failed', False):
            if getattr(player, 'ready', False):
                self._reconnect_guard.reset()
            return
        msg = str(getattr(state, 'message', '') or '')
        if not self._reconnect_timer.isActive() and self._reconnect_guard.should_attempt(msg):
            self._reconnect_guard.mark_attempt()
            self._reconnect_timer.start()

    def _execute_mixdown_reconnect(self):
        mixdown = getattr(self._bridge, '_mixdown', None)
        if mixdown is None or getattr(mixdown, '_closed', False):
            return
        replace_fn = getattr(mixdown, '_replace_player', None)
        if callable(replace_fn):
            replace_fn()
            self._bridge.pushToast('音频输出设备已变动，已自动切换并恢复试听', 'info')

    @Property('QVariantList', notify=audioDevicesChanged)
    def audioOutputDevices(self):
        if self._audio_devices_cache is None:
            if os.environ.get('VWA_TESTING') == '1':
                self._audio_devices_cache = []
            else:
                self._audio_devices_cache = _audio_rec.list_wasapi_devices(root=self._root)
        return [
            {'index': -1, 'name': '', 'label': tr('跟随系统默认'), 'is_default': True},
            *[
                {
                    'index': int(row.get('index', idx)),
                    'name': str(row.get('name') or ''),
                    'label': str(row.get('name') or ''),
                    'is_default': bool(row.get('is_default', False)),
                }
                for idx, row in enumerate(self._audio_devices_cache)
                if row.get('name')
            ],
        ]

    @Property(str, notify=audioDevicesChanged)
    def selectedAudioOutputDevice(self) -> str:
        cfg, _ = vwa_config.load_validated_config(root=self._root, persist_migration=False)
        return str(cfg.get('audio_output_device') or '')

    @Slot()
    def refreshAudioDevices(self):
        self._audio_devices_cache = _audio_rec.list_wasapi_devices(root=self._root)
        self.audioDevicesChanged.emit()

    @Slot(str)
    def selectAudioOutputDevice(self, device_name: str):
        from vwa_io import atomic_json
        cfg, _ = vwa_config.load_validated_config(root=self._root, persist_migration=False)
        clean = str(device_name or '').strip()
        if str(cfg.get('audio_output_device') or '') == clean:
            return
        cfg['audio_output_device'] = clean
        atomic_json(self._root / 'config.json', cfg)
        self.audioDevicesChanged.emit()
        self._bridge.pushToast(tr('音频输出设备已更新'), 'success')

    @Property('QVariantList', notify=prepQueueChanged)
    def prepQueue(self):
        return self._prep_queue.list_items()

    @Slot('QVariant')
    def enqueuePreparation(self, path_or_url):
        local = _to_local_path(path_or_url)
        if not local or not Path(local).is_file():
            return
        try:
            item = self._prep_queue.enqueue(local)
        except (OSError, ValueError) as exc:
            self._bridge.pushToast(str(exc), 'warning')
            return
        self.prepQueueChanged.emit()
        self._bridge.pushToast(tr('已加入准备队列') + f'：{item["title"]}', 'info')
        if not self._bridge.preparationBusy:
            self._advance_preparation_queue()

    @Slot(str)
    def removeQueuedPreparation(self, item_id: str):
        if self._prep_queue.remove(str(item_id)):
            self.prepQueueChanged.emit()

    @Slot(str, int)
    def moveQueuedPreparation(self, item_id: str, delta: int):
        if self._prep_queue.move(str(item_id), int(delta)):
            self.prepQueueChanged.emit()

    @Slot()
    def clearPrepQueue(self):
        if self._prep_queue.clear() > 0:
            self.prepQueueChanged.emit()
            self._bridge.pushToast(tr('已清空准备队列'), 'info')

    def _watch_preparation_queue(self):
        busy = bool(getattr(self._bridge, 'preparationBusy', False))
        finished = self._was_prep_busy and not busy
        self._was_prep_busy = busy
        if finished and not getattr(self._bridge, '_closed', False):
            self._advance_preparation_queue()

    def _advance_preparation_queue(self):
        if getattr(self._bridge, '_closed', False) or getattr(self._bridge, 'preparationBusy', False):
            return
        nxt = self._prep_queue.dequeue()
        if nxt is None:
            return
        self.prepQueueChanged.emit()
        music_path = str(nxt.get('music_path') or '')
        if music_path and Path(music_path).is_file():
            self._bridge.chooseMusic(QUrl.fromLocalFile(music_path))
            if hasattr(self._bridge, 'startPreparation'):
                self._bridge.startPreparation()
