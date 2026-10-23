"""Reference-relative dynamics for generated SVP files; no synthesis model needed."""
import bisect
import copy
import json
import math
from pathlib import Path

ALGORITHM_VERSION = 3
SV_QUARTER = 705600000
HOP_SECONDS = .02
WINDOW_SECONDS = .06
CURVE_TOLERANCE_DB = .12
CONTROL_NAMES = {'loudness': '力度', 'tension': '张力', 'breathiness': '气声', 'voicing': '发声强度'}
SKIP_REASONS = {'no_notes_or_zero_strength': '没有有效音符或强度为零',
                'insufficient_voiced_signal': '有效有声片段不足',
                'insufficient_sustained_variation': '有效变化的持续时间不足',
                'constant_or_unreliable': '未检测到可靠变化',
                'below_output_resolution': '变化小于输出精度'}


def settings(options):
    enabled = options.get('reference_dynamics', False)
    strength = options.get('dynamics_strength', .6)
    if type(enabled) is not bool or type(strength) not in (int, float) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('参考演唱力度的强度应为 0–100%。')
    return enabled, float(strength)


def expression_settings(options):
    from vocal_timbre import settings as timbre_settings
    dynamics, strength = settings(options)
    return dynamics, strength, timbre_settings(options)


class TempoMap:
    """Convert generated SVP's absolute blicks and audio seconds without rounding beats."""
    def __init__(self, tempos):
        tempos = sorted(tempos, key=lambda item: item['position'])
        if not tempos or tempos[0]['position'] != 0:
            raise ValueError('SVP 速度表需要从工程起点开始。')
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

    def to_seconds(self, position):
        index = max(0, bisect.bisect_right(self.positions, position) - 1)
        return self.seconds[index] + (position - self.positions[index]) / SV_QUARTER * 60 / self.bpms[index]

    def to_position(self, seconds):
        index = max(0, bisect.bisect_right(self.seconds, seconds) - 1)
        return round(self.positions[index] + (seconds - self.seconds[index]) * self.bpms[index] / 60 * SV_QUARTER)


def energy_frames(path, cancel=lambda: None):
    """Centered, DC-removed RMS; bounded reads preserve stereo anti-phase signals."""
    import numpy as np
    import soundfile as sf
    levels = []
    with sf.SoundFile(str(path)) as audio:
        rate, length = audio.samplerate, audio.frames
        if rate <= 0 or length <= 0:
            raise ValueError('参考人声音频没有有效采样。')
        hop = max(1, round(rate * HOP_SECONDS))
        half = max(1, round(rate * WINDOW_SECONDS / 2))
        centers = np.arange(0, length, hop, dtype=np.int64)
        # Each read spans at most ten seconds, regardless of song duration.
        batch = max(1, round(10 * rate / hop))
        for first in range(0, len(centers), batch):
            cancel()
            current = centers[first:first + batch]
            left, right = max(0, int(current[0]) - half), min(length, int(current[-1]) + half + 1)
            audio.seek(left)
            block = audio.read(right - left, dtype='float64', always_2d=True)
            if len(block) != right - left or not np.isfinite(block).all():
                raise ValueError('参考人声音频包含无效采样或文件不完整。')
            sums = np.vstack([np.zeros((1, audio.channels)), np.cumsum(block, axis=0)])
            squares = np.vstack([np.zeros((1, audio.channels)), np.cumsum(block * block, axis=0)])
            start = np.maximum(current - half, 0) - left
            end = np.minimum(current + half + 1, length) - left
            counts = (end - start)[:, None]
            means = (sums[end] - sums[start]) / counts
            power = np.maximum(0, (squares[end] - squares[start]) / counts - means * means).mean(axis=1)
            levels.append(10 * np.log10(np.maximum(power, 1e-24)))
        return centers / rate, np.concatenate(levels), {
            'samplerate': rate, 'channels': audio.channels, 'duration_seconds': length / rate,
            'hop_seconds': hop / rate, 'window_seconds': (2 * half + 1) / rate}


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
        estimated = data[left, 1] + (data[left + 1:right, 0] - data[left, 0]) / (data[right, 0] - data[left, 0]) * (data[right, 1] - data[left, 1])
        errors = np.abs(estimated - data[left + 1:right, 1])
        offset = int(np.argmax(errors))
        if errors[offset] > tolerance:
            split = left + 1 + offset
            keep.add(split)
            pending.extend([(left, split), (split, right)])
    return [points[index] for index in sorted(keep)]


