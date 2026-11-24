"""The editable state of a song, independent of views and analysis caches."""
from dataclasses import asdict, dataclass, replace
from bisect import bisect_right
from hashlib import file_digest
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


ROLES = ('vocal', 'guide', 'backing', 'take')
VARIANTS = ('original', 'timing', 'pitch', 'processed')
SYLLABLE_GAIN = 'syllable_gain'
SCORE_SUFFIXES = ('.mid', '.midi', '.svp', '.ustx', '.vpr', '.vsqx', '.acep', '.ufdata')
LYRIC_LANGUAGES = ('ja', 'zh', 'en', 'yue')
ALIGNMENT_ENGINES = ('auto', 'tifa', 'hubertfa')
PITCH_ENGINES = ('auto', 'signalsmith', 'vwa', 'r3')
NOTE_EDIT_KINDS = ('split', 'merge')
SCORE_SOURCE_KINDS = ('external', 'preparation_draft')


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
    phrase_reference_only: bool = False

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
    source_kind: str = 'external'
    source_job: str = ''
    source_label: str = ''
    confirmed: bool = True

    @staticmethod
    def read(path, *, source_kind='external', source_job='', source_label='', confirmed=None):
        if source_kind not in SCORE_SOURCE_KINDS:
            raise ValueError('未知的乐谱来源类型。')
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
        is_confirmed = (source_kind != 'preparation_draft') if confirmed is None else bool(confirmed)
        return Score(str(path), digest, lanes, 0., source_kind, str(source_job or ''),
                     str(source_label or ''), is_confirmed)


@dataclass(frozen=True)
class Lyric:
    track: str
    text: str
    language: str = 'ja'
    confirmed: bool = False


@dataclass(frozen=True)
class Phrase:
    start: float
    end: float
    label: str = ''
    id: str = ''
    lyrics: tuple = ()
    reference: str = ''

    def lyric(self, track=''):
        return next((line for line in self.lyrics if line.track == track), None)

    def resolved_lyric(self, track):
        local, shared = self.lyric(track), self.lyric()
        return local if local and (local.confirmed or not (shared and shared.confirmed)) else shared


def interpolate(points, time):
    if time <= points[0][0]:
        return points[0][1]
    if time >= points[-1][0]:
        return points[-1][1]
    index = bisect_right(points, (time, math.inf))
    (a, left), (b, right) = points[index - 1:index + 1]
    return left + (right - left) * (time - a) / (b - a)


@dataclass(frozen=True)
class Edit:
    track: str
    kind: str
    start: float
    end: float
    value: float
    points: tuple = ()
    source: str = ''
    manual: bool = False
    basis: str = ''

    # Timing maps output project seconds to source project seconds. Pitch and
    # syllable gain follow take seconds. Phrase trim uses the output timeline.
    def __post_init__(self):
        if self.kind not in ('timing', 'pitch', 'gain', 'take', SYLLABLE_GAIN) or number(self.start) >= number(self.end):
            raise ValueError('处理区间无效。')
        number(self.value)
        if self.kind == 'take' and (not self.source or self.source == self.track):
            raise ValueError('请选择不同的备选录音。')
        if self.points:
            if self.kind not in ('timing', 'pitch', SYLLABLE_GAIN):
                raise ValueError('此编辑类型不支持曲线。')
            if len(self.points) < 2 or not all(math.isfinite(x) and math.isfinite(y) for x, y in self.points):
                raise ValueError('曲线至少需要两个有效点。')
            if self.points[0][0] != self.start or self.points[-1][0] != self.end:
                raise ValueError('曲线需覆盖完整编辑区间。')
            if any(a[0] >= b[0] or self.kind == 'timing' and a[1] >= b[1]
                   for a, b in zip(self.points, self.points[1:])):
                raise ValueError('时间锚点须保持先后顺序。')
        if self.kind == SYLLABLE_GAIN and (
                not self.points or not 0. < self.value <= 6.
                or any(not 0. <= db <= self.value for _, db in self.points)
                or self.points[0][1] != 0. or self.points[-1][1] != 0.):
            raise ValueError('音节增益曲线无效。')

    def clip(self, start, end):
        points = ()
        if self.points:
            left, right = start, end
            if self.kind == SYLLABLE_GAIN:
                fade = min(.018, (end - start) * .4)
                left = start + fade if start > self.start else start
                right = end - fade if end < self.end else end
            points = ((left, interpolate(self.points, left)),
                      *(p for p in self.points if left < p[0] < right),
                      (right, interpolate(self.points, right)))
            if left > start:
                points = ((start, 0.), *points)
            if right < end:
                points = (*points, (end, 0.))
        return replace(self, start=start, end=end, points=points)

    def source_time(self, time):
        return interpolate(self.points, time) if self.points else time - self.value

    def output_time(self, time):
        if self.points:
            return interpolate(tuple((source, output) for output, source in self.points), time)
        return time + self.value


