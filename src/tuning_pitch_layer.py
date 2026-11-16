"""A note-anchored manual pitch layer over the original native performance."""
from bisect import bisect_right
from dataclasses import dataclass
import json
import math
import re

from style_presets import _curve
from tuning_score_edits import FORMATS


MAX_CENTS = 200
MAX_POINTS = 16_384
MAX_BYTES = 224 * 1024
MIN_NOTE_SECONDS = .08
MIN_STROKE_SECONDS = .012
EDGE_SECONDS = .018
UNSUPPORTED = '此工程格式暂不支持手绘音高。'
PROTECTED = '此音符或片段的原生音高受保护。'
SHORT = '音符太短，无法留出手绘过渡。'
SPAN = '请在一个演唱音符内画出一段音高。'
BOUNDS = '手绘范围超出当前音符，请先擦除相关笔触。'
CAPACITY = '手绘音高超过当前工程曲线容量。'
LIMIT = '手绘与原曲线合成后超出音高范围；请缩小偏移。'


def normalize_strokes(rows):
    if not isinstance(rows, list) or len(rows) > 1024:
        raise ValueError('手绘音高笔触无效或过多。')
    result, count = [], 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'note_id', 'points'}
                or not isinstance(row['note_id'], str) or not re.fullmatch(r'note_[0-9a-f]{24}', row['note_id'])
                or not isinstance(row['points'], list) or len(row['points']) < 2):
            raise ValueError('手绘音高笔触无效或过多。')
        count += len(row['points'])
        if count > MAX_POINTS:
            raise ValueError(CAPACITY)
        points, previous = [], -1
        for point in row['points']:
            if (not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point)
                    or not previous < point[0] < 2**52 or not -MAX_CENTS <= point[1] <= MAX_CENTS):
                raise ValueError('手绘音高笔触无效或过多。')
            points.append(point.copy())
            previous = point[0]
        result.append({'note_id': row['note_id'], 'points': points})
    result.sort(key=lambda row: (row['note_id'], row['points'][0][0]))
    if any(a['note_id'] == b['note_id'] and a['points'][-1][0] >= b['points'][0][0]
           for a, b in zip(result, result[1:])):
        raise ValueError('同一音符的手绘范围不能重叠。')
    if len(json.dumps(result, separators=(',', ':')).encode()) > MAX_BYTES:
        raise ValueError(CAPACITY)
    return result


def _get(data, path):
    for key in path:
        data = data[key]
    return data


def _clip_points(points, first, last):
    if first >= last:
        return None

    def sample(x):
        i = bisect_right(points, [x, MAX_CENTS + 1]) - 1
        a, y = points[i]
        if x == a:
            return [x, y]
        b, z = points[i + 1]
        return [x, round(y + (z - y) * (x - a) / (b - a))]

    return [sample(first), *(point for point in points if first < point[0] < last), sample(last)]


@dataclass(frozen=True)
class Span:
    placement: tuple
    track: int
    group: str
    points: list
    left: int
    right: int