def analyze(path, intervals, strength=.6, cancel=lambda: None, *, preserve_samples=False):
    """Follow measured dynamics inside note regions, resetting in phrase gaps."""
    _, strength = settings({'reference_dynamics': True, 'dynamics_strength': strength})
    import numpy as np
    intervals = sorted((max(0., float(a)), float(b)) for a, b in intervals
                       if math.isfinite(a) and math.isfinite(b) and b > max(0, a))
    report = {'algorithm_version': ALGORITHM_VERSION, 'strength': strength,
              'status': 'skipped', 'reason': 'no_notes', 'points': []}
    if not intervals or strength == 0:
        if strength == 0:
            report['reason'] = 'zero_strength'
        return report
    times, db, metadata = energy_frames(path, cancel)
    report.update(metadata)
    note_mask = np.zeros(len(times), dtype=bool)
    for start, end in intervals:
        # Interior samples reduce onset/release bias in the reference baseline.
        margin = min(.03, (end - start) / 4)
        left, right = np.searchsorted(times, [start + margin, end - margin])
        note_mask[left:right] = True
    if not note_mask.any():
        report['reason'] = 'notes_outside_audio'
        return report
    gate = max(-85., float(np.percentile(db[note_mask], 90)) - 35)
    reliable = note_mask & (db > gate)
    report.update(gate_db=round(gate, 3), reliable_seconds=round(float(reliable.sum()) * metadata['hop_seconds'], 3),
                  reliable_fraction=round(float(reliable.sum() / note_mask.sum()), 4))
    if reliable.sum() < 6 or report['reliable_fraction'] < .25:
        report['reason'] = 'insufficient_signal'
        return report
    baseline = float(np.median(db[reliable]))
    report['baseline_db'] = round(baseline, 3)
    clipped = np.clip(db, baseline - 30, baseline + 20)
    # A short median rejects spikes, followed by a 100 ms symmetric smoother.
    padded = np.pad(clipped, (2, 2), mode='edge')
    filtered = np.median(np.lib.stride_tricks.sliding_window_view(padded, 5), axis=1)
    filtered = np.convolve(np.pad(filtered, (2, 2), mode='edge'), np.ones(5) / 5, mode='valid')
    gains = np.clip(filtered - baseline, -12, 6) * strength
    phrases = []
    for start, end in intervals:
        start, end = min(start, metadata['duration_seconds']), min(end, metadata['duration_seconds'])
        if end <= start:
            continue
        if phrases and start - phrases[-1][1] <= .18:
            phrases[-1][1] = max(end, phrases[-1][1])
        else:
            phrases.append([start, end])
    raw, active_values, used = [], [], 0
    for index, (start, end) in enumerate(phrases):
        cancel()
        left, right = np.searchsorted(times, [start, end])
        if reliable[left:right].sum() < 3:
            continue
        # Only silence outside actual notes resets the curve. No fixed fade is
        # imposed on a sustained release; the source envelope supplies it.
        before = min(.08, max(0, start - (phrases[index - 1][1] if index else 0)) / 3)
        after = min(.08, max(0, (phrases[index + 1][0] if index + 1 < len(phrases) else end + .24) - end) / 3)
        values = [(float(times[i]), float(gains[i])) for i in range(left, right)]
        if not values:
            continue
        values = [(start, float(np.interp(start, times, gains))), *values,
                  (end, float(np.interp(end, times, gains)))]
        # Deduplicate exact note/frame boundaries before simplification.
        values = list(dict(values).items())
        active_values.extend(value for _, value in values)
        raw.extend([(max(0., start - before), 0.), *values, (end + after, 0.)])
        used += 1
    if not active_values or max(active_values) - min(active_values) < .4 or max(map(abs, active_values)) < .2:
        report['reason'] = 'constant_level'
        return report
    samples = sorted(dict(raw).items())
    points = samples if preserve_samples else simplify(samples)
    report.update(status='applied', reason=None, phrase_count=used,
                  gain_min_db=round(min(active_values), 3), gain_max_db=round(max(active_values), 3),
                  point_count=len(points), frames_analyzed=len(times), samples_preserved=preserve_samples,
                  points=[[round(t, 6), round(value, 3)] for t, value in points])
    return report


