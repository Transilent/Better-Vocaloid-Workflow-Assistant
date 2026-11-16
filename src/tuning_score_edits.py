"""Revision-bound edits to existing native notes. No format conversion or rendering."""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
import unicodedata

from vocal_expression import TempoMap, SV_QUARTER


FORMATS = frozenset(('ustx', 'svp'))
FIELDS = frozenset(('lyric', 'pitch', 'onset', 'duration'))
MAX_EDITS = 4096
MAX_EDIT_BYTES = 640 * 1024  # Leave room in the shared draft for styles and metadata.
UNSUPPORTED = '此工程格式暂不支持改词或改音符。'
PHONEMES = '此音符有手工发音或延音设置，请在原编辑器改词。'
TIMING = '此音符或片段有手工时序、曲线或音素，已保护原位置和时长。'
PITCH = '此片段有原生音高锚点或重录参数，已保护音符音高。'


def _lyric(text):
    if not isinstance(text, str):
        raise ValueError('歌词必须是文字。')
    text = text.strip()
    if (not text or len(text) > 300 or any(unicodedata.category(c) in ('Cc', 'Cs', 'Zl', 'Zp') for c in text)
            or any(c in text for c in '[]') or text.startswith('.') or text.casefold() in
            ('+', '++', '-', 'r', 'sil', 'pau', 'br', 'ap', 'sp')):
        raise ValueError('请输入单个音符的歌词文字；延音、换气和手工发音请在原编辑器处理。')
    return text


