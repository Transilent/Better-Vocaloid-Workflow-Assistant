"""Pure pitch/curve math helpers shared by vocal expression and native pitch layers."""
from __future__ import annotations

CURVE_TOLERANCE_DB = .12
PROTECTED = '此音符或片段的原生音高受保护。'
CAPACITY = '手绘音高超过当前工程曲线容量。'
LIMIT = '手绘与原曲线合成后超出音高范围；请缩小偏移。'


def simplify(points, tolerance=CURVE_TOLERANCE_DB):
    """Keep endpoints and bound the vertical error of linear interpolation in dB."""
    import numpy as np

    if len(points) < 3:
        return points
    data = np.asarray(points, dtype=np.float64)
    keep, pending = {0, len(points) - 1}, [(0, len(points) - 1)]
    while pending:
        left, right = pending.pop()
        if right <= left + 1:
            continue
        estimated = (
            data[left, 1]
            + (data[left + 1:right, 0] - data[left, 0])
            / (data[right, 0] - data[left, 0])
            * (data[right, 1] - data[left, 1])
        )
        errors = np.abs(estimated - data[left + 1:right, 1])
        offset = int(np.argmax(errors))
        if errors[offset] > tolerance:
            split = left + 1 + offset
            keep.add(split)
            pending.extend([(left, split), (split, right)])
    return [points[index] for index in sorted(keep)]


def mix_pitch_samples(grid, native, styled, spans, low, high, *, cancel):
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


def pitch_tick_grid(spans, limit):
    import numpy as np

    lo = min(span.left for span in spans)
    hi = max(span.right for span in spans)
    if hi - lo + 1 > limit:
        raise ValueError(CAPACITY)
    return np.arange(lo, hi + 1, dtype=np.int64)
