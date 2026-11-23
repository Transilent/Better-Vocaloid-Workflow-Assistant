"""The editable state of a song, independent of views and analysis caches."""
from dataclasses import asdict, dataclass, replace
from hashlib import file_digest
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


ROLES = ('vocal', 'guide', 'backing')
VARIANTS = ('original', 'timing', 'pitch', 'processed')
SCORE_SUFFIXES = ('.mid', '.midi', '.svp', '.ustx', '.vpr', '.vsqx', '.acep', '.ufdata')


def fingerprint(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def sha256(path):
    with Path(path).open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('时间或参数必须是有限数值。')
    return result


@dataclass(frozen=True)
class Recording:
    id: str
    path: str
    sha: str
    stamp: tuple
    rate: int
    frames: int
    channels: int
    name: str
    role: str = 'vocal'
    part: str = ''
    offset: float = 0.
    score_lane: str = ''
    timing_ref: str = ''
    muted: bool = False

    @property
    def duration(self):
        return self.frames / self.rate

    @classmethod
    def read(cls, path, role='vocal', *, identity=None):
        import soundfile

        if role not in ROLES:
            raise ValueError('未知音轨角色。')
        path = Path(path).expanduser().resolve(strict=True)
        stamp = fingerprint(path)
        info = soundfile.info(str(path))
        if not info.frames or info.samplerate < 8000:
            raise ValueError(f'音频为空或采样率无效：{path.name}')
        digest = sha256(path)
        if fingerprint(path) != stamp:
            raise ValueError(f'读取期间素材已变化：{path.name}')
        return cls(identity or uuid4().hex, str(path), digest, stamp, info.samplerate,
                   info.frames, info.channels, path.stem, role)

    def check_source(self):
        if fingerprint(self.path) != self.stamp:
            raise ValueError(f'素材已变化，请重新载入：{self.name}')


@dataclass(frozen=True)
class ScoreNote:
    start: float
    end: float
    pitch: int
    lyric: str = ''


@dataclass(frozen=True)
class ScoreLane:
    id: str
    name: str
    notes: tuple


@dataclass(frozen=True)
class Score:
    path: str
    sha: str
    lanes: tuple
    offset: float = 0.

    @staticmethod
    def read(path):
        path = Path(path).expanduser().resolve(strict=True)
        suffix = path.suffix.casefold()
        if suffix not in SCORE_SUFFIXES:
            raise ValueError('请选择 MIDI、SVP、USTX、VPR、VSQX、ACEP 或 UFData。')
        before = fingerprint(path)
        if suffix in ('.mid', '.midi'):
            from agent_score import read_score

            score = read_score(path)
            metadata = score['tracks']
            rows = score['notes']
            digest = score['source_sha256']
        else:
            from agent_style import context_for_snapshot
            from tuning_workflow import read_source

            source = read_source(path)
            context = context_for_snapshot(source)
            metadata, rows = context['tracks'], context['notes']
            digest = source.sha256
        if fingerprint(path) != before:
            raise ValueError('读取期间乐谱已变化。')
        notes = {}
        for row in rows:
            if row.get('end_seconds') is None:
                continue
            start = number(row.get('start_seconds', row.get('onset_seconds')))
            end = number(row['end_seconds'])
            pitch = int(row['pitch'])
            if start < end and 0 <= pitch <= 127:
                notes.setdefault(str(row['track_index']), []).append(
                    ScoreNote(start, end, pitch, str(row.get('lyric') or '')))
        lanes = tuple(
            ScoreLane(key, str(track.get('name') or f'谱轨 {int(key) + 1}'),
                      tuple(sorted(notes[key], key=lambda note: (note.start, note.end, note.pitch))))
            for track in metadata if (key := str(track['track_index'])) in notes
        )
        if not lanes:
            raise ValueError('乐谱中没有可用音符。')
        return Score(str(path), digest, lanes)


@dataclass(frozen=True)
class Phrase:
    start: float
    end: float
    label: str = ''


@dataclass(frozen=True)
class Edit:
    track: str
    kind: str
    start: float
    end: float
    value: float

    # Timing intervals use project seconds and values use seconds. Pitch
    # intervals use take seconds and values use cents.
    def __post_init__(self):
        if self.kind not in ('timing', 'pitch') or number(self.start) >= number(self.end):
            raise ValueError('处理区间无效。')
        number(self.value)


@dataclass(frozen=True)
class ChorusProject:
    title: str = '未命名合唱'
    id: str = ''
    rate: int = 44100
    tracks: tuple = ()
    score: Score | None = None
    guide: str = ''
    phrases: tuple = ()
    edits: tuple = ()

    @classmethod
    def fresh(cls, title='未命名合唱'):
        return cls(title=title, id=uuid4().hex)

    @property
    def start(self):
        return min(0., *(track.offset for track in self.tracks)) if self.tracks else 0.

    @property
    def end(self):
        return max((track.offset + track.duration for track in self.tracks), default=0.)

    @property
    def first_frame(self):
        return round(self.start * self.rate)

    @property
    def frames(self):
        return max(0, round(self.end * self.rate) - self.first_frame)

    def track(self, identity):
        return next((track for track in self.tracks if track.id == identity), None)

    def reference(self, track=None):
        return self.track(track.timing_ref if track and track.timing_ref else self.guide)

    def add(self, tracks):
        tracks = self.tracks + tuple(tracks)
        guide = self.guide
        if not guide:
            reference = next((item for item in tracks if item.role == 'guide'), None)
            reference = reference or next((item for item in tracks if item.role == 'vocal'), None)
            guide = reference.id if reference else ''
        if not self.tracks and tracks:
            return replace(self, tracks=tracks, rate=tracks[0].rate, guide=guide)
        return replace(self, tracks=tracks, guide=guide)

    def update_track(self, identity, **updates):
        if self.track(identity) is None:
            raise ValueError('请选择一条音轨。')
        return replace(self, tracks=tuple(replace(track, **updates) if track.id == identity else track
                                          for track in self.tracks))

    def remove(self, identity):
        tracks = tuple(replace(track, timing_ref='') if track.timing_ref == identity else track
                       for track in self.tracks if track.id != identity)
        updated = replace(self, tracks=(), guide='' if self.guide == identity else self.guide,
                          edits=tuple(edit for edit in self.edits if edit.track != identity))
        return replace(updated.add(tracks), rate=self.rate)

    def replace_region(self, track, kind, start, end, edits):
        """Replace only the chosen interval, retaining both outside fragments."""
        retained = []
        for edit in self.edits:
            if edit.track != track or edit.kind != kind or edit.end <= start or edit.start >= end:
                retained.append(edit)
                continue
            if edit.start < start:
                retained.append(replace(edit, end=start))
            if edit.end > end:
                retained.append(replace(edit, start=end))
        entries = tuple(sorted((*retained, *edits), key=lambda edit: (edit.track, edit.kind, edit.start)))
        return replace(self, edits=entries)

    def edits_for(self, track, kind):
        return tuple(edit for edit in self.edits if edit.track == track and edit.kind == kind)

    def save(self, path):
        path = Path(path).expanduser().resolve()
        document = asdict(self)
        if self.score:
            document['score'].pop('lanes')
        document['schema'] = 'vwa-chorus/1'
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                    prefix='.chorus-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return path
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def open(cls, path, check=lambda: None, progress=lambda _: None):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if data.get('schema') != 'vwa-chorus/1':
            raise ValueError('请选择新版 VWA 合唱工程。')
        records = []
        for raw in data['tracks']:
            check()
            progress(str(raw['name']))
            source = Recording.read(raw['path'], raw['role'], identity=str(raw['id']))
            if source.sha != raw['sha']:
                raise ValueError(f'源文件和保存工程时不一致：{source.name}')
            records.append(replace(source, name=str(raw['name']), offset=number(raw['offset']),
                                   part=str(raw['part']), score_lane=str(raw['score_lane']),
                                   timing_ref=str(raw['timing_ref']), muted=bool(raw['muted'])))
        ids = {track.id for track in records}
        if len(ids) != len(records) or not data['id']:
            raise ValueError('工程标识重复或无效。')
        score = None
        if data['score']:
            raw_score = data['score']
            score = Score.read(raw_score['path'])
            if score.sha != raw_score['sha']:
                raise ValueError('乐谱和保存工程时不一致，请重新绑定。')
            score = replace(score, offset=number(raw_score['offset']))
        phrases = tuple(Phrase(number(row['start']), number(row['end']), str(row['label']))
                        for row in data['phrases'] if number(row['start']) < number(row['end']))
        edits = tuple(Edit(str(row['track']), str(row['kind']), number(row['start']),
                           number(row['end']), number(row['value']))
                      for row in data['edits'] if row['track'] in ids)
        rate = int(data['rate'])
        if not 8000 <= rate <= 384000:
            raise ValueError('工程采样率无效。')
        return cls(str(data['title']), str(data['id']), rate, tuple(records), score,
                   str(data['guide']) if data['guide'] in ids else '', phrases, edits)