def _automation_anchors(points):
    """Validate measured anchors without allowing order or duplicate ambiguity."""
    if not isinstance(points,(list,tuple)):
        raise ValueError('参数自动化样本必须是时间/数值点对。')
    anchors=[]
    previous=None
    for index,point in enumerate(points):
        if not isinstance(point,(list,tuple)) or len(point)!=2:
            raise ValueError(f'参数自动化样本格式无效（样本={index}）。')
        seconds,value=point
        if (type(seconds) not in (int,float) or not math.isfinite(seconds)
                or type(value) not in (int,float) or not math.isfinite(value)):
            raise ValueError(f'参数自动化样本不是有限数值（样本={index}，秒={seconds!r}，数值={value!r}）。')
        if previous is not None:
            if seconds==previous:
                raise ValueError(f'参数自动化时间重复（相邻样本={index-1}/{index}，秒={seconds!r}）。')
            if seconds<previous:
                raise ValueError(f'参数自动化时间乱序（相邻样本={index-1}/{index}，秒={previous!r}>{seconds!r}）。')
        anchors.append({'seconds':seconds,'value':value,'sample_index':index})
        previous=seconds
    return anchors


def _equivalent_automation_value(first,second):
    if first==second:
        return True
    return math.isclose(float(first),float(second),rel_tol=1e-12,abs_tol=1e-12)


def automation(points, clock, offset):
    """Store audio-time curves in group-local blicks, including tempo anchors.

    Blick quantization may collapse distinct raw seconds. Equivalent values are
    merged with an explicit receipt; non-equivalent values fail before output.
    """
    if type(offset) is not int:
        raise ValueError('参数自动化的轨道时间偏移无效。')
    anchors=_automation_anchors(points)
    positions=[anchor['seconds'] for anchor in anchors]
    for seconds in clock.seconds[1:]:
        index=bisect.bisect_right(positions,seconds)
        if 0<index<len(positions):
            left,right=anchors[index-1:index+1]
            if seconds in (left['seconds'],right['seconds']):
                continue
            value=left['value']+(seconds-left['seconds'])/(right['seconds']-left['seconds'])*(right['value']-left['value'])
            anchors.append({'seconds':seconds,'value':value,'sample_index':None})
    buckets={};merges=[]
    for anchor in anchors:
        absolute=clock.to_position(anchor['seconds'])
        blick=absolute-offset
        previous=buckets.get(blick)
        if previous is None:
            buckets[blick]=anchor;continue
        if not _equivalent_automation_value(previous['value'],anchor['value']):
            raise ValueError('参数自动化取整碰撞：秒 '
                f"{previous['seconds']!r}/{anchor['seconds']!r} 均映射到 blick {blick}"
                f"（绝对 blick {absolute}），数值 {previous['value']!r}/{anchor['value']!r} 不等价，"
                '拒绝静默覆盖。')
        merges.append({'blick':blick,'absolute_blick':absolute,
                       'seconds':[previous['seconds'],anchor['seconds']],
                       'values':[previous['value'],anchor['value']]})
    curve={'mode': 'linear', 'points': [item for blick in sorted(buckets)
            for item in (blick,buckets[blick]['value'])]}
    if merges:
        curve.update(rounding_collision_policy='merge_equivalent_reject_distinct',
                     rounding_collision_merges=merges)
    return curve


