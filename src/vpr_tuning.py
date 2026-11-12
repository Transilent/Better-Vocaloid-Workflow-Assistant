"""Native VOCALOID 5/6 project editing over part-local controllers."""

from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import dataclass, field
import hashlib
import io
import json
import math
from pathlib import Path
import stat
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from common import native_path
from svp_tuning import Note, Track
from tuning_project_identity import MAX_VPR_PROJECT_BYTES as MAX_FILE_BYTES
from vocaloid_pitch import PBS_SPEC, PIT_SPEC, step_values


SEQUENCE = 'Project/sequence.json'
MAX_SEQUENCE_BYTES = 32 * 1024**2
MAX_MEMBER_BYTES = 2 * 1024**3 - 1
BLICK = 705600000 // 480
DYN, PIT, PBS = 'dynamics', 'pitchBend', 'pitchBendSens'
CONTROLLERS = {DYN: (64, 0, 127), PIT: PIT_SPEC, PBS: PBS_SPEC}
STYLE_ONLY = 'VPR 支持「保留原表现」下的 DYN 力度和 PIT 音高弯曲。'
MAX_EDIT_TICKS = 750_000
MAX_EVENTS = 250_000
_CHUNK = 1024**2


def _authored_vibrato(note):
    if note.get('isAiVibratoEnabled'):
        return True
    value = note.get('vibrato')
    if value is None or value == {}:
        return False
    # Yamaha stores duration, depths and rates, rather than a generic
    # controller event list. Only a known disabled shape permits a new layer.
    return not (isinstance(value, dict)
                and set(value) <= {'type', 'duration', 'depths', 'rates'}
                and type(value.get('duration')) is int and value['duration'] == 0)


@contextmanager
def _archive(source):
    try:
        if isinstance(source, (bytes, bytearray)):
            if len(source) > MAX_FILE_BYTES:
                raise ValueError('VPR 文件超过 512 MiB。')
            with ZipFile(io.BytesIO(source)) as archive:
                yield archive
        else:
            path = Path(native_path(source))
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('VPR 文件超过 512 MiB。')
            with path.open('rb') as stream, ZipFile(stream) as archive:
                yield archive
    except (BadZipFile, NotImplementedError, EOFError) as error:
        raise ValueError('VPR 不是可读取的 VOCALOID 工程归档。') from error


def _members(archive):
    entries = archive.infolist()
    if not 0 < len(entries) <= 4096 or sum(i.file_size for i in entries) > MAX_MEMBER_BYTES:
        raise ValueError('VPR 归档成员过多或展开后超过 2 GiB。')
    seen = set()
    for info in entries:
        name = info.filename
        chunks = name.removesuffix('/').split('/')
        key = name.removesuffix('/').casefold()
        if (info.orig_filename != name or key in seen or '\\' in name or ':' in name
                or any(x in ('', '.', '..') for x in chunks) or stat.S_ISLNK(info.external_attr >> 16)
                or info.flag_bits & 1 or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
                or (info.is_dir() and info.file_size)):
            raise ValueError('VPR 归档有歧义或不支持的成员，未读取。')
        seen.add(key)
    try:
        sequence = archive.getinfo(SEQUENCE)
    except KeyError as error:
        raise ValueError('VPR 归档缺少 Project/sequence.json。') from error
    if not 0 < sequence.file_size <= MAX_SEQUENCE_BYTES:
        raise ValueError('VPR 的 sequence.json 为空或超过 32 MiB。')
    return entries, sequence


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('VPR JSON 键重复。')
        result[key] = value
    return result


def _finite_number(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('VPR JSON 包含非有限数值。')
    return value


def read(source):
    """Read only the score member. No archive member is extracted to disk."""
    with _archive(source) as archive:
        _, sequence = _members(archive)
        raw = archive.read(sequence)
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                          parse_float=_finite_number, parse_constant=_finite_number)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError('VPR 的 sequence.json 无效：' + str(error)) from error
    if not isinstance(data, dict) or not isinstance(data.get('tracks'), list):
        raise ValueError('VPR 缺少有效的轨道列表。')
    return data


