"""Continuous vocal pitch correction and sample-aligned native pitch effects."""
from ctypes import POINTER, PyDLL, c_double, c_float, c_int, c_uint, c_void_p, cast
from functools import lru_cache

import numpy as np
import soxr


_FLOAT_PTR = POINTER(c_float)
_PLANAR = POINTER(_FLOAT_PTR)
_OPTIONS = (
    0x00000001  # ProcessRealTime
    | 0x00010000  # ThreadingNever
    | 0x00100000  # WindowShort
    | 0x01000000  # FormantPreserved
    | 0x04000000  # PitchHighConsistency
    | 0x10000000  # ChannelsTogether
    | 0x20000000  # EngineFiner
)


@lru_cache(maxsize=1)
def _rubberband():
    # The managed Pedalboard wheel exports the public Rubber Band C API.
    # time_stretch selects a fixed-ratio pitch mode even for variable input.
    try:
        import pedalboard_native

        # These calls process one short block on a background thread. Keep the
        # GIL through each call instead of competing with the audio pump for it
        # several times per block.
        api = PyDLL(pedalboard_native.__file__)
        for name, result, args in (
            ('new', c_void_p, (c_uint, c_uint, c_int, c_double, c_double)),
            ('delete', None, (c_void_p,)),
            ('get_engine_version', c_int, (c_void_p,)),
            ('get_preferred_start_pad', c_uint, (c_void_p,)),
            ('get_start_delay', c_uint, (c_void_p,)),
            ('set_max_process_size', None, (c_void_p, c_uint)),
            ('set_pitch_scale', None, (c_void_p, c_double)),
            ('process', None, (c_void_p, _PLANAR, c_uint, c_int)),
            ('available', c_int, (c_void_p,)),
            ('retrieve', c_uint, (c_void_p, _PLANAR, c_uint)),
        ):
            fn = getattr(api, 'rubberband_' + name)
            fn.restype, fn.argtypes = result, args
    except (ImportError, OSError, AttributeError) as error:
        raise RuntimeError('修音组件不支持连续变调，请在模型与组件中修复 Pedalboard。') from error
    return api


def _pointers(planar, frame):
    start = planar.ctypes.data + frame * planar.strides[1]
    return (_FLOAT_PTR * len(planar))(
        *(cast(start + channel * planar.strides[0], _FLOAT_PTR) for channel in range(len(planar))))


