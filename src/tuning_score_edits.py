"""Revision-bound edits to existing native notes. No format conversion or rendering."""
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import re
import unicodedata

from preparation_languages import lyric_continuation, normalize_english_punctuation
from vocal_expression import SV_QUARTER
from tuning_native import at_path as _native, native_group_path
from tuning_note_detail import DETAILS, field_views, integer_field, read_fields, valid_value


DIRECT_FORMATS = frozenset(('ustx', 'svp'))
VOCALOID_FORMATS = frozenset(('vpr', 'vsqx'))
ADAPTED_FIELDS = {
    'vpr': {'lyric': 'lyric', 'pitch': 'number', 'onset': 'pos', 'duration': 'duration'},
    'vsqx': {'lyric': 'lyric', 'pitch': 'number', 'onset': 'pos', 'duration': 'duration'},
    'acep': {'lyric': 'lyric', 'pitch': 'pitch', 'onset': 'pos', 'duration': 'dur'},
    'ufdata': {'lyric': 'lyric', 'pitch': 'key', 'onset': 'tickOn', 'duration': 'tickOff'},
}
FORMATS = DIRECT_FORMATS | frozenset(ADAPTED_FIELDS)
FIELDS = frozenset(('lyric', 'pitch', 'onset', 'duration')) | DETAILS.keys()
MAX_EDITS = 4096
MAX_EDIT_BYTES = 640 * 1024  # Leave room in the shared draft for styles and metadata.
UNSUPPORTED = '此工程格式暂不支持改词或改音符。'
PHONEMES = '此音符有手工发音或延音设置，请在原编辑器改词。'
TIMING = '此音符或片段有手工时序、曲线或音素，已保护原位置和时长。'
PITCH = '此片段有原生音高锚点或重录参数，已保护音符音高。'
VPR_PHONEME = '无法为此 VOCALOID 歌词确定原语言的发音。可使用工程里同语言的已有词，或在 VOCALOID 中改词。'
VPR_PHONE_COUNT = '新发音与原音素的数量不同，无法保留音素位置，请在 VOCALOID 中改词。'
VOCALOID_LINKED_LYRIC = '这个英文词有需要保护的续唱发音，请在 VOCALOID 中一起修改。'


def _lyric(text):
    if not isinstance(text, str):
        raise ValueError('歌词必须是文字。')
    text = text.strip()
    if (not text or len(text) > 300 or any(unicodedata.category(c) in ('Cc', 'Cs', 'Zl', 'Zp') for c in text)
            or _special_lyric(text, extended=False)):
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
            elif key in DETAILS:
                if not valid_value(key, value):
                    raise ValueError('音符参数超出可编辑范围。')
                item[key] = int(value) if int(value) == value else value
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


def _special_lyric(lyric, *, extended=True):
    lyric = lyric.strip().casefold()
    return (not lyric or lyric_continuation(lyric) is not None or lyric in ('r', 'sil', 'pau', 'br', 'ap', 'sp')
            or lyric.startswith('.') or '[' in lyric or ']' in lyric or extended and
            (lyric == '/' or re.fullmatch(r'br\d+', lyric) is not None or re.search(r'#\d+$', lyric) is not None))


def _trim_phones(value):
    if isinstance(value, str):
        tokens = value.split()
        if tokens and len(tokens) <= 40 and len(value) <= 300 and not _special_lyric(value):
            return ' '.join(tokens)
    return None


@dataclass(frozen=True)
class NativeGroup:
    path: tuple
    raw: dict
    minimum: int
    maximum: int
    timed: bool
    protected: frozenset
    pitch_starts: tuple = ()
    pitch_ends: tuple = ()
    pitch_absolute: bool = False
    unknown_pitch: bool = False

    def pitch_window(self, start, end):
        i = bisect_right(self.pitch_ends, start)
        before = self.pitch_ends[i-1] if i else self.minimum
        after = self.pitch_starts[i] if i < len(self.pitch_starts) else self.maximum
        return max(self.minimum, before), min(self.maximum, after), after < end


