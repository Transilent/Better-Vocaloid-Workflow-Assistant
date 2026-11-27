"""The musical clock used by the tuning editor and its project readers."""

from bisect import bisect_right
from dataclasses import dataclass
import math

SV_QUARTER = 705600000


class TempoMap:
    """Convert musical positions and seconds about content time zero."""

    def __init__(self, tempos):
        if not isinstance(tempos, (list, tuple)) or any(
                not isinstance(row, dict) or type(row.get('position')) is not int for row in tempos):
            raise ValueError('工程速度表的位置格式无效。')
        tempos = sorted(tempos, key=lambda item: item['position'])
        if not tempos or tempos[0]['position'] > 0:
            raise ValueError('工程速度表需要覆盖内容起点。')
        self.positions, self.bpms, self.seconds = [], [], []
        elapsed = 0.
        for item in tempos:
            position, bpm = item['position'], item['bpm']
            if type(position) is not int or type(bpm) not in (int, float) or not math.isfinite(bpm) or bpm <= 0:
                raise ValueError('SVP 速度表包含无效数值。')
            if self.positions:
                if position <= self.positions[-1]:
                    raise ValueError('SVP 速度表包含重复位置。')
                elapsed += (position - self.positions[-1]) / SV_QUARTER * 60 / self.bpms[-1]
            self.positions.append(position)
            self.bpms.append(bpm)
            self.seconds.append(elapsed)
        zero = self.to_seconds(0)
        if zero:
            self.seconds = [seconds - zero for seconds in self.seconds]

    def to_seconds(self, position):
        index = max(0, bisect_right(self.positions, position) - 1)
        return self.seconds[index] + (position - self.positions[index]) / SV_QUARTER * 60 / self.bpms[index]

    def to_position(self, seconds):
        index = max(0, bisect_right(self.seconds, seconds) - 1)
        return round(self.positions[index] + (seconds - self.seconds[index]) * self.bpms[index] / 60 * SV_QUARTER)


@dataclass(frozen=True)
class Meter:
    bar: int
    numerator: int
    denominator: int

    @property
    def beat(self):
        return 4 * SV_QUARTER // self.denominator

    @property
    def length(self):
        return self.beat * self.numerator


def meter_events(rows, bar, numerator, denominator, *, offset=0):
    """Project native meter field names without changing their bar coordinates."""
    if not isinstance(rows, (tuple, list)):
        raise ValueError('工程拍号表格式无效。')
    result = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get(bar)) is not int:
            raise ValueError('工程小节位置无效。')
        result.append({'index': row[bar] - offset, 'numerator': row.get(numerator),
                       'denominator': row.get(denominator)})
    return result


class ProjectClock(TempoMap):
    """Content-relative time, with native tick and project-origin information."""

    def __init__(self, time, *, step=1, native_origin=0, measure_prefix=0):
        super().__init__(time['tempo'])
        if type(measure_prefix) is not int or not 0 <= measure_prefix <= 10_000:
            raise ValueError('工程的预备小节数无效。')
        self.step = step
        self.native_origin = native_origin
        self.measure_prefix = measure_prefix
        signatures = {}
        for row in meter_events(time.get('meter', ()), 'index', 'numerator', 'denominator'):
            index, count, unit = row['index'], row['numerator'], row['denominator']
            if (not -measure_prefix <= index < 2**31 or type(count) is not int or not 1 <= count <= 255
                    or type(unit) is not int or unit < 1 or (4 * SV_QUARTER) % unit):
                raise ValueError('工程拍号或小节位置无效。')
            meter = Meter(index, count, unit)
            if index in signatures and signatures[index] != meter:
                raise ValueError('同一小节包含相互冲突的拍号。')
            signatures[index] = meter
        if not signatures or min(signatures) > -measure_prefix:
            # UFData describes its empty measures separately, using the first
            # content meter. VSQX measure events already use the prelude axis.
            initial = signatures.get(0) if measure_prefix and not native_origin else None
            signatures[-measure_prefix] = Meter(-measure_prefix, initial.numerator if initial else 4,
                                                initial.denominator if initial else 4)
        self.meters = tuple(signatures[index] for index in sorted(signatures))
        self._bars = tuple(meter.bar for meter in self.meters)
        positions = [0]
        for first, last in zip(self.meters, self.meters[1:]):
            positions.append(positions[-1] + (last.bar - first.bar) * first.length)
        i = self._meter_index(0)
        zero = positions[i] - self.meters[i].bar * self.meters[i].length
        self._positions = tuple(position - zero for position in positions)
        self.project_start = self.position_at_bar(-measure_prefix)

    def _meter_index(self, bar):
        return max(0, bisect_right(self._bars, bar) - 1)

    def native_position(self, tick):
        return (tick - self.native_origin) * self.step

    def position_at_bar(self, bar):
        i = self._meter_index(bar)
        meter = self.meters[i]
        return self._positions[i] + (bar - meter.bar) * meter.length

    def bar_at(self, position):
        i = max(0, bisect_right(self._positions, position) - 1)
        return self._bars[i] + (position - self._positions[i]) // self.meters[i].length

    def snap(self, position, division):
        bar = self.bar_at(position)
        start = self.position_at_bar(bar)
        unit = 4 * SV_QUARTER // division
        lower = start + (position - start) // unit * unit
        upper = min(lower + unit, self.position_at_bar(bar + 1))
        return lower if position - lower < upper - position else upper

    def marks(self, start, end, division, *, limit=180):
        """Bound work by visible marks, giving downbeats and signature changes priority."""
        first, last = self.bar_at(start), self.bar_at(end)
        count = last - first + 1
        visible_meters = self.meters[self._meter_index(first):self._meter_index(last) + 1]
        unit = 4 * SV_QUARTER // division
        beat_min = min(meter.beat for meter in visible_meters)
        dense = (end - start) // unit + (end - start) // beat_min + 2 * count <= limit
        beats = dense or (end - start) // beat_min + 2 * count <= limit
        budget = limit if beats else max(1, limit // 2)
        stride = max(1, (count + budget - 1) // budget)
        bars = set(range(first, last + 1, stride))
        changes = [meter.bar for meter in visible_meters if first <= meter.bar <= last]
        bars.update(changes[:max(0, limit - len(bars))])
        output = {}
        for bar in sorted(bars):
            pos = self.position_at_bar(bar)
            meter = self.meters[self._meter_index(bar)]
            if start <= pos <= end:
                label = bar + 1 if bar >= 0 else bar
                signature = f'{meter.numerator}/{meter.denominator}' if bar == meter.bar else ''
                output[pos] = (2, label, signature)
            for spacing, level in ((unit if dense else 0, 0), (meter.beat if beats else 0, 1)):
                if not spacing:
                    continue
                left = max(spacing, ((start - pos + spacing - 1) // spacing) * spacing)
                right = min(meter.length - 1, end - pos)
                for delta in range(left, right + 1, spacing):
                    output[pos + delta] = (level, None, '')
        return [[self.to_seconds(position), *output[position]] for position in sorted(output)]