def _stream(clip, rate, shift, check):
    api = _rubberband()
    channels, frames = clip.shape
    ratios = np.exp2(shift / 12.)
    state = api.rubberband_new(rate, channels, _OPTIONS, 1., ratios[0])
    if not state:
        raise RuntimeError('无法初始化合唱修音。')
    try:
        if api.rubberband_get_engine_version(state) != 3:
            raise RuntimeError('修音组件缺少 Rubber Band R3。')
        start_pad = api.rubberband_get_preferred_start_pad(state)
        delay = api.rubberband_get_start_delay(state)
        block = max(64, round(512 * rate / 44100))
        api.rubberband_set_max_process_size(state, block)
        # Pad before the first sample and give the tail room to drain. Advance
        # scheduled changes and trim the output by the reported start delay.
        total = start_pad + frames + start_pad + delay + block
        source = np.zeros((channels, total), dtype=np.float32)
        source[:, start_pad:start_pad + frames] = clip
        output = np.empty((channels, frames + delay), dtype=np.float32)
        written = 0
        for start in range(0, total, block):
            check()
            count = min(block, total - start)
            at = min(frames - 1, max(0, start - start_pad + delay + count // 2))
            api.rubberband_set_pitch_scale(state, ratios[at])
            api.rubberband_process(state, _pointers(source, start), count, start + count == total)
            available = min(api.rubberband_available(state), output.shape[1] - written)
            if available > 0:
                written += api.rubberband_retrieve(state, _pointers(output, written), available)
            if written == output.shape[1]:
                return output[:, delay:]
        raise RuntimeError('修音组件返回的采样不足。')
    finally:
        api.rubberband_delete(state)


def render_pitch(clip, rate, shift, check):
    """Render a channel-major excerpt of the same length as its input."""
    if clip.shape[1] != len(shift):
        raise ValueError('修音曲线与音频长度不一致。')
    if not np.any(shift):
        return clip
    if not np.isfinite(shift).all() or np.max(np.abs(shift)) > 72.:
        raise ValueError('修音曲线包含无效音高。')
    if rate <= 96000:
        return _stream(clip, rate, shift, check)
    # Use a stable analysis rate for very high-rate recordings, then restore
    # the exact project length. Voice harmonics stay well within this band.
    work_rate = rate
    while work_rate > 96000:
        work_rate = round(work_rate / 2)
    down = soxr.resample(clip.T, rate, work_rate)
    control = np.interp(np.arange(len(down)) * (rate / work_rate), np.arange(len(shift)), shift)
    changed = _stream(down.T, work_rate, control, check)
    restored = soxr.resample(changed.T, work_rate, rate).T
    if restored.shape[1] < len(shift):
        return np.concatenate((restored, clip[:, restored.shape[1]:]), axis=1)
    return restored[:, :len(shift)]


def _waveform_overlap(source, rate, source_clock, check):
    """Restore the output clock with phase-matched, overlapping waveform grains."""
    from scipy.signal import correlate

    frames = len(source_clock)
    hop = max(16, round(rate * .017))
    radius = max(4, round(rate * .010))
    pad = hop + radius
    expected = np.rint(source_clock[::hop]).astype(np.int64) + pad
    padded = np.pad(source, ((pad, pad + 2 * hop), (0, 0)))
    mono = padded.mean(axis=1)
    channel_power = np.mean(padded ** 2, axis=0)
    if np.mean(mono ** 2) < .2 * channel_power.max():
        mono = padded[:, channel_power.argmax()]
    energy = np.r_[0., np.cumsum(mono.astype(np.float64) ** 2)]
    fade = (.5 - .5 * np.cos(np.pi * np.arange(hop) / hop)).astype(np.float32)[:, None]
    output = np.empty((frames + 2 * hop, source.shape[1]), dtype=np.float32)
    head = expected[0]
    output[:2 * hop] = padded[head:head + 2 * hop]
    for index, nominal in enumerate(expected[1:], 1):
        check()
        prior = head + hop
        left, right = max(head + max(1, hop // 4), nominal - radius), nominal + radius
        tail = mono[prior:prior + hop]
        power = float(np.dot(tail, tail))
        head = nominal
        if left <= right and abs(prior - nominal) > 2 and power >= hop * 1e-9:
            locations = np.arange(left, right + 1)
            match = correlate(mono[left:right + hop], tail, mode='valid')
            denominator = np.sqrt(power * np.maximum(energy[locations + hop] - energy[locations], 1e-10))
            scores = match / denominator - .14 * ((locations - nominal) / radius) ** 2
            nearest = int(np.argmax(scores))
            if scores[nearest] > .32:
                head = locations[nearest]
        offset = index * hop
        output[offset:offset + hop] *= 1. - fade
        output[offset:offset + hop] += padded[head:head + hop] * fade
        output[offset + hop:offset + 2 * hop] = padded[head + hop:head + 2 * hop]
    return output[:frames]


def render_vocal_pitch(clip, rate, shift, check):
    """Resample to change pitch, then recover duration without resetting the phase at each note."""
    if clip.shape[1] != len(shift):
        raise ValueError('修音曲线与音频长度不一致。')
    if not np.any(shift):
        return clip
    check()
    # The inverse rate integrates an original-sample to converted-sample map.
    # Interpolating an oversampled source keeps the time-varying conversion smooth.
    clock = np.r_[0., np.cumsum(np.exp2(-np.asarray(shift, dtype=np.float64) / 12.))]
    src_frames = np.interp(np.arange(round(clock[-1])), clock, np.arange(len(clock)))
    expanded = soxr.resample(clip.T, rate, rate * 2, quality='HQ')
    positions = np.minimum(src_frames * 2., len(expanded) - 1.)
    left = positions.astype(np.int64)
    right = np.minimum(left + 1, len(expanded) - 1)
    fraction = (positions - left).astype(np.float32)[:, None]
    converted = expanded[left] * (1. - fraction) + expanded[right] * fraction
    return _waveform_overlap(converted, rate, clock[:-1], check).T


def voiced_short_gap(audio, start, end, rate, max_seconds=.058):
    """A short correction gap can still be part of the same voiced sound."""
    if not 0 < end - start <= round(max_seconds * rate):
        return False
    from scipy.signal import correlate

    margin = round(.009 * rate)
    left, right = max(0, start - margin), min(len(audio), end + margin)
    channel = np.argmax(np.mean(audio[left:right] ** 2, axis=0))
    sides = np.r_[audio[left:start, channel], audio[end:right, channel]]
    level = float(np.mean(audio[start:end, channel] ** 2))
    if level < max(2e-8, .12 * float(np.mean(sides ** 2))):
        return False
    radius = round(.022 * rate)
    frames = max(1, round((end - start) / (.04 * rate)))
    for center in np.linspace(start, end, frames + 2, dtype=int)[1:-1]:
        frame = audio[max(0, center - radius):center + radius, channel].astype(np.float64)
        if np.mean(frame ** 2) < max(2e-8, .10 * level):
            return False
        frame -= frame.mean()
        periodic = correlate(frame, frame, mode='full', method='fft')[len(frame) - 1:]
        best = np.max(periodic[rate // 950:rate // 70 + 1], initial=0.)
        if best < .60 * max(periodic[0], 1e-12):
            return False
    return True


def blend_pitch(dry, wet, frames):
    """Account for the phase relationship at each wet/dry transition."""
    if not frames:
        dry[:] = wet
        return
    t = np.arange(frames, dtype=np.float64) / frames
    fade = t * t * (3. - 2. * t)
    for edge, weight in ((slice(None, frames), fade), (slice(-frames, None), fade[::-1])):
        source, changed = dry[edge], wet[edge]
        norm = max(np.linalg.norm(source) * np.linalg.norm(changed), 1e-12)
        correlation = np.sum(source * changed) / norm
        inverse = 1. - weight
        # Bound the gain when the two signals nearly oppose one another.
        power = np.maximum(.62, inverse ** 2 + weight ** 2 + 2. * correlation * inverse * weight)
        dry[edge] = (source * inverse[:, None] + changed * weight[:, None]) / np.sqrt(power)[:, None]
    dry[frames:len(dry) - frames] = wet[frames:len(wet) - frames]


def preserve_level(dry, mixed, rate):
    """Restore short-time vocal level while keeping the correction out of the low band."""
    from scipy.signal import butter, sosfiltfilt

    radius, hop = round(.007 * rate), round(.005 * rate)
    if len(mixed) <= max(24, 2 * radius):
        return mixed
    positions = np.r_[np.arange(0, len(mixed) - 1, hop), len(mixed) - 1]
    first = np.maximum(positions - radius, 0)
    last = np.minimum(positions + radius, len(mixed))
    levels = []
    for audio in (dry, mixed):
        integral = np.r_[0., np.cumsum(np.mean(audio.astype(np.float64) ** 2, axis=1))]
        levels.append((integral[last] - integral[first]) / (last - first))
    ratio = np.sqrt((levels[0] + 1e-8) / (levels[1] + 1e-8))
    gain = np.interp(np.arange(len(mixed)), positions, np.clip(ratio, .72, 1.48))
    delta = mixed * (gain[:, None] - 1.)
    sos = butter(5, 145, 'highpass', fs=rate, output='sos')
    return (mixed + sosfiltfilt(sos, delta, axis=0)).astype(np.float32)
