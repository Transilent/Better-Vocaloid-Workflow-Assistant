"""Native OpenUtau style editing. The style view is never serialized as a project."""

from copy import deepcopy
from dataclasses import dataclass, field
import math
import re

from svp_tuning import Note, Track


QUARTER = 705600000
RESOLUTION = 480
BLICK = QUARTER // RESOLUTION
VERSIONS = frozenset(f'0.{minor}' for minor in range(6, 11))
STYLE_ONLY = 'USTX 支持「保留原表现」下的响度和音高偏差风格编辑。'
CHANNELS = {'dyn': ('loudness', 10, -240, 120), 'pitd': ('pitchDelta', 1, -1200, 1200)}
MAX_BYTES = 64 * 1024**2
MAX_SAMPLES = 750_000
MAX_POINTS = 250_000
_TEXT_FIELDS = frozenset(('ustx_version', 'name', 'comment', 'lyric', 'singer', 'phonemizer',
                          'phoneme', 'relative_path', 'abbr', 'track_name', 'track_color',
                          'output_dir', 'cache_dir', 'renderer', 'resampler', 'wavtool'))


def read(raw, *, require_version=True):
    """Parse ordinary OpenUtau YAML without aliases, ambiguous keys or YAML 1.1 booleans."""
    import yaml

    fast_loader = getattr(yaml, 'CSafeLoader', None)

    class Loader(fast_loader or yaml.SafeLoader):
        depth = 0

        def compose_node(self, parent, index):
            if self.depth >= 64 or self.check_event(yaml.AliasEvent):
                raise ValueError('USTX 不支持 YAML 别名或过深的嵌套。')
            self.depth += 1
            try:
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError('USTX 包含非文本或重复的 YAML 键。')
                if (key in _TEXT_FIELDS and isinstance(value_node, yaml.ScalarNode)
                        and value_node.tag != 'tag:yaml.org,2002:null'):
                    # The editor reads these fields as strings, even for a plain lyric like 012.
                    value = value_node.value
                else:
                    value = self.construct_object(value_node, deep=deep)
                result[key] = value
            return result

    Loader.yaml_implicit_resolvers = {
        ch: [rule for rule in rules if rule[0] not in
             ('tag:yaml.org,2002:bool', 'tag:yaml.org,2002:timestamp')]
        for ch, rules in Loader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver('tag:yaml.org,2002:bool',
                                 re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'), list('tTfF'))
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('USTX 工程为空或超过 64 MiB。')
    try:
        text = raw.decode('utf-8-sig')
        if fast_loader:
            # LibYAML does not call Python's compose_node override. Check
            # events before composing to keep the same alias and depth limits.
            depth = 0
            for event in yaml.parse(text, Loader=fast_loader):
                if isinstance(event, yaml.AliasEvent):
                    raise ValueError('USTX 不支持 YAML 别名。')
                if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                    depth += 1
                    if depth >= 64:
                        raise ValueError('USTX 的 YAML 嵌套过深。')
                elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                    depth -= 1
        data = yaml.load(text, Loader=Loader)
    except (UnicodeError, yaml.YAMLError, TypeError, OverflowError) as error:
        raise ValueError('USTX 工程不是有效的 UTF-8 YAML。') from error
    if not isinstance(data, dict) or (require_version and 'ustx_version' not in data):
        raise ValueError('请选择带版本号的 OpenUtau USTX 工程。')
    return data


def encode(output, raw):
    import yaml
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    dumper = getattr(yaml, 'CSafeDumper', yaml.SafeDumper)
    return yaml.dump(output, Dumper=dumper, allow_unicode=True, sort_keys=False, width=100).encode(encoding)


def _tick(value):
    return type(value) is int and -2**30 < value < 2**30


def _objects(data, key, *, default=None):
    value = data.get(key, default)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f'USTX 工程的 {key} 列表无效。')
    return value


@dataclass
class Part:
    track_index: int
    index: int
    offset: int
    notes: list[Note] = field(default_factory=list)
    adapted = True

    @property
    def key(self):
        return f'part:{self.index}'

    def style_track(self, track):
        return {'name': track['name'], 'mainRef': {'blickOffset': self.offset},
                'mainGroup': {'notes': [n.data for n in self.notes], 'parameters': {}}}


class Layout:
    """Place voice parts on actual tracks. Wave parts never become note targets."""

    def __init__(self, data):
        self.version = data['ustx_version']
        tracks = _objects(data, 'tracks')
        voice = _objects(data, 'voice_parts', default=[])
        waves = _objects(data, 'wave_parts', default=[])
        if data.get('resolution', RESOLUTION) != RESOLUTION:
            raise ValueError('USTX 调音需要每四分音符 480 tick。')
        if not tracks or any(not _tick(p.get('track_no')) or not 0 <= p['track_no'] < len(tracks)
                             for p in (*voice, *waves)):
            raise ValueError('USTX 片段引用的轨道无效。')
        vocal = {p['track_no'] for p in voice}
        wave = {p['track_no'] for p in waves}
        self.tracks = tuple(Track(i, i in wave and i not in vocal, native_refs=i in vocal)
                            for i in range(len(tracks)))
        self.score = {
            'time': {'tempo': self._tempo(data)},
            'tracks': [{'name': t.get('track_name') or f'轨道 {i+1}',
                        'mainRef': {'isInstrumental': self.tracks[i].instrumental,
                                    'voice': {'singer': t.get('singer'), 'phonemizer': t.get('phonemizer')}}}
                       for i, t in enumerate(tracks)],
        }
        for i, row in enumerate(self.tracks):
            if row.instrumental:
                continue
            if self.version not in VERSIONS:
                row.issues['track.ustx_version'] = '此 USTX 版本尚未验证写回，当前只读。'
            if data.get('expression_graphs') or tracks[i].get('expression_graph'):
                row.issues['track.ustx_graph'] = '此轨使用 OpenUtau 表达式图，当前只读。'
        for i, part in enumerate(voice):
            self._part(i, part)
        for row in self.tracks:
            if row.instrumental:
                continue
            if not row.note_count:
                row.issues.setdefault('track.no_notes', '轨道没有可安全定位的音符。')
            latest = -math.inf
            for start, end in sorted((n.start, n.end) for p in row.placements for n in p.notes):
                if start < latest:
                    row.issues['track.ustx_overlap'] = '此轨包含重叠音符，当前只读。'
                    break
                latest = end

    @staticmethod
    def _tempo(data):
        source = _objects(data, 'tempos')
        result = []
        last = -1
        for t in source:
            position, bpm = t.get('position'), t.get('bpm')
            if (not _tick(position) or position <= last or (not result and position != 0)
                    or type(bpm) not in (float, int) or not 0 < bpm <= 1000):
                raise ValueError('USTX 速度表需要从 0 开始，且位置和 BPM 必须有效。')
            result.append({'position': position * BLICK, 'bpm': bpm})
            last = position
        if not result:
            raise ValueError('USTX 工程缺少速度表。')
        return result

    def _part(self, index, source):
        row = self.tracks[source['track_no']]
        pos = source.get('position')
        notes = source.get('notes')
        if not _tick(pos) or not isinstance(notes, list):
            row.issues.setdefault('track.ustx_part', '片段位置或音符列表无效。')
            return
        row.raw_count += len(notes)
        part = Part(row.index, index, pos * BLICK)
        for j, n in enumerate(notes):
            if not isinstance(n, dict):
                row.issues.setdefault('note.invalid', '片段中有无效音符。')
                continue
            start, duration, pitch = n.get('position'), n.get('duration'), n.get('tone')
            if (not _tick(start) or start < 0 or not _tick(duration) or duration <= 0
                    or not _tick(pos + start + duration) or type(pitch) is not int or not 0 <= pitch <= 127):
                row.issues.setdefault('note.timing_invalid', '片段包含无法定位的音符。')
                continue
            onset, duration = start * BLICK, duration * BLICK
            project_start = onset + part.offset
            lyric = n.get('lyric', '')
            vibrato = n.get('vibrato')
            explicit_vibrato = (isinstance(vibrato, dict) and type(vibrato.get('length')) in (int, float)
                                and vibrato['length'] > 0)
            score_note = {'onset': onset, 'duration': duration, 'pitch': pitch,
                          'lyrics': 'sil' if isinstance(lyric, str) and lyric.strip().casefold() == 'r' else lyric,
                          'attributes': {'dF0Vbr': 1 if explicit_vibrato else 0}}
            # Keep the native lyric visible, including a written rest alias.
            if score_note['lyrics'] != lyric:
                score_note['source_lyric'] = lyric
            part.notes.append(Note(j, score_note, project_start, project_start + duration, pitch))
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
        raise ValueError('USTX 工程没有可编辑的演唱轨。')
    for index in indices:
        if not 0 <= index < len(layout.tracks) or layout.tracks[index].instrumental:
            raise ValueError('所选索引不是有效的 USTX 演唱轨。')
        if not layout.tracks[index].writable:
            raise ValueError(f'演唱轨 {index+1}：{layout.tracks[index].reason}')
    return list(indices)


def _native_curve(data, part, abbr):
    """Return the exact curve and index, or preserve a custom/invalid channel."""
    _, _, low, high = CHANNELS[abbr]
    expressions = data.get('expressions', {})
    if not isinstance(expressions, dict):
        return None, None, 'ustx_custom_expression_preserved'
    descriptor = expressions.get(abbr)
    if abbr in expressions and (not isinstance(descriptor, dict)
            or descriptor.get('abbr') != abbr or descriptor.get('type') != 'Curve'
            or (descriptor.get('min'), descriptor.get('max'), descriptor.get('default_value')) != (low, high, 0)):
        return None, None, 'ustx_custom_expression_preserved'
    curves = part.get('curves', [])
    if not isinstance(curves, list) or any(not isinstance(c, dict) for c in curves):
        return None, None, 'ustx_invalid_curve_preserved'
    indices = [i for i, c in enumerate(curves) if c.get('abbr') == abbr]
    if len(indices) > 1:
        return None, None, 'ustx_invalid_curve_preserved'
    if not indices:
        return None, {'abbr': abbr, 'xs': [], 'ys': []}, ''
    index = indices[0]
    curve = curves[index]
    xs, ys = curve.get('xs'), curve.get('ys')
    if (not isinstance(xs, list) or not isinstance(ys, list) or len(xs) != len(ys)
            or any(not _tick(x) for x in xs) or any(a >= b for a, b in zip(xs, xs[1:]))
            or any(type(y) is not int or not low <= y <= high for y in ys)):
        return None, None, 'ustx_invalid_curve_preserved'
    return index, curve, ''


def _native_samples(grid, xs, ys):
    import numpy as np

    result = np.zeros(len(grid), dtype=np.int64)
    if len(xs):
        xs, ys = np.asarray(xs, dtype=np.int64), np.asarray(ys, dtype=np.int64)
        right = np.searchsorted(xs, grid, side='right')
        inside = (right > 0) & (right < len(xs))
        r = right[inside]
        l = r - 1
        # Match MusicMath.Linear's arithmetic and UCurve.Sample's rounding.
        result[inside] = np.rint(ys[l] + (ys[r] - ys[l]) * (grid[inside] - xs[l]) / (xs[r] - xs[l]))
        result[grid == xs[-1]] = ys[-1]
    return result


def _add_native_delta(original, rendered, abbr):
    """Quantize once on the OpenUtau tick grid and prove equality outside the edit."""
    import numpy as np

    _, multiplier, low, high = CHANNELS[abbr]
    points = rendered['points']
    style_x = np.asarray(points[::2], dtype=np.float64) / BLICK
    style_y = np.asarray(points[1::2], dtype=np.float64) * multiplier
    xs, ys = original['xs'], original['ys']
    lo = min(math.floor(style_x[0]), xs[0] if xs else math.inf) - 1
    hi = max(math.ceil(style_x[-1]), xs[-1] if xs else -math.inf) + 1
    if hi - lo + 1 > MAX_SAMPLES:
        return None, 'ustx_curve_capacity'
    grid = np.arange(lo, hi + 1, dtype=np.int64)
    # OpenUtau UCurve.Sample returns the descriptor default outside its knots.
    base = _native_samples(grid, xs, ys)
    delta = np.interp(grid, style_x, style_y, left=0., right=0.)
    if abbr == 'dyn':
        delta[base == low] = 0  # The minimum is native hard mute, not -24 dB.
        low += 1
    positive, negative = delta > 1e-10, delta < -1e-10
    scale = 1.
    if positive.any():
        scale = min(scale, float(np.min((high - base[positive]) / delta[positive])))
    if negative.any():
        scale = min(scale, float(np.min((low - base[negative]) / delta[negative])))
    target = np.rint(base + delta * max(0., scale)).astype(np.int64)
    changed = target != base
    if not changed.any():
        return None, 'ustx_no_native_delta'
    keep = np.zeros(len(grid), dtype=bool)
    keep[[0, -1]] = True
    if xs:
        keep[np.asarray(xs, dtype=np.int64) - lo] = True
        keep[[xs[0] - lo - 1, xs[-1] - lo + 1]] = True
    transitions = np.flatnonzero(changed[1:] != changed[:-1])
    keep[transitions] = keep[transitions + 1] = True
    bends = np.flatnonzero((np.diff(target, n=2) != 0) & changed[1:-1]) + 1
    keep[bends] = True
    knots = np.flatnonzero(keep)
    observed = _native_samples(grid, grid[knots], target[knots])
    bad = np.flatnonzero(observed != target)
    if len(bad):
        # Integer anchors can otherwise change rounding in the old linear
        # interval. Densify only the intervals that need exact preservation.
        right = np.unique(np.searchsorted(knots, bad))
        spans = np.zeros(len(grid) + 1, dtype=np.int32)
        spans[knots[right - 1] + 1] += 1
        spans[knots[right]] -= 1
        keep |= np.cumsum(spans[:-1]) > 0
        knots = np.flatnonzero(keep)
    if len(knots) > MAX_POINTS:
        return None, 'ustx_curve_capacity'
    if not np.array_equal(_native_samples(grid, grid[knots], target[knots]), target):
        raise ValueError('USTX 曲线整数回读不一致，未写入候选。')
    return {**original, 'xs': grid[knots].tolist(), 'ys': target[knots].tolist()}, ''


def process_project(data, options, *, cancel=lambda:None, progress=lambda text:None):
    from tuning_engine import VERSION, StyleTargets, _has_incomplete, apply_style_plan, digest, settings

    options = settings(options)
    layout = Layout(data)
    indices = selected_indices(data, options, layout=layout)
    if any(r.get('track_index') not in (*indices, None) for r in options['style_regions']):
        raise ValueError('风格片段包含本次未选中的演唱轨。')
    parts = tuple(p for i in indices for p in layout.tracks[i].placements)
    targets = StyleTargets({'time': layout.score['time'], 'tracks': [
        p.style_track(layout.score['tracks'][p.track_index]) for p in parts]}, parts)
    style, attempted = apply_style_plan(targets, options, indices, controls={'loudness'},
                                        cancel=cancel, progress=progress)
    output = deepcopy(data)
    changes, skipped, mutations = [], [], []
    for part, styled in zip(parts, targets.project['tracks']):
        cancel()
        source = data['voice_parts'][part.index]
        dest = output['voice_parts'][part.index]
        parameters = styled['mainGroup']['parameters']
        for abbr, (name, _, _, _) in CHANNELS.items():
            if name not in parameters:
                continue
            idx, original, reason = _native_curve(data, source, abbr)
            if original is not None:
                replacement, reason = _add_native_delta(original, parameters[name], abbr)
            evidence = {'track_index': part.track_index, 'group_placement': part.key, 'parameter': abbr}
            if reason:
                skipped.append({**evidence, 'status': 'skipped', 'reason': reason})
                continue
            curves = dest.setdefault('curves', [])
            if idx is None:
                curves.append(replacement)
                mutations.append((part.index, len(curves) - 1, None))
            else:
                curves[idx] = replacement
                mutations.append((part.index, idx, original))
            changes.append(evidence)
    # Restore only the whitelisted coordinates on a copy, then compare every
    # other field (notes, render settings, authored curves and unknown metadata).
    protected = deepcopy(output)
    for part, idx, original in reversed(mutations):
        curves = protected['voice_parts'][part]['curves']
        if original is None:
            curves.pop(idx)
            if not curves and 'curves' not in data['voice_parts'][part]:
                del protected['voice_parts'][part]['curves']
        else:
            curves[idx].update(xs=original['xs'], ys=original['ys'])
    if protected != data:
        raise ValueError('USTX 处理改变了保护范围之外的工程内容。')
    cancel()
    changed = bool(changes)
    partial = bool(skipped or _has_incomplete(style))
    return output, {
        'schema': VERSION, 'format': 'ustx', 'mode': 'preserve', 'track_indices': indices,
        'stages': ['primary', 'style'] if attempted else ['primary'],
        'primary': {'status': 'preserved', 'mode': 'preserve'}, 'style': style,
        'curve_changes': changes, 'native_curve_skips': skipped,
        'expression_density': {'status': 'native_ticks', 'mode': 'original', 'added_points': 0},
        'status': 'partial' if partial else 'applied' if changed else 'unchanged',
        'changed': changed, 'pitch_changed': any(r['parameter'] == 'pitd' for r in changes),
        'notes_and_timing_unchanged': True, 'protected_content_unchanged': True,
        'source_content_sha256': digest(data), 'output_content_sha256': digest(output),
        'original_input_unchanged': True, 'project_writes': 0, 'final_density_passes': 0,
        'synthesized': False, 'listened': False, 'acoustic_improvement_verified': False,
    }
