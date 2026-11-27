"""Conservative ACE Studio / UFData pitch editing over original project data.

ACE JSON/Zstd and ACEP2 header/CBOR layout follow the bundled LibreSVIP
2.9.2 reader. UFData ticks and step changes follow utaformatix-data v1.
No converter rebuilds the score and no media is loaded during inspection.
"""
from base64 import b64decode, b64encode
from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from io import BytesIO
import json
import math
from pathlib import Path
import struct
from common import ROOT, bundled_export_module
from svp_tuning import Note, Track
from tuning_clock import meter_events


BLICK = 705600000 // 480
FILE_LIMIT = 64 * 1024**2
DECODE_LIMIT = 64 * 1024**2
EDIT_LIMIT = 750_000
CURVE_LIMIT = 350_000
NOTE_LIMIT = 100_000
# Preserve larger authored offsets, but never add style to an extreme pitch.
MAX_EDIT_SEMITONES = 12.

ACE_READ_ONLY = '此 ACEP2 或未验证版本仅可浏览；若要调音，请在 ACE Studio 导出 UFData。'
ACE_HOME = 'ACEP 候选请另存到原工程所在的文件夹，再从 ACE Studio 打开，以保留 Samples 和其他文件引用。'
ACE_BAD = '无法读取此 ACEP；请在 ACE Studio 通过「导出 MIDI」选择 UFData。'
ACE_STYLE = 'ACEP 风格处理只写音高偏移，保留歌手、原表现参数和工程资源。'
UF_STYLE = 'UFData 支持编辑已有音符和音高；歌手、音频和 ACE 专用参数不在交换范围内。'
PITCH_CHANNEL = 'pitchDelta'


def _unique(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError('JSON 包含重复字段。')
        values[key] = value
    return values


def _json(raw):
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('JSON 包含无效数值。')
        return value
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique,
                      parse_float=number, parse_constant=number)


def _bytes(value, bom=False):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8-sig' if bom else 'utf-8')


def _bundled_codec(name):
    try:
        return bundled_export_module(ROOT, name)
    except (ImportError, OSError, RuntimeError) as error:
        raise ValueError('本机缺少 ACEP 解码库，请修复 VWA，或使用 UFData。') from error


def _inflate(payload, expected=None):
    codec = _bundled_codec('backports.zstd')
    try:
        info = codec.get_frame_info(payload)
        size = info.decompressed_size
        if (info.dictionary_id or size is not None and not 0 < size <= DECODE_LIMIT
                or expected is not None and size not in (None, expected)):
            raise ValueError(ACE_BAD)
        decoder = codec.ZstdDecompressor(options={codec.DecompressionParameter.window_log_max: 26})
        content = decoder.decompress(payload, DECODE_LIMIT + 1)
        if (not 0 < len(content) <= DECODE_LIMIT or not decoder.eof or decoder.unused_data
                or expected is not None and len(content) != expected):
            raise ValueError(ACE_BAD)
        return content
    except codec.ZstdError as error:
        raise ValueError(ACE_BAD) from error


def _check_cbor_json(value):
    # CBOR is richer than JSON. Inspect only the known JSON-shaped score with
    # bounded depth and element count; never interpret opaque CBOR tags.
    count = 0
    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > 64 or count > 3_000_000:
            raise ValueError(ACE_BAD)
        if type(item) is dict:
            for key, content in item.items():
                if type(key) is not str:
                    raise ValueError(ACE_BAD)
                visit(content, depth + 1)
        elif type(item) is list:
            for content in item:
                visit(content, depth + 1)
        elif not (item is None or type(item) in (bool, str, int)
                  or type(item) is float and math.isfinite(item)):
            raise ValueError(ACE_BAD)
    visit(value, 0)


