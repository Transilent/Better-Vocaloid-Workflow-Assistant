"""Native SVP note placement and safe style targets, shared by desktop and Agent."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field


# SV2 182 (existing project fixture) through 196 (public SV2 project). A newer
# format can be inspected, but its placement and write contract is unverified.
SV2_MIN = 182
SV2_MAX = 196
GROUP_STYLE_ONLY = '引用、裁剪或移调的音符组目前只支持「保留原表现」下的风格编辑。'


def _obj(value):
    return value if isinstance(value, dict) else {}


def _id(value):
    return value if isinstance(value, str) and value else None


def _tick(value):
    return type(value) is int and abs(value) < 2**52


@dataclass(frozen=True)
class Note:
    index: int
    data: dict
    start: int
    end: int
    pitch: int | float | None


@dataclass
class Placement:
    track_index: int
    reference_index: int | None
    library_index: int | None
    group: dict
    ref: dict
    offset: int
    notes: list[Note] = field(default_factory=list)

    @property
    def adapted(self):
        return (self.library_index is not None or self.ref.get('blickAbsoluteBegin', 0) != 0
                or self.ref.get('blickAbsoluteEnd', -1) != -1 or bool(self.ref.get('pitchOffset')))

    @property
    def key(self):
        return 'main' if self.reference_index is None else f'group:{self.reference_index}'

    def group_in(self, project):
        if self.library_index is None:
            return project['tracks'][self.track_index]['mainGroup']
        return project['library'][self.library_index]

    def style_track(self, project):
        """Translate a placement to the style engine's group-local time basis."""
        track = project['tracks'][self.track_index]
        group = self.group_in(project)
        return {
            'name': track.get('name', ''),
            'mainRef': {**self.ref, 'blickAbsoluteBegin': 0, 'blickAbsoluteEnd': -1},
            'mainGroup': {**group, 'notes': [
                {**note.data, 'pitch': note.pitch} for note in self.notes
            ]},
        }


@dataclass
class Track:
    index: int
    instrumental: bool | None
    placements: list[Placement] = field(default_factory=list)
    issues: dict[str, str] = field(default_factory=dict)
    raw_count: int = 0
    native_refs: bool = False

    @property
    def note_count(self):
        return sum(len(placement.notes) for placement in self.placements)

    @property
    def writable(self):
        return self.instrumental is False and not self.issues and bool(self.note_count)

    @property
    def reason(self):
        return next(iter(self.issues.values()), '')

    @property
    def style_only(self):
        return self.native_refs or any(placement.adapted for placement in self.placements)


