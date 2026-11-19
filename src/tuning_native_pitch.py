"""Viewport rendering of authored pitch controls, independent of write targets."""

from dataclasses import dataclass
import math

import numpy as np

from calibration_refinement import _curve_points
from tuning_native import at_path


POINT_BUDGET = 128


@dataclass(frozen=True)
class Curve:
    mode: str
    positions: np.ndarray
    values: np.ndarray
    baseline: float = 0.


def _curve(mode, positions, values, baseline=0.):
    return Curve(mode, np.asarray(positions), np.asarray(values, dtype=float), baseline)


def _pairs(values):
    return (list(zip(values[::2], values[1::2])) if isinstance(values, list) and len(values) % 2 == 0
            else [])


def _number(value):
    return type(value) in (int, float) and -2**62 < value < 2**62


def _anchors(pairs, scale=1.):
    points = sorted((x, y * scale) for x, y in pairs if _number(x) and _number(y))
    if not points:
        return None
    xs, ys = zip(*points)
    return _curve('anchors', xs, ys)


def _collect(data, format, part):
    if format == 'svp':
        params = part.get('parameters')
        param = params.get('pitchDelta') if isinstance(params, dict) else None
        result = []
        try:
            points = _curve_points(param, track_index=None, channel='pitchDelta', source='style') if param is not None else []
        except ValueError:
            points = []
        if points:
            xs, ys = zip(*points)
            result.append(_curve('linear' if param.get('mode') == 'linear' else 'anchors', xs, ys))
        controls = part.get('pitchControls')
        anchors = []
        for item in controls if isinstance(controls, list) else []:
            if not isinstance(item, dict) or not _number(item.get('pos')) or not _number(item.get('pitch')):
                continue
            pos, key = item['pos'], item['pitch']
            if item.get('type') == 'point':
                anchors.append((pos, key))
            elif item.get('type') == 'curve':
                anchors.extend((pos + x, key + y) for x, y in _pairs(item.get('points'))
                               if _number(x) and _number(y))
        if anchors:
            xs, ys = zip(*sorted(anchors))
            result.append(_curve('absolute_anchors', xs, np.asarray(ys) * 100.))
        return tuple(result)
    if format == 'ustx':
        from ustx_tuning import _native_curve
        _, points, _ = _native_curve(data, part, 'pitd')
        return (_curve('ustx', points['xs'], points['ys']),) if points and points['xs'] else ()
    if format in ('vpr', 'vsqx'):
        from vpr_tuning import PBS, PIT, _curve as controller
        from vocaloid_pitch import PBS_SPEC, PIT_SPEC, step_values
        _, bend = controller(part, PIT)
        _, sensitivity = controller(part, PBS)
        if not bend or not bend['events'] or not sensitivity:
            return ()
        ticks = np.asarray(sorted({row['pos'] for stream in (bend, sensitivity) for row in stream['events']}))
        values = step_values(ticks, bend['events'], PIT_SPEC[0]) * step_values(ticks, sensitivity['events'], PBS_SPEC[0])
        return (_curve('held', ticks, values * (100. / 8192.)),)
    if format == 'acep':
        from ace_tuning import _ace_curve, PITCH_CHANNEL
        params = part.get('parameters')
        if not isinstance(params, dict):
            return ()
        segments = params.get(PITCH_CHANNEL, [])
        voiced = _ace_curve({'parameters': {PITCH_CHANNEL: params.get('vuv', [])}}) or []
        voiced = [(row['offset'], np.asarray(row['values']) == 0) for row in voiced]
        result = []
        for row in segments if isinstance(segments, list) else []:
            if not isinstance(row, dict):
                continue
            if row.get('type') == 'anchor':
                if curve := _anchors(_pairs(row.get('points')), 100.):
                    result.append(curve)
            elif _ace_curve({'parameters': {PITCH_CHANNEL: [row]}}):
                start, values = row['offset'], row['values']
                end = start + len(values)
                values = np.asarray(values, dtype=float) * 100.
                for origin, silent in voiced:
                    a, b = max(start, origin), min(end, origin + len(silent))
                    if a < b:
                        values[a - start:b - start][silent[a - origin:b - origin]] = np.nan
                result.append(_curve('sampled', np.arange(start, end), values))
        return tuple(result)
    from ace_tuning import _uf_curve
    raw = _uf_curve(part, for_display=True)
    if raw and raw['ticks']:
        values = np.asarray(raw['values'], dtype=float) * 100.
        if raw['isAbsolute']:
            # Zero is a break in LibreSVIP's repeated-tick segments. In other
            # native UFData, numeric zero is an actual absolute key.
            if any(a == b for a, b in zip(raw['ticks'], raw['ticks'][1:])):
                values[values == 0.] = np.nan
            return (_curve('absolute', raw['ticks'], values, baseline=np.nan),)
        return (_curve('held', raw['ticks'], values),)
    return ()