def _read_ace(raw):
    if not 0 < len(raw) <= FILE_LIMIT:
        raise ValueError('ACEP 工程为空或超过 64 MiB。')
    if raw.startswith(b'ACEP2'):
        codec = _bundled_codec('cbor2')
        # Read the regular public Zstd payload only; metadata is opaque.
        if len(raw) < 51 or raw[5] != 1:
            raise ValueError(ACE_BAD)
        offset, compressed, decoded = struct.unpack_from('<QQQ', raw, 8)
        metadata_size, = struct.unpack_from('<H', raw, 32)
        if (not 51 + metadata_size <= offset <= min(1024**2, len(raw))
                or raw[34 + metadata_size] != 1
                or not 0 < compressed == len(raw) - offset or not 0 < decoded <= DECODE_LIMIT):
            raise ValueError(ACE_BAD)
        stream = BytesIO(_inflate(raw[offset:], decoded))
        try:
            data = codec.CBORDecoder(stream, max_depth=64, allow_duplicate_keys=False).decode()
            if stream.read(1):
                raise ValueError(ACE_BAD)
            _check_cbor_json(data)
        except (codec.CBORDecodeError, TypeError, ValueError, RecursionError, OverflowError) as error:
            raise ValueError(ACE_BAD) from error
        return {'container': 'acep2', 'project': data}
    try:
        envelope = _json(raw)
        if (not isinstance(envelope, dict) or type(envelope.get('version')) is not int
                or envelope['version'] != 1000 or envelope.get('compressMethod') != 'zstd'
                or envelope.get('salt') not in (None, '') or not isinstance(envelope.get('content'), str)):
            raise ValueError(ACE_BAD)
        payload = b64decode(envelope['content'], validate=True)
    except (UnicodeError, RecursionError, ValueError) as error:
        raise ValueError(ACE_BAD) from error
    try:
        data = _json(_inflate(payload))
    except (UnicodeError, RecursionError, ValueError) as error:
        if str(error) == ACE_BAD or '解码库' in str(error):
            raise
        raise ValueError(ACE_BAD) from error
    return {'container': 'acep-json', 'project': data}


def _objects(value, name, *, default=None, maximum=NOTE_LIMIT):
    rows = value.get(name, default)
    if not isinstance(rows, list) or len(rows) > maximum or any(not isinstance(x, dict) for x in rows):
        raise ValueError(f'调音工程的 {name} 列表无效或超限。')
    return rows


def _ticks(value):
    return type(value) is int and -10**8 < value < 10**8


def _integral_tick(value):
    return _ticks(value) or type(value) is float and -10**8 < value < 10**8 and value.is_integer()


def _tempo(project, native):
    rows = _objects(project, 'tempos', maximum=150_000)
    projected, last = [], -1
    for row in rows:
        pos, bpm = row.get('position' if native else 'tickPosition'), row.get('bpm')
        if native and (row.get('isLerp') not in (None, False) or row.get('bend') not in (None, 0)):
            raise ValueError('暂不能准确对齐 ACEP 连续速度曲线，请从 ACE Studio 导出 UFData。')
        valid_position = _integral_tick(pos) if native else _ticks(pos)
        if (not valid_position or pos <= last or (not projected and pos != 0)
                or type(bpm) not in (int, float) or not 0 < bpm <= 1000):
            raise ValueError('工程速度表需要从第 0 tick 开始，并按位置递增。')
        projected.append({'position': int(pos) * BLICK, 'bpm': bpm})
        last = pos
    if not projected:
        raise ValueError('工程缺少可用于调音的速度表。')
    return projected


@dataclass
class _Part:
    track_index: int
    index: int
    pos: int
    notes: list[Note] = field(default_factory=list)
    protected: list[tuple[int, int]] = field(default_factory=list)
    protection: tuple[str, str] | None = None

    adapted = True

    @property
    def offset(self):
        return self.pos * BLICK

    @property
    def key(self):
        return f'clip:{self.index}'

    def style_track(self, name):
        return {'name': name, 'mainRef': {'blickOffset': self.offset},
                'mainGroup': {'notes': [n.data for n in self.notes], 'parameters': {}}}