def normalize_edits(rows):
    if not isinstance(rows, list) or len(rows) > MAX_EDITS:
        raise ValueError('音符修改清单无效或过长。')
    output, seen = [], set()
    size = 0
    for row in rows:
        if (not isinstance(row, dict) or not row.keys() <= FIELDS | {'note_id'}
                or len(row) < 2):
            raise ValueError('音符修改含有未知字段。')
        identity = row.get('note_id')
        if (not isinstance(identity, str) or not re.fullmatch(r'note_[0-9a-f]{24}', identity)
                or identity in seen):
            raise ValueError('音符修改的身份无效或重复。')
        seen.add(identity)
        item = {'note_id': identity}
        for key in FIELDS & row.keys():
            value = row[key]
            if key == 'lyric':
                item[key] = _lyric(value)
            elif (type(value) is not int or value < (1 if key == 'duration' else 0)
                  or value > (127 if key == 'pitch' else 2**52 - 1)):
                raise ValueError('音符音高、位置或时长无效。')
            else:
                item[key] = value
        size += len(json.dumps(item, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) + 1
        if size > MAX_EDIT_BYTES:
            raise ValueError('音符修改清单无效或过长。')
        output.append(item)
    return sorted(output, key=lambda row: row['note_id'])


def _nonempty(value):
    if isinstance(value, dict):
        return any(_nonempty(entry) for key, entry in value.items() if key != 'mode')
    return bool(value)


@dataclass(frozen=True)
class Target:
    track: int
    path: tuple
    offset: int
    step: int
    minimum: int
    maximum: int
    old: dict
    fields: dict
    lyric_reason: str
    timing_reason: str
    pitch_reason: str

    def reason(self, field):
        return (self.lyric_reason if field == 'lyric' else self.pitch_reason if field == 'pitch'
                else self.timing_reason)

    def capabilities(self):
        return {'lyricReason': self.lyric_reason, 'timingReason': self.timing_reason,
                'pitchReason': self.pitch_reason}


class ScoreEdits:
    def __init__(self, data, format, geometry, context):
        self.format = format
        self.context = context
        project, layout = geometry
        self.clock = TempoMap(project['time']['tempo']) if format in FORMATS else None
        self.targets = {}
        self.editable_tracks = set()
        self.rows = {row['note_id']: row for row in context['notes']}
        if not self.clock:
            return
        placements = {(track.index, place.key): place for track in layout.tracks if track.writable
                      for place in track.style_placements}
        part_limits = {}
        self.track_notes = {}
        for row in context['notes']:
            track = row['track_index']
            self.track_notes.setdefault(track, []).append(row)
            place = placements.get((track, row.get('group_placement', 'main')))
            if place is None or row.get('read_only_reason'):
                continue
            local_index = row['note_index']
            minimum = 0
            if format == 'ustx':
                path = ('voice_parts', place.index)
                group = data['voice_parts'][place.index]
                note = group['notes'][local_index]
                fields = {'lyric': 'lyric', 'pitch': 'tone', 'onset': 'position', 'duration': 'duration'}
                step = SV_QUARTER // 480
                # Keep the part and the location of its automation fixed.
                if path not in part_limits:
                    maximum = group.get('duration')
                    actual_end = max((n.end - place.offset) // step for n in place.notes)
                    limit = 2**30 - 1 - max(0, place.offset // step)
                    part_limits[path] = min(limit, maximum) if type(maximum) is int and maximum >= actual_end else actual_end
                maximum = part_limits[path]
                manual_phones = bool(note.get('phoneme_overrides') or note.get('phoneme_expressions'))
                timed = _nonempty(group.get('curves')) or bool(note.get('phoneme_overrides'))
                pitched = False
            else:
                if place.ref.get('pitchOffset', 0):
                    continue
                path = (('tracks', track, 'mainGroup') if place.library_index is None
                        else ('library', place.library_index))
                group = place.group
                note = group['notes'][local_index]
                fields = {'lyric': 'lyrics', 'pitch': 'pitch', 'onset': 'onset', 'duration': 'duration'}
                step = 1
                maximum = min(2**52 - 1, 2**52 - 1 - place.offset)
                begin, end = place.ref.get('blickAbsoluteBegin', 0), place.ref.get('blickAbsoluteEnd', -1)
                # Layout already protects any clip that cuts off a note. A clip
                # can store boundaries even when its entire score is visible.
                if begin != 0 or end != -1:
                    minimum = max(0, begin - place.offset)
                    if end != -1:
                        maximum = min(maximum, end - place.offset)
                manual_phones = bool(note.get('phonemes'))
                attributes = note.get('attributes') or {}
                timed = (_nonempty(group.get('parameters')) or bool(note.get('retakes'))
                         or bool(note.get('phonemes')) or
                         (isinstance(attributes, dict) and bool(attributes.get('dur'))))
                params = group.get('parameters') or {}
                pitched = (_nonempty(group.get('pitchControls')) or _nonempty(params.get('pitchDelta'))
                           or bool(note.get('pitchControls') or note.get('retakes')))
                timed = timed or pitched
            old = {key: note.get(raw, '' if key == 'lyric' else None) for key, raw in fields.items()}
            if (type(old['onset']) is not int or old['onset'] < 0
                    or type(old['duration']) is not int or old['duration'] <= 0
                    or type(old['pitch']) is not int or not 0 <= old['pitch'] <= 127
                    or not isinstance(old['lyric'], str)):
                continue
            lyric = old['lyric'].strip()
            explicit = (not lyric or lyric.casefold() in ('+', '++', '-', 'r', 'sil', 'pau', 'br', 'ap', 'sp')
                        or lyric.startswith('.') or '[' in lyric or ']' in lyric)
            # Keep an explicitly notated rest/continuation out of the lyric tool.
            self.targets[row['note_id']] = Target(
                track, (*path, 'notes', local_index), place.offset, step, minimum, maximum, old, fields,
                PHONEMES if manual_phones or explicit else '', TIMING if timed else '', PITCH if pitched else '')
            if not (self.targets[row['note_id']].lyric_reason and timed and pitched):
                self.editable_tracks.add(track)

    def capabilities(self, identity):
        target = self.targets.get(identity)
        if target is None:
            reason = (self.rows.get(identity, {}).get('read_only_reason') or '此音符的原生配置尚不支持编辑。'
                      if self.format in FORMATS else UNSUPPORTED)
            return {'lyricReason': reason, 'timingReason': reason, 'pitchReason': reason}
        return target.capabilities()

    def projected(self, rows):
        edits = normalize_edits(rows)
        if not edits:
            return self.context
        if self.format not in FORMATS:
            raise ValueError(UNSUPPORTED)
        changed, changed_timing = {}, set()
        for edit in edits:
            identity = edit['note_id']
            target = self.targets.get(identity)
            if target is None:
                raise ValueError('所选音符的身份或原生编辑能力已变化，请重新载入。')
            local = dict(target.old)
            for field in edit.keys() - {'note_id'}:
                if edit[field] == local[field]:
                    continue
                if problem := target.reason(field):
                    raise ValueError(problem)
                local[field] = edit[field]
            onset, duration = local['onset'], local['duration']
            if onset != target.old['onset'] or duration != target.old['duration']:
                if onset < target.minimum or onset + duration > target.maximum:
                    raise ValueError('音符超出原片段可编辑范围。')
                changed_timing.add(identity)
            start = target.offset + onset * target.step
            end = start + duration * target.step
            row = self.rows[identity]
            changed[identity] = {**row, 'lyric': local['lyric'], 'lyric_raw': local['lyric'],
                                 'pitch': local['pitch'], 'onset_tick': onset * target.step,
                                 'duration_tick': duration * target.step, 'project_onset_tick': start,
                                 **({'project_end_tick': end} if 'project_end_tick' in row else {}),
                                 'start_seconds': self.clock.to_seconds(start), 'end_seconds': self.clock.to_seconds(end)}
        for track in {self.targets[identity].track for identity in changed_timing}:
            ordered = sorted((changed.get(row['note_id'], row) for row in self.track_notes[track]),
                             key=lambda row: (row['start_seconds'], row['end_seconds']))
            furthest = touched_end = -math.inf
            for note in ordered:
                start, end = note['start_seconds'], note['end_seconds']
                touched = note['note_id'] in changed_timing
                if start < (furthest if touched else touched_end):
                    raise ValueError('改动会使同轨音符重叠，已保留原位置。')
                furthest = max(furthest, end)
                if touched:
                    touched_end = max(touched_end, end)
        return {**self.context, 'notes': [changed.get(row['note_id'], row) for row in self.context['notes']]}

    def update(self, rows, identity, changes):
        target = self.targets.get(identity)
        if target is None:
            raise ValueError('此音符不能修改。')
        other = {row['note_id']: dict(row) for row in rows}
        edit = {**other.pop(identity, {'note_id': identity}), **changes}
        edit = {key: value for key, value in edit.items()
                if key == 'note_id' or value != target.old.get(key)}
        if len(edit) > 1:
            other[identity] = edit
        result = normalize_edits(list(other.values()))
        return result

    def gesture(self, identity, current, action, seconds, pitch, horizontal):
        target = self.targets.get(identity)
        if target is None or not math.isfinite(seconds) or type(pitch) is not int or not 0 <= pitch <= 127:
            raise ValueError('音符位置无效。')
        start = current.get('onset', target.old['onset'])
        duration = current.get('duration', target.old['duration'])
        if action == 'move' and not horizontal:
            return {'pitch': pitch}
        position = round((self.clock.to_position(seconds) - target.offset) / target.step)
        if action == 'move':
            start = max(target.minimum, min(target.maximum - duration, position))
            return {'onset': start, 'pitch': pitch}
        if action == 'resize':
            return {'duration': max(1, min(target.maximum, position) - start)}
        raise ValueError('未知音符操作。')

    def geometry(self, identity, patch, changes):
        target = self.targets[identity]
        note = {**target.old, **patch, **changes}
        first = target.offset + note['onset'] * target.step
        return {'start': self.clock.to_seconds(first),
                'end': self.clock.to_seconds(first + note['duration'] * target.step), 'pitch': note['pitch']}

    def materialize(self, data, rows):
        edits = normalize_edits(rows)
        self.projected(edits)
        output = deepcopy(data)
        details = []
        for edit in edits:
            target = self.targets[edit['note_id']]
            native = output
            for key in target.path:
                native = native[key]
            changed = {}
            for field, value in sorted(edit.items()):
                if field == 'note_id' or value == target.old[field]:
                    continue
                native[target.fields[field]] = value
                changed[field] = {'before': target.old[field], 'after': value}
            if changed:
                details.append({'note_id': edit['note_id'], 'track_index': target.track, 'fields': changed})
        return output, details
