"""Revision-bound note navigation and per-track style drafts for desktop and tools."""

from bisect import bisect_right
from copy import copy, deepcopy
import hashlib
import json
import math
from pathlib import Path
import unicodedata
from uuid import uuid4

import agent_style
from style_drafts import DraftStore
from style_presets import region_settings, time_range_settings
from svp_tuning import GROUP_STYLE_ONLY, GROUPS_PARTLY_PROTECTED
from tuning_engine import DEFAULTS, digest
from tuning_execution import EDITOR_OPTION_KEYS, editor_options
from tuning_pitch_layer import PitchLayer
from tuning_preset_documents import plan_application, validate_plan
from tuning_score_edits import PHONEMES, ScoreEdits, _special_lyric
from tuning_workflow import STYLE_ADAPTERS, inspect_project, read_source, score_layout, style_adapter


_REGION_FIELDS = frozenset(('id', 'track_id', 'start_note_id', 'end_note_id', 'recipe'))
_DRAFT_FIELDS = frozenset(('schema_version', 'input_sha256', 'regions', 'options', 'preset', 'confirmed', '_sync'))
LYRIC_BATCH_LIMIT = 128
_SMALL_KANA = frozenset('ぁぃぅぇぉゃゅょゎァィゥェォャュョヮー')


def _is_han(char):
    return (char == '〇' or '\u3400' <= char <= '\u9fff' or '\uf900' <= char <= '\ufaff'
            or '\U00020000' <= char <= '\U000323af')


def split_batch_lyrics(text):
    """Split pasted text without detaching small kana or the vowel-length mark."""
    if len(text) > 8192:
        raise ValueError('歌词太长，请分句粘贴。')
    words, run, kana = [], [], False
    for char in unicodedata.normalize('NFC', text):
        is_kana = '\u3041' <= char <= '\u30fa' or char == 'ー'
        is_ideograph = _is_han(char)
        separator = char.isspace() or unicodedata.category(char).startswith('P')
        if run and (separator or is_ideograph or (is_kana and not (kana and char in _SMALL_KANA)) or
                    (kana and not is_kana)):
            words.append(''.join(run))
            run.clear()
            kana = False
        if is_ideograph:
            words.append(char)
        elif not separator:
            run.append(char)
            kana = is_kana
    if run:
        words.append(''.join(run))
    return words


def batch_lyric_row(project, note, proposed):
    """Prepare a UI row. A slur, breath or special native marker retains its text."""
    old = note['lyric']
    row = {**note, 'before': old, 'after': proposed, 'reason': '', 'readings': [],
           'preserved': _special_lyric(old), 'changed': False}
    if row['preserved']:
        marker = project.score_editor.rows[note['id']].get('lyric_raw') or old
        return {**row, 'before': marker, 'after': marker, 'reason': '延音、换气或原生标记已保留。'}
    text = proposed.strip()
    if note['lyricReason']:
        return {**row, 'reason': note['lyricReason'], 'blocked': text != old}
    if not text:
        return {**row, 'reason': '请填写歌词或保留原词。', 'blocked': True}
    target = project.score_editor.targets.get(note['id'])
    if len(text) == 1 and _is_han(text) and (not target or target.language in (None, 4)):
        from vpr_tuning import chinese_score_readings
        try:
            row['readings'] = list(chinese_score_readings(text))
        except (ImportError, OSError, RuntimeError, ValueError):
            # Optional reading hints must not change the native edit policy.
            pass
    if text == old:
        return row
    try:
        project.score_editor.validate_lyric(note['id'], text)
    except ValueError as error:
        return {**row, 'reason': str(error), 'blocked': True}
    return {**row, 'changed': True}


def _notes_and_overlaps(notes):
    farthest = None
    for note in notes:
        yield note, farthest if farthest is not None and note['start'] < farthest['end'] - 1e-7 else None
        if farthest is None or note['end'] > farthest['end']:
            farthest = note