def _ace_curve(clip, channel=PITCH_CHANNEL):
    params = clip.get('parameters', {})
    if not isinstance(params, dict):
        return None
    curves = params.get(channel, [])
    if not isinstance(curves, list) or len(curves) > CURVE_LIMIT:
        return None
    spans, total = [], 0
    for row in curves:
        if not isinstance(row, dict) or row.get('type', 'data') != 'data':
            return None
        if (row.get('points') is not None or row.get('pointsVUV') is not None
                or set(row) - {'type','offset','values','points','pointsVUV'}):
            return None
        start, values = row.get('offset'), row.get('values')
        if not _ticks(start) or not isinstance(values, list) or not values:
            return None
        total += len(values)
        if total > 2_000_000 or any(x is not None and (type(x) not in (int, float) or not -128 <= x <= 128)
                                    for x in values):
            return None
        spans.append((start, start + len(values)))
    ordered = sorted(spans)
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
        return None
    return curves


def _uf_curve(track, *, for_display=False):
    data = track.get('pitch')
    if data is None:
        return {'ticks': [], 'values': [], 'isAbsolute': True}
    if not isinstance(data, dict) or set(data) - {'ticks', 'values', 'isAbsolute'}:
        return None
    ts, vs, absolute = data.get('ticks'), data.get('values'), data.get('isAbsolute')
    if (not isinstance(ts, list) or not isinstance(vs, list) or len(ts) != len(vs)
            or len(ts) > CURVE_LIMIT or type(absolute) is not bool):
        return None
    if (any(not _ticks(t) for t in ts) or any(a > b for a, b in zip(ts, ts[1:]))
            or any(not (x is None and absolute or type(x) in (int, float) and -128 <= x <= 128) for x in vs)):
        return None
    # LibreSVIP emits an absolute zero and a pitched value at the same
    # tick to start a segment. Retain both markers, not the last one alone.
    for i, (a, b) in enumerate(zip(ts, ts[1:])):
        if a == b:
            seam = absolute and vs[i] == 0 and vs[i+1] is not None and vs[i+1] > 0
            end = for_display and absolute and vs[i+1] == 0 and vs[i] is not None and vs[i] > 0
            if (not (seam or end) or i > 0 and ts[i-1] == a or i + 2 < len(ts) and ts[i+2] == a):
                return None
    return data


def _notes(row, part, source, *, native, lower=None, upper=None):
    items = _objects(source, 'notes', default=[])
    row.raw_count += len(items)
    for index, item in enumerate(items):
        start, pitch = ((item.get('pos'), item.get('pitch')) if native
                        else (item.get('tickOn'), item.get('key')))
        dur = item.get('dur') if native else None
        end = (start + dur if _ticks(start) and _ticks(dur) else None) if native else item.get('tickOff')
        lyric = item.get('lyric', '')
        if (not _ticks(start) or not _ticks(end) or end <= start or type(pitch) is not int
                or not 0 <= pitch <= 127 or not isinstance(lyric, str)):
            raise ValueError('ACEP / UFData 里有不能定位的音符，未打开工程。')
        a = max(start, lower, -part.pos) if native else start
        b = min(end, upper) if native else end
        if a >= b:
            continue
        if (a, b) != (start, end):
            part.protection = part.protection or ('ace_clipped_note',
                'ACEP 裁剪穿过音符；此片段保持只读。')
        raw = {'onset': a * BLICK, 'duration': (b - a) * BLICK, 'pitch': pitch, 'lyrics': lyric}
        if item.get('muted') is True:
            raw['attributes'] = {'muted': True}
            part.protected.append((a, b))
        vibrato = item.get('vibrato') if native else None
        if vibrato and (not isinstance(vibrato, dict) or vibrato.get('amplitude', 0) or not set(vibrato) <= {
                            'start', 'amplitude', 'frequency', 'phase', 'startPos', 'releaseLevel',
                            'releaseRatio', 'attackLevel', 'attackRatio', 'attackLen', 'releaseLen', 'releaseVol'}):
            part.protected.append((a, b))
        part.notes.append(Note(index, raw, part.offset + raw['onset'], (part.pos + b) * BLICK, pitch))


