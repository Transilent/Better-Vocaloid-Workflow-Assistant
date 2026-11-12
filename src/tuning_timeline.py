"""Revision-bound note navigation and per-track style drafts for desktop and tools."""

from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import unicodedata
from uuid import uuid4

import agent_style
from style_drafts import DraftStore
from style_presets import region_settings, time_range_settings
from svp_tuning import GROUP_STYLE_ONLY
from tuning_engine import DEFAULTS, digest, settings
from tuning_preset_documents import plan_application, validate_plan
from tuning_workflow import STYLE_ADAPTERS, inspect_project, read_source, score_layout, style_adapter


_REGION_FIELDS = frozenset(('id', 'track_id', 'start_note_id', 'end_note_id', 'recipe'))
_DRAFT_FIELDS = frozenset(('schema_version', 'input_sha256', 'regions', 'options', 'preset', 'confirmed', '_sync'))


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

    def __init__(self, project, context):
        self.project = deepcopy(project)
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
                           'start': row['start_seconds'], 'end': row['end_seconds']} for row in values)
            self.notes.update((row['id'], row) for row in notes)
            ends, farthest = [], -math.inf
            for row in notes:
                farthest = max(farthest, row['end'])
                ends.append(farthest)
            self._windows[key] = (notes, ends)
            components = agent_style.note_components(values)
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
        return cls(project, agent_style.context_for_snapshot(snapshot, geometry))

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
                    if len(result) >= limit:
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
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('逐轨草稿含有未知调音选项。')
    temporary = {}
    if value.get('expression_mode') == 'calibrated' and not value.get('reference_calibration'):
        temporary['reference_calibration'] = 'pending'
    if value.get('expression_mode') == 'retained' and not (value.get('retained_project') or value.get('retained_sources')):
        temporary['retained_project'] = 'pending'
    options = settings({**value, **temporary})
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
        if cleaned or not isinstance(preset, dict) or preset.get('input_sha256') != input_sha256:
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
    normalized = settings(options)
    if normalized['expression_mode'] != 'preserve' and project.requires_style_mode(normalized['track_indices'] or []):
        raise ValueError(project.style_only_reason)
    if project.verify_regions(regions) != normalized['style_regions']:
        raise ValueError('逐轨片段与本次执行计划不一致。')
    if preset is not None:
        preset = validate_plan(preset, project=project.project, options=normalized)
    data = {'schema': 'vwa.track-tuning-plan/v2', 'input_sha256': project.sha256,
            'options': normalized, 'regions': deepcopy(regions), 'preset': preset}
    return digest(data), json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
