"""Verified local subtitle editor backend for the Qt Quick workspace.

The object reuses :mod:`subtitles` for LRC/MIDI parsing and rendering.  Task
MIDI is accepted only through the same verified artifact identity used by the
Quick handoff pages.  Export reuses the media publisher's complete-file,
no-overwrite transaction.

Accepts MIDI Type 0/1 with lyric events, timed LRC, and a selected NetEase
version. The shared lookup controller supplies network references; exports
are independent SRT or WebVTT files.
"""
from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from agent_media import _publish_without_overwrite
from common import file_hash, native_path
from i18n import tr
from quick_media_handoff import verify_task_artifact
import subtitles


_MAX_MIDI_BYTES = 64 * 1024 * 1024
_MAX_LRC_BYTES = 4 * 1024 * 1024
_MAX_CUE_TEXT = 4096


class SubtitleEditError(ValueError):
    """A user-correctable subtitle input or export error."""


def _local_path(value: Any) -> Path:
    if isinstance(value, QUrl):
        if value.isEmpty():
            raise SubtitleEditError('没有选择本地文件。')
        if not value.isLocalFile():
            raise SubtitleEditError('只接受本机文件。')
        value = value.toLocalFile()
    if not isinstance(value, (str, os.PathLike)):
        raise SubtitleEditError('文件路径无效。')
    text = os.fspath(value).strip().strip('"').strip()
    if not text:
        raise SubtitleEditError('文件路径为空。')
    try:
        path = Path(text).expanduser().resolve()
    except (OSError,RuntimeError,TypeError,ValueError) as exc:
        raise SubtitleEditError('无法解析文件路径。') from exc
    if not path.is_file():
        raise SubtitleEditError('所选文件不存在或不是普通文件。')
    return path


def _output_path(value: Any, format_name: str) -> Path:
    if isinstance(value, QUrl):
        if value.isEmpty():
            raise SubtitleEditError('没有选择导出文件。')
        if not value.isLocalFile():
            raise SubtitleEditError('字幕只能导出到本机文件。')
        value = value.toLocalFile()
    if not isinstance(value, (str, os.PathLike)):
        raise SubtitleEditError('导出路径无效。')
    text = os.fspath(value).strip().strip('"').strip()
    if not text:
        raise SubtitleEditError('导出路径为空。')
    raw = Path(text).expanduser()
    try:
        parent = raw.parent.resolve()
    except (OSError,RuntimeError,TypeError,ValueError) as exc:
        raise SubtitleEditError('无法解析导出目录。') from exc
    if not parent.is_dir():
        raise SubtitleEditError('导出目录不存在。')
    target = parent / raw.name
    suffix = '.' + format_name
    if not target.suffix:
        target = target.with_name(target.name + suffix)
    elif target.suffix.casefold() != suffix:
        raise SubtitleEditError(f'{format_name.upper()} 文件名必须使用 {suffix} 后缀。')
    return target


def _read_lrc(path: Path) -> str:
    if path.stat().st_size > _MAX_LRC_BYTES:
        raise SubtitleEditError('LRC 文件超过 4 MiB，请先精简歌词。')
    try:
        return path.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError as exc:
        raise SubtitleEditError('LRC 需要 UTF-8 或带 BOM 的 UTF-8 文本。') from exc