def _identity(value, prefix):
    return (isinstance(value, str) and value.startswith(prefix) and len(value) == len(prefix) + 24
            and all(c in '0123456789abcdef' for c in value[len(prefix):]))


def _lyric_key(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if not c.isspace())


def _fragment_identity(parent_id, start, end, used):
    """Derive a stable editor ID for the surviving part of one region."""
    nonce = 0
    while True:
        material = f'vwa.style-region-fragment/v1|{parent_id}|{start:.17g}|{end:.17g}|{nonce}'
        identity = hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]
        if identity not in used:
            used.add(identity)
            return identity
        nonce += 1


class ProjectNotes:
    """Index the same project-coordinate IDs and tempo conversion as the Agent."""

    def __init__(self, project, context, *, score_editor=None, note_edits=(), pitch_layer=None):
        self.project = deepcopy(project)
        self.score_editor = score_editor
        self.note_edits = tuple(note_edits)
        self.note_patches = {row['note_id']: row for row in note_edits}
        self.pitch_layer = pitch_layer
        self.pitch_strokes = ()
        self.sha256 = project['sha256']
        if context['source_sha256'] != self.sha256:
            raise ValueError('工程在读取轨道期间已改变，请重新载入。')
        self.tracks = {
            row['track_id']: {'id': row['track_id'], 'index': row['track_index'],
                              'name': row['name'] or f"演唱轨 {row['track_index'] + 1}",
                              'noteCount': row['note_count_parsed'], 'editable': row['eligible_for_style'],
                              'styleOnly': row.get('style_only', False),
                              'reason': row.get('read_only_reason', '')}
            for row in context['tracks'] if row['vocal']
        }
        self.editable = tuple(key for key, row in self.tracks.items() if row['editable'])
        if not self.tracks:
            raise ValueError('工程中没有可显示的演唱轨。')
        by_track = {key: [] for key in self.tracks}
        for row in context['notes']:
            if row['track_id'] in by_track:
                by_track[row['track_id']].append(row)
        self.minimum, self.maximum = 0., 0.
        self.notes, self._windows, self._lyrics, self._components, self._component_ends = {}, {}, {}, {}, {}
        for key, values in by_track.items():
            values.sort(key=lambda row: (row['start_seconds'], row['end_seconds'], row['note_index']))
            notes = tuple({'id': row['note_id'], 'trackId': key, 'trackName': self.tracks[key]['name'],
                           'lyric': (row['lyric'] or '')[:300], 'pitch': row['pitch'],
                           'start': row['start_seconds'], 'end': row['end_seconds'],
                           **(score_editor.capabilities(row['note_id']) if score_editor else {}),
                           **({'drawReason': pitch_layer.reason(row['note_id'], {
                               'start': row['start_seconds'], 'end': row['end_seconds']})} if pitch_layer else {}),
                           **({'readOnlyReason': row['read_only_reason']} if row.get('read_only_reason') else {})}
                          for row in values)
            if self.tracks[key]['editable'] and any(n.get('readOnlyReason') for n in notes):
                self.tracks[key]['reason'] = GROUPS_PARTLY_PROTECTED
            self.notes.update((row['id'], row) for row in notes)
            ends, farthest = [], -math.inf
            for row in notes:
                farthest = max(farthest, row['end'])
                ends.append(farthest)
            self._windows[key] = (notes, ends)
            components = agent_style.note_components([row for row in values if not row.get('read_only_reason')])
            self._components[key] = components
            self._component_ends[key] = [row['end'] for row in components]
            if notes:
                self.minimum = min(self.minimum, notes[0]['start'])
                self.maximum = max(self.maximum, farthest)
            text, owners = [], []
            for i, row in enumerate(notes):
                token = _lyric_key(row['lyric'])
                text.append(token)
                owners.extend((i,) * len(token))
            self._lyrics[key] = (''.join(text), owners)

    @classmethod
    def load(cls, path, *, expected_sha256=None):
        path = Path(path).expanduser().resolve()
        snapshot = read_source(path)
        if expected_sha256 and snapshot.sha256 != expected_sha256:
            raise ValueError('已核验的工程在交接时发生变化。')
        geometry = score_layout(snapshot.data, snapshot.format)
        project = inspect_project(path, snapshot=snapshot, geometry=geometry)
        context = agent_style.context_for_snapshot(snapshot, geometry)
        editor = ScoreEdits(snapshot.data, snapshot.format, geometry, context)
        return cls(project, context, score_editor=editor, pitch_layer=PitchLayer(snapshot.data, editor, geometry[1]))

    def with_note_edits(self, rows):
        return self.with_edits(rows, self.pitch_strokes)

    def with_edits(self, rows, strokes):
        rows, strokes = tuple(rows), tuple(strokes)
        if (rows, strokes) == (self.note_edits, self.pitch_strokes):
            return self
        if self.score_editor is None:
            raise ValueError('当前工程没有可编辑的原生音符。')
        if rows == self.note_edits:
            result = copy(self)
        else:
            result = ProjectNotes(self.project, self.score_editor.projected(list(rows)), score_editor=self.score_editor,
                                  note_edits=rows, pitch_layer=self.pitch_layer)
        if strokes:
            result.pitch_layer.resolve(strokes, result.note_patches)
        result.pitch_strokes = strokes
        return result

    @property
    def has_score_changes(self):
        return bool(self.note_edits or self.pitch_strokes)

    def edit_track_indices(self):
        return ({self.score_editor.targets[row['note_id']].track for row in self.note_edits}
                | {self.pitch_layer.notes[row['note_id']].track for row in self.pitch_strokes})

    def next_note(self, identity):
        row = self.notes.get(identity)
        if row is None:
            return None
        notes, _ = self._windows[row['trackId']]
        index = next(i for i, note in enumerate(notes) if note['id'] == identity)
        return next((note for note in notes[index + 1:] if not note.get('lyricReason')), None)

    def lyric_batch(self, track, start, end, text):
        notes = self.visible((track,), start, end, limit=LYRIC_BATCH_LIMIT + 1) if end > start else []
        if not notes:
            raise ValueError('请先框选当前轨道的一句歌词。')
        if len(notes) > LYRIC_BATCH_LIMIT:
            raise ValueError('选区音符太多，请分句贴词。')
        if any(note['start'] < start - 1e-7 or note['end'] > end + 1e-7 for note in notes):
            raise ValueError('选区切到了音符，请选中完整音符。')
        words = split_batch_lyrics(text)
        rows, index = [], 0
        overlapping = {row['id'] for note, other in _notes_and_overlaps(notes)
                       if other is not None for row in (note, other)}
        for note in notes:
            special = _special_lyric(note['lyric'])
            proposed = note['lyric'] if special else words[index] if index < len(words) else ''
            rows.append({**batch_lyric_row(self, note, proposed), 'overlap': note['id'] in overlapping})
            if not special:
                index += 1
        return rows, words[index:]

    def editing_issues(self, track, limit=120):
        """Locate actual collisions and authored phoneme guards without modifying notes."""
        rows = self._windows.get(track, ((), ()))[0]
        issues = []
        for note, other in _notes_and_overlaps(rows):
            if other is not None:
                issues.append({**note, 'reason': '同轨音符重叠', 'otherLyric': other['lyric']})
            if note.get('lyricReason') == PHONEMES and not _special_lyric(note['lyric']):
                issues.append({**note, 'reason': PHONEMES})
            if len(issues) > limit:
                break
        return issues[:limit], len(issues) > limit

    def visible(self, tracks, start, end, *, limit=4000):
        result = []
        for key in tracks:
            if key not in self._windows:
                continue
            notes, prefix_ends = self._windows[key]
            for index in range(bisect_right(prefix_ends, start), len(notes)):
                row = notes[index]
                if row['start'] >= end:
                    break
                if row['end'] > start:
                    result.append(row)
                    if limit is not None and len(result) >= limit:
                        return result
        return result

    def uses_group_styles(self, indices):
        return any(row['styleOnly'] and row['index'] in indices for row in self.tracks.values())

    def requires_style_mode(self, indices):
        return self.project.get('format') in STYLE_ADAPTERS or self.uses_group_styles(indices)

    @property
    def style_only_reason(self):
        if adapter := style_adapter(self.project.get('format')):
            return adapter.STYLE_ONLY
        return GROUP_STYLE_ONLY

    def matches(self, query, *, limit=160):
        needle = _lyric_key(query[:256])
        if not needle:
            return []
        result = []
        for key, (text, owners) in self._lyrics.items():
            notes, _ = self._windows[key]
            last, used = -1, set()
            for _ in range(limit):
                last = text.find(needle, last + 1)
                if last < 0:
                    break
                first_note, last_note = notes[owners[last]], notes[owners[last + len(needle) - 1]]
                span = (first_note['id'], last_note['id'])
                if span not in used:
                    used.add(span)
                    lyric = ''.join(row['lyric'] for row in notes[owners[last]:owners[last + len(needle) - 1] + 1])
                    result.append({**first_note, 'endId': last_note['id'], 'lyric': lyric,
                                   'end': max(first_note['end'], last_note['end'])})
        return sorted(result, key=lambda row: (row['start'], row['trackId']))[:limit]

    def region(self, track, start, end, brush, *, identity=None):
        if track not in self.editable:
            raise ValueError('请选择可编辑的演唱轨。')
        components = self._components[track]
        touched = []
        for i in range(bisect_right(self._component_ends[track], start), len(components)):
            row = components[i]
            if row['start'] >= end:
                break
            touched.append(row)
        if not touched:
            return None
        first, last = touched[0], touched[-1]
        start, end = first['start'], last['end']
        time_range_settings((start, end))
        end_id = max(last['note_ids'], key=lambda key: (self.notes[key]['end'], self.notes[key]['start'], key))
        recipe = region_settings([{**brush, 'start': start, 'end': end,
                                   'track_index': self.tracks[track]['index']}])[0]
        return {'id': identity or uuid4().hex, 'track_id': track,
                'start_note_id': first['note_ids'][0], 'end_note_id': end_id, 'recipe': recipe}

    def _region_fragment(self, row, start, end, used):
        """Resolve a surviving interval back to complete lyric-note anchors."""
        brush = {key: deepcopy(value) for key, value in row['recipe'].items()
                 if key not in ('start', 'end', 'track_index')}
        identity = _fragment_identity(row['id'], start, end, used)
        fragment = self.region(row['track_id'], start, end, brush, identity=identity)
        if (fragment is None
                or fragment['recipe']['start'] < start - 1e-9
                or fragment['recipe']['end'] > end + 1e-9):
            raise ValueError('重叠覆盖后留下的旧片段无法对齐到完整歌词音符。')
        return fragment

    def overlay_regions(self, values, additions):
        """Apply later, already snapped regions over earlier same-track regions.

        The returned rows are the exact non-overlapping rows used by drafts and
        exports. A later region wins as one complete recipe after note
        snapping; surviving left and right intervals keep the earlier recipe.
        """
        if not isinstance(values, (list, tuple)) or not isinstance(additions, (list, tuple)):
            raise ValueError('风格时间片段必须为列表。')
        current = deepcopy(list(values))
        incoming = deepcopy(list(additions))
        self.verify_regions(current)
        for addition in incoming:
            self.verify_regions([addition])
        current_ids = {row['id'] for row in current}
        incoming_ids = [row['id'] for row in incoming]
        if len(incoming_ids) != len(set(incoming_ids)) or current_ids.intersection(incoming_ids):
            raise ValueError('风格时间片段 ID 必须唯一。')
        used = current_ids | set(incoming_ids)

        for addition in incoming:
            recipe = addition['recipe']
            updated = []
            for row in current:
                old = row['recipe']
                if (row['track_id'] != addition['track_id']
                        or old['end'] <= recipe['start'] or recipe['end'] <= old['start']):
                    updated.append(row)
                    continue
                if old['start'] < recipe['start']:
                    updated.append(self._region_fragment(row, old['start'], recipe['start'], used))
                if recipe['end'] < old['end']:
                    updated.append(self._region_fragment(row, recipe['end'], old['end'], used))
            updated.append(addition)
            if len(updated) > 128:
                raise ValueError('风格时间片段过多。')
            current = updated

        self.verify_regions(current)
        return sorted(current, key=lambda row: (row['recipe']['track_index'], row['recipe']['start'],
                                                row['recipe']['end'], row['id']))

    def verify_regions(self, values):
        if not isinstance(values, (list, tuple)):
            raise ValueError('风格时间片段必须为列表。')
        if len(values) > 128:
            raise ValueError('风格时间片段过多。')
        identities = [row.get('id') for row in values if isinstance(row, dict)]
        if len(identities) != len(values) or len(identities) != len(set(identities)):
            raise ValueError('风格时间片段 ID 必须唯一。')
        for row in values:
            recipe = row['recipe']
            track = row['track_id']
            brush = {k: v for k, v in recipe.items() if k not in ('start', 'end', 'track_index')}
            resolved = self.region(track, recipe['start'], recipe['end'], brush, identity=row['id'])
            if row != resolved:
                raise ValueError('片段边界或轨道身份与当前工程不符，请重新选择歌词音符。')
        return region_settings([row['recipe'] for row in values])

    def rebase_regions(self, previous, rows):
        """Keep every style attached to exactly the same set of native notes."""
        rebased = []
        for row in rows:
            track, recipe = row['track_id'], row['recipe']
            members = {note['id'] for note in previous.visible([track], recipe['start'], recipe['end'], limit=None)
                       if not note.get('readOnlyReason')}
            start = min(self.notes[key]['start'] for key in members)
            end = max(self.notes[key]['end'] for key in members)
            brush = {key: value for key, value in recipe.items() if key not in ('start', 'end', 'track_index')}
            moved = self.region(track, start, end, brush, identity=row['id'])
            now = {note['id'] for note in self.visible([track], start, end, limit=None)
                   if not note.get('readOnlyReason')}
            if now != members or moved is None:
                raise ValueError('这个位置会改变风格片段包含的音符。请先调整相关风格片段。')
            rebased.append(moved)
        self.verify_regions(rebased)
        return rebased

    def expand_legacy(self, data, fallback):
        indices = data.get('track_indices', [])
        if indices:
            by_index = {row['index']: key for key, row in self.tracks.items()}
            if any(by_index.get(index) not in self.editable for index in indices):
                raise ValueError('旧草稿包含无法编辑的演唱轨，请在原调音页检查旧草稿。')
            targets = [by_index[index] for index in indices]
        else:
            targets = list(fallback)
        if not targets:
            raise ValueError('旧草稿没有固定轨道，请先勾选迁移目标。')
        result = []
        for recipe in data['style_regions']:
            brush = {k: v for k, v in recipe.items() if k not in ('start', 'end')}
            found = [self.region(track, recipe['start'], recipe['end'], brush) for track in targets]
            if not any(found):
                raise ValueError('旧草稿有片段没有命中任何演唱音符，无法转换。')
            result.extend(row for row in found if row is not None)
        self.verify_regions(result)
        return result