def _reduce(xs, ys):
    """Retain boundary values, gaps and both extrema within a visual bucket."""
    if len(xs) <= POINT_BUDGET:
        return xs, ys
    keep = []
    # Bin by time, since multiple samples can occupy the exact same instant.
    span = xs[-1] - xs[0]
    if not span:
        return xs[:POINT_BUDGET], ys[:POINT_BUDGET]
    boundaries = np.searchsorted(xs, np.linspace(xs[0], xs[-1], POINT_BUDGET // 6 + 1))
    boundaries[-1] = len(xs)
    for a, b in zip(boundaries, boundaries[1:]):
        if a == b:
            continue
        chunk = ys[a:b]
        valid = np.flatnonzero(np.isfinite(chunk))
        indices = {a, b - 1}
        if valid.size:
            indices.update((a + int(valid[np.argmin(chunk[valid])]), a + int(valid[np.argmax(chunk[valid])])))
        missing = np.flatnonzero(~np.isfinite(chunk))
        if missing.size:
            indices.update((a + int(missing[0]), a + int(missing[-1])))
        keep.extend(sorted(indices))
    return xs[keep], ys[keep]


def _viewport(curve, first, last, key):
    xs, ys = curve.positions, curve.values
    if curve.mode == 'sampled':
        first, last = max(first, xs[0]), min(last, xs[-1])
    if first > last:
        return (), ()
    a, b = np.searchsorted(xs, [first, last])
    if curve.mode in ('absolute', 'held'):
        # Search the last point at a tick and keep both sides of each jump.
        visible = np.unique(xs[np.searchsorted(xs, first, side='right'):np.searchsorted(xs, last, side='right')])
        owners = np.searchsorted(xs, visible, side='right')
        first_value = np.searchsorted(xs, first, side='right')
        grid = np.r_[first, np.repeat(visible, 2), last]
        indices = np.r_[first_value, np.column_stack((np.searchsorted(xs, visible), owners)).ravel(),
                        owners[-1] if owners.size else first_value]
        values = np.full(indices.shape, curve.baseline)
        known = indices > 0
        values[known] = ys[indices[known] - 1]
        if curve.mode == 'absolute':
            values -= key * 100.
    elif curve.mode in ('anchors', 'absolute_anchors', 'sampled'):
        hi = np.searchsorted(xs, last, side='right')
        values = ys[a:hi]
        if curve.mode == 'absolute_anchors':
            values = values - key * 100.
        return _reduce(xs[a:hi], values)
    else:
        grid = np.unique(np.r_[np.linspace(first, last, POINT_BUDGET), xs[a:b]])
        if curve.mode == 'ustx':
            from ustx_tuning import _native_samples
            grid = np.unique(np.rint(grid).astype(np.int64))
            grid = grid[(grid >= first) & (grid <= last)]
            values = _native_samples(grid, xs, ys)
        else:
            values = np.interp(grid, xs, ys)
    return _reduce(grid, values)


def _note_controls(format, raw, start, end, clock):
    """Read note-local control geometry. Authored controls remain separate layers."""
    if format == 'vpr':
        items = raw.get('directPitches')
        if isinstance(items, list):
            rows = [(item.get('pos'), item.get('value')) for item in items if isinstance(item, dict)]
            if curve := _anchors(rows):
                origin = clock.to_position(start)
                yield curve, lambda local: clock.to_seconds(origin + local * clock.step)
        return
    if format != 'ustx':
        return
    pitch = raw.get('pitch')
    if isinstance(pitch, dict) and isinstance(pitch.get('data'), list):
        rows = [(node.get('x'), node.get('y')) for node in pitch['data'] if isinstance(node, dict)]
        if curve := _anchors(rows, 10.):
            yield curve, lambda ms: start + ms / 1000.
    vib = raw.get('vibrato')
    if not isinstance(vib, dict):
        return
    length, period, depth, fade_in, fade_out, shift, drift = (
        vib.get(name, 0) for name in ('length', 'period', 'depth', 'in', 'out', 'shift', 'drift'))
    if (not all(_number(x) for x in (length, period, depth, fade_in, fade_out, shift, drift))
            or not 0 < length <= 100 or not 1 <= period <= 2000 or not 0 < depth <= 1200
            or not 0 <= fade_in <= 100 or not 0 <= fade_out <= 100):
        return
    left, right = clock.to_position(start), clock.to_position(end)
    if right <= left:
        return
    fraction = length / 100.
    x = np.linspace(1. - fraction, 1., POINT_BUDGET)
    local_period = period / ((end - start) * 1000.)
    values = depth * (np.sin(2 * np.pi * ((x - (1. - fraction)) / local_period + shift / 100.)) + drift / 100.)
    if fade_in:
        values *= np.clip((x - (1. - fraction)) / (fraction * fade_in / 100.), 0., 1.)
    if fade_out:
        values *= np.clip((1. - x) / (fraction * fade_out / 100.), 0., 1.)
    yield _curve('vibrato', x, values), lambda fraction: clock.to_seconds(left + (right - left) * fraction)


class NativePitch:
    def __init__(self, native, rows, data):
        self.native = native
        self.clock = native.clock
        self.parts = {identity: (part, row['note_index']) for identity, row in rows.items()
                      if (part := native.parts.get((row['track_index'], row.get('group_placement', 'main')))) is not None}
        self.original = self.curves(data, {part.path for part, _ in self.parts.values()})

    def curves(self, data, paths, *, cancel=lambda: None):
        result = {}
        for path in sorted(paths):
            cancel()
            if curves := _collect(data, self.native.format, at_path(data, path)):
                result[path] = curves
        return result

    def plot(self, notes, start, end, styled):
        output = {'original': [], 'style': []}
        for note in notes[:300]:
            identity = note['id']
            item = self.parts.get(identity)
            if item is None:
                continue
            part, index = item
            left, right = max(start, note['start']), min(end, note['end'])
            if right <= left:
                continue
            offset, step = part.placement.offset, self.clock.step
            first, last = ((self.clock.to_position(t) - offset) / step for t in (left, right))
            for name, source in (('original', self.original), ('style', styled)):
                for curve in source.get(part.path, ()):
                    key = note['pitch']
                    if not _number(key):
                        continue
                    if curve.mode == 'absolute_anchors':
                        key -= part.placement.ref.get('pitchOffset', 0)
                    xs, ys = _viewport(curve, first, last, key)
                    points = [[self.clock.to_seconds(offset + step * float(x)), float(y)] if math.isfinite(y) else None
                              for x, y in zip(xs, ys)]
                    if points:
                        output[name].append({'noteId': identity, 'points': points,
                                             'anchors': curve.mode in ('anchors', 'absolute_anchors')})
            for curve, to_seconds in _note_controls(self.native.format, part.notes[index],
                                                    note['start'], note['end'], self.clock):
                xs, ys = _reduce(curve.positions, curve.values)
                points = []
                for x, y in zip(xs, ys):
                    time = to_seconds(float(x))
                    if start <= time <= end:
                        points.append([time, float(y)])
                if points:
                    output['original'].append({'noteId': identity, 'points': points,
                                               'anchors': curve.mode == 'anchors'})
        return output