def _adapted_group(data, format, place):
    step = getattr(place, 'step', SV_QUARTER // 480)
    path = native_group_path(format, place.track_index, place.index)
    raw = _native(data, path)
    offset = place.offset // step
    lower = max(0, -offset)
    if format in VOCALOID_FORMATS:
        from vpr_tuning import score_automation_changes_in_part
        upper = place.length
        if upper is None:
            upper = max((note.end - place.offset) // step for note in place.notes)
        timed = (raw.get('timingProtected') or score_automation_changes_in_part(raw, lower)
                 or len(place.notes) != len(raw['notes']))
        return NativeGroup(path, raw, lower, min(upper, 2**30 - 1 - max(0, offset)),
                           timed, frozenset(place.overlap_spans))
    if format == 'acep':
        first = int(raw.get('clipPos', 0))
        upper = first + int(raw.get('clipDur', raw.get('dur')))
        params = raw.get('parameters', {})
        # The named offset/voice lanes can't override the base MIDI note.
        relative = {'pitchDelta', 'energy', 'breathiness', 'tension', 'falsetto', 'gender', 'vuv',
                    'realEnergy', 'realBreathiness', 'realTension', 'realFalsetto'}
        unknown = any(v for key, v in params.items() if key not in relative)
        return NativeGroup(path, raw, max(lower, first), min(10**8 - 1, upper), any(params.values()),
                           frozenset(place.protected), unknown_pitch=unknown)
    pitch = raw.get('pitch') or {'ticks': [], 'values': [], 'isAbsolute': True}
    ticks, values = pitch['ticks'], pitch['values']
    starts, ends = [], []
    for i, value in enumerate(values):
        if value is not None and value != 0:
            start, end = ticks[i], ticks[i+1] if i+1 < len(ticks) else 10**8
            if start < end:
                starts.append(start)
                ends.append(end)
    return NativeGroup(path, raw, lower, 10**8 - 1, False, frozenset(),
                       tuple(starts), tuple(ends), pitch['isAbsolute'])


def _vpr_phone_positions(note, phones):
    points = note.get('phonemePositions')
    if points is None or points == []:
        return ()
    if (not phones or not isinstance(points, list) or len(points) != len(phones.split()) + 1
            or any(not isinstance(p, dict) or set(p) != {'pos'} or type(p['pos']) is not int
                   or not -2**30 < p['pos'] < 2**30 for p in points)):
        return None
    positions = tuple(p['pos'] for p in points)
    return positions if positions == tuple(sorted(positions)) else None


def _adapted_target(format, group, index, old):
    note = group.raw['notes'][index]
    start, end = old['onset'], old['onset'] + old['duration']
    timed = group.timed or (start, end) in group.protected
    pitched = group.unknown_pitch
    if index + 1 < len(group.raw['notes']):
        next_note = group.raw['notes'][index + 1]
        next_lyric = next_note.get('lyric')
        if isinstance(next_lyric, str) and (lyric_continuation(next_lyric) is not None or next_lyric == '/'):
            timed = True
    lyric_reason, minimum_duration, lang, slots = '', 1, None, 0
    if format in VOCALOID_FORMATS:
        from vpr_tuning import _authored_vibrato, score_language
        phones = _trim_phones(note.get('phoneme'))
        positions = _vpr_phone_positions(note, phones)
        lock = note.get('isProtected')
        locked = lock is not None and lock is not False
        lang = score_language(group.raw, note)
        if locked or positions is None or note.get('phoneme') and not phones:
            lyric_reason = PHONEMES
        elif lang is None:
            lyric_reason = VPR_PHONEME
        elif lang == 1 and old['lyric'].endswith('-'):
            # Yamaha can store an already split English syllable here.
            lyric_reason = VOCALOID_LINKED_LYRIC
        if positions:
            slots = len(positions) - 1
            minimum_duration = max(1, min(old['duration'], positions[-1] + 1))
        skill = note.get('singingSkill')
        if isinstance(skill, dict) and type(skill.get('duration')) is int:
            minimum_duration = max(minimum_duration, min(old['duration'], skill['duration']))
        pitched = bool(note.get('directPitches'))
        timed = timed or pitched or locked or positions is None or _authored_vibrato(note)
        if format == 'vsqx' and start < group.minimum:
            timed = True
    elif format == 'acep':
        custom_timing = (note.get('headConsonants') or note.get('tailConsonants')
                         or note.get('consonantLen') or note.get('brLen') or note.get('phonemes'))
        if (custom_timing or note.get('pronunciation') or note.get('syllable')
                or note.get('freezedDefaultSyllable')):
            lyric_reason = PHONEMES
        timed = timed or custom_timing
    elif note.get('phoneme'):
        lyric_reason = PHONEMES
    return lyric_reason, timed, pitched, minimum_duration, lang, slots


def _english_linked_notes(part, index):
    """Find the original contiguous continuation notes for one word."""
    from vpr_tuning import score_language

    head = part['notes'][index]
    end = head['pos'] + head['duration']
    linked = []
    for next_index in range(index + 1, len(part['notes'])):
        note = part['notes'][next_index]
        text = note.get('lyric')
        if (not isinstance(text, str) or (not lyric_continuation(text) and text != '/')
                or type(note.get('pos')) is not int or type(note.get('duration')) is not int
                or not 0 <= note['pos'] - end <= 1 or score_language(part, note) != 1):
            break
        linked.append(note)
        end = note['pos'] + note['duration']
    return tuple(linked)


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
    pronunciation: str
    minimum_duration: int = 1
    language: int | None = None
    phoneme_slots: int = 0
    linked_notes: tuple = ()

    def reason(self, field):
        if field not in self.fields:
            return '所选音符没有此原生参数，或该参数受保护。'
        return {'lyric': self.lyric_reason, 'pitch': self.pitch_reason,
                'onset': self.timing_reason, 'duration': self.timing_reason}.get(field, '')

    def capabilities(self):
        return {'lyricReason': self.lyric_reason, 'timingReason': self.timing_reason,
                'pitchReason': self.pitch_reason}


class ScoreEdits:
    def __init__(self, data, format, geometry, context):
        self.format = format
        self.context = context
        project, layout = geometry
        self.vsqx_sequence = layout.sequence if format == 'vsqx' else None
        native_data = self.vsqx_sequence if self.vsqx_sequence is not None else data
        self.clock = layout.clock if format in FORMATS else None
        self.targets = {}
        self.editable_tracks = set()
        self.vpr_words = {}
        self.rows = {row['note_id']: row for row in context['notes']}
        if not self.clock:
            return
        placements = {(track.index, place.key): place for track in layout.tracks if track.writable
                      for place in track.style_placements}
        part_limits = {}
        native_groups = {}
        self.group_rows = {}
        self.track_notes = {}
        for row in context['notes']:
            track = row['track_index']
            self.track_notes.setdefault(track, []).append(row)
            place = placements.get((track, row.get('group_placement', 'main')))
            if place is None or row.get('read_only_reason'):
                continue
            local_index = row['note_index']
            minimum = 0
            absolute_pitch = False
            adapted = format in ADAPTED_FIELDS
            if adapted:
                group_key = track, place.key
                self.group_rows.setdefault(group_key, []).append(row)
                if group_key not in native_groups:
                    native_groups[group_key] = _adapted_group(native_data, format, place)
                group = native_groups[group_key]
                path = group.path
                note = group.raw['notes'][local_index]
                fields = ADAPTED_FIELDS[format]
                step = getattr(place, 'step', SV_QUARTER // 480)
                minimum, maximum = group.minimum, group.maximum
            elif format == 'ustx':
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
                absolute_pitch = (_nonempty(group.get('pitchControls')) or
                                  bool(note.get('pitchControls') or note.get('retakes')))
                pitched = absolute_pitch or _nonempty(params.get('pitchDelta'))
                timed = timed or pitched
            old = {key: note.get(raw, '' if key == 'lyric' else None) for key, raw in fields.items()}
            if format == 'ufdata':
                old['duration'] -= old['onset']
            if (type(old['onset']) is not int or old['onset'] < 0
                    or type(old['duration']) is not int or old['duration'] <= 0
                    or type(old['pitch']) is not int or not 0 <= old['pitch'] <= 127
                    or not isinstance(old['lyric'], str)):
                continue
            floor, language, slots, linked = 1, None, 0, ()
            if adapted:
                lyric_reason, timed, pitched, floor, language, slots = _adapted_target(format, group, local_index, old)
                if format == 'ufdata':
                    minimum, maximum, curve_overlaps = group.pitch_window(old['onset'], old['onset']+old['duration'])
                    timed = timed or curve_overlaps
                    pitched = group.pitch_absolute and curve_overlaps
                if _special_lyric(old['lyric']) or not row['singing']:
                    lyric_reason = PHONEMES
                if not row['singing'] or _special_lyric(old['lyric']):
                    timed = pitched = True
                if language == 1 and not lyric_reason:
                    linked = _english_linked_notes(group.raw, local_index)
                if format in VOCALOID_FORMATS and not lyric_reason and not linked and (phones := _trim_phones(note.get('phoneme'))):
                    word = self._word_key(track, language, old['lyric'])
                    self.vpr_words[word] = phones if self.vpr_words.get(word, phones) == phones else None
            else:
                lyric_reason = PHONEMES if manual_phones or _special_lyric(old['lyric'], extended=False) else ''
            fields = {key: (value,) for key, value in fields.items()}
            if row['singing'] and not _special_lyric(old['lyric']):
                consonant_protected = format == 'vpr' and (
                    data['tracks'][track].get('type') != 0 or group.raw.get('aiVoice') or note.get('phonemePositions'))
                detail_fields, detail_values = read_fields(
                    format, note, pitch_protected=absolute_pitch, consonant_protected=consonant_protected)
                fields.update(detail_fields)
                old.update(detail_values)
            phones = note.get('phonemes' if format == 'svp' else 'phoneme')
            if format == 'ustx' and (hint := re.search(r'\[([^\]]+)\]', old['lyric'])):
                phones = hint[1]
            target = Target(
                track, (*path, 'notes', local_index), place.offset, step, minimum, maximum, old, fields,
                lyric_reason, TIMING if timed else '', PITCH if pitched else '',
                phones.strip()[:300] if isinstance(phones, str) else '', floor, language, slots, linked)
            self.targets[row['note_id']] = target
            if not (lyric_reason and timed and pitched) or DETAILS.keys() & fields.keys():
                self.editable_tracks.add(track)
        for rows in self.group_rows.values():
            rows.sort(key=lambda row: row['note_index'])

    @staticmethod
    def _word_key(track, language, lyric):
        text = normalize_english_punctuation(lyric) if language == 1 else lyric
        return track, language, text.strip().casefold()

    def _pronunciations(self, target, lyric):
        from vpr_tuning import score_phoneme

        phones = []
        if target.linked_notes:
            from vocaloid_english import pronounce_notes
            tails = [(row['pos'], row['duration'], '-' if row['lyric'] == '/' else row['lyric'])
                     for row in target.linked_notes]
            head = target.old['onset'], target.old['duration']
            before, issues = pronounce_notes([(*head, target.old['lyric']), *tails])
            phones, new_issues = pronounce_notes([(*head, lyric), *tails])
            if issues or new_issues:
                raise ValueError(VOCALOID_LINKED_LYRIC)
            for old, expected, new in zip(target.linked_notes, before[1:], phones[1:]):
                raw = old.get('phoneme')
                if new != raw and (raw != expected or old.get('isProtected') or old.get('phonemePositions')):
                    raise ValueError(VOCALOID_LINKED_LYRIC)
            if before[0] != target.pronunciation and phones[0] != target.pronunciation:
                raise ValueError(VOCALOID_LINKED_LYRIC)
        else:
            word = self._word_key(target.track, target.language, lyric)
            result = self.vpr_words[word] if word in self.vpr_words else score_phoneme(target.language, lyric)
            if not result:
                raise ValueError(VPR_PHONEME)
            phones = [result]
        if target.phoneme_slots and len(phones[0].split()) != target.phoneme_slots:
            raise ValueError(VPR_PHONE_COUNT)
        return phones

    def capabilities(self, identity):
        target = self.targets.get(identity)
        if target is None:
            reason = (self.rows.get(identity, {}).get('read_only_reason') or '此音符的原生配置尚不支持编辑。'
                      if self.format in FORMATS else UNSUPPORTED)
            return {'lyricReason': reason, 'timingReason': reason, 'pitchReason': reason}
        return target.capabilities()

    def validate_lyric(self, identity, value):
        """Check a single replacement using the same policy as candidate generation."""
        target = self.targets.get(identity)
        if target is None:
            raise ValueError('此音符不能修改。')
        text = _lyric(value)
        if text != target.old['lyric']:
            if target.lyric_reason:
                raise ValueError(target.lyric_reason)
            if self.format in ADAPTED_FIELDS and _special_lyric(text):
                raise ValueError(PHONEMES)
            if self.format in VOCALOID_FORMATS:
                self._pronunciations(target, text)
        return text

    def note_details(self, identity, patch):
        target = self.targets.get(identity)
        if target is None:
            return [], ''
        pronunciation = (self._pronunciations(target, patch['lyric'])[0]
                         if self.format in VOCALOID_FORMATS and 'lyric' in patch else target.pronunciation)
        return field_views(target.fields, {**target.old, **patch}), pronunciation

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
                if field in local and edit[field] == local[field]:
                    continue
                if field == 'lyric':
                    self.validate_lyric(identity, edit[field])
                elif problem := target.reason(field):
                    raise ValueError(problem)
                if field in DETAILS and integer_field(field, target.fields[field]) and type(edit[field]) is not int:
                    raise ValueError('此音符参数需要整数。')
                local[field] = edit[field]
            if edit.keys() & {'vibrato_in', 'vibrato_out'} and local.get('vibrato_in', 0) + local.get('vibrato_out', 0) > 100:
                raise ValueError('颤音渐入与渐出的比例之和不能超过 100%。')
            onset, duration = local['onset'], local['duration']
            if onset != target.old['onset'] or duration != target.old['duration']:
                if onset < target.minimum or onset + duration > target.maximum:
                    raise ValueError('音符超出原片段或原生曲线之间的可编辑范围。')
                if duration < target.minimum_duration:
                    raise ValueError('音符过短，会截断原有音素位置。')
                changed_timing.add(identity)
            start = target.offset + onset * target.step
            end = start + duration * target.step
            row = self.rows[identity]
            lyric = ({'lyric': local['lyric'], 'lyric_raw': local['lyric'], 'lyric_known': True}
                     if local['lyric'] != target.old['lyric'] else {})
            changed[identity] = {**row, **lyric,
                                 'pitch': local['pitch'], 'onset_tick': onset * target.step,
                                 'duration_tick': duration * target.step, 'project_onset_tick': start,
                                 **({'project_end_tick': end} if 'project_end_tick' in row else {}),
                                 'start_seconds': self.clock.to_seconds(start), 'end_seconds': self.clock.to_seconds(end)}
        for group in {(self.rows[i]['track_index'], self.rows[i].get('group_placement', 'main'))
                      for i in changed_timing} & self.group_rows.keys():
            # Preserve native note order and stable IDs. Crossing an adjacent
            # note would otherwise silently leave the native note list unsorted.
            rows = self.group_rows[group]
            for a, b in zip(rows, rows[1:]):
                ai, bi = a['note_id'], b['note_id']
                if ((ai in changed_timing or bi in changed_timing)
                        and a['project_onset_tick'] <= b['project_onset_tick']
                        and changed.get(ai, a)['project_onset_tick'] > changed.get(bi, b)['project_onset_tick']):
                    raise ValueError('音符不能移到同片段的相邻音符之外。')
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
        return self.update_many(rows, {identity: changes})

    def update_many(self, rows, changes_by_note):
        other = {row['note_id']: dict(row) for row in rows}
        for identity, changes in changes_by_note.items():
            target = self.targets.get(identity)
            if target is None:
                raise ValueError('此音符不能修改。')
            edit = {**other.pop(identity, {'note_id': identity}), **changes}
            edit = {key: value for key, value in edit.items()
                    if key == 'note_id' or value != target.old.get(key)}
            if len(edit) > 1:
                other[identity] = edit
        return normalize_edits(list(other.values()))

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
            return {'duration': max(target.minimum_duration, min(target.maximum, position) - start)}
        raise ValueError('未知音符操作。')

    def geometry(self, identity, patch, changes):
        target = self.targets[identity]
        note = {**target.old, **patch, **changes}
        first = target.offset + note['onset'] * target.step
        return {'start': self.clock.to_seconds(first),
                'end': self.clock.to_seconds(first + note['duration'] * target.step), 'pitch': note['pitch']}

    def gesture_selection(self, patches, selected, primary, action, seconds, pitch, horizontal):
        patch = patches.get(primary, {})
        changes = self.gesture(primary, patch, action, seconds, pitch, horizontal)
        if len(selected) == 1:
            return {primary: changes}
        anchor = self.targets[primary]
        original = {**anchor.old, **patch}
        delta = {name: (value - original[name]) * (anchor.step if name != 'pitch' else 1)
                 for name, value in changes.items() if value != original[name]}
        result = {}
        for identity in selected:
            target = self.targets.get(identity)
            if target is None:
                raise ValueError('选区内有受保护的音符。')
            current = {**target.old, **patches.get(identity, {})}
            proposed = {name: current[name] + round(value / (target.step if name != 'pitch' else 1))
                        for name, value in delta.items()}
            for field in proposed:
                if reason := target.reason(field):
                    raise ValueError(reason)
            geometry = {**current, **proposed}
            if (not 0 <= geometry['pitch'] <= 127 or geometry['onset'] < target.minimum
                    or geometry['onset'] + geometry['duration'] > target.maximum
                    or geometry['duration'] < target.minimum_duration):
                raise ValueError('所选音符无法一起移到此位置或拉伸到此长度。')
            result[identity] = proposed
        return result

    def materialize(self, data, rows):
        edits = normalize_edits(rows)
        self.projected(edits)
        output = deepcopy(self.vsqx_sequence if self.vsqx_sequence is not None else data)
        details = []
        for edit in edits:
            target = self.targets[edit['note_id']]
            native = _native(output, target.path)
            changed = {}
            derived = {}
            for field, value in sorted(edit.items()):
                if field == 'note_id' or value == target.old[field]:
                    continue
                if not (self.format == 'ufdata' and field == 'duration'):
                    path = target.fields[field]
                    _native(native, path[:-1])[path[-1]] = value
                changed[field] = {'before': target.old[field], 'after': value}
            if changed:
                if self.format == 'ufdata' and changed.keys() & {'onset', 'duration'}:
                    end = edit.get('onset', target.old['onset']) + edit.get('duration', target.old['duration'])
                    derived['tickOff'] = {'before': native['tickOff'], 'after': end}
                    native['tickOff'] = end
                elif self.format in VOCALOID_FORMATS and 'lyric' in changed:
                    phones, *tails = self._pronunciations(target, edit['lyric'])
                    if phones != native.get('phoneme'):
                        derived['phoneme'] = {'before': native.get('phoneme'), 'after': phones}
                        native['phoneme'] = phones
                    for offset, value in enumerate(tails, start=1):
                        index = target.path[-1] + offset
                        peer = _native(output, (*target.path[:-1], index))
                        previous = peer.get('phoneme')
                        if previous != value:
                            derived.setdefault('continuation_phonemes', []).append({
                                'note_index': index, 'before': previous, 'after': value})
                            peer['phoneme'] = value
                details.append({'note_id': edit['note_id'], 'track_index': target.track, 'fields': changed,
                                **({'native_fields': derived} if derived else {})})
        if self.vsqx_sequence is not None:
            from vsqx_tuning import merge_sequence, verify_sequence
            edited = output
            output = merge_sequence(data, self.vsqx_sequence, output)
            verify_sequence(output, edited)
        return output, details