def _midi_tracks(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > _MAX_MIDI_BYTES:
        raise SubtitleEditError('MIDI 文件超过 64 MiB。')
    import mido
    try:
        midi = mido.MidiFile(str(path), charset='utf8')
    except Exception as exc:
        raise SubtitleEditError('无法读取 MIDI：' + (str(exc) or exc.__class__.__name__)) from exc
    if midi.type == 2:
        raise SubtitleEditError('字幕工具暂不支持 MIDI Type 2。')
    rows = []
    for index, track in enumerate(midi.tracks):
        count = sum(1 for message in track if message.type == 'lyrics' and message.text.strip())
        if not count:
            continue
        name = str(track.name or '').strip()
        rows.append({'id': index, 'label': name or tr('轨道 {0}', index + 1),
                     'lyricEvents': count})
    if not rows:
        raise SubtitleEditError('MIDI 中没有带歌词事件的声部。')
    return rows


def _read_midi(path, expected_digest=''):
    if path.suffix.casefold() not in ('.mid', '.midi'):
        raise SubtitleEditError('这里需要 MIDI（.mid/.midi）文件。')
    if path.stat().st_size > _MAX_MIDI_BYTES:
        raise SubtitleEditError('MIDI 文件超过 64 MiB。')
    digest = expected_digest or file_hash(path)
    tracks = _midi_tracks(path)
    cues = subtitles.midi_cues(path, int(tracks[0]['id']))
    if file_hash(path) != digest:
        raise SubtitleEditError('MIDI 在读取时发生变化；请重新选择来源。')
    return path, tracks, cues, digest


def read_task_midi(job, row, check=None):
    """Recheck the chosen identity and read a stable MIDI off the UI thread."""
    actual = verify_task_artifact(job, row, 'midi', check=check)
    if check is not None and check():
        raise InterruptedError('已取消字幕加载。')
    return _read_midi(Path(actual['path']), actual['sha256'])


def _publish_new_text(target: Path, text: str) -> None:
    """Reuse the media publisher's complete-file, no-overwrite transaction."""
    target_native = Path(native_path(target))
    if os.path.lexists(target_native):
        raise FileExistsError('导出目标已存在；没有覆盖现有文件。')
    descriptor, temporary_name = tempfile.mkstemp(
        prefix='.' + target_native.name + '.', suffix='.tmp', dir=str(target_native.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        if not _publish_without_overwrite(temporary, target_native):
            raise FileExistsError('导出目标已存在；没有覆盖现有文件。')
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class QuickSubtitles(QObject):
    """The window's subtitle draft; closing the dialog preserves it."""

    changed = Signal()
    cuesChanged = Signal()
    _DRAFT_FIELDS = (
        '_cues', '_tracks', '_selected_track', '_source_kind', '_source_origin', '_source_path',
        '_source_label', '_source_message', '_source_sha256', '_task_job_id', '_task_artifact_id',
        '_offset', '_format', '_message', '_error', '_exported_path', '_dirty',
    )

    def __init__(self, owner: QObject | None = None):
        super().__init__(owner if isinstance(owner, QObject) else None)
        self._cues: list[subtitles.Cue] = []
        self._tracks: list[dict[str, Any]] = []
        self._selected_track = -1
        self._source_kind = ''
        self._source_origin = ''
        self._source_path = ''
        self._source_label = ''
        self._source_message = ''
        self._source_sha256 = ''
        self._task_job_id = ''
        self._task_artifact_id = ''
        self._offset = 0.0
        self._format = 'srt'
        self._message = ''
        self._error = ''
        self._exported_path = ''
        self._dirty = False
        self._drafts = {}
        self.revision = 0

    def _view_map(self) -> dict[str, Any]:
        problem = ''
        if self._cues:
            try:
                subtitles.validate(self._cues)
                if max(cue.end for cue in self._cues) + self._offset <= 0:
                    problem = tr('整体偏移后没有剩余字幕，请调小负偏移。')
            except ValueError as error:
                problem = str(error)
        return {
            'sourceKind': self._source_kind,
            'sourceOrigin': self._source_origin,
            'sourcePath': self._source_path,
            'sourceLabel': self._source_label,
            'sourceMessage': self._source_message,
            'sourceVerified': self._source_origin == 'task' and bool(self._source_sha256),
            'taskJobId': self._task_job_id,
            'taskArtifactId': self._task_artifact_id,
            'selectedTrack': self._selected_track,
            'offset': self._offset,
            'format': self._format,
            'message': self._message,
            'error': self._error or problem,
            'exportedPath': self._exported_path,
            'dirty': self._dirty,
            'hasCues': bool(self._cues),
            'canExport': bool(self._cues) and not problem,
            'cueCount': len(self._cues),
            'draftKinds': sorted(set(self._drafts) | ({self._source_kind} if self._source_kind else set())),
        }

    @Property('QVariantMap', notify=changed)
    def view(self):
        return self._view_map()

    @Property('QVariantList', notify=cuesChanged)
    def cues(self):
        return [{'start': float(row.start), 'end': float(row.end), 'text': row.text}
                for row in self._cues]

    @Property('QVariantList', notify=changed)
    def midiTracks(self):
        return [dict(row) for row in self._tracks]

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    def _touch(self, *, cues: bool = False) -> None:
        self.revision += 1
        self.changed.emit()
        if cues:
            self.cuesChanged.emit()

    def _fail(self, error: Exception) -> bool:
        self._error = str(error) or error.__class__.__name__
        self._message = ''
        self.changed.emit()
        return False

    def _success(self, message: str) -> None:
        self._error = ''
        self._message = message
        self._touch()

    def _stash(self):
        if self._source_kind:
            self._drafts[self._source_kind] = {name: deepcopy(getattr(self, name)) for name in self._DRAFT_FIELDS}

    @Slot(str, result=bool)
    def restoreSource(self, kind):
        if kind == self._source_kind:
            return True
        if kind not in self._drafts:
            return False
        self._stash()
        for name, value in self._drafts[kind].items():
            setattr(self, name, deepcopy(value))
        self._touch(cues=True)
        return True

    def _check_source_hash(self) -> None:
        if not self._source_sha256 or not self._source_path:
            return
        path = Path(self._source_path)
        if not path.is_file() or file_hash(path) != self._source_sha256:
            raise SubtitleEditError('源文件已变化或缺失；请重新注入任务 MIDI 或选择本地文件。')

    def accept_midi(self, data, *, job: str = '', artifact_id: str = '') -> None:
        path, tracks, cues, digest = data
        track = tracks[0]
        self._stash()
        self._cues = list(cues)
        self._tracks = tracks
        self._selected_track = int(track['id'])
        self._source_kind = 'midi'
        self._source_origin = 'task' if job else 'local'
        self._source_path = str(path)
        self._source_label = path.name + ' · ' + str(track['label'])
        self._source_message = tr('来自 MIDI 歌词时间；分句由停顿推断，请预览后核对。')
        self._source_sha256 = digest
        self._task_job_id = job
        self._task_artifact_id = artifact_id
        self._format = 'srt'
        self._offset = 0.0
        self._exported_path = ''
        self._dirty = False
        self._success(tr('已载入 {0} 行，共 {1} 个可用 MIDI 声部。', len(self._cues), len(tracks)))
        self.cuesChanged.emit()

    def accept_reference(self, reference):
        from lyrics_sources import song_id
        try:
            if reference.origin.get('provider') != 'netease' or not reference.cues:
                raise SubtitleEditError('这个版本没有可用的网易云字幕时间轴。')
            identifier = song_id(reference.origin.get('song_id', ''))
            cues = list(reference.cues)
            subtitles.validate(cues)
            if any(len(cue.text) > _MAX_CUE_TEXT for cue in cues):
                raise SubtitleEditError('网易云歌词单行超过 4096 个字符。')
        except (TypeError, ValueError, AttributeError) as exc:
            return self._fail(exc)
        self._stash()
        self._cues, self._tracks, self._selected_track = cues, [], -1
        self._source_kind = self._source_origin = 'netease'
        self._source_path = self._source_sha256 = self._task_job_id = self._task_artifact_id = ''
        self._source_label = f'网易云歌曲 #{identifier}'
        self._source_message = tr('采用所选歌曲版本的时间轴，请核对演唱版本与整体偏移。')
        self._offset, self._format, self._exported_path, self._dirty = 0., 'srt', '', False
        self._success(tr('已载入 {0} 行定时歌词。', len(cues)))
        self.cuesChanged.emit()
        return True

    @Slot(QUrl, result=bool)
    def loadLocal(self, url):
        try:
            path = _local_path(url)
            suffix = path.suffix.casefold()
            if suffix in ('.mid', '.midi'):
                self.accept_midi(_read_midi(path))
                return True
            if suffix != '.lrc':
                raise SubtitleEditError('只接受 MIDI（.mid/.midi）或定时 LRC（.lrc）。')
            cues = subtitles.lrc(_read_lrc(path))
            self._stash()
            self._cues = list(cues)
            self._tracks = []
            self._selected_track = -1
            self._source_kind = 'lrc'
            self._source_origin = 'local'
            self._source_path = str(path)
            self._source_label = path.name
            self._source_message = tr('保留 LRC 时间戳；末行无结束标记时默认显示 3 秒，可修改。')
            self._source_sha256 = file_hash(path)
            self._task_job_id = ''
            self._task_artifact_id = ''
            self._format = 'srt'
            self._offset = 0.0
            self._exported_path = ''
            self._dirty = False
            self._success(tr('已载入 {0} 行 LRC 字幕。', len(self._cues)))
            self.cuesChanged.emit()
            return True
        except Exception as exc:
            return self._fail(exc)

    @Slot(int, result=bool)
    def selectMidiTrack(self, track_id):
        try:
            if self._source_kind != 'midi':
                raise SubtitleEditError('当前来源不是 MIDI。')
            if self._selected_track == track_id:
                return True
            self._check_source_hash()
            row = next((item for item in self._tracks if int(item['id']) == int(track_id)), None)
            if row is None:
                raise SubtitleEditError('所选的 MIDI 声部不存在或没有歌词。')
            cues = subtitles.midi_cues(Path(self._source_path), int(track_id))
            discarded = self._dirty
            self._cues = list(cues)
            self._selected_track = int(track_id)
            self._source_label = Path(self._source_path).name + ' · ' + str(row['label'])
            self._exported_path = ''
            self._dirty = False
            message = tr('已切换到 {0}，共 {1} 行。', row['label'], len(self._cues))
            if discarded:
                message += tr(' 上一声部未导出的编辑已丢弃。')
            self._success(message)
            self.cuesChanged.emit()
            return True
        except Exception as exc:
            return self._fail(exc)

    @Slot(int, float, float, str, result=bool)
    def updateCue(self, index, start, end, text):
        try:
            if not 0 <= int(index) < len(self._cues):
                raise SubtitleEditError('字幕行不存在。')
            start, end = float(start), float(end)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise SubtitleEditError('开始时间应大于等于 0，结束时间应晚于开始时间。')
            value = str(text)
            if not value.strip():
                raise SubtitleEditError('字幕文字不能为空。')
            if len(value) > _MAX_CUE_TEXT:
                raise SubtitleEditError('单行字幕超过 4096 个字符。')
            self._cues[int(index)] = subtitles.Cue(start, end, value.strip())
            self._dirty = True
            self._exported_path = ''
            self._error = ''
            self._message = tr('字幕行已更新；导出前会检查时间顺序。')
            self._touch(cues=True)
            return True
        except Exception as exc:
            return self._fail(exc)

    @Slot(result=bool)
    def addCue(self):
        start = max((row.end for row in self._cues), default=0.0)
        self._cues.append(subtitles.Cue(start, start + 3.0, '新字幕'))
        self._dirty = True
        self._exported_path = ''
        self._error = ''
        self._message = tr('已添加字幕行。')
        self._touch(cues=True)
        return True

    @Slot(int, result=bool)
    def removeCue(self, index):
        try:
            if not 0 <= int(index) < len(self._cues):
                raise SubtitleEditError('字幕行不存在。')
            del self._cues[int(index)]
            self._dirty = True
            self._exported_path = ''
            self._error = ''
            self._message = tr('已删除字幕行。')
            self._touch(cues=True)
            return True
        except Exception as exc:
            return self._fail(exc)

    @Slot(float, result=bool)
    def setOffset(self, value):
        try:
            value = float(value)
            if not math.isfinite(value) or not -3600 <= value <= 3600:
                raise SubtitleEditError('整体偏移应为 -3600 到 3600 秒之间的有限数值。')
            if value == self._offset:
                return True
            self._offset = value
            self._exported_path = ''
            self._dirty = True
            self._error = ''
            self._message = tr('整体偏移已更新；原始时间不会被改写。')
            self._touch()
            return True
        except Exception as exc:
            return self._fail(exc)

    @Slot(str, result=bool)
    def setFormat(self, format_name):
        try:
            value = str(format_name or '').casefold()
            if value not in ('srt', 'vtt'):
                raise SubtitleEditError('字幕格式只能是 SRT 或 WebVTT。')
            self._format = value
            self._exported_path = ''
            self._error = ''
            self._message = tr('导出格式已切换为 {0}。', value.upper())
            self._touch()
            return True
        except Exception as exc:
            return self._fail(exc)

    def export(self, target: Any, format_name: str | None = None) -> Path:
        """Validate, render, then publish one new subtitle file."""
        selected = str(format_name or self._format).casefold()
        if selected not in ('srt', 'vtt'):
            raise SubtitleEditError('字幕格式只能是 SRT 或 WebVTT。')
        path = _output_path(target, selected)
        text = subtitles.render(self._cues, selected, self._offset)
        _publish_new_text(path, text)
        return path

    @Slot(QUrl, str, result=bool)
    def exportTo(self, url, format_name):
        try:
            path = self.export(url, format_name)
            self._format = str(format_name or self._format).casefold()
            self._exported_path = str(path)
            self._dirty = False
            self._success(tr('字幕已导出：{0}', path))
            return True
        except FileExistsError as exc:
            return self._fail(SubtitleEditError(str(exc)))
        except Exception as exc:
            return self._fail(exc)


__all__ = ('QuickSubtitles', 'SubtitleEditError', 'read_task_midi')
