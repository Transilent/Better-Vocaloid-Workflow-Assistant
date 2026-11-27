"""Qt projection for the standalone Project Format Converter tool page."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

import project_convert_service as _svc
from quick_work import WorkerLane


def _local_path(value) -> str:
    if isinstance(value, QUrl):
        return value.toLocalFile() if value.isLocalFile() else ''
    text = str(value or '').strip()
    if text.startswith('file:///'):
        return QUrl(text).toLocalFile()
    return text


class QuickConvert(QObject):
    changed = Signal()
    tuningHandoffRequested = Signal(str)
    chorusScoreHandoffRequested = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        journal = getattr(parent, 'journal', None)
        self.worker = WorkerLane(self, 'vwa-convert', journal=journal)
        self.worker.changed.connect(self.changed)
        self._catalog = _svc.catalog()
        self._inspection: dict | None = None
        self._selected_tracks: list[int] = []
        self._target_format: str = 'ustx'
        self._output_dir: str = ''
        self._pitch_mode: str = 'keep'
        self._lyric_mode: str = 'adapt'
        self._include_instrumental: bool = True
        self._split_tracks: bool = False
        self._receipt: dict | None = None
        self._notice: str = ''
        self._error: bool = False
        self._closed: bool = False

    def _say(self, message, *, error: bool = False):
        self._notice = str(message or '')
        self._error = bool(error)
        self.changed.emit()

    def _current_options(self) -> dict:
        return {
            'pitch_mode': self._pitch_mode,
            'lyric_mode': self._lyric_mode,
            'include_instrumental': self._include_instrumental,
            'split_tracks': self._split_tracks,
            'selected_tracks': list(self._selected_tracks) if self._inspection else None,
        }

    def _planned_preview(self) -> list[dict]:
        if not self._inspection:
            return []
        vocals = [
            t for t in self._inspection.get('tracks', [])
            if t.get('kind') == 'vocal' and t.get('vocalIndex') in self._selected_tracks
        ]
        if not vocals:
            return []
        try:
            return _svc.plan_output_paths(
                self._inspection['path'],
                self._target_format,
                self._output_dir or self._inspection.get('defaultOutputDir') or '',
                vocal_track_names=[t['name'] for t in vocals],
                split_tracks=self._split_tracks,
            )
        except (OSError, ValueError):
            return []

    @Property('QVariantMap', constant=True)
    def catalog(self):
        return self._catalog

    @Property('QVariantMap', notify=changed)
    def view(self):
        info = self._inspection
        tracks = []
        if info:
            selected_set = set(self._selected_tracks)
            for row in info.get('tracks', []):
                item = dict(row)
                item['selected'] = (row.get('kind') == 'vocal' and row.get('vocalIndex') in selected_set)
                tracks.append(item)
        target_spec = _svc.spec_to_dict(_svc.SPECS_BY_ID[self._target_format])
        warnings = _svc.build_precheck_warnings(info, self._target_format, self._current_options()) if info else []
        planned = self._planned_preview()
        busy = bool(self.worker.kind)
        can_convert = bool(
            info
            and not busy
            and self._selected_tracks
            and self._catalog.get('runtimeAvailable', False)
        )
        return {
            'busy': busy,
            'busyDetail': self.worker.activity.get('detail', '') if self.worker.activity else '',
            'hasSource': info is not None,
            'source': {
                'path': info['path'],
                'fileName': info['fileName'],
                'format': info['format'],
                'formatLabel': info['formatLabel'],
                'formatShort': info['formatShort'],
                'formatVersion': info['formatVersion'],
                'engineSource': info['engineSource'],
                'tempoSummary': info['tempoSummary'],
                'meterSummary': info['meterSummary'],
                'durationSeconds': info['durationSeconds'],
                'totalNotes': info['totalNotes'],
                'vocalTrackCount': info['vocalTrackCount'],
                'instrumentalTrackCount': info['instrumentalTrackCount'],
            } if info else {},
            'tracks': tracks,
            'selectedVocalCount': len(self._selected_tracks),
            'targetFormat': self._target_format,
            'targetSpec': target_spec,
            'outputDir': self._output_dir or (info.get('defaultOutputDir', '') if info else ''),
            'pitchMode': self._pitch_mode,
            'lyricMode': self._lyric_mode,
            'includeInstrumental': self._include_instrumental,
            'splitTracks': self._split_tracks,
            'warnings': warnings,
            'plannedOutputs': planned,
            'canConvert': can_convert,
            'runtimeAvailable': bool(self._catalog.get('runtimeAvailable', False)),
            'receipt': self._receipt or {},
            'hasReceipt': bool(self._receipt),
            'notice': self._notice,
            'error': self._error,
        }

    @Slot('QVariant')
    def loadSource(self, path_or_url):
        if self._closed or self.worker.kind:
            return
        local = _local_path(path_or_url)
        if not local:
            self._say('请选择本机工程文件。', error=True)
            return
        self._receipt = None

        def work(stop, send):
            send('正在体检源工程结构与轨道信息…')
            return _svc.inspect_project_file(local, cancel=stop.is_set)

        def finish(result, error):
            if error is not None:
                self._say(error, error=True)
                return
            self._inspection = result
            vocals = [t for t in result.get('tracks', []) if t.get('kind') == 'vocal']
            self._selected_tracks = [
                int(t['vocalIndex']) for t in vocals if t.get('noteCount', 0) > 0
            ] or ([int(vocals[0]['vocalIndex'])] if vocals else [])
            if not self._output_dir:
                self._output_dir = result.get('defaultOutputDir', '')
            # Pick a sensible default target different from the source format
            src_fmt = result.get('format', '')
            if src_fmt == self._target_format:
                if src_fmt == 'acep':
                    self._target_format = 'ufdata'
                elif src_fmt == 'ustx':
                    self._target_format = 'svp'
                else:
                    self._target_format = 'ustx'
            elif src_fmt == 'acep':
                self._target_format = 'ufdata'
            self._say(f"已载入 {result['fileName']}（{result['formatShort']} · {result['totalNotes']} 个音符）")

        self.worker.start('project-convert-inspect', work, finish, route='project_convert')

    @Slot()
    def clearSource(self):
        if self.worker.kind:
            return
        self._inspection = None
        self._selected_tracks = []
        self._receipt = None
        self._notice = ''
        self._error = False
        self.changed.emit()

    @Slot(int)
    def toggleVocalTrack(self, vocal_index: int):
        if self.worker.kind or not self._inspection:
            return
        valid = {
            int(t['vocalIndex']) for t in self._inspection.get('tracks', [])
            if t.get('kind') == 'vocal' and t.get('vocalIndex') is not None
        }
        if vocal_index not in valid:
            return
        if vocal_index in self._selected_tracks:
            self._selected_tracks = [i for i in self._selected_tracks if i != vocal_index]
        else:
            self._selected_tracks = sorted([*self._selected_tracks, vocal_index])
        self.changed.emit()

    @Slot()
    def selectAllVocals(self):
        if self.worker.kind or not self._inspection:
            return
        self._selected_tracks = sorted(
            int(t['vocalIndex']) for t in self._inspection.get('tracks', [])
            if t.get('kind') == 'vocal' and t.get('vocalIndex') is not None
        )
        self.changed.emit()

    @Slot()
    def selectLeadOnly(self):
        if self.worker.kind or not self._inspection:
            return
        vocals = [
            t for t in self._inspection.get('tracks', [])
            if t.get('kind') == 'vocal' and t.get('vocalIndex') is not None
        ]
        if vocals:
            best = max(vocals, key=lambda t: t.get('noteCount', 0))
            self._selected_tracks = [int(best['vocalIndex'])]
            self.changed.emit()

    @Slot(str)
    def setTargetFormat(self, fmt: str):
        if self.worker.kind:
            return
        spec = _svc.SPECS_BY_ID.get(fmt)
        if spec and spec.can_write and fmt != self._target_format:
            self._target_format = fmt
            self.changed.emit()

    @Slot('QVariant')
    def setOutputDir(self, path_or_url):
        if self.worker.kind:
            return
        local = _local_path(path_or_url)
        if local:
            self._output_dir = str(Path(local).expanduser().resolve())
            self.changed.emit()

    @Slot(str)
    def setPitchMode(self, mode: str):
        if self.worker.kind or mode not in ('keep', 'strip'):
            return
        if self._pitch_mode != mode:
            self._pitch_mode = mode
            self.changed.emit()

    @Slot(str)
    def setLyricMode(self, mode: str):
        if self.worker.kind or mode not in ('adapt', 'raw'):
            return
        if self._lyric_mode != mode:
            self._lyric_mode = mode
            self.changed.emit()

    @Slot(bool)
    def setIncludeInstrumental(self, enabled: bool):
        if self.worker.kind:
            return
        value = bool(enabled)
        if self._include_instrumental != value:
            self._include_instrumental = value
            self.changed.emit()

    @Slot(bool)
    def setSplitTracks(self, enabled: bool):
        if self.worker.kind:
            return
        value = bool(enabled)
        if self._split_tracks != value:
            self._split_tracks = value
            self.changed.emit()

    @Slot()
    def startConvert(self):
        if self._closed or self.worker.kind or not self._inspection:
            return
        if not self._selected_tracks:
            self._say('请至少勾选 1 条人声轨再开始转换。', error=True)
            return
        snapshot = dict(self._inspection)
        target = self._target_format
        out_dir = self._output_dir or snapshot.get('defaultOutputDir') or ''
        options = self._current_options()
        self._receipt = None

        def work(stop, send):
            send(f'正在转换为 {_svc.SPECS_BY_ID[target].short_label} 并执行回读校验…')
            return _svc.convert_project_file(
                snapshot['path'],
                target,
                out_dir,
                options,
                inspection=snapshot,
                cancel=stop.is_set,
            )

        def finish(result, error):
            if error is not None:
                self._say(error, error=True)
                return
            self._receipt = result
            count = len(result.get('outputs', []))
            self._say(f"转换完成：已生成 {count} 个 {result['targetLabel']} 文件并完成核验。")

        self.worker.start('project-convert-run', work, finish, route='project_convert')

    @Slot()
    def cancel(self):
        self.worker.cancel()

    @Slot(str)
    def openFolder(self, path_str: str):
        p = Path(path_str) if path_str else None
        if p is None:
            return
        folder = p if p.is_dir() else p.parent
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @Slot(str)
    def sendToTuning(self, path_str: str):
        if path_str and Path(path_str).is_file():
            self.tuningHandoffRequested.emit(path_str)

    @Slot(str)
    def sendToChorusScore(self, path_str: str):
        if path_str and Path(path_str).is_file():
            self.chorusScoreHandoffRequested.emit(path_str)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.worker.close()