def draft_options(value):
    """Validate persistent editor options even while local inputs are pending."""
    if not isinstance(value, dict) or set(value) - set(DEFAULTS) - EDITOR_OPTION_KEYS:
        raise ValueError('逐轨草稿含有未知调音选项。')
    temporary = {}
    if value.get('expression_mode') == 'calibrated' and not value.get('reference_calibration'):
        temporary['reference_calibration'] = 'pending'
    if value.get('expression_mode') == 'retained' and not (value.get('retained_project') or value.get('retained_sources')):
        temporary['retained_project'] = 'pending'
    options = editor_options({**value, **temporary})
    options.update(dict.fromkeys(temporary))
    return options


def normalize_timeline_draft(input_sha256, data):
    if (not isinstance(data, dict) or set(data) - _DRAFT_FIELDS or data.get('schema_version') != 2
            or data.get('input_sha256') != input_sha256):
        raise ValueError('逐轨草稿与当前工程身份不一致。')
    if type(data.get('confirmed', False)) is not bool:
        raise ValueError('候选基线确认无效。')
    rows = data.get('regions', [])
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError('逐轨草稿最多允许 128 个片段。')
    ids = set()
    cleaned = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != _REGION_FIELDS
                or not isinstance(row['id'], str) or len(row['id']) != 32
                or not all(c in '0123456789abcdef' for c in row['id']) or row['id'] in ids
                or not _identity(row['track_id'], 'track_')
                or not _identity(row['start_note_id'], 'note_') or not _identity(row['end_note_id'], 'note_')
                or not isinstance(row['recipe'], dict) or 'track_index' not in row['recipe']):
            raise ValueError('逐轨草稿包含无效的片段或音符 ID。')
        ids.add(row['id'])
        cleaned.append({**row, 'recipe': region_settings([row['recipe']])[0]})
    region_settings([row['recipe'] for row in cleaned])
    options = draft_options(data.get('options', {}))
    if options['style_regions']:
        raise ValueError('逐轨片段只能通过音符身份保存。')
    preset = data.get('preset')
    if preset is not None:
        if (cleaned or EDITOR_OPTION_KEYS.intersection(options) or not isinstance(preset, dict)
                or preset.get('input_sha256') != input_sha256):
            raise ValueError('逐轨草稿的预设绑定与当前工程不符。')
        if preset.get('missing'):
            rebuilt = plan_application(preset['document'], preset['project'], preset['bindings'],
                                       raw_document=preset['document_text'])
            if {**preset, 'changes': []} != rebuilt:
                raise ValueError('待绑定的预设已发生变化，请重新选择。')
            preset = rebuilt
        else:
            preset = validate_plan(preset)
    return {'schema_version': 2, 'input_sha256': input_sha256, 'regions': cleaned,
            'options': options, 'preset': preset, 'confirmed': data.get('confirmed', False)}


class TimelineDraftStore(DraftStore):
    """v2 is stored beside v1; neither editor overwrites the other's format."""

    normalize = staticmethod(normalize_timeline_draft)


def freeze_plan(project, regions, options, preset):
    normalized = editor_options(options)
    if tuple(normalized.get('note_edits', [])) != project.note_edits:
        raise ValueError('草稿里的音符修改与本次执行计划不一致。')
    if tuple(normalized.get('pitch_strokes', [])) != project.pitch_strokes:
        raise ValueError('草稿里的手绘修改与本次执行计划不一致。')
    if normalized['expression_mode'] != 'preserve' and project.requires_style_mode(normalized['track_indices'] or []):
        raise ValueError(project.style_only_reason)
    if project.verify_regions(regions) != normalized['style_regions']:
        raise ValueError('逐轨片段与本次执行计划不一致。')
    if preset is not None:
        preset = validate_plan(preset, project=project.project, options=normalized)
    data = {'schema': 'vwa.track-tuning-plan/v2', 'input_sha256': project.sha256,
            'options': normalized, 'regions': deepcopy(regions), 'preset': preset}
    return digest(data), json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