class _Layout:
    step = BLICK

    def __init__(self, data, native):
        project = data.get('project')
        if not isinstance(project, dict):
            raise ValueError('ACEP / UFData 缺少可用的工程数据。')
        self.native, self.project = native, project
        version = project.get('version') if native else data.get('formatVersion')
        if type(version) is not int or not 0 < version < 10000:
            raise ValueError('ACEP / UFData 工程版本无效。')
        editable = (data['container'] == 'acep-json' and version == 9
                    and type(project.get('versionRevision', 0)) is int and project.get('versionRevision', 0) == 0
                    if native else version == 1)
        self.version = (('2 / ' if data['container'] == 'acep2' else 'JSON / ') if native else '') + str(version)
        self.notice = (ACE_HOME if editable else ACE_READ_ONLY) if native else UF_STYLE
        tracks = _objects(project, 'tracks', maximum=512)
        ids = Counter(clip['uuid'] for track in tracks if native and track.get('type') == 'sing'
                      for clip in _objects(track,'patterns',default=[],maximum=10_000)
                      if isinstance(clip.get('uuid'), str) and clip['uuid'])
        self.tracks = tuple(Track(i, native and track.get('type') != 'sing', native_refs=True)
                            for i, track in enumerate(tracks))
        self.measure_prefix = 0 if native else project.get('measurePrefix', 0)
        meters = meter_events(project.get('timeSignatures', []), 'barPos' if native else 'measurePosition',
                              'numerator', 'denominator')
        if native and not meters and 'beatsPerBar' in project:
            meters = [{'index': 0, 'numerator': project['beatsPerBar'], 'denominator': 4}]
        self.score = {'time': {'tempo': _tempo(project, native), 'meter': meters}, 'tracks': [
            {'name': t.get('name') if isinstance(t.get('name'), str) and t['name'] else f'轨道 {row.index + 1}',
             'mainRef': {'isInstrumental': row.instrumental}} for t, row in zip(tracks, self.tracks)]}
        count = 0
        for row, track in zip(self.tracks, tracks):
            if row.instrumental:
                continue
            if not editable:
                row.issues['track.format_version'] = (ACE_READ_ONLY if native else '此 UFData 版本尚未验证写入，当前只读。')
            if native:
                for index, clip in enumerate(_objects(track, 'patterns', default=[], maximum=10_000)):
                    notes = _objects(clip, 'notes', default=[])
                    if not notes:
                        continue
                    pos, lower = clip.get('pos'), clip.get('clipPos', 0)
                    duration = clip.get('clipDur', clip.get('dur'))
                    if (clip.get('timeUnit', 'tick') != 'tick'
                            or not all(_integral_tick(x) for x in (pos,lower,duration)) or duration <= 0):
                        raise ValueError('ACEP 演唱片段的时基或裁剪边界无法安全读取，请导出 UFData。')
                    part = _Part(row.index, index, int(pos))
                    uid = clip.get('uuid')
                    enabled = clip.get('enabled', True)
                    if (enabled is not True and enabled is not None) or isinstance(uid, str) and ids[uid] > 1:
                        part.protection = ('ace_disabled_or_shared', '停用或共用的 ACEP 片段保持只读。')
                    _notes(row, part, clip, native=True, lower=int(lower), upper=int(lower+duration))
                    if editable and not part.protection and _ace_curve(clip) is None:
                        part.protection = ('ace_pitch_controls', 'ACEP 已有音高锚点或未知曲线，此片段保持只读。')
                    if part.notes:
                        row.placements.append(part)
                    if row.raw_count + count > NOTE_LIMIT:
                        raise ValueError('调音工程的音符数超限。')
            else:
                part = _Part(row.index, 0, 0)
                _notes(row, part, track, native=False)
                if _uf_curve(track) is None:
                    part.protection = ('ufdata_pitch_controls', 'UFData 的音高曲线形状未知，此轨保持只读。')
                if part.notes:
                    row.placements.append(part)
            count += row.raw_count
            if count > NOTE_LIMIT:
                raise ValueError('调音工程的音符数超限。')
            spans = sorted((n.start, n.end, p.index, n.index, p) for p in row.placements for n in p.notes)
            max_end = -math.inf
            for i, (start, end, _, _, part) in enumerate(spans):
                if start < max_end or (i + 1 < len(spans) and spans[i + 1][0] < end):
                    part.protection = part.protection or ('ace_overlap', '重叠音符所在片段保持只读。')
                max_end = max(max_end, end)
            if not row.style_placements:
                row.issues.setdefault('track.no_editable_clip', next((p.protection[1] for p in row.placements
                    if p.protection), '轨道没有可安全定位的音符。'))