class PitchLayer:
    """Uses the same immutable identity, native note coordinates and clock as score edits."""

    def __init__(self, data, score, layout):
        self.score, self.format = score, score.format
        self.tracks, self.notes, self.reasons = set(), {}, {}
        if self.format not in FORMATS:
            return
        placements = {(track.index, place.key): place for track in layout.tracks if track.writable
                      for place in track.style_placements}
        overlapping = set()
        for rows in score.track_notes.values():
            last = None
            for row in sorted(rows, key=lambda n: n['start_seconds']):
                if last and row['start_seconds'] < last['end_seconds']:
                    overlapping.update((last['note_id'], row['note_id']))
                if last is None or row['end_seconds'] > last['end_seconds']:
                    last = row
        group_ok = {}
        for identity, target in score.targets.items():
            row = score.rows[identity]
            key = row.get('group_placement', 'main')
            group_path = target.path[:-2]
            group = _get(data, group_path)
            if group_path not in group_ok:
                if self.format == 'ustx':
                    from ustx_tuning import _native_curve
                    group_ok[group_path] = not _native_curve(data, group, 'pitd')[2]
                else:
                    placement = placements[target.track, key]
                    system = placement.ref.get('systemPitchDelta')
                    if isinstance(system, dict):
                        system = system.get('points', system)
                    try:
                        _curve(group.get('parameters', {}).get('pitchDelta'))
                        group_ok[group_path] = not (group.get('pitchControls') or system)
                    except ValueError:
                        group_ok[group_path] = False
            lyric = target.old['lyric'].strip().casefold()
            native = group['notes'][target.path[-1]]
            if (identity in overlapping or not row['singing'] or lyric in ('', 'r', 'ap', '-')
                    or not group_ok[group_path] or (self.format == 'svp' and
                        (native.get('pitchControls') or native.get('retakes')))):
                self.reasons[identity] = PROTECTED
                continue
            self.notes[identity] = (target, key)
            self.tracks.add(target.track)

    def reason(self, identity, note):
        if identity not in self.notes:
            return self.reasons.get(identity, PROTECTED)
        return SHORT if note['end'] - note['start'] < MIN_NOTE_SECONDS else ''

    def _basis(self, identity, patch):
        if identity not in self.notes:
            raise ValueError(UNSUPPORTED if self.format not in FORMATS else PROTECTED)
        target, group = self.notes[identity]
        lyric = patch.get('lyric', target.old['lyric']).strip().casefold()
        if lyric in ('', 'r', 'ap', '-', 'sil', 'pau', 'sp', 'br') or (lyric.startswith('br') and lyric[2:].isdigit()):
            raise ValueError(PROTECTED)
        step = target.step
        onset = patch.get('onset', target.old['onset'])
        duration = patch.get('duration', target.old['duration'])
        absolute = target.offset + onset * step
        clock = self.score.clock
        start, end = clock.to_seconds(absolute), clock.to_seconds(absolute + duration * step)
        if end - start < MIN_NOTE_SECONDS:
            raise ValueError(SHORT)
        inner_a = max(2, math.ceil((clock.to_position(start + EDGE_SECONDS) - absolute) / step))
        inner_b = min(duration - 2, math.floor((clock.to_position(end - EDGE_SECONDS) - absolute) / step))
        return target, group, onset, absolute, duration, inner_a, inner_b

    def resolve(self, strokes, patches):
        """Resolve normalized spans to the native curve grid and cap their fades to the note."""
        clock = self.score.clock
        rows = []
        for row in strokes:
            identity, points = row['note_id'], row['points']
            target, group, onset, absolute, duration, lower, upper = self._basis(identity, patches.get(identity, {}))
            a, b = points[0][0], points[-1][0]
            if a < lower or b > upper:
                raise ValueError(BOUNDS)
            step = target.step
            start, end = clock.to_seconds(absolute + a * step), clock.to_seconds(absolute + b * step)
            guard_a = max(1, min(a - 1, round((clock.to_position(start - EDGE_SECONDS) - absolute) / step)))
            guard_b = min(duration - 1, max(b + 1, round((clock.to_position(end + EDGE_SECONDS) - absolute) / step)))
            rows.append(Span(target.path[:-2], target.track, group,
                             [[x + onset, y] for x, y in points], guard_a + onset, guard_b + onset))
        return rows

    def gesture(self, rows, patches, identity, kind, samples):
        if kind not in ('draw', 'erase') or not 1 <= len(samples) <= 2048:
            raise ValueError(SPAN)
        target, _, _, absolute, _, lower, upper = self._basis(identity, patches.get(identity, {}))
        clock, step = self.score.clock, target.step
        if upper <= lower:
            raise ValueError(SHORT)
        points = {}
        for sample in samples:
            if (not isinstance(sample, list) or len(sample) != 2
                    or not all(type(x) in (int, float) and math.isfinite(x) for x in sample)):
                raise ValueError(SPAN)
            x, cents = sample
            offset = round((clock.to_position(max(-1e7, min(1e7, x))) - absolute) / step)
            points[max(lower, min(upper, offset))] = max(-MAX_CENTS, min(MAX_CENTS, round(cents)))
        a, b = min(points), max(points)
        if kind == 'draw' and clock.to_seconds(absolute + b * step) - clock.to_seconds(absolute + a * step) < MIN_STROKE_SECONDS:
            raise ValueError(SPAN)
        moved = []
        for row in rows:
            old = row['points']
            first, last = old[0][0], old[-1][0]
            if row['note_id'] != identity or b < first or last < a:
                moved.append(row)
            elif kind == 'draw' or b != a:
                for lo, hi in ((first, min(last, a - 1)), (max(first, b + 1), last)):
                    if slice_ := _clip_points(old, lo, hi):
                        moved.append({'note_id': identity, 'points': slice_})
            # An eraser click removes the complete hit span.
        if kind == 'draw':
            moved.append({'note_id': identity, 'points': [[x, points[x]] for x in sorted(points)]})
        return normalize_strokes(moved)

    def display(self, strokes, patches, visible):
        result = []
        for row in strokes:
            identity = row['note_id']
            if identity not in visible:
                continue
            patch = patches.get(identity, {})
            target, _, _, start, _, _, _ = self._basis(identity, patch)
            pitch = patch.get('pitch', target.old['pitch'])
            result.append({'noteId': identity, 'points': [
                [self.score.clock.to_seconds(start + x * target.step), pitch + y / 100.]
                for x, y in row['points']]})
        return result