def _transfer(src, dst, cancel):
    sha = hashlib.sha256()
    for block in iter(lambda: src.read(_CHUNK), b''):
        cancel()
        sha.update(block)
        if dst is not None:
            dst.write(block)
    return sha.hexdigest()


def write_candidate(source, stream, output, original, *, cancel):
    """Replace only sequence.json and verify all other member contents by SHA."""
    with _archive(source) as base:
        entries, sequence = _members(base)
        audio = sum(i.filename.startswith('Project/Audio/') and not i.is_dir() for i in entries)
        if output == original:
            with Path(native_path(source)).open('rb') as original_file:
                _transfer(original_file, stream, cancel)
            return {'embedded_audio_members': audio, 'preserved_members': len(entries) - 1,
                    'non_sequence_members_unchanged': True, 'copied_archive_bytes': True}
        text = json.dumps(output, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        raw_sequence = base.read(sequence)
        payload = text.encode('utf-8-sig' if raw_sequence.startswith(b'\xef\xbb\xbf') else 'utf-8')
        if len(payload) > MAX_SEQUENCE_BYTES:
            raise ValueError('处理后的 VPR sequence.json 超过 32 MiB。')
        hashes = {}
        with ZipFile(stream, 'w', allowZip64=False) as target:
            target.comment = base.comment
            for entry in entries:
                cancel()
                clone = copy(entry)
                if entry.filename == SEQUENCE:
                    target.writestr(clone, payload)
                else:
                    with base.open(entry) as src, target.open(clone, 'w') as dst:
                        hashes[entry.filename] = _transfer(src, dst, cancel)
                # ZipFile supplies default Unix permissions for zero attributes.
                # The central directory should retain the source's exact value.
                clone.external_attr = entry.external_attr
        stream.flush()
        with ZipFile(stream) as candidate:
            items, _ = _members(candidate)
            if candidate.comment != base.comment or [i.filename for i in items] != [i.filename for i in entries]:
                raise ValueError('VPR 候选归档成员清单或注释发生变化。')
            for a, b in zip(entries, items):
                if a.filename == SEQUENCE:
                    continue
                if any(getattr(a, k) != getattr(b, k) for k in
                       ('date_time', 'compress_type', 'comment', 'extra', 'external_attr', 'CRC', 'file_size')):
                    raise ValueError('VPR 非序列成员的元数据回读不一致。')
                with candidate.open(b) as content:
                    if _transfer(content, None, cancel) != hashes[b.filename]:
                        raise ValueError('VPR 非序列成员的内容回读不一致。')
    return {'embedded_audio_members': audio, 'preserved_members': len(hashes),
            'non_sequence_members_unchanged': True, 'copied_archive_bytes': False}


def _tick(value):
    return type(value) is int and -2**30 < value < 2**30


def _objects(data, key, default=None):
    items = data.get(key, default)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f'VPR 的 {key} 列表无效。')
    return items


def _tempo(data):
    master = data.get('masterTrack')
    tempo = master.get('tempo') if isinstance(master, dict) else None
    if not isinstance(tempo, dict):
        raise ValueError('VPR 缺少速度轨。')
    fixed = tempo.get('global')
    if isinstance(fixed, dict) and fixed.get('isEnabled') is True:
        events = [{'pos': 0, 'value': fixed.get('value')}]
    else:
        events = _objects(tempo, 'events')
    rows = []
    last = -1
    for event in events:
        pos, rate = event.get('pos'), event.get('value')
        if (not _tick(pos) or pos <= last or (not rows and pos != 0)
                or type(rate) not in (int, float) or not 0 < rate <= 100000):
            raise ValueError('VPR 速度表需要从 0 开始，且位置和 BPM 必须有效。')
        rows.append({'position': pos * BLICK, 'bpm': rate / 100.})
        last = pos
    if not rows:
        raise ValueError('VPR 没有可用的速度事件。')
    return rows


@dataclass
class Part:
    track_index: int
    index: int
    offset: int
    length: int | None
    notes: list[Note] = field(default_factory=list)
    direct_pitch_spans: list[tuple[int, int]] = field(default_factory=list)
    overlap_spans: list[tuple[int, int]] = field(default_factory=list)
    adapted = True

    @property
    def key(self):
        return f'part:{self.index}'

    def style_track(self, name):
        return {'name': name, 'mainRef': {'blickOffset': self.offset},
                'mainGroup': {'notes': [note.data for note in self.notes], 'parameters': {}}}


