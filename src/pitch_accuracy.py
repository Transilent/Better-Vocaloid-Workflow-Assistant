"""Assistant-side pitch guards; upstream code and model weights stay unchanged."""
import math

ALGORITHM_VERSION = 4
_destination = None
_last_result = None


def current_pitch():
    """Current worker's measured f0 for local alignment checks; no new inference."""
    return _last_result


def audit_svp_pitch(track):
    """Warn about note-step artifacts without rejecting intentional manual edits."""
    import numpy as np
    raw=track.get('mainGroup',{}).get('parameters',{}).get('pitchDelta',{}).get('points',[])
    if not raw:return {'status':'absent','points':0}
    data=np.asarray(raw,float)
    if data.ndim!=1 or len(data)%2 or not np.isfinite(data).all():
        return {'status':'invalid','reason':'invalid_curve'}
    x,y=data[::2],data[1::2]
    if len(x)>1 and not np.all(np.diff(x)>0):return {'status':'invalid','reason':'unordered_curve'}
    semitone=float(np.mean(abs(y/100-np.rint(y/100))<1e-5))
    zero_spans=(y[:-1]==0)&(y[1:]==0)
    zero_duration=float(np.diff(x)[zero_spans].sum()/max(x[-1]-x[0],1)) if len(x)>1 else float(y[0]==0)
    suspicious=len(y)>100 and np.count_nonzero(y)>20 and semitone>.98 and zero_duration>.5
    return {'status':'suspicious' if suspicious else 'ok','points':len(y),'unique_values':len(np.unique(y)),
            'semitone_quantized_fraction':round(semitone,5),'zero_span_fraction':round(zero_duration,5),
            'reason':'mostly_neutral_with_semitone_steps' if suspicious else None}


def save_pitch(path, result):
    import numpy as np
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    try:
        with temporary.open('wb') as stream:
            np.savez_compressed(stream, midi_pitch=result.midi_pitch, voiced_mask=result.voiced_mask,
                                time_step=result.time_step_seconds, schema=1)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_pitch(path):
    import hashlib
    import io
    import numpy as np
    from pathlib import Path
    from types import SimpleNamespace
    source_path = Path(path)
    raw = source_path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    with np.load(io.BytesIO(raw), allow_pickle=False) as data:
        pitch = np.asarray(data['midi_pitch'], dtype=np.float32)
        mask = np.asarray(data['voiced_mask'], dtype=bool)
        step = float(data['time_step'])
    if (pitch.ndim != 1 or mask.shape != pitch.shape or not .001 <= step <= .1
            or not np.isfinite(pitch[mask]).all() or ((pitch[mask] < 0) | (pitch[mask] > 127)).any()):
        raise ValueError('连续音高数据无效，请重新提取音高。')
    pitch = pitch.copy(); pitch[~mask] = np.nan
    return SimpleNamespace(midi_pitch=pitch, voiced_mask=mask, time_step_seconds=step,
                           source_sha256=source_sha256)


def exclude_unvoiced_pitch(result, notes, phonemes):
    """Veto large f0 excursions only inside explicitly aligned unvoiced phones."""
    import numpy as np
    from dataclasses import replace
    pitch = result.midi_pitch.copy()
    times = np.arange(len(pitch)) * result.time_step_seconds
    expected = np.full(len(pitch), np.nan)
    for note in notes:
        expected[(times >= note.onset) & (times < note.offset)] = round(note.pitch)
    valid = np.isfinite(pitch)
    if result.voiced_mask is not None:
        valid &= result.voiced_mask
    excluded = {}
    for phone in phonemes:
        # Leave 10 ms at both boundaries for alignment uncertainty/coarticulation.
        left, right = phone['start'] + .01, phone['end'] - .01
        mask = valid & (times >= left) & (times < right) & (abs(pitch - expected) > 3.)
        for index in np.flatnonzero(mask):
            excluded.setdefault(int(index), {'frame':int(index), 'pitch':float(pitch[index]), 'phone':phone['phone']})
    if excluded:
        indices = list(excluded)
        pitch[indices] = np.nan
        valid[indices] = False
    return replace(result, midi_pitch=pitch, voiced_mask=valid), {
        'status':'applied' if excluded else 'unchanged', 'excluded_frames':len(excluded),
        'excluded_seconds':round(len(excluded)*result.time_step_seconds, 4),
        'aligned_unvoiced_phones':len(phonemes), 'excluded':[excluded[i] for i in sorted(excluded)]}


def cache_export_pitch(result):
    if _destination is not None:
        save_pitch(_destination, result)


ABSOLUTE_F0_ALGORITHM = 'absolute-f0-v2'
ABSOLUTE_F0_PITCH_AXIS_OFFSET_TICKS = 1920
ABSOLUTE_F0_MIN_VOICED_RUN_FRAMES = 3
ABSOLUTE_F0_BRIDGE_GAP_SECONDS = .080
ABSOLUTE_F0_ENDPOINT_GUARD_BEFORE_TICKS = 2
ABSOLUTE_F0_ENDPOINT_GUARD_AFTER_TICKS = 4
ABSOLUTE_F0_TREND_WINDOW_POINTS = 5
ABSOLUTE_F0_MAX_TREND_CENTS_PER_TICK = 120.
ABSOLUTE_F0_SPIKE_THRESHOLD_CENTS = 300.
ABSOLUTE_F0_SPIKE_NEIGHBOR_SPAN_CENTS = 150.
ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS = .080
ABSOLUTE_F0_MAX_SPIKE_SAMPLES = 32
ABSOLUTE_F0_SUSTAINED_OFFSET_THRESHOLD_CENTS = ABSOLUTE_F0_SPIKE_THRESHOLD_CENTS
ABSOLUTE_F0_SUSTAINED_OFFSET_MIN_DURATION_SECONDS = .160
ABSOLUTE_F0_SUSTAINED_OFFSET_MIN_FRAMES = 3
ABSOLUTE_F0_MAX_OFFSET_WARNINGS = 32
ABSOLUTE_F0_MAX_OFFSET_UNEVALUATED = 32
ABSOLUTE_F0_MAX_AMBIGUOUS_INTERVALS = 32
ABSOLUTE_F0_MAX_DIAGNOSTIC_PAGE_SIZE = 100


def clear_explicit_pitch_scaffolding(group):
    """Remove old pitch layers before installing a rebuilt F0 curve.

    This boundary is only for an explicit continuous-F0 rebuild. Ordinary
    style edits keep ``vibratoEnv`` and every authored non-pitch
    expression curve untouched. Callers replace ``pitchDelta`` separately.
    """
    if not isinstance(group, dict):
        raise ValueError('SVP 音高参数组无效。')
    removed = []
    if 'pitchControls' in group:
        group.pop('pitchControls')
        removed.append('pitchControls')
    parameters = group.get('parameters')
    if parameters is not None and not isinstance(parameters, dict):
        raise ValueError('SVP 音高参数容器无效。')
    if isinstance(parameters, dict) and 'vibratoEnv' in parameters:
        parameters.pop('vibratoEnv')
        removed.append('vibratoEnv')
    return removed


