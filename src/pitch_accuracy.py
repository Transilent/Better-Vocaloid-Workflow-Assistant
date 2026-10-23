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
    import numpy as np
    from types import SimpleNamespace
    with np.load(path, allow_pickle=False) as data:
        pitch = np.asarray(data['midi_pitch'], dtype=np.float32)
        mask = np.asarray(data['voiced_mask'], dtype=bool)
        step = float(data['time_step'])
    if (pitch.ndim != 1 or mask.shape != pitch.shape or not .001 <= step <= .1
            or not np.isfinite(pitch[mask]).all() or ((pitch[mask] < 0) | (pitch[mask] > 127)).any()):
        raise ValueError('连续音高数据无效，请重新提取音高。')
    pitch = pitch.copy(); pitch[~mask] = np.nan
    return SimpleNamespace(midi_pitch=pitch, voiced_mask=mask, time_step_seconds=step)


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


def absolute_curve(track, project, result):
    """Use measured absolute f0, bypassing USTX's simulated portamento base."""
    import numpy as np
    from libresvip.core.time_sync import TimeSynchronizer
    from libresvip.model.point import Point
    from libresvip.model.base import ParamCurve
    clock = TimeSynchronizer(project.song_tempo_list)
    pitch = result.midi_pitch
    times = np.arange(len(pitch)) * result.time_step_seconds
    active = result.voiced_mask & np.isfinite(pitch)
    inside = np.zeros(len(pitch), dtype=bool)
    for note in track.note_list:
        start, end = clock.get_actual_secs_from_ticks(note.start_pos), clock.get_actual_secs_from_ticks(note.end_pos)
        inside |= (times >= start) & (times < end)
    active &= inside
    changes = np.diff(np.r_[False, active, False].astype(int))
    points = [Point.start_point()]
    # Sentinel-separated runs do not invent pitch in pauses or unsupported f0.
    runs = 0
    for left, right in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]):
        if right - left < 3:
            continue
        current = {}
        for i in range(left, right):
            tick = round(clock.get_actual_ticks_from_secs(float(times[i]))) + 1920
            current[tick] = round(float(pitch[i]) * 100)
        ordered = sorted(current.items())
        if not ordered:
            continue
        points.append(Point(ordered[0][0], -100))
        points.extend(Point(tick, value) for tick, value in ordered)
        points.append(Point(ordered[-1][0] + 1, -100))
        runs += 1
    points.append(Point.end_point())
    track.edited_params.pitch = ParamCurve(points=points)
    return {'source': 'absolute_rmvpe', 'voiced_frames': int(active.sum()), 'runs': runs,
            'points': sum(p.y != -100 for p in points)}


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