def curve_statistics(parameters, name, *, require_variation=False):
    """Audit serialized automation, keeping absent controls distinct from flat ones."""
    curve = parameters.get(name, {})
    points = curve.get('points', []) if isinstance(curve, dict) else None
    failure = f'SVP 的{CONTROL_NAMES[name]}曲线无效，请查看导出日志。'
    if not isinstance(points, list) or len(points) % 2:
        raise ValueError(failure)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in points):
        raise ValueError(failure)
    positions, values = points[::2], points[1::2]
    if any(a >= b for a, b in zip(positions, positions[1:])):
        raise ValueError(failure)
    low, high = (-48, 12) if name == 'loudness' else ((0, 1) if name == 'voicing' else (-1, 1))
    if any(not low <= value <= high for value in values):
        raise ValueError(failure)
    changing = bool(values and max(values) - min(values) > 1e-6)
    if require_variation and not changing:
        raise ValueError(f'SVP 的{CONTROL_NAMES[name]}未写入有效变化，请重新导出并查看日志。')
    return {'point_count': len(values), 'has_variation': changing,
            'value_min': min(values, default=None), 'value_max': max(values, default=None)}


def _parameter_statuses(output, reports, requested):
    """Audit serialized-shape curve objects without performing serialization."""
    saved_vocals = [track for track in output['tracks'] if not track.get('mainRef', {}).get('isInstrumental')]
    for track, report in zip(saved_vocals, reports):
        status = {}
        for name in CONTROL_NAMES:
            measured = report if name == 'loudness' else report.get('timbre', {}).get('parameters', {}).get(name, {})
            enabled = name in requested
            generated = enabled and measured.get('status') == 'applied'
            stats = curve_statistics(track['mainGroup'].get('parameters', {}), name, require_variation=generated)
            if generated:
                measured['written_points'] = stats['point_count']
            status[name] = {'requested': enabled,
                            'status': 'applied' if generated else ('skipped' if enabled else 'disabled'),
                            'reason': None if generated else measured.get('reason', 'disabled' if not enabled else 'no_result'),
                            **stats}
        report['parameter_status'] = status
    return reports