class Layout:
    def __init__(self, data):
        tracks = _objects(data, 'tracks')
        version = data.get('version')
        fields = [version.get(key) for key in ('major', 'minor', 'revision')] if isinstance(version, dict) else []
        if len(fields) != 3 or any(type(v) is not int or not 0 <= v <= 9999 for v in fields):
            raise ValueError('VPR 工程的版本号无效。')
        self.version = '.'.join(map(str, fields))
        self.tracks = tuple(Track(i, type(t.get('type')) is int and t['type'] == 1, native_refs=True)
                            for i, t in enumerate(tracks))
        self.score = {
            'time': {'tempo': _tempo(data)},
            'tracks': [{'name': t.get('name') or f'轨道 {i+1}', 'mainRef': {'isInstrumental': row.instrumental}}
                       for i, (t, row) in enumerate(zip(tracks, self.tracks))],
        }
        for row, track in zip(self.tracks, tracks):
            if row.instrumental:
                continue
            if fields[0] not in (5, 6):
                row.issues['track.vpr_version'] = '此 VPR 版本尚未验证写回，当前只读。'
            if type(track.get('type')) is not int or track['type'] not in (0, 2):
                row.issues['track.vpr_type'] = '此 VPR 轨道类型尚未验证写回，当前只读。'
            for index, source in enumerate(_objects(track, 'parts', [])):
                self._part(row, index, source)
            if not row.note_count:
                row.issues.setdefault('track.no_notes', '轨道没有可安全定位的音符。')
            spans = sorted((n.start, n.end, part.index, n.index, part)
                           for part in row.placements for n in part.notes)
            latest = -math.inf
            for i, (left, right, _, _, part) in enumerate(spans):
                if left < latest or (i + 1 < len(spans) and spans[i + 1][0] < right):
                    part.overlap_spans.append(((left - part.offset) // BLICK, (right - part.offset) // BLICK))
                latest = max(latest, right)
            if spans and sum(len(part.overlap_spans) for part in row.placements) == len(spans):
                row.issues['track.vpr_overlap'] = '此轨没有可安全写入的独立音符，重叠部分保持只读。'

    @staticmethod
    def _part(row, index, source):
        pos, length = source.get('pos'), source.get('duration')
        if not _tick(pos) or (length is not None and (not _tick(length) or length <= 0)):
            row.issues.setdefault('track.vpr_part', 'VPR 片段位置或长度无效。')
            return
        part = Part(row.index, index, pos * BLICK, length)
        notes = _objects(source, 'notes', [])
        row.raw_count += len(notes)
        for i, note in enumerate(notes):
            start, dur, pitch = note.get('pos'), note.get('duration'), note.get('number')
            if (not _tick(start) or start < 0 or not _tick(dur) or dur <= 0 or not _tick(pos + start + dur)
                    or type(pitch) is not int or not 0 <= pitch <= 127):
                row.issues.setdefault('note.timing_invalid', 'VPR 片段包含无法定位的音符。')
                continue
            if length is not None and start + dur > length:
                row.issues.setdefault('track.vpr_part_boundary', 'VPR 片段边界截断了音符，当前只读。')
                continue
            if note.get('directPitches'):
                part.direct_pitch_spans.append((start, start + dur))
            onset, duration = start * BLICK, dur * BLICK
            data = {'onset': onset, 'duration': duration, 'pitch': pitch, 'lyrics': note.get('lyric'),
                    'attributes': {'dF0Vbr': 1 if _authored_vibrato(note) else 0}}
            project_start = onset + part.offset
            part.notes.append(Note(i, data, project_start, project_start + duration, pitch))
        if part.notes:
            row.placements.append(part)


def selected_indices(data, options, *, layout=None):
    layout = layout or Layout(data)
    if options['expression_mode'] != 'preserve':
        raise ValueError(STYLE_ONLY)
    indices = options['track_indices']
    if indices is None:
        if options['style_scope'] == 'all_vocals':
            indices = [t.index for t in layout.tracks if not t.instrumental and t.note_count]
        else:
            indices = [t.index for t in layout.tracks if t.writable][:1]
    if not indices:
        raise ValueError('VPR 工程没有可编辑的演唱轨。')
    for index in indices:
        if not 0 <= index < len(layout.tracks) or layout.tracks[index].instrumental:
            raise ValueError('所选索引不是有效的 VPR 演唱轨。')
        if not layout.tracks[index].writable:
            raise ValueError(f'演唱轨 {index+1}：{layout.tracks[index].reason}')
    return list(indices)


def _curve(part, name):
    controls = part.get('controllers')
    if controls is None:
        return None, {'name': name, 'events': []}
    if not isinstance(controls, list) or any(not isinstance(c, dict) for c in controls):
        return None, None
    matches = [i for i, c in enumerate(controls) if c.get('name') == name]
    if not matches:
        return None, {'name': name, 'events': []}
    if len(matches) != 1:
        return None, None
    index = matches[0]
    curve = controls[index]
    events = curve.get('events')
    _, low, high = CONTROLLERS[name]
    if (not isinstance(events, list) or any(not isinstance(e, dict) or set(e) != {'pos', 'value'}
            or not _tick(e['pos']) or type(e['value']) is not int or not low <= e['value'] <= high
            for e in events) or any(a['pos'] >= b['pos'] for a, b in zip(events, events[1:]))):
        return None, None
    return index, curve


def _add_delta(part, raw, name, original, rendered):
    """Apply an additive, bounded delta and restore the authored step at every exit."""
    import numpy as np

    points = rendered['points']
    xs = np.asarray(points[::2], dtype=np.float64) / BLICK
    ys = np.asarray(points[1::2], dtype=np.float64)
    lo, hi = max(0, math.floor(xs[0])), math.ceil(xs[-1]) + 1
    if part.length is not None:
        hi = min(hi, part.length)
    if hi <= lo or hi - lo > MAX_EDIT_TICKS:
        return None, 'vpr_curve_capacity', []
    grid = np.arange(lo, hi + 1, dtype=np.int64)
    delta = np.interp(grid, xs, ys, left=0., right=0.)
    delta[-1] = 0  # Keep the original step at the final guard.
    default, low, high = CONTROLLERS[name]
    baseline = step_values(grid, original['events'], default)
    protections = []

    def protect(spans, reason, end_guard=0):
        for start, end in spans:
            left, right = max(0, start - lo), min(len(grid), end - lo + end_guard)
            if left < right and np.any(np.abs(delta[left:right]) > 1e-10):
                delta[left:right] = 0
                if reason not in protections:
                    protections.append(reason)

    protect(part.overlap_spans, 'vpr_overlapping_note_preserved')
    if name == PIT:
        _, sensitivity = _curve(raw, PBS)
        if sensitivity is None:
            return None, 'vpr_invalid_sensitivity_preserved', protections
        protect(part.direct_pitch_spans, 'vpr_direct_pitch_preserved', end_guard=1)
        delta *= 8192. / (100 * step_values(grid, sensitivity['events'], CONTROLLERS[PBS][0]))
    else:
        # A bounded DYN gesture, not a claim that DYN units are decibels.
        delta *= 4.
        delta[baseline == 0] = 0
    scale = 1.
    positive, negative = delta > 1e-10, delta < -1e-10
    if positive.any():
        scale = min(scale, float(np.min((high - baseline[positive]) / delta[positive])))
    if negative.any():
        scale = min(scale, float(np.min((low - baseline[negative]) / delta[negative])))
    values = np.rint(baseline + max(scale, 0.) * delta).astype(np.int64)
    if not np.any(values != baseline):
        return None, 'vpr_no_native_delta', protections
    kept = {e['pos']: dict(e) for e in original['events']}
    for x, event in kept.items():
        if lo <= x <= hi:
            event['value'] = int(values[x - lo])
    knots = np.flatnonzero(np.diff(values)) + 1
    if values[0] != baseline[0]:
        knots = np.r_[0, knots]
    for i in knots:
        pos = int(grid[i])
        if pos not in kept:
            kept[pos] = {'pos': pos, 'value': int(values[i])}
    if len(kept) > MAX_EVENTS:
        return None, 'vpr_curve_capacity', protections
    events = [kept[x] for x in sorted(kept)]
    if not np.array_equal(step_values(grid, events, default), values):
        raise ValueError('VPR 控制事件回读不一致，未写入候选。')
    return {**original, 'events': events}, '', protections


def process_project(data, options, *, cancel=lambda: None, progress=lambda text: None):
    from tuning_engine import VERSION, StyleTargets, _has_incomplete, apply_style_plan, digest, settings

    options = settings(options)
    layout = Layout(data)
    indices = selected_indices(data, options, layout=layout)
    if any(r.get('track_index') not in (*indices, None) for r in options['style_regions']):
        raise ValueError('风格片段包含本次未选中的演唱轨。')
    parts = tuple(p for i in indices for p in layout.tracks[i].placements)
    targets = StyleTargets({'time': layout.score['time'], 'tracks': [
        p.style_track(layout.score['tracks'][p.track_index]['name']) for p in parts]}, parts)
    style, attempted = apply_style_plan(targets, options, indices, controls={'loudness'},
                                        cancel=cancel, progress=progress)
    output = deepcopy(data)
    changed, skipped, writes = [], [], []
    for part, styled in zip(parts, targets.project['tracks']):
        cancel()
        raw = data['tracks'][part.track_index]['parts'][part.index]
        dest = output['tracks'][part.track_index]['parts'][part.index]
        for name, channel in ((DYN, 'loudness'), (PIT, 'pitchDelta')):
            rendered = styled['mainGroup']['parameters'].get(channel)
            if not rendered:
                continue
            info = {'track_index': part.track_index, 'group_placement': part.key, 'parameter': name}
            idx, original = _curve(raw, name)
            if original is None:
                skipped.append({**info, 'status': 'skipped', 'reason': 'vpr_invalid_controller_preserved'})
                continue
            replacement, reason, protections = _add_delta(part, raw, name, original, rendered)
            skipped.extend({**info, 'status': 'skipped', 'reason': why} for why in protections)
            if reason:
                skipped.append({**info, 'status': 'skipped', 'reason': reason})
                continue
            if dest.get('controllers') is None:
                dest['controllers'] = []
            controls = dest['controllers']
            if idx is None:
                writes.append((part, len(controls), None))
                controls.append(replacement)
            else:
                writes.append((part, idx, original))
                controls[idx] = replacement
            changed.append(info)
    protected = deepcopy(output)
    for part, index, original in reversed(writes):
        raw = data['tracks'][part.track_index]['parts'][part.index]
        dest = protected['tracks'][part.track_index]['parts'][part.index]
        controls = dest['controllers']
        if original is None:
            controls.pop(index)
            if not controls and raw.get('controllers') is None:
                if 'controllers' in raw:
                    dest['controllers'] = None
                else:
                    del dest['controllers']
        else:
            controls[index]['events'] = original['events']
    if protected != data:
        raise ValueError('VPR 处理改变了保护范围之外的工程内容。')
    cancel()
    return output, {
        'schema': VERSION, 'format': 'vpr', 'mode': 'preserve', 'track_indices': indices,
        'stages': ['primary', 'style'] if attempted else ['primary'],
        'primary': {'status': 'preserved', 'mode': 'preserve'}, 'style': style,
        'curve_changes': changed, 'native_curve_skips': skipped,
        'expression_density': {'status': 'native_ticks', 'mode': 'original', 'added_points': 0},
        'status': 'partial' if skipped or _has_incomplete(style) else 'applied' if changed else 'unchanged',
        'changed': bool(changed), 'pitch_changed': any(row['parameter'] == PIT for row in changed),
        'notes_and_timing_unchanged': True, 'protected_content_unchanged': True,
        'source_content_sha256': digest(data), 'output_content_sha256': digest(output),
        'original_input_unchanged': True, 'project_writes': 0, 'final_density_passes': 0,
        'synthesized': False, 'listened': False, 'acoustic_improvement_verified': False,
    }