def _mix(grid, native, styled, spans, low, high, *, cancel):
    """Replace only the style contribution inside the hand-drawn intervals."""
    import numpy as np

    result = styled.copy()
    for i, span in enumerate(spans):
        cancel()
        xs, ys = zip(*span.points)
        a, b = xs[0], xs[-1]
        previous = spans[i - 1].points[-1][0] if i else span.left - 1
        left = max(span.left, previous + 1, (previous + a) // 2) if i else span.left
        right = min(span.right, (b + spans[i + 1].points[0][0]) // 2) if i + 1 < len(spans) else span.right
        lo, first, last, hi = np.searchsorted(grid, [left, a, b, right])
        value = native[first:last + 1] + np.interp(grid[first:last + 1], xs, ys)
        if np.any(value < low - 1e-8) or np.any(value > high + 1e-8):
            raise ValueError(LIMIT)
        result[first:last + 1] = value
        if lo < first:
            result[lo:first] = np.interp(grid[lo:first], [left, a], [styled[lo], value[0]])
        if last < hi:
            result[last + 1:hi] = np.interp(grid[last + 1:hi], [b, right], [value[-1], styled[hi]])
    return result


def apply_layer(base, output, format, spans, *, cancel):
    """Mutation whitelist: only PITD or linear pitchDelta coordinates are writable."""
    import numpy as np

    from collections import defaultdict
    by_group = defaultdict(list)
    for span in spans:
        by_group[span.placement].append(span)
    changes = []
    for path, batch in by_group.items():
        cancel()
        original, dest = _get(base, path), _get(output, path)
        batch.sort(key=lambda span: span.points[0][0])
        if format == 'ustx':
            from ustx_tuning import MAX_SAMPLES, _native_curve, _native_samples, encode_native_curve
            idx, styled, _ = _native_curve(output, dest, 'pitd')
            _, native, _ = _native_curve(base, original, 'pitd')
            if styled is None or native is None:
                raise ValueError(PROTECTED)
            bounds = [batch[0].left, batch[-1].right, *styled['xs'], *native['xs']]
            lo, hi = min(bounds) - 1, max(bounds) + 1
            if hi - lo + 1 > MAX_SAMPLES:
                raise ValueError(CAPACITY)
            grid = np.arange(lo, hi + 1, dtype=np.int64)
            values = _native_samples(grid, styled['xs'], styled['ys']).astype(float)
            baseline = _native_samples(grid, native['xs'], native['ys'])
            target = np.rint(_mix(grid, baseline, values, batch, -1200, 1200, cancel=cancel)).astype(np.int64)
            changed = target != values
            if not changed.any():
                continue
            replacement, error = encode_native_curve(styled, grid, target, changed)
            if error:
                raise ValueError(CAPACITY)
            if idx is None:
                dest.setdefault('curves', []).append(replacement)
            else:
                dest['curves'][idx] = replacement
            parameter = 'pitd'
        else:
            parameter = 'pitchDelta'
            styled = dest.get('parameters', {}).get(parameter)
            old_x, old_y = _curve(original.get('parameters', {}).get(parameter))
            xs, ys = _curve(styled)
            # Include both transition margins, all original/style knots and
            # any midpoint where two adjoining fades meet.
            ends = [point for span in batch for point in (span.left, span.right, *(x for x, _ in span.points))]
            for a, b in zip(batch, batch[1:]):
                mid = (a.points[-1][0] + b.points[0][0]) // 2
                if a.left <= mid <= a.right or b.left <= mid <= b.right:
                    ends.append(mid)
            if len(xs) + len(old_x) + len(ends) > 2_000_000:
                raise ValueError(CAPACITY)
            grid = np.unique(np.asarray(xs + old_x + ends, dtype=np.float64))
            values = np.interp(grid, xs, ys) if xs else np.zeros(len(grid))
            baseline = np.interp(grid, old_x, old_y) if old_x else np.zeros(len(grid))
            target = _mix(grid, baseline, values, batch, -1200, 1200, cancel=cancel)
            if np.array_equal(values, target):
                continue
            old_values = dict(zip(xs, ys))
            points = []
            for x, y, style in zip(grid.tolist(), target.tolist(), values.tolist()):
                points.extend((int(x) if x.is_integer() else x, old_values.get(x, y) if y == style else y))
            dest.setdefault('parameters', {})[parameter] = {**(styled or {}), 'mode': 'linear', 'points': points}
        changes.append({'track_index': batch[0].track, 'group_placement': batch[0].group, 'parameter': parameter})
    return changes