_DIAGNOSTIC_PAGE_LIMITS = {
    'warnings': ABSOLUTE_F0_MAX_OFFSET_WARNINGS,
    'unevaluated_notes': ABSOLUTE_F0_MAX_OFFSET_UNEVALUATED,
    'not_assessed_intervals': ABSOLUTE_F0_MAX_AMBIGUOUS_INTERVALS,
}


def _normalized_diagnostic_pagination(value):
    """Validate per-category offsets while retaining the legacy first page."""
    from collections.abc import Mapping
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError('diagnostic_pagination 必须是以诊断类别为键的对象。')
    unknown = set(value) - set(_DIAGNOSTIC_PAGE_LIMITS)
    if unknown:
        raise ValueError('未知的音高诊断分页类别：' + '、'.join(sorted(unknown)))
    pages = {}
    for category, default_limit in _DIAGNOSTIC_PAGE_LIMITS.items():
        raw = value.get(category, {})
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValueError(f'{category} 分页参数必须是对象。')
        unknown_fields = set(raw) - {'offset', 'limit'}
        if unknown_fields:
            raise ValueError(
                f'{category} 分页参数含未知字段：'
                + '、'.join(sorted(unknown_fields)))
        offset = raw.get('offset', 0)
        limit = raw.get('limit', default_limit)
        if type(offset) is not int or offset < 0:
            raise ValueError(f'{category}.offset 必须是非负整数。')
        if (type(limit) is not int
                or not 1 <= limit <= ABSOLUTE_F0_MAX_DIAGNOSTIC_PAGE_SIZE):
            raise ValueError(
                f'{category}.limit 必须是 1 到 '
                f'{ABSOLUTE_F0_MAX_DIAGNOSTIC_PAGE_SIZE} 的整数。')
        pages[category] = {'offset': offset, 'limit': limit}
    return pages


def _diagnostic_page_truncated(settings, metadata):
    # A positive offset omits earlier items even when no later page remains.
    return settings['offset'] > 0 or metadata['has_more']


def _diagnostic_page_metadata(settings, total, returned, *, pageable=True):
    if not pageable:
        return {
            'offset': settings['offset'],
            'limit': settings['limit'],
            'returned': returned,
            'total': total,
            'has_more': False,
            'next_offset': None,
            'available': False,
            'unavailable_reason': 'note_centers_unavailable',
        }
    page_end = settings['offset'] + returned
    has_more = page_end < total
    return {
        'offset': settings['offset'],
        'limit': settings['limit'],
        'returned': returned,
        'total': total,
        'has_more': has_more,
        'next_offset': page_end if has_more else None,
    }


def _pitch_runs(mask):
    if not mask.size:
        return []
    import numpy as np
    changes = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))


def _endpoint_slope(ordered, from_end):
    """Return a bounded local slope in cents per pitch-axis tick."""
    import numpy as np
    sample = ordered[-ABSOLUTE_F0_TREND_WINDOW_POINTS:] if from_end else ordered[:ABSOLUTE_F0_TREND_WINDOW_POINTS]
    if len(sample) < 2:
        return 0.
    x = np.asarray([point[0] for point in sample], dtype=np.float64)
    y = np.asarray([point[1] for point in sample], dtype=np.float64)
    if float(np.ptp(x)) <= 0:
        return 0.
    slope = float(np.polyfit(x, y, 1)[0])
    return float(np.clip(slope, -ABSOLUTE_F0_MAX_TREND_CENTS_PER_TICK,
                         ABSOLUTE_F0_MAX_TREND_CENTS_PER_TICK))


def _bounded_bridge_tangent(slope, secant):
    # Preserve the measured trend without allowing cubic bridge overshoot.
    if abs(secant) < 1e-12 or slope * secant <= 0:
        return 0.
    return math.copysign(min(abs(slope), 3. * abs(secant)), secant)


def _bridge_points(previous, following):
    """Fill only the short missing interval; endpoints remain source exact."""
    import numpy as np
    x0, y0 = previous[-1]
    x1, y1 = following[0]
    width = x1 - x0
    if width <= 1:
        return []
    secant = (y1 - y0) / width
    m0 = _bounded_bridge_tangent(_endpoint_slope(previous, True), secant)
    m1 = _bounded_bridge_tangent(_endpoint_slope(following, False), secant)
    result = []
    for tick in range(int(x0) + 1, int(x1)):
        u = (tick - x0) / width
        u2, u3 = u * u, u * u * u
        value = ((2 * u3 - 3 * u2 + 1) * y0
                 + (u3 - 2 * u2 + u) * width * m0
                 + (-2 * u3 + 3 * u2) * y1
                 + (u3 - u2) * width * m1)
        result.append((tick, int(round(float(np.clip(value, 0., 12700.))))))
    return result


def _guard_points(anchor, slope, ticks):
    x, y = anchor
    import numpy as np
    return [(tick, int(round(float(np.clip(y + slope * (tick - x), 0., 12700.)))))
            for tick in ticks]


def assign_note_owners(times, note_intervals):
    """Assign each frame to one half-open note; overlapping notes are ambiguous."""
    import numpy as np
    owner = np.full(len(times), -1, dtype=np.int64)
    for note_index, (start, end) in enumerate(note_intervals):
        left = int(np.searchsorted(times, start, side='left'))
        right = int(np.searchsorted(times, end, side='left'))
        if right <= left:
            continue
        section = owner[left:right]
        # Preserve an already ambiguous frame. Treating every negative owner
        # as unassigned would let a third note claim a triple overlap.
        owner[left:right] = np.where(section == -1, note_index, -2)
    return owner


def pitch_note_descriptors(notes, clock, *, vocal_track_index=None):
    """Return shared GUI/MCP note context for read-only pitch review.

    The note ordinal and written MIDI center identify the review target. Tick
    and playback bounds are retained so a warning remains useful after JSON
    serialization without exposing GUI-only objects to the MCP layer.
    """
    if vocal_track_index is not None and (type(vocal_track_index) is not int
                                          or vocal_track_index < 0):
        raise ValueError('vocal_track_index 必须是非负整数或 None。')
    ticks_to_seconds = getattr(clock, 'get_actual_secs_from_ticks', clock)
    if not callable(ticks_to_seconds):
        raise ValueError('音符描述需要可调用的播放时间转换器。')
    intervals = []
    descriptors = []
    track_id = 'unknown-track' if vocal_track_index is None else f'vocal-{vocal_track_index}'
    for note_index, note in enumerate(notes):
        try:
            start_tick = int(note.start_pos)
            try:
                end_tick = int(note.end_pos)
            except AttributeError:
                end_tick = start_tick + int(note.length)
            center_midi = float(note.key_number)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError('当前工程含缺少时轴或音高中心的音符。') from error
        if end_tick <= start_tick:
            raise ValueError('当前工程含零长或倒置音符。')
        if not math.isfinite(center_midi) or not 0 <= center_midi <= 127:
            raise ValueError('音符中心音高必须是 0 到 127 的有限值。')
        start_seconds = float(ticks_to_seconds(start_tick))
        end_seconds = float(ticks_to_seconds(end_tick))
        if (not math.isfinite(start_seconds) or not math.isfinite(end_seconds)
                or end_seconds <= start_seconds):
            raise ValueError('当前工程音符的播放时间无效。')
        center_cents = int(round(center_midi * 100.))
        note_id = (f'{track_id}:note-{note_index}:ticks-{start_tick}-{end_tick}'
                   f':center-{center_cents}')
        lyric = getattr(note, 'lyric', None)
        intervals.append((start_seconds, end_seconds))
        descriptors.append({
            'vocal_track_index': vocal_track_index,
            'note_index': note_index,
            'note_id': note_id,
            'start_tick': start_tick,
            'end_tick': end_tick,
            'start_seconds': round(start_seconds, 9),
            'end_seconds': round(end_seconds, 9),
            'center_midi': round(center_midi, 6),
            'center_cents': center_cents,
            'lyric': None if lyric is None else str(lyric),
        })
    return intervals, descriptors