@dataclass(frozen=True)
class NoteEdit:
    """Explicit user split or merge on source-recording vocal note boundaries."""
    track: str
    kind: str
    start: float
    end: float

    def __post_init__(self):
        if self.kind not in NOTE_EDIT_KINDS:
            raise ValueError('音符结构编辑类型无效。')
        a, b = number(self.start), number(self.end)
        if self.kind == 'split':
            if a < 0. or a != b:
                raise ValueError('音符拆分位置无效。')
        elif a < 0. or a >= b:
            raise ValueError('音符合并范围无效。')


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
    alignment_engine: str = 'auto'
    ensemble_language: str = 'ja'
    pitch_engine: str = 'auto'
    note_edits: tuple = ()

    @classmethod
    def fresh(cls, title='未命名合唱'):
        return cls(title=title, id=uuid4().hex)

    @property
    def timeline_tracks(self):
        return tuple(track for track in self.tracks if track.role != 'take' and
                     not (track.role == 'guide' and track.phrase_reference_only)) or self.tracks

    @property
    def start(self):
        return min((0., *(track.offset for track in self.timeline_tracks)))

    @property
    def end(self):
        return max((track.offset + track.duration for track in self.timeline_tracks), default=0.)

    @property
    def first_frame(self):
        return round(self.start * self.rate)

    @property
    def frames(self):
        return max(0, round(self.end * self.rate) - self.first_frame)

    def track(self, identity):
        return next((track for track in self.tracks if track.id == identity), None)

    def reference(self, track=None, phrase=None):
        for identity in (phrase.reference if phrase else '', track.timing_ref if track else '', self.guide):
            source = self.track(identity) if identity else None
            if source and source.role != 'backing' and (track is None or source.id != track.id):
                return source
        return None

    def add(self, tracks):
        tracks = self.tracks + tuple(tracks)
        guide = self.guide
        if not guide:
            reference = next((item for item in tracks if item.role == 'guide' and not item.phrase_reference_only), None)
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
        phrases = tuple(replace(phrase, reference='' if phrase.reference == identity else phrase.reference,
                                lyrics=tuple(line for line in phrase.lyrics if line.track != identity))
                        for phrase in self.phrases)
        updated = replace(self, tracks=(), guide='' if self.guide == identity else self.guide,
                          edits=tuple(edit for edit in self.edits if identity not in (edit.track, edit.source)),
                          phrases=phrases,
                          note_edits=tuple(item for item in self.note_edits if item.track != identity))
        return replace(updated.add(tracks), rate=self.rate)

    def replace_region(self, track, kind, start, end, edits):
        """Replace only the chosen interval, retaining both outside fragments."""
        retained = []
        for edit in self.edits:
            if edit.track != track or edit.kind != kind or edit.end <= start or edit.start >= end:
                retained.append(edit)
                continue
            if edit.start < start:
                retained.append(edit.clip(edit.start, start))
            if edit.end > end:
                retained.append(edit.clip(end, edit.end))
        entries = tuple(sorted((*retained, *edits), key=lambda edit: (edit.track, edit.kind, edit.start)))
        return replace(self, edits=entries)

    def edits_for(self, track, kind):
        return tuple(edit for edit in self.edits if edit.track == track and edit.kind == kind)

    def note_edits_for(self, track):
        return tuple(edit for edit in self.note_edits if edit.track == track)

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
                                   timing_ref=str(raw['timing_ref']), muted=bool(raw['muted']),
                                   phrase_reference_only=bool(raw.get('phrase_reference_only', False))))
        ids = {track.id for track in records}
        if len(ids) != len(records) or not data['id']:
            raise ValueError('工程标识重复或无效。')
        score = None
        if data['score']:
            raw_score = data['score']
            source_kind = str(raw_score.get('source_kind', 'external'))
            score = Score.read(
                raw_score['path'],
                source_kind=source_kind,
                source_job=str(raw_score.get('source_job', '')),
                source_label=str(raw_score.get('source_label', '')),
                confirmed=raw_score.get('confirmed', source_kind != 'preparation_draft'),
            )
            if score.sha != raw_score['sha']:
                raise ValueError('乐谱和保存工程时不一致，请重新绑定。')
            score = replace(score, offset=number(raw_score['offset']))
        phrases = tuple(Phrase(number(row['start']), number(row['end']), str(row['label']),
                               str(row.get('id', '')),
                               tuple(Lyric(str(line['track']), str(line['text']), str(line['language']),
                                           bool(line['confirmed']))
                                     for line in row.get('lyrics', ())
                                     if not line['track'] or line['track'] in ids),
                               str(row.get('reference', '')) if row.get('reference') in ids else '')
                        for row in data['phrases'] if number(row['start']) < number(row['end']))
        edits = tuple(Edit(str(row['track']), str(row['kind']), number(row['start']),
                           number(row['end']), number(row['value']),
                           tuple((number(x), number(y)) for x, y in row.get('points', ())),
                           str(row.get('source', '')), bool(row.get('manual', False)), str(row.get('basis', '')))
                      for row in data['edits'] if row['track'] in ids and
                      (row['kind'] != 'take' or any(item.id == row.get('source') and item.role == 'take'
                                                   for item in records)))
        note_edits = tuple(
            NoteEdit(str(row['track']), str(row['kind']), number(row['start']), number(row['end']))
            for row in data.get('note_edits', ()) if row.get('track') in ids
        )
        rate = int(data['rate'])
        if not 8000 <= rate <= 384000:
            raise ValueError('工程采样率无效。')
        engine = data.get('alignment_engine', 'auto')
        if engine not in ALIGNMENT_ENGINES:
            raise ValueError('唱段对齐引擎无效。')
        language = data.get('ensemble_language', 'ja')
        if language not in ('', *LYRIC_LANGUAGES):
            raise ValueError('请选择受支持的歌词语言。')
        pitch_engine = data.get('pitch_engine', 'auto')
        if pitch_engine not in PITCH_ENGINES:
            raise ValueError('修音引擎无效。')
        return cls(str(data['title']), str(data['id']), rate, tuple(records), score,
                   str(data['guide']) if data['guide'] in ids else '', phrases, edits, engine, language,
                   pitch_engine, note_edits)