def apply_project(data, sources, strength=.6, cancel=lambda: None, *, dynamics=True, timbre=(), feature_cache=None, density_mode='dense', finalize=True):
    """Apply reference expression to a deep copy of an in-memory SVP project."""
    if not isinstance(data, dict):
        raise ValueError('SVP 工程数据格式无效。')
    if type(finalize) is not bool:
        raise ValueError('SVP 参数后处理开关必须为布尔值。')
    output = copy.deepcopy(data)
    sources = list(sources)
    clock = TempoMap(output['time']['tempo'])
    vocals = [track for track in output['tracks'] if not track.get('mainRef', {}).get('isInstrumental')]
    if len(vocals) != len(sources):
        raise ValueError('参考力度的声部与 SVP 轨道数量不一致。')
    requested = (['loudness'] if dynamics else []) + list(timbre)
    if any(name not in CONTROL_NAMES for name in requested):
        raise ValueError('参考演唱参数名称无效。')
    print('本次启用 SVP 参数：' + ('、'.join(CONTROL_NAMES[name] for name in requested) or '无'), flush=True)
    reports = []
    for track, source in zip(vocals, sources):
        cancel()
        title = {'lead': '主唱', 'backing': '和声', 'vocals': '人声'}.get(source['voice'], source['voice'])
        print(f'正在分析{title}参考演唱……', flush=True)
        group = track['mainGroup']
        offset = track.get('mainRef', {}).get('blickOffset', 0)
        intervals = [(clock.to_seconds(note['onset'] + offset),
                      clock.to_seconds(note['onset'] + note['duration'] + offset)) for note in group['notes']]
        result = (analyze(source['path'], intervals, strength, cancel, preserve_samples=True) if dynamics else
                  {'status': 'skipped', 'reason': 'disabled', 'points': []})
        result.update(voice=source['voice'], track_name=track['name'])
        reports.append(result)
        if result['status'] == 'applied':
            curve = automation(result['points'], clock, offset)
            group.setdefault('parameters', {})['loudness'] = curve
            result['written_points'] = len(curve['points']) // 2
            print(f"{title}参考力度：{result['written_points']} 个关键点，变化范围 {result['gain_min_db']:+.1f} 至 {result['gain_max_db']:+.1f} dB。", flush=True)
        elif dynamics:
            print(f'{title}参考力度：有效信号或力度变化不足，未添加曲线。', flush=True)
        if timbre:
            from vocal_timbre import analyze as analyze_timbre, phonetic_context
            notes = [(a, b, note['pitch']) for (a, b), note in zip(intervals, group['notes'])]
            database = track.get('mainRef', {}).get('database', {})
            language = database.get('languageOverride') or database.get('language')
            nuclei = phonetic_context(group['notes'], language) if 'tension' in timbre else None
            if feature_cache is not None and source['voice'] not in ('lead', 'backing', 'vocals'):
                raise ValueError('参考演唱的声部名称无效。')
            cache_key=source.get('cache_key',source['voice'])
            if not isinstance(cache_key,str) or not cache_key or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in cache_key):
                raise ValueError('参考演唱缓存标识无效。')
            cache = Path(feature_cache)/(cache_key+'.timbre.npz') if feature_cache is not None else None
            result['timbre'] = analyze_timbre(source['path'], notes, strength, timbre, cancel,
                cache=cache, nuclei=nuclei, preserve_samples=True)
            if result['timbre'].get('cache_hit'):
                print(f'{title}音色分析已复用，正在应用参数设置。', flush=True)
            for name, measured in result['timbre']['parameters'].items():
                if measured['status'] == 'applied':
                    curve = automation(measured['points'], clock, offset)
                    group.setdefault('parameters', {})[name] = curve
                    measured['written_points'] = len(curve['points']) // 2
                    print(f"{title}{CONTROL_NAMES[name]}：已生成 {measured['written_points']} 个关键点。", flush=True)
                else:
                    reason = SKIP_REASONS.get(measured.get('reason'), '缺少可靠的分析结果')
                    print(f'{title}{CONTROL_NAMES[name]}：{reason}，未生成新曲线，保留已有设置。', flush=True)
    if finalize:
        from calibration_refinement import finalize_expression
        density = finalize_expression(output, mode=density_mode, cancel=cancel)
        deferred = []
    else:
        density = {'status': 'deferred', 'deferred': True, 'mode': density_mode}
        deferred = ['expression_density']
    cancel()
    _parameter_statuses(output, reports, requested)
    result = {'enabled': True, 'strength': strength, 'algorithm_version': ALGORITHM_VERSION,
              'expression_density': density, 'deferred': deferred,
              'scope': 'svp_expression' if timbre else 'svp_loudness', 'parameters':
              requested, 'tracks': reports}
    return output, result


def apply_svp(path, sources, strength=.6, cancel=lambda: None, *, dynamics=True, timbre=(), feature_cache=None, density_mode='dense', finalize=True):
    """Enrich only generated main vocal groups, keeping notes/pitch/accompaniment intact."""
    path = Path(path)
    raw = path.read_bytes()
    data = json.loads(raw.removesuffix(b'\x00').decode('utf-8-sig'))
    output, report = apply_project(data, sources, strength, cancel, dynamics=dynamics, timbre=timbre,
        feature_cache=feature_cache, density_mode=density_mode, finalize=finalize)
    serialized = json.dumps(output, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    saved = json.loads(serialized)
    _parameter_statuses(saved, report['tracks'], report['parameters'])
    path.write_bytes(serialized + (b'\x00' if raw.endswith(b'\x00') else b''))
    return report