def _normalized_source_sha256(value):
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value.lower())):
        raise ValueError('source_sha256 必须是 64 位十六进制字符串或 None。')
    return value.lower()


def analyze_sustained_pitch_offsets(pitch, voiced_mask, owner, time_step_seconds,
                                    note_intervals, note_descriptors=(),
                                    time_origin_seconds=0., *, source_sha256=None,
                                    vocal_track_index=None,
                                    diagnostic_pagination=None):
    """Describe sustained F0 offsets from written note centers without edits.

    Slow drift is detected by comparing every frame directly with the note's
    written MIDI center; no adjacent-frame jump is required. A warning needs a
    contiguous same-note voiced interval of at least 160 ms and three frames.
    Chinese-opera head/tail guards and concurrent-note ambiguity are excluded
    from attribution. The result is evidence for review, not a truth decision.

    diagnostic_pagination may independently page warnings, unevaluated_notes
    and not_assessed_intervals with an offset and limit object. Each category
    defaults to offset 0 and limit 32; an explicit limit may be 1 through 100.
    Counts always describe the complete source, while lists contain only the
    requested page.
    """
    import numpy as np
    values = np.asarray(pitch, dtype=np.float64)
    mask = np.asarray(voiced_mask, dtype=bool)
    owners = np.asarray(owner, dtype=np.int64)
    step = float(time_step_seconds)
    origin = float(time_origin_seconds)
    if values.ndim != 1 or mask.shape != values.shape or owners.shape != values.shape:
        raise ValueError('音高偏移核查输入长度不一致。')
    if not math.isfinite(step) or not .001 <= step <= .1 or not math.isfinite(origin):
        raise ValueError('音高偏移核查时间基准无效。')
    if vocal_track_index is not None and (type(vocal_track_index) is not int
                                          or vocal_track_index < 0):
        raise ValueError('vocal_track_index 必须是非负整数或 None。')
    source_sha256 = _normalized_source_sha256(source_sha256)
    page_settings = _normalized_diagnostic_pagination(diagnostic_pagination)
    intervals = []
    for interval in note_intervals:
        try:
            start, end = map(float, interval)
        except (TypeError, ValueError) as error:
            raise ValueError('音高偏移核查的音符时间无效。') from error
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError('音高偏移核查的音符时间无效。')
        intervals.append((start, end))
    descriptors = list(note_descriptors or ())
    if descriptors and len(descriptors) != len(intervals):
        raise ValueError('音高偏移核查的音符身份与时间数量不一致。')
    from collections.abc import Mapping
    for note_index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, Mapping):
            raise ValueError('音高偏移核查的音符身份必须是对象。')
        try:
            descriptor_interval = (float(descriptor['start_seconds']),
                                   float(descriptor['end_seconds']))
            center = float(descriptor['center_midi'])
            descriptor_index = int(descriptor['note_index'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('音高偏移核查的音符身份缺少必要字段。') from error
        descriptor_track = descriptor.get('vocal_track_index')
        if (descriptor_index != note_index
                or abs(descriptor_interval[0] - intervals[note_index][0]) > 1e-8
                or abs(descriptor_interval[1] - intervals[note_index][1]) > 1e-8
                or not math.isfinite(center) or not 0 <= center <= 127
                or (descriptor_track is not None
                    and descriptor_track != vocal_track_index)):
            raise ValueError('音高偏移核查的音符身份与时间不一致。')
    times = np.arange(values.size, dtype=np.float64) * step + origin
    valid = mask & np.isfinite(values) & (values >= 0) & (values <= 127)
    warnings = []
    unevaluated = []
    warning_count = 0
    evaluated_notes = 0
    clear_notes = 0
    short_candidates = 0
    edge_guard_suppressed = 0
    unevaluated_observed = 0
    warnings_page = page_settings['warnings']
    unevaluated_page = page_settings['unevaluated_notes']
    not_assessed_page = page_settings['not_assessed_intervals']

    ambiguous = owners == -2
    ambiguous_frames = int(np.count_nonzero(ambiguous))
    not_assessed_intervals = []
    ambiguous_interval_count = 0
    for left, right in _pitch_runs(ambiguous):
        ambiguous_interval_count += 1
        interval_index = ambiguous_interval_count - 1
        page_start = not_assessed_page['offset']
        if page_start <= interval_index < page_start + not_assessed_page['limit']:
            start = float(times[left])
            end = float(times[right - 1] + step)
            covered_notes = [
                index for index, (note_start, note_end) in enumerate(intervals)
                if note_start < end - 1e-12 and note_end > start + 1e-12]
            not_assessed_intervals.append({
                'status': 'not_assessed',
                'reason': 'concurrent_note_ownership_ambiguous',
                'start_seconds': round(start, 9),
                'end_seconds': round(end, 9),
                'note_indices': covered_notes,
                'source_sha256': source_sha256,
                'truth_determined': False,
                'automatic_change': False,
            })

    if not descriptors:
        unavailable_unevaluated_count = len(intervals)
        page_metadata = {
            'warnings': _diagnostic_page_metadata(warnings_page, 0, 0),
            'unevaluated_notes': _diagnostic_page_metadata(
                unevaluated_page, unavailable_unevaluated_count, 0,
                pageable=False),
            'not_assessed_intervals': _diagnostic_page_metadata(
                not_assessed_page, ambiguous_interval_count,
                len(not_assessed_intervals)),
        }
        return {
            'algorithm': 'sustained-note-center-offset-v1',
            'status': 'unavailable',
            'reason': 'note_centers_unavailable',
            'threshold_cents': ABSOLUTE_F0_SUSTAINED_OFFSET_THRESHOLD_CENTS,
            'minimum_duration_seconds': ABSOLUTE_F0_SUSTAINED_OFFSET_MIN_DURATION_SECONDS,
            'minimum_frames': ABSOLUTE_F0_SUSTAINED_OFFSET_MIN_FRAMES,
            'edge_guard_seconds': ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS,
            'vocal_track_index': vocal_track_index,
            'source_sha256': source_sha256,
            'warning_count': 0,
            'warnings': [],
            'warnings_truncated': _diagnostic_page_truncated(
                warnings_page, page_metadata['warnings']),
            'unevaluated_count': unavailable_unevaluated_count,
            'unevaluated_notes': [],
            'unevaluated_truncated': _diagnostic_page_truncated(
                unevaluated_page, page_metadata['unevaluated_notes']),
            'not_assessed_interval_count': ambiguous_interval_count,
            'not_assessed_intervals': not_assessed_intervals,
            'not_assessed_intervals_truncated': _diagnostic_page_truncated(
                not_assessed_page, page_metadata['not_assessed_intervals']),
            'diagnostic_pagination': page_metadata,
            'ambiguous_note_frames': ambiguous_frames,
            'edge_guard_suppressed_frames': 0,
            'short_offset_candidates': 0,
            'evaluated_notes': 0,
            'clear_notes': 0,
            'truth_determined': False,
            'automatic_change': False,
            'statement': '缺少音符中心；仅报告未评估，不判断音符或音高线真值。',
            'evidence_limits': [
                'note_centers_unavailable',
                'continuous_offset_cannot_identify_ground_truth',
                'vibrato_or_expression_may_explain_threshold_crossing',
            ] + (['source_sha256_unavailable'] if source_sha256 is None else []),
        }

    threshold_cents = float(ABSOLUTE_F0_SUSTAINED_OFFSET_THRESHOLD_CENTS)
    minimum_seconds = float(ABSOLUTE_F0_SUSTAINED_OFFSET_MIN_DURATION_SECONDS)
    minimum_frames = int(ABSOLUTE_F0_SUSTAINED_OFFSET_MIN_FRAMES)
    guard = float(ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS)
    for note_index, descriptor in enumerate(descriptors):
        note_start, note_end = intervals[note_index]
        note_left = int(np.searchsorted(times, note_start, side='left'))
        note_right = int(np.searchsorted(times, note_end, side='left'))
        local_times = times[note_left:note_right]
        local_values = values[note_left:note_right]
        note = {key: descriptor.get(key) for key in (
            'vocal_track_index', 'note_index', 'note_id', 'start_tick', 'end_tick',
            'start_seconds', 'end_seconds', 'center_midi', 'center_cents', 'lyric')}
        if note['vocal_track_index'] is None:
            note['vocal_track_index'] = vocal_track_index
        if note['note_index'] is None:
            note['note_index'] = note_index
        if not note['note_id']:
            note['note_id'] = (
                f"note-{note_index}:seconds-{float(intervals[note_index][0]):.9f}-"
                f"{float(intervals[note_index][1]):.9f}:center-{int(round(float(descriptor['center_midi']) * 100))}")
        local_sole = valid[note_left:note_right] & (owners[note_left:note_right] == note_index)
        local_edge = local_sole & ((local_times < note_start + guard - 1e-12)
                                   | (local_times >= note_end - guard + 1e-12))
        edge_deviation = (np.abs(local_values[local_edge]
                                 - float(descriptor['center_midi'])) * 100.)
        edge_guard_suppressed += int(np.count_nonzero(edge_deviation > threshold_cents))
        eligible = local_sole & ~local_edge
        eligible_indices = np.flatnonzero(eligible)
        evidence_seconds = (float(local_times[int(eligible_indices[-1])]
                                  - local_times[int(eligible_indices[0])] + step)
                            if eligible_indices.size else 0.)
        if eligible_indices.size < minimum_frames or evidence_seconds < minimum_seconds:
            item_index = unevaluated_observed
            unevaluated_observed += 1
            page_start = unevaluated_page['offset']
            if page_start <= item_index < page_start + unevaluated_page['limit']:
                unevaluated.append({
                    'status': 'not_assessed',
                    'reason': ('no_unambiguous_voiced_frames' if not eligible_indices.size
                               else 'insufficient_unambiguous_voiced_duration'),
                    'note': note,
                    'eligible_voiced_frames': int(eligible_indices.size),
                    'eligible_duration_seconds': round(evidence_seconds, 9),
                    'source_sha256': source_sha256,
                    'truth_determined': False,
                    'automatic_change': False,
                })
            continue
        evaluated_notes += 1
        offsets = (local_values - float(descriptor['center_midi'])) * 100.
        flagged = eligible & (np.abs(offsets) > threshold_cents + 1e-12)
        note_warning_count = 0
        for local_left, local_right in _pitch_runs(flagged):
            left = note_left + local_left
            right = note_left + local_right
            frames = local_right - local_left
            observed_end = min(note_end, float(local_times[local_right - 1] + step))
            duration = observed_end - float(local_times[local_left])
            if frames < minimum_frames or duration < minimum_seconds - 1e-12:
                short_candidates += 1
                continue
            warning_count += 1
            note_warning_count += 1
            item_index = warning_count - 1
            page_start = warnings_page['offset']
            if page_start <= item_index < page_start + warnings_page['limit']:
                local = offsets[local_left:local_right]
                direction = ('sharp' if np.all(local > 0) else
                             'flat' if np.all(local < 0) else 'mixed')
                limitations = [
                    'continuous_offset_does_not_identify_note_or_f0_ground_truth',
                    'normal_vibrato_or_intentional_expression_may_explain_crossing',
                    'edge_and_overlap_evidence_is_excluded_from_attribution',
                ]
                warnings.append({
                    'warning_id': f"offset:{note['note_id']}:{left}-{right}",
                    'kind': 'sustained_pitch_offset_review',
                    'severity': 'review',
                    'review_target': 'note_pitch_or_f0_curve',
                    'reason': (f'absolute offset above {threshold_cents:.0f} cents '
                               f'for at least {minimum_seconds:.3f} seconds'),
                    'note': note,
                    'interval': {
                        'start_seconds': round(float(local_times[local_left]), 9),
                        'end_seconds': round(observed_end, 9),
                        'duration_seconds': round(duration, 9),
                        'frame_start': int(left),
                        'frame_end_exclusive': int(right),
                    },
                    'observed': {
                        'direction': direction,
                        'min_offset_cents': round(float(np.min(local)), 6),
                        'max_offset_cents': round(float(np.max(local)), 6),
                        'median_offset_cents': round(float(np.median(local)), 6),
                        'peak_absolute_offset_cents': round(float(np.max(np.abs(local))), 6),
                        'evidence_frames': int(frames),
                    },
                    'source_sha256': source_sha256,
                    'truth_determined': False,
                    'automatic_change': False,
                    'limitations': limitations,
                })
        if not note_warning_count:
            clear_notes += 1

    unevaluated_total = len(intervals) - evaluated_notes
    page_metadata = {
        'warnings': _diagnostic_page_metadata(
            warnings_page, warning_count, len(warnings)),
        'unevaluated_notes': _diagnostic_page_metadata(
            unevaluated_page, unevaluated_total, len(unevaluated)),
        'not_assessed_intervals': _diagnostic_page_metadata(
            not_assessed_page, ambiguous_interval_count,
            len(not_assessed_intervals)),
    }
    if warning_count:
        status = 'review_required'
    elif unevaluated_total or ambiguous_interval_count:
        status = 'insufficient_evidence'
    else:
        status = 'no_sustained_offset_observed'
    evidence_limits = [
        'continuous_offset_cannot_identify_ground_truth',
        'vibrato_or_expression_may_explain_threshold_crossing',
        'edge_and_overlap_intervals_are_not_attributed',
    ]
    if source_sha256 is None:
        evidence_limits.append('source_sha256_unavailable')
    return {
        'algorithm': 'sustained-note-center-offset-v1',
        'status': status,
        'threshold_cents': threshold_cents,
        'comparison': 'abs(observed_f0_midi - written_note_center_midi) * 100',
        'minimum_duration_seconds': minimum_seconds,
        'minimum_frames': minimum_frames,
        'edge_guard_seconds': guard,
        'vocal_track_index': vocal_track_index,
        'source_sha256': source_sha256,
        'warning_count': warning_count,
        'warnings': warnings,
        'warnings_truncated': _diagnostic_page_truncated(
            warnings_page, page_metadata['warnings']),
        'unevaluated_count': unevaluated_total,
        'unevaluated_notes': unevaluated,
        'unevaluated_truncated': _diagnostic_page_truncated(
            unevaluated_page, page_metadata['unevaluated_notes']),
        'not_assessed_interval_count': ambiguous_interval_count,
        'not_assessed_intervals': not_assessed_intervals,
        'not_assessed_intervals_truncated': _diagnostic_page_truncated(
            not_assessed_page, page_metadata['not_assessed_intervals']),
        'diagnostic_pagination': page_metadata,
        'ambiguous_note_frames': ambiguous_frames,
        'edge_guard_suppressed_frames': edge_guard_suppressed,
        'short_offset_candidates': short_candidates,
        'evaluated_notes': evaluated_notes,
        'clear_notes': clear_notes,
        'truth_determined': False,
        'automatic_change': False,
        'statement': (
            '这是只读复核证据：持续偏离可能来自音符中心或 F0 线。'
            '告警不判断真值、不修改音高，也不把颤音或过渡无条件认定为错误。'
        ),
        'evidence_limits': evidence_limits,
    }


def _owned_pitch_runs(values, mask, owner):
    import numpy as np
    valid = mask & np.isfinite(values) & (owner >= 0)
    indices = np.flatnonzero(valid)
    if not indices.size:
        return
    changes = np.flatnonzero((np.diff(indices) != 1)
                             | (owner[indices[1:]] != owner[indices[:-1]])) + 1
    for group in np.split(indices, changes):
        if group.size:
            yield int(owner[group[0]]), group


def stabilize_isolated_pitch_spikes(pitch, voiced_mask, owner, time_step_seconds,
                                    time_origin_seconds=0., *, note_intervals=()):
    """Interpolate only an isolated same-note F0 spike above 300 cents.

    A correction requires both neighbours to be adjacent voiced frames in the
    same note and to agree within 150 cents. Adjacent candidate frames are a
    sustained shift and are retained. Candidates within the note-edge guard
    are also retained so an opera onset, release or rapid head/tail ornament
    is not mistaken for an isolated model spike. Retained large steps are
    returned as review hints, never silently rewritten.
    """
    import numpy as np
    values = np.asarray(pitch, dtype=np.float64).copy()
    mask = np.asarray(voiced_mask, dtype=bool)
    owners = np.asarray(owner, dtype=np.int64)
    step = float(time_step_seconds)
    origin = float(time_origin_seconds)
    if values.ndim != 1 or mask.shape != values.shape or owners.shape != values.shape:
        raise ValueError('音高峰值平滑输入长度不一致。')
    if not math.isfinite(step) or step <= 0 or not math.isfinite(origin):
        raise ValueError('音高峰值平滑时间基准无效。')
    if (not math.isfinite(ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS)
            or ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS < 0):
        raise ValueError('音高峰值边界保护时长无效。')
    invalid_voiced = mask & (~np.isfinite(values) | (values < 0) | (values > 127))
    if invalid_voiced.any():
        frame = int(np.flatnonzero(invalid_voiced)[0])
        raise ValueError(f'有声 F0 帧必须是 0 到 127 的有限数值：frame {frame}。')
    intervals = tuple(note_intervals or ())
    times = np.arange(values.size, dtype=np.float64) * step + origin
    corrected = np.zeros(values.size, dtype=bool)
    replacements = {}
    samples = []
    boundary_protected = 0
    for note_index, indices in _owned_pitch_runs(values, mask, owners):
        if indices.size < 3:
            continue
        inner = indices[1:-1]
        previous = values[indices[:-2]]
        current = values[indices[1:-1]]
        following = values[indices[2:]]
        reference = (previous + following) / 2.
        neighbour_span = np.abs(following - previous) * 100.
        deviation = np.abs(current - reference) * 100.
        potential = ((deviation > ABSOLUTE_F0_SPIKE_THRESHOLD_CENTS)
                     & (neighbour_span <= ABSOLUTE_F0_SPIKE_NEIGHBOR_SPAN_CENTS))
        if not potential.any():
            continue
        # Require room inside the measured voiced run. This deliberately
        # protects a Chinese-opera entry/release and every unvoiced-gap edge
        # from a generic single-frame smooth.
        inside_guard = (
            (times[inner] - times[int(indices[0])]
             >= ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS - 1e-12)
            & (times[int(indices[-1])] - times[inner]
               >= ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS - 1e-12)
        )
        if note_index < len(intervals):
            note_start, note_end = map(float, intervals[note_index])
            inside_guard &= (
                (times[inner] - note_start >= ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS - 1e-12)
                & (note_end - times[inner] >= ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS - 1e-12)
            )
        boundary_protected += int(np.count_nonzero(potential & ~inside_guard))
        candidates = potential & inside_guard
        if not candidates.any():
            continue
        isolated = candidates.copy()
        if isolated.size > 1:
            isolated[1:] &= ~candidates[:-1]
            isolated[:-1] &= ~candidates[1:]
        for frame, expected, original, error in zip(
                inner[isolated], reference[isolated], current[isolated], deviation[isolated]):
            if int(frame) in replacements:
                continue
            replacements[int(frame)] = float(expected)
            corrected[int(frame)] = True
            if len(samples) < ABSOLUTE_F0_MAX_SPIKE_SAMPLES:
                samples.append({
                    'frame': int(frame),
                    'seconds': round(origin + int(frame) * step, 9),
                    'note_index': int(note_index),
                    'original_midi': round(float(original), 6),
                    'corrected_midi': round(float(expected), 6),
                    'deviation_cents': round(float(error), 6),
                })
    for frame, value in replacements.items():
        values[frame] = value
    hints = []
    retained_count = 0
    for note_index, indices in _owned_pitch_runs(values, mask, owners):
        if indices.size < 2:
            continue
        for offset, (left, right) in enumerate(zip(indices, indices[1:])):
            step_cents = abs(float(values[right]) - float(values[left])) * 100.
            if step_cents <= ABSOLUTE_F0_SPIKE_THRESHOLD_CENTS:
                continue
            retained_count += 1
            if len(hints) < ABSOLUTE_F0_MAX_SPIKE_SAMPLES:
                before = values[indices[max(0, offset - 2):offset + 1]]
                after = values[indices[offset + 1:min(indices.size, offset + 4)]]
                sustained = (
                    before.size >= 3 and after.size >= 3
                    and float(np.ptp(before)) <= .5
                    and float(np.ptp(after)) <= .5
                    and abs(float(np.median(after)) - float(np.median(before))) * 100.
                    > ABSOLUTE_F0_SPIKE_THRESHOLD_CENTS
                )
                near_edge = (
                    times[right] - times[int(indices[0])]
                    < ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS - 1e-12
                    or times[int(indices[-1])] - times[left]
                    < ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS - 1e-12
                )
                hints.append({
                    'frame': int(right),
                    'seconds': round(origin + int(right) * step, 9),
                    'note_index': int(note_index),
                    'step_cents': round(step_cents, 6),
                    'reason': ('boundary_transition_review' if near_edge else
                               'sustained_large_offset_review' if sustained
                               else 'large_pitch_step_review'),
                })
    return values, {
        'status': 'applied' if replacements else 'unchanged',
        'threshold_cents': ABSOLUTE_F0_SPIKE_THRESHOLD_CENTS,
        'neighbour_span_cents': ABSOLUTE_F0_SPIKE_NEIGHBOR_SPAN_CENTS,
        'edge_guard_seconds': ABSOLUTE_F0_SPIKE_EDGE_GUARD_SECONDS,
        'corrected_frames': int(corrected.sum()),
        'corrections': samples,
        'corrections_truncated': int(corrected.sum()) > len(samples),
        'boundary_protected_frames': boundary_protected,
        'retained_large_steps': retained_count,
        'review_hints': hints,
        'review_hints_truncated': retained_count > len(hints),
    }

def build_absolute_f0_curve(pitch, voiced_mask, time_step_seconds, note_intervals,
                            seconds_to_tick, tick_to_seconds, *, time_origin_seconds=0.,
                            axis_offset_ticks=ABSOLUTE_F0_PITCH_AXIS_OFFSET_TICKS,
                            min_voiced_run_frames=ABSOLUTE_F0_MIN_VOICED_RUN_FRAMES,
                            bridge_gap_seconds=ABSOLUTE_F0_BRIDGE_GAP_SECONDS,
                            guard_before_ticks=ABSOLUTE_F0_ENDPOINT_GUARD_BEFORE_TICKS,
                            guard_after_ticks=ABSOLUTE_F0_ENDPOINT_GUARD_AFTER_TICKS,
                            bridge_evidence_intervals=(), note_descriptors=(),
                            source_sha256=None, vocal_track_index=None,
                            diagnostic_pagination=None, cancel=None):
    """Build one absolute-F0 curve representation for GUI and MCP export.

    Source-active frames are emitted verbatim except for an isolated same-note
    spike above 300 cents.  ``voiced_mask=false`` is treated as a real silence
    or phoneme guard by default.  A short gap is bridged only when it belongs
    to one note and the caller supplies explicit bridge evidence covering the
    gap.  Long rests and all unsupported gaps keep an explicit ``-100`` break.
    Endpoint guards follow the local trend rather than flattening a
    Chinese-opera head or tail.
    """
    import numpy as np
    values = np.asarray(pitch, dtype=np.float64)
    mask = np.asarray(voiced_mask, dtype=bool)
    if values.ndim != 1 or mask.shape != values.shape:
        raise ValueError('音高与有声标记长度不一致。')
    step = float(time_step_seconds)
    origin = float(time_origin_seconds)
    if not math.isfinite(step) or not .001 <= step <= .1:
        raise ValueError('连续音高时间步长无效。')
    if not math.isfinite(origin):
        raise ValueError('连续音高时间偏移无效。')
    if type(axis_offset_ticks) is not int or axis_offset_ticks < 0:
        raise ValueError('音高轴偏移无效。')
    if type(min_voiced_run_frames) is not int or min_voiced_run_frames < 1:
        raise ValueError('最短有声段无效。')
    if not math.isfinite(bridge_gap_seconds) or not 0 <= bridge_gap_seconds <= .5:
        raise ValueError('音高短缺口阈值无效。')
    if type(guard_before_ticks) is not int or type(guard_after_ticks) is not int or min(guard_before_ticks, guard_after_ticks) < 0:
        raise ValueError('音高端点保护长度无效。')
    if cancel is not None and not callable(cancel):
        raise ValueError('音高导出取消检查器必须可调用或为 None。')
    def check_cancel():
        if cancel is not None and cancel() is True:
            raise InterruptedError('音高曲线导出已取消。')
    if not callable(seconds_to_tick) or not callable(tick_to_seconds):
        raise ValueError('音高时间转换器必须可调用。')
    evidence = []
    for interval in bridge_evidence_intervals:
        try:
            start, end = map(float, interval)
        except (TypeError, ValueError) as error:
            raise ValueError('音高桥接证据时间无效。') from error
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError('音高桥接证据时间无效。')
        evidence.append((start, end))
    intervals = []
    for interval in note_intervals:
        try:
            start, end = map(float, interval)
        except (TypeError, ValueError) as error:
            raise ValueError('音高音符时间无效。') from error
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError('音高音符的播放时间无效。')
        intervals.append((start, end))
    times = np.arange(values.size, dtype=np.float64) * step + origin
    owner = assign_note_owners(times, intervals)
    offset_diagnostics = analyze_sustained_pitch_offsets(
        values, mask, owner, step, intervals, note_descriptors,
        origin, source_sha256=source_sha256,
        vocal_track_index=vocal_track_index,
        diagnostic_pagination=diagnostic_pagination,
    )
    # Overlapping notes are ambiguous for edits, but their measured F0 is
    # still valid evidence and must not disappear from the exported curve.
    inside = owner != -1
    values, spike_audit = stabilize_isolated_pitch_spikes(
        values, mask, owner, step, origin, note_intervals=intervals)
    active = mask & np.isfinite(values) & (values >= 0) & (values <= 127) & inside
    written = np.zeros(values.size, dtype=bool)
    short_runs = duplicate_frames = 0
    errors, curve_values = [], []
    raw_runs = []
    for left, right in _pitch_runs(active):
        if right - left < min_voiced_run_frames:
            short_runs += 1
            continue
        current, expected = {}, {}
        for frame in range(int(left), int(right)):
            if frame % 8192 == 0:
                check_cancel()
            tick = int(round(float(seconds_to_tick(float(times[frame]))))) + axis_offset_ticks
            cents = int(round(float(values[frame]) * 100.))
            current[tick] = cents
            expected.setdefault(tick, []).append(float(values[frame]) * 100.)
        if not current:
            continue
        ordered = sorted(current.items())
        written[left:right] = True
        for tick, samples in expected.items():
            actual = float(current[tick])
            errors.extend(abs(actual - sample) for sample in samples)
            curve_values.append(actual)
            duplicate_frames += max(0, len(samples) - 1)
        raw_runs.append({'left': int(left), 'right': int(right), 'ordered': ordered,
                         'first_tick': ordered[0][0], 'last_tick': ordered[-1][0],
                         'first_value': ordered[0][1], 'last_value': ordered[-1][1]})
    segments = []
    bridged_gaps = 0
    bridged_seconds = 0.
    bridge_points_count = 0
    retained_gaps = 0
    retained_gap_samples = []
    for run in raw_runs:
        if not segments:
            segments.append({'ordered': list(run['ordered']), 'first_tick': run['first_tick'],
                             'last_tick': run['last_tick'], 'first_value': run['first_value'],
                             'last_value': run['last_value'], 'last_source_ordered': run['ordered'],
                             'raw_run_count': 1, 'right': run['right']})
            continue
        previous = segments[-1]
        missing_frames = max(0, int(run['left']) - int(previous['right']))
        fine_gap = float(missing_frames) * step
        previous_owner = int(owner[int(previous['right']) - 1])
        next_owner = int(owner[int(run['left'])])
        gap_owners = owner[int(previous['right']):int(run['left'])]
        same_note_gap = (previous_owner >= 0 and previous_owner == next_owner
                         and gap_owners.size > 0
                         and np.all(gap_owners == previous_owner))
        gap_start = float(times[int(previous['right'])] ) if int(previous['right']) < times.size else float('inf')
        gap_end = float(times[int(run['left'])])
        explicitly_bridged = any(start <= gap_start + 1e-12 and gap_end <= end + 1e-12
                                 for start, end in evidence)
        if fine_gap <= bridge_gap_seconds + 1e-12 and same_note_gap and explicitly_bridged:
            bridge = _bridge_points(previous['last_source_ordered'], run['ordered'])
            previous['ordered'].extend(bridge)
            previous['ordered'].extend(run['ordered'])
            previous['last_tick'] = run['last_tick']
            previous['last_value'] = run['last_value']
            previous['last_source_ordered'] = run['ordered']
            previous['raw_run_count'] += 1
            previous['right'] = run['right']
            bridge_points_count += len(bridge)
            if missing_frames:
                bridged_gaps += 1
                bridged_seconds += fine_gap
        else:
            if missing_frames:
                retained_gaps += 1
                if len(retained_gap_samples) < 32:
                    retained_gap_samples.append({
                        'start_seconds': round(gap_start, 9),
                        'end_seconds': round(gap_end, 9),
                        'seconds': round(fine_gap, 9),
                        'same_note': bool(same_note_gap),
                        'reason': ('too_long' if fine_gap > bridge_gap_seconds + 1e-12
                                   else 'note_boundary' if not same_note_gap
                                   else 'no_explicit_evidence'),
                    })
            segments.append({'ordered': list(run['ordered']), 'first_tick': run['first_tick'],
                             'last_tick': run['last_tick'], 'first_value': run['first_value'],
                             'last_value': run['last_value'], 'last_source_ordered': run['ordered'],
                             'raw_run_count': 1, 'right': run['right']})
    points = []
    guard_before = guard_after = 0
    max_guard_seconds = 0.
    for index, segment in enumerate(segments):
        check_cancel()
        previous_last = segments[index - 1]['last_tick'] if index else None
        next_first = segments[index + 1]['first_tick'] if index + 1 < len(segments) else None
        first_tick, last_tick = int(segment['first_tick']), int(segment['last_tick'])
        start_guard = max(0, first_tick - guard_before_ticks)
        if previous_last is not None:
            start_guard = max(start_guard, int(previous_last) + 2)
        if start_guard >= first_tick:
            start_guard = first_tick
        end_guard = last_tick + guard_after_ticks
        if next_first is not None:
            end_guard = min(end_guard, int(next_first) - 2)
        if end_guard <= last_tick:
            end_guard = last_tick
        points.append((start_guard - 1, -100))
        if start_guard < first_tick:
            head_slope = _endpoint_slope(segment['ordered'], False)
            points.extend(_guard_points((first_tick, segment['first_value']), head_slope,
                                        range(start_guard, first_tick)))
            guard_before += first_tick - start_guard
            max_guard_seconds = max(max_guard_seconds, abs(float(tick_to_seconds(first_tick))
                                                            - float(tick_to_seconds(start_guard))))
        points.extend(segment['ordered'])
        if end_guard > last_tick:
            tail_slope = _endpoint_slope(segment['ordered'], True)
            points.extend(_guard_points((last_tick, segment['last_value']), tail_slope,
                                        range(last_tick + 1, end_guard + 1)))
            guard_after += end_guard - last_tick
            max_guard_seconds = max(max_guard_seconds, abs(float(tick_to_seconds(end_guard))
                                                            - float(tick_to_seconds(last_tick))))
        points.append((end_guard + 1, -100))
    non_break_values = [value for _tick, value in points if value != -100]
    return {
        'algorithm': ABSOLUTE_F0_ALGORITHM,
        'points': points,
        'times': times,
        'active_mask': active,
        'written_mask': written,
        'raw_runs': raw_runs,
        'segments': segments,
        'note_owner': owner,
        'source_points': sum(len(run['ordered']) for run in raw_runs),
        'gated_frames': int(active.sum()),
        'written_frames': int(written.sum()),
        'short_runs_dropped': short_runs,
        'duplicate_frame_collisions': duplicate_frames,
        'errors': errors,
        'curve_values': curve_values,
        'bridged_gaps': bridged_gaps,
        'bridge_points': bridge_points_count,
        'bridged_seconds': bridged_seconds,
        'gaps_retained_silent': retained_gaps,
        'retained_gap_samples': retained_gap_samples,
        'bridge_evidence_intervals': evidence,
        'bridge_policy': (
            'same-note short voiced_mask=false gaps only with explicit continuous-pitch evidence; '
            'otherwise retain an explicit -100 break'),
        'isolated_spike_policy': spike_audit,
        'pitch_offset_diagnostics': offset_diagnostics,
        'native_guard_points': guard_before + guard_after,
        'native_guard_before_points': guard_before,
        'native_guard_after_points': guard_after,
        'native_guard_extension_seconds_max': max_guard_seconds,
        'object_points': len(points),
        'curve_pitch_span_cents': (max(non_break_values) - min(non_break_values)) if non_break_values else None,
    }

def absolute_curve(track, project, result):
    """Install measured absolute F0 through the shared GUI/MCP export core."""
    import numpy as np
    from libresvip.core.time_sync import TimeSynchronizer
    from libresvip.model.point import Point
    from libresvip.model.base import ParamCurve
    clock = TimeSynchronizer(project.song_tempo_list)
    vocal_tracks = [candidate for candidate in getattr(project, 'track_list', [])
                    if hasattr(candidate, 'note_list') and hasattr(candidate, 'edited_params')]
    vocal_track_index = next(
        (index for index, candidate in enumerate(vocal_tracks) if candidate is track), None)
    intervals, descriptors = pitch_note_descriptors(
        track.note_list, clock, vocal_track_index=vocal_track_index)
    curve = build_absolute_f0_curve(
        result.midi_pitch, result.voiced_mask, result.time_step_seconds, intervals,
        clock.get_actual_ticks_from_secs, clock.get_actual_secs_from_ticks,
        note_descriptors=descriptors,
        source_sha256=getattr(result, 'source_sha256', None),
        vocal_track_index=vocal_track_index,
    )
    points = [Point.start_point()]
    points.extend(Point(tick, value) for tick, value in curve['points'])
    points.append(Point.end_point())
    track.edited_params.pitch = ParamCurve(points=points)
    return {
        'source': 'absolute_rmvpe',
        'algorithm': curve['algorithm'],
        'voiced_frames': curve['gated_frames'],
        'runs': len(curve['raw_runs']),
        'segments': len(curve['segments']),
        'points': curve['source_points'],
        'bridge_points': curve['bridge_points'],
        'bridged_gaps': curve['bridged_gaps'],
        'bridged_seconds': round(curve['bridged_seconds'], 9),
        'gaps_retained_silent': curve['gaps_retained_silent'],
        'retained_gap_samples': curve['retained_gap_samples'],
        'bridge_policy': curve['bridge_policy'],
        'isolated_spike_policy': curve['isolated_spike_policy'],
        'pitch_offset_diagnostics': curve['pitch_offset_diagnostics'],
        'source_sha256': curve['pitch_offset_diagnostics']['source_sha256'],
        'native_guard_points': curve['native_guard_points'],
        'native_guard_extension_seconds_max': round(
            curve['native_guard_extension_seconds_max'], 9),
        'object_points': len(points),
        'short_runs_dropped': curve['short_runs_dropped'],
        'duplicate_frame_collisions': curve['duplicate_frame_collisions'],
    }

def prepare_audio(waveform, sample_rate):
    """Avoid stereo cancellation and hard clipping before the pitch model."""
    import numpy as np
    import librosa
    if type(sample_rate) not in (int, float) or not math.isfinite(sample_rate) or sample_rate < 8000:
        raise ValueError('音高提取需要有效的音频采样率。')
    x = np.asarray(waveform, dtype=np.float32)
    if x.ndim not in (1, 2) or not np.isfinite(x).all():
        raise ValueError('音高提取音频包含无效采样或声道。')
    if not len(x):
        return np.empty(0, dtype=np.float32)
    x = x - np.mean(x, axis=0)
    if x.ndim == 2:
        powers = np.mean(x * x, axis=0)
        average = x.mean(axis=1)
        x = average if np.mean(average * average) >= np.max(powers) * .2 else x[:, np.argmax(powers)]
    if sample_rate != 16000:
        x = librosa.resample(x, orig_sr=sample_rate, target_sr=16000)
    peak = float(np.max(abs(x), initial=0))
    if peak > .98:
        x = x * (.98 / peak)
    return np.ascontiguousarray(x, dtype=np.float32)


def supported_pitch(result):
    """Honor voicing and repair only isolated octave jumps lasting at most 20 ms."""
    import numpy as np
    from dataclasses import replace
    pitch = np.asarray(result.midi_pitch, dtype=np.float32).copy()
    step = result.time_step_seconds
    if pitch.ndim != 1 or not math.isfinite(step) or not .001 <= step <= .1:
        raise ValueError('音高数据或时间步长无效。')
    valid = np.isfinite(pitch) & (pitch >= 0) & (pitch <= 127)
    if result.voiced_mask is not None:
        mask = np.asarray(result.voiced_mask, dtype=bool)
        if mask.shape != pitch.shape:
            raise ValueError('音高与有声标记长度不一致。')
        valid &= mask
    pitch[~valid] = np.nan
    original = pitch.copy()
    for length in range(1, min(2, math.floor(.0200001/step))+1):
        for left in range(1, len(pitch) - length):
            right = left + length
            local = original[left-1:right+1]
            if not np.isfinite(local).all() or abs(local[-1] - local[0]) > .4:
                continue
            reference = np.linspace(local[0], local[-1], length+2)[1:-1]
            delta = local[1:-1] - reference
            # A whole sustained shifted note, semitone ornament or vibrato is
            # not an isolated octave inference error and must remain intact.
            octaves = np.rint(abs(delta) / 12)
            if (np.all((octaves >= 1) & (octaves <= 2)) and np.all(octaves == octaves[0])
                    and np.all(abs(abs(delta) - octaves * 12) < .45)
                    and np.all(np.sign(delta) == np.sign(delta[0]))):
                pitch[left:right] = reference
    return replace(result, midi_pitch=pitch, voiced_mask=valid)


def pitd_curve(notes, result, tempo):
    """Keep supported f0 detail and neutralize long unsupported pitch gaps."""
    import numpy as np
    step = result.time_step_seconds
    if not math.isfinite(step) or step <= 0 or not math.isfinite(tempo) or tempo <= 0:
        raise ValueError('音高曲线的时间步长或速度无效。')
    result = supported_pitch(result)
    points = {}
    seconds_per_tick = 1 / (tempo * 8)
    quantum = 5
    for note in sorted(notes, key=lambda value: value.onset):
        if not all(math.isfinite(v) for v in (note.onset, note.offset, note.pitch)) or note.offset <= note.onset:
            continue
        margin = min(.025, (note.offset - note.onset) * .15)
        first = max(0, math.ceil((note.onset + margin) / step))
        last = min(len(result.midi_pitch), math.ceil((note.offset - margin) / step))
        for index in range(first, last):
            pitch = result.midi_pitch[index]
            if not np.isfinite(pitch):
                continue
            tick = round((index * step / seconds_per_tick) / quantum) * quantum
            # The native writer rounds note pitches. Relative automation must
            # use that same center, otherwise fractional GAME pitches detune it.
            key = int(np.clip(round(note.pitch), 0, 127))
            points[tick] = round(float(np.clip((pitch - key) * 100, -1200, 1200)))
    if not points:
        return [], []
    sampled = sorted(points)
    points[max(0, sampled[0] - quantum)] = 0
    for left, right in zip(sampled, sampled[1:]):
        if (right - left) * seconds_per_tick >= .08:
            points[left + quantum] = 0
            points[right - quantum] = 0
    points[sampled[-1] + quantum] = 0
    return list(sorted(points)), [points[key] for key in sorted(points)]


def install(destination=None):
    """Patch only the assistant's v2 worker process, once per interpreter."""
    global _destination, _last_result
    _destination = destination
    _last_result = None
    from inference.API import rmvpe_api, ustx_api
    cls = rmvpe_api.RmvpeTranscriber
    if getattr(cls, '_vwa_pitch_guard', False):
        return
    original = cls.infer

    def infer(self, waveform, sample_rate, cancel_checker=None):
        global _last_result
        if cancel_checker and cancel_checker():
            raise InterruptedError('音高提取已取消。')
        audio = prepare_audio(waveform, sample_rate)
        result = supported_pitch(original(self, audio, 16000, cancel_checker=cancel_checker))
        _last_result = result
        cache_export_pitch(result)
        return result

    cls.infer = infer
    cls._vwa_pitch_guard = True
    ustx_api._build_pitd_curve = pitd_curve