class Layout:
    def __init__(self, project):
        version = project.get('version')
        self.version = version if type(version) is int else None
        self.sv2 = self.version is not None and self.version >= SV2_MIN
        library = project.get('library', [])
        self._library = library if isinstance(library, list) else []
        self._definitions = defaultdict(list)
        for index, group in enumerate(self._library):
            if group_id := _id(_obj(group).get('uuid')):
                self._definitions[group_id].append(index)
        # Count across tracks, including muted or otherwise read-only placements.
        self._references = Counter()
        for track in project['tracks']:
            track = _obj(track)
            ref = _obj(track.get('mainRef'))
            group = _obj(track.get('mainGroup'))
            main_id = _id(ref.get('groupID'))
            inline_id = _id(group.get('uuid'))
            if main_id and (main_id != inline_id or main_id in self._definitions):
                self._references[main_id] += 1
            groups = track.get('groups', [])
            if isinstance(groups, list):
                for item in groups:
                    if target := _id(_obj(item).get('groupID')):
                        self._references[target] += 1
        self.tracks = tuple(self._track(i, track) for i, track in enumerate(project['tracks']))

    def _track(self, index, raw):
        if not isinstance(raw, dict):
            return Track(index, None, issues={'track.invalid': '轨道格式无效。'})
        raw_ref = raw.get('mainRef')
        ref = _obj(raw_ref)
        instrumental = ref.get('isInstrumental', False)
        row = Track(index, instrumental if type(instrumental) is bool else None)
        issue = row.issues.setdefault
        if raw_ref is not None and not isinstance(raw_ref, dict):
            issue('track.main_ref_invalid', '主音符组引用的格式无效。')
        if row.instrumental is not False:
            if row.instrumental:
                issue('track.instrumental', '该轨道是伴奏轨。')
            else:
                issue('track.instrumental_unknown', '无法确认该轨道是否为伴奏轨。')
            return row
        if self.sv2 and self.version > SV2_MAX:
            issue('track.sv2_version', '此 SV2 工程格式尚未验证写回，当前只读。')
        inline = raw.get('mainGroup')
        main_id = _id(ref.get('groupID'))
        inline_id = _id(_obj(inline).get('uuid'))
        groups = raw.get('groups', [])
        if not isinstance(groups, list):
            issue('track.linked_groups', '音符组引用列表格式无效。')
            groups = []
        if groups and not self.sv2:
            issue('track.linked_groups', '当前只支持已核验的 SV2 素材库组引用。')
        row.native_refs = self.sv2 and bool(groups)
        if self.sv2 and main_id and main_id != inline_id:
            if not _obj(inline).get('notes'):
                self._reference(row, None, ref)
            else:
                issue('track.group_reference', '主音符组与引用目标不一致。')
        elif isinstance(inline, dict):
            if self.sv2 and inline_id and (inline_id in self._definitions or self._references[inline_id]):
                issue('track.group_shared', '该主音符组与其他组引用冲突，当前只读。')
            self._placement(row, None, None, inline, ref)
        elif not self.sv2 or not groups or inline is not None:
            issue('track.main_group_missing', '轨道缺少有效的主音符组。')
        if self.sv2:
            for i, item in enumerate(groups):
                self._reference(row, i, item)
        # Each displayed note must resolve to one style target at its time.
        # Overlapping notes *within* a group still use the existing style rules.
        if len(row.placements) > 1:
            furthest = -float('inf')
            previous = None
            spans = sorted((n.start, n.end, p.key) for p in row.placements for n in p.notes)
            for start, end, key in spans:
                if start < furthest and key != previous:
                    issue('track.group_overlap', '同一轨道的多个音符组在此处重叠，当前只读。')
                    break
                if end > furthest:
                    furthest, previous = end, key
        if not row.note_count:
            issue('track.no_notes', '轨道没有可安全定位的音符。')
        return row

    def _reference(self, row, index, ref):
        ref = _obj(ref)
        target = _id(ref.get('groupID'))
        slots = self._definitions.get(target, ())
        if len(slots) != 1:
            row.issues.setdefault('track.group_reference', '音符组引用缺失或素材库中有重复 ID。')
            return
        if self._references[target] != 1:
            row.issues.setdefault('track.group_shared', '音符组被多个片段引用；请先在 SV2 中将此组独立。')
        self._placement(row, index, slots[0], self._library[slots[0]], ref)

    def _placement(self, row, index, slot, group, ref):
        issue = row.issues.setdefault
        notes = group.get('notes')
        if not isinstance(notes, list):
            issue('track.notes_invalid', '音符组缺少有效的音符列表。')
            return
        row.raw_count += len(notes)
        offset = ref.get('blickOffset', 0)
        pitch_offset = ref.get('pitchOffset', 0)
        begin = ref.get('blickAbsoluteBegin', 0)
        end = ref.get('blickAbsoluteEnd', -1)
        if not _tick(offset):
            issue('track.offset_invalid', '音符组时间偏移无效。')
            return
        if not _tick(begin) or not _tick(end) or (end != -1 and end <= begin):
            issue('track.trim_invalid', '音符组裁剪范围无效。')
            return
        if type(pitch_offset) is not int or abs(pitch_offset) > 127:
            issue('track.pitch_offset_invalid', '音符组移调值无效。')
            return
        if type(ref.get('mute', False)) is not bool or ref.get('isInstrumental', False) is not False:
            issue('track.group_reference', '音符组静音或伴奏标记无效。')
            return
        if self.sv2 and ref.get('mute', False):
            return
        placement = Placement(row.index, index, slot, group, ref, offset)
        if not self.sv2 and placement.adapted:
            issue('track.cropped', '当前只支持已核验的 SV2 组裁剪和移调。')
        if 'parameters' in group and not isinstance(group['parameters'], dict):
            issue('track.parameters_invalid', '音符组参数格式无效。')
        trimmed = self.sv2 and (begin != 0 or end != -1)
        for i, note in enumerate(notes):
            if not isinstance(note, dict):
                issue('note.invalid', '音符组中存在无效的音符对象。')
                continue
            start, duration = note.get('onset'), note.get('duration')
            if not _tick(start) or not _tick(duration) or duration <= 0 or not _tick(start + offset + duration):
                issue('note.timing_invalid', '音符时间无效。')
                continue
            left, right = start + offset, start + offset + duration
            if trimmed and (left < begin or (end != -1 and right > end)):
                issue('track.group_trimmed_notes', '裁剪隐藏了全部或部分音符；此组当前只读。')
                left, right = max(left, begin), min(right, end) if end != -1 else right
                if left >= right:
                    continue
            if self.sv2 and note.get('musicalType') == 'rap':
                issue('track.rap', '此轨包含 SV2 说唱音符，当前只读。')
            pitch = note.get('pitch')
            if type(pitch) in (int, float) and -1e6 <= pitch <= 1e6:
                if self.sv2 and pitch_offset:
                    pitch += pitch_offset
            else:
                pitch = None
            placement.notes.append(Note(i, note, left, right, pitch))
        if placement.notes:
            row.placements.append(placement)