def _delta(part, curve, clock, regions):
    import numpy as np
    points = curve['points']
    xs = np.asarray(points[::2], dtype=np.float64) / BLICK
    ys = np.asarray(points[1::2], dtype=np.float64) / 100.
    start, end = math.floor(xs[0]), math.ceil(xs[-1])+1
    if end - start > EDIT_LIMIT:
        return
    grid = np.arange(start, end, dtype=np.int64)
    amount = np.interp(grid, xs, ys, left=0., right=0.)
    if regions:
        keep = np.zeros(len(grid), dtype=bool)
        for region in regions:
            if region.get('track_index', part.track_index) != part.track_index:
                continue
            left, right = (clock.to_position(region[k]) for k in ('start', 'end'))
            lo = max(0, (left - part.offset + BLICK - 1)//BLICK - start)
            hi = min(len(grid), (right - part.offset + BLICK - 1)//BLICK - start)
            if lo < hi:
                keep[lo:hi] = True
        amount[~keep] = 0.
    keys = np.full(len(grid), -1, dtype=np.int16)
    for note in part.notes:
        local_start = note.data['onset'] // BLICK
        local_end = local_start + note.data['duration'] // BLICK
        lo, hi = max(0, local_start - start), min(len(grid), local_end - start)
        if lo < hi:
            keys[lo:hi] = note.pitch
    for a, b in part.protected:
        lo, hi = max(0, a-start), min(len(grid), b-start)
        if lo < hi:
            keys[lo:hi] = -1
    # No new authored pitch in rests, in protected notes, or at the exit.
    amount[keys < 0] = 0.
    amount[-1] = 0.
    return grid, amount, keys


def _native_pitch(original, grid, amount, *, neutral=0., limits=(-MAX_EDIT_SEMITONES, MAX_EDIT_SEMITONES)):
    import numpy as np
    from itertools import groupby
    start = int(grid[0])
    low, high = limits
    used = np.zeros(len(grid), dtype=bool)
    output = deepcopy(original)
    edited = 0
    for row in output:
        values = row['values']
        offset = row['offset']
        lo, hi = max(0, offset-start), min(len(grid), offset+len(values)-start)
        if lo >= hi:
            continue
        used[lo:hi] = True
        for i in np.flatnonzero(np.abs(amount[lo:hi]) > 1e-9) + lo:
            j = start + int(i) - offset
            previous = values[j]
            if previous is None:
                continue
            value = round(previous + float(amount[i]),6)
            if low <= previous <= high and low <= value <= high and value != previous:
                values[j] = value
                edited += 1
    new_values = amount + neutral
    new = np.flatnonzero((~used) & (np.abs(amount) >= .000001) & (low <= new_values) & (new_values <= high)).tolist()
    for _, group in groupby(enumerate(new), key=lambda item:item[1]-item[0]):
        run = [i for _, i in group]
        output.append({'type':'data','offset':start+run[0],
                       'values':[round(float(new_values[i]),6) for i in run]})
    if not new and not edited:
        return None, 'no_native_pitch_delta', 0
    if len(output) > CURVE_LIMIT or sum(len(row['values']) for row in output) > 2_000_000:
        return None, 'pitch_capacity', 0
    # Keep the original curve order if it was noncanonical and no new segment
    # is needed. New data could otherwise silently reorder that old topology.
    if new and any(a['offset'] > b['offset'] for a,b in zip(original,original[1:])):
        return None, 'ace_unsorted_original_pitch', 0
    if new:
        output.sort(key=lambda row: row['offset'])
    return output, '', edited+len(new)


def _exchange_pitch(original, grid, amount, keys):
    import numpy as np
    ticks, values = original['ticks'], original['values']
    hi = int(grid[-1])
    absolute = original['isAbsolute']
    default = None if absolute else 0.
    original_points = {}
    for tick, value in zip(ticks, values):
        original_points.setdefault(tick, []).append(value)
    output = dict(original_points)
    def baseline(tick):
        p = bisect_right(ticks, tick)-1
        return values[p] if p >= 0 else default
    edited = 0
    for i in np.flatnonzero(np.abs(amount) > 1e-9):
        tick = int(grid[i])
        old = original_points.get(tick, ())
        if len(old) > 1:
            continue
        base = baseline(tick)
        # Absolute zero is the observed legacy break marker. Never bend
        # it into a near-zero pitch, or generate that frequency in a rest.
        if absolute and base == 0:
            continue
        # A null absolute span is the default note pitch at that tick.
        key = int(keys[i]) if absolute else 0
        base = key if base is None else base
        value = round(base + float(amount[i]), 6)
        if (abs(base-key) > MAX_EDIT_SEMITONES or abs(value-key) > MAX_EDIT_SEMITONES
                or absolute and not 0 < value <= 127 or value == base):
            continue
        output[tick] = [value]
        if tick < hi:
            output.setdefault(tick+1, [baseline(tick+1)])
        edited += 1
    if not edited:
        return None, 'no_exchange_pitch_delta', 0
    if sum(map(len, output.values())) > CURVE_LIMIT:
        return None, 'pitch_capacity', 0
    last, kept = default, []
    for tick, current in sorted(output.items()):
        if current[-1] != last or tick in original_points:
            kept.extend((tick, value) for value in current)
        last = current[-1]
    return {'isAbsolute':absolute,'ticks':[t for t,_ in kept],'values':[v for _,v in kept]}, '', edited


def _ace_pitch_samples(curves, grid, _keys):
    import numpy as np

    values = np.zeros(len(grid), dtype=float)
    for row in curves:
        offset = row['offset']
        lo, hi = np.searchsorted(grid, [offset, offset + len(row['values'])])
        if lo < hi:
            indexes = grid[lo:hi] - offset
            values[lo:hi] = (row['values'][indexes[0]:indexes[-1] + 1]
                             if indexes[-1] - indexes[0] + 1 == len(indexes)
                             else [row['values'][index] for index in indexes])
    return np.nan_to_num(values), np.isnan(values)


def _uf_pitch_samples(curve, grid, keys):
    """Sample v1 held values, protecting zero breaks and duplicate start markers."""
    import numpy as np

    absolute = curve['isAbsolute']
    ticks = np.asarray(curve['ticks'], dtype=np.int64)
    owners = np.searchsorted(ticks, grid, side='right')
    values = np.asarray([None if absolute else 0., *curve['values']], dtype=float)[owners]
    protected = np.zeros(len(grid), dtype=bool)
    if absolute:
        protected |= values == 0
        duplicates = ticks[1:][ticks[1:] == ticks[:-1]]
        slots = np.searchsorted(grid, duplicates)
        valid = slots < len(grid)
        protected[slots[valid][grid[slots[valid]] == duplicates[valid]]] = True
        values = np.where(np.isnan(values), 0., values - keys)
    return values, protected


def apply_manual_pitch(original, dest, format, spans, *, cancel):
    """Reuse the native and exchange writers after replacing the style contribution."""
    import numpy as np
    from tuning_pitch_math import CAPACITY, LIMIT, PROTECTED, mix_pitch_samples, pitch_tick_grid

    native = format == 'acep'
    read_curve, samples = (_ace_curve, _ace_pitch_samples) if native else (_uf_curve, _uf_pitch_samples)
    old, styled = read_curve(original), read_curve(dest)
    if old is None or styled is None or not native and old['isAbsolute'] != styled['isAbsolute']:
        raise ValueError(PROTECTED)
    grid = pitch_tick_grid(spans, EDIT_LIMIT)
    keys = np.full(len(grid), -1, dtype=np.int16)
    for span in spans:
        keys[span.left - grid[0]:span.right - grid[0] + 1] = span.key
    baseline, original_protected = samples(old, grid, keys)
    current, style_protected = samples(styled, grid, keys)
    if np.any((original_protected | style_protected) & (keys >= 0)):
        # ACE nulls and UFData's explicit break markers are not pitch zeroes.
        raise ValueError('笔触或过渡经过原生音高断点，请把笔触移离断点。')
    limit = MAX_EDIT_SEMITONES * 100
    target = mix_pitch_samples(grid, baseline * 100., current * 100., spans, -limit, limit, cancel=cancel) / 100.
    changed = np.abs(target - current) >= .000001
    parameter = PITCH_CHANNEL if native else 'pitch'
    if not changed.any():
        return False, parameter
    if np.any(np.abs(current[changed]) > MAX_EDIT_SEMITONES) or (not native and styled['isAbsolute']
            and np.any((target[changed] + keys[changed] <= 0) | (target[changed] + keys[changed] > 127))):
        raise ValueError(LIMIT)
    amount = np.where(changed, target - current, 0.)
    if native:
        replacement, reason, _ = _native_pitch(styled, grid, amount)
    else:
        replacement, reason, _ = _exchange_pitch(styled, grid, amount, keys)
    if replacement is None:
        raise ValueError(CAPACITY if reason == 'pitch_capacity' else PROTECTED)
    readback, protected = samples(replacement, grid, keys)
    if not np.array_equal(protected, style_protected) or not np.allclose(readback, target, rtol=0., atol=.00000101):
        raise ValueError('音高候选回读与手绘目标不一致，未写入候选。')
    if native:
        dest.setdefault('parameters', {})[parameter] = replacement
    else:
        dest[parameter] = replacement
    return True, parameter


class _PitchAdapter:
    def __init__(self, native):
        self.native = native
        self.format = 'acep' if native else 'ufdata'
        self.STYLE_ONLY = ACE_STYLE if native else UF_STYLE

    def read(self, raw):
        if self.native:
            return _read_ace(raw)
        if not 0 < len(raw) <= FILE_LIMIT:
            raise ValueError('UFData 工程为空或超过 64 MiB。')
        try:
            data = _json(raw)
        except (UnicodeError, RecursionError, ValueError) as error:
            raise ValueError('请选择有效的 UFData 工程。') from error
        if not isinstance(data, dict):
            raise ValueError('请选择有效的 UFData 工程。')
        return data

    def encode(self, output, raw):
        bom = raw.startswith(b'\xef\xbb\xbf')
        if self.native:
            if output['container'] != 'acep-json':
                raise ValueError(ACE_READ_ONLY)
            envelope = _json(raw)
            payload = _bytes(output['project'])
            if len(payload) > DECODE_LIMIT:
                raise ValueError('调音后的 ACEP 展开内容超过 64 MiB。')
            codec = _bundled_codec('backports.zstd')
            envelope['content'] = b64encode(codec.compress(payload)).decode('ascii')
            encoded = _bytes(envelope, bom)
        else:
            encoded = _bytes(output, bom)
        if len(encoded) > FILE_LIMIT:
            raise ValueError('调音后的工程文件超过 64 MiB。')
        return encoded

    def Layout(self, data):
        return _Layout(data, self.native)

    def selected_indices(self, data, options, *, layout=None):
        from tuning_plan_core import selected_indices
        layout = self.Layout(data) if layout is None else layout
        if options['expression_mode'] != 'preserve':
            raise ValueError(self.STYLE_ONLY)
        return selected_indices(layout.score, options, layout=layout)

    def process_project(self, data, options, *, cancel=lambda:None, progress=lambda _text:None):
        from tuning_plan_core import StyleTargets, VERSION, _has_incomplete, apply_style_plan, digest, settings
        from vocal_expression import TempoMap
        options = settings(options)
        layout = self.Layout(data)
        indices = self.selected_indices(data, options, layout=layout)
        regions = options['style_regions']
        if any(r.get('track_index') not in (*indices, None) for r in regions):
            raise ValueError('风格片段包含本次未选中的演唱轨。')
        parts = tuple(p for i in indices for p in layout.tracks[i].style_placements)
        targets = StyleTargets({'time':layout.score['time'], 'tracks':[
            p.style_track(layout.score['tracks'][p.track_index]['name']) for p in parts]}, parts)
        style, attempted = apply_style_plan(targets, options, indices, controls=set(), cancel=cancel, progress=progress)
        attempted = attempted and ((not regions and (options['style_pitch'] or
            options['style_technique']!='none' and options['technique_pitch']))
            or any(r.get('pitch') or r.get('technique_pitch') and r.get('technique','none')!='none' for r in regions))
        skipped, changes, allowed = [], [], []
        clock = TempoMap(layout.score['time']['tempo'])
        if attempted:
            for index in indices:
                for part in layout.tracks[index].placements:
                    if part.protection and part.notes and (not regions or any(
                            r.get('track_index',index) == index and any(clock.to_seconds(n.start)<r['end']
                            and clock.to_seconds(n.end)>r['start'] for n in part.notes) for r in regions)):
                        skipped.append({'track_index':index, 'group_placement':part.key,
                                        'reason':part.protection[0], 'message':part.protection[1],
                                        'parameter':PITCH_CHANNEL, 'status':'skipped'})
        output = deepcopy(data)
        for part, styled in zip(parts, targets.project['tracks']):
            cancel()
            curve = styled['mainGroup']['parameters'].get(PITCH_CHANNEL)
            if not curve:
                continue
            source = layout.project['tracks'][part.track_index]
            dest = output['project']['tracks'][part.track_index]
            row = {'track_index':part.track_index, 'group_placement':part.key,
                   'parameter':PITCH_CHANNEL if self.native else 'pitch'}
            sampled = _delta(part, curve, clock, regions)
            if sampled is None:
                skipped.append({**row,'reason':'pitch_capacity','status':'skipped'})
                continue
            grid, amount, keys = sampled
            if self.native:
                source, dest = source['patterns'][part.index], dest['patterns'][part.index]
                replacement, why, count = _native_pitch(_ace_curve(source),grid,amount)
                if replacement is not None:
                    dest.setdefault('parameters',{})[PITCH_CHANNEL] = replacement
            else:
                replacement, why, count = _exchange_pitch(_uf_curve(source),grid,amount,keys)
                if replacement is not None:
                    dest['pitch'] = replacement
            if why:
                skipped.append({**row,'reason':why,'status':'skipped'})
            else:
                allowed.append((part.track_index, part.index))
                changes.append({**row,'changed_samples':count})
        protected = deepcopy(output)
        for index, part_index in allowed:
            source = layout.project['tracks'][index]
            dest = protected['project']['tracks'][index]
            if self.native:
                old = source['patterns'][part_index]
                clip = dest['patterns'][part_index]
                if 'parameters' not in old:
                    del clip['parameters']
                elif PITCH_CHANNEL not in old['parameters']:
                    del clip['parameters'][PITCH_CHANNEL]
                else:
                    clip['parameters'][PITCH_CHANNEL] = old['parameters'][PITCH_CHANNEL]
            elif 'pitch' in source:
                dest['pitch'] = source['pitch']
            else:
                del dest['pitch']
        if protected != data:
            raise ValueError('音高适配改变了保护范围之外的工程内容。')
        incomplete = bool(skipped) or attempted and _has_incomplete(style)
        return output, {'schema':VERSION,'format':self.format,'mode':'preserve','track_indices':indices,
                        'style':style,'curve_changes':changes,'native_curve_skips':skipped,
                        'status':'partial' if changes and incomplete else 'done' if changes else 'unchanged',
                        'changed':bool(changes),'pitch_changed':bool(changes),
                        'notes_and_timing_unchanged':True,'protected_content_unchanged':True,
                        'source_content_sha256':digest(data),'output_content_sha256':digest(output),
                        'original_input_unchanged':True,'external_review':'save_beside_source' if self.native else 'import_ufdata',
                        'project_writes':0,'final_density_passes':0,'synthesized':False,'listened':False,
                        'acoustic_improvement_verified':False}


ACEP = _PitchAdapter(native=True)
UFDATA = _PitchAdapter(native=False)
