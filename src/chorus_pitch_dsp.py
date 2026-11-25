"""Continuous vocal pitch correction and sample-aligned native pitch effects."""
from ctypes import CDLL, POINTER, PyDLL, c_double, c_float, c_int, c_uint, c_void_p, cast
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import soxr


_FLOAT_PTR = POINTER(c_float)
_DOUBLE_PTR = POINTER(c_double)
_PLANAR = POINTER(_FLOAT_PTR)
_SIGNALSMITH_TONALITY_HZ = 8000.0
_OPTIONS = (
    0x00000001  # ProcessRealTime
    | 0x00010000  # ThreadingNever
    | 0x00100000  # WindowShort
    | 0x01000000  # FormantPreserved
    | 0x04000000  # PitchHighConsistency
    | 0x10000000  # ChannelsTogether
    | 0x20000000  # EngineFiner
)


@lru_cache(maxsize=8)
def _verified_signalsmith_dll(path_text, dll_stamp, manifest_stamp, manifest_text):
    try:
        manifest = json.loads(manifest_text)
        digest = hashlib.sha256(Path(path_text).read_bytes()).hexdigest()
        if manifest.get('dll_sha256', '').lower() == digest and int(manifest.get('abi', 0)) == 1:
            return Path(path_text)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def signalsmith_dll_path(root=None, environment=None):
    """Resolve an explicit developer Signalsmith DLL or the verified local component."""
    env = os.environ if environment is None else environment
    explicit = env.get('VWA_CHORUS_PITCH_DLL', '')
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    root = Path(root) if root else Path(__file__).resolve().parent
    if root.name == 'src':
        root = root.parent
    directory = root / 'components/native-audio'
    manifest_path = directory / 'chorus-pitch.json'
    dll_path = directory / 'vwa_chorus_pitch.dll'
    try:
        m_stat = manifest_path.stat()
        d_stat = dll_path.stat()
        manifest_text = manifest_path.read_text(encoding='utf-8-sig')
    except OSError:
        return None
    return _verified_signalsmith_dll(
        str(dll_path), (d_stat.st_size, d_stat.st_mtime_ns),
        (m_stat.st_size, m_stat.st_mtime_ns), manifest_text,
    )


def signalsmith_available(root=None, environment=None):
    return signalsmith_dll_path(root=root, environment=environment) is not None


@lru_cache(maxsize=4)
def _signalsmith_api(path_text):
    api = CDLL(path_text)
    abi_fn = api.vwa_chorus_pitch_abi
    abi_fn.restype = c_int
    abi_fn.argtypes = ()
    if abi_fn() != 1:
        raise RuntimeError('Signalsmith 原生修音组件 ABI 不匹配。')
    stretch_fn = api.vwa_chorus_pitch_stretch
    stretch_fn.restype = c_int
    stretch_fn.argtypes = (c_int, c_int, c_int, _FLOAT_PTR, _DOUBLE_PTR, c_int, c_float, _FLOAT_PTR)
    return stretch_fn


@lru_cache(maxsize=4)
def _signalsmith_time_warp_api(path_text):
    api = CDLL(path_text)
    abi_fn = api.vwa_chorus_pitch_abi
    abi_fn.restype = c_int
    abi_fn.argtypes = ()
    if abi_fn() != 1:
        return None
    warp_fn = getattr(api, 'vwa_chorus_time_warp', None)
    if warp_fn is None:
        return None
    warp_fn.restype = c_int
    warp_fn.argtypes = (
        c_int, c_int, c_int, c_int, c_int,
        _FLOAT_PTR, _DOUBLE_PTR, _FLOAT_PTR,
    )
    return warp_fn


def _signalsmith_time_warp_stream(clip, rate, pad_frames, source_map, check):
    dll = signalsmith_dll_path()
    if dll is None:
        return None
    warp_fn = _signalsmith_time_warp_api(str(dll))
    if warp_fn is None:
        return None
    check()
    channels, in_frames = clip.shape
    out_frames = len(source_map) - 1
    source = np.ascontiguousarray(clip, dtype=np.float32)
    control = np.ascontiguousarray(source_map, dtype=np.float64)
    output = np.empty((channels, out_frames), dtype=np.float32)
    latency = warp_fn(
        int(rate), int(channels), int(in_frames), int(out_frames), int(pad_frames),
        source.ctypes.data_as(_FLOAT_PTR),
        control.ctypes.data_as(_DOUBLE_PTR),
        output.ctypes.data_as(_FLOAT_PTR),
    )
    check()
    if latency < 0:
        raise RuntimeError(f'Signalsmith 连续时间拉伸返回错误码：{latency}')
    return output


def render_continuous_time_warp(clip, rate, pad_frames, source_map, check):
    """Continuously time-warp multichannel audio along a monotone output->source sample map."""
    channels, in_frames = clip.shape
    out_frames = len(source_map) - 1
    if out_frames <= 0:
        return np.zeros((channels, 0), dtype=np.float32)
    if not np.isfinite(source_map).all() or np.any(np.diff(source_map) < -1e-9):
        raise ValueError('时间拉伸映射包含无效或倒退节点。')
    if rate > 96000:
        work_rate = rate
        while work_rate > 96000:
            work_rate = round(work_rate / 2)
        scale = work_rate / rate
        down = soxr.resample(clip.T, rate, work_rate)
        work_out = max(1, round(out_frames * scale))
        work_pad = min(len(down) - 1, max(0, round(pad_frames * scale)))
        work_map = np.interp(
            np.linspace(0., out_frames, work_out + 1),
            np.arange(out_frames + 1),
            source_map,
        ) * scale
        work_map = np.maximum.accumulate(np.clip(work_map, 0., max(0., len(down) - work_pad - 1.)))
        warped = render_continuous_time_warp(down.T, work_rate, work_pad, work_map, check)
        restored = soxr.resample(warped.T, work_rate, rate).T
        if restored.shape[1] < out_frames:
            tail = np.repeat(restored[:, -1:], out_frames - restored.shape[1], axis=1)
            return np.ascontiguousarray(np.concatenate((restored, tail), axis=1), dtype=np.float32)
        return np.ascontiguousarray(restored[:, :out_frames], dtype=np.float32)
    rendered = _signalsmith_time_warp_stream(clip, rate, pad_frames, source_map, check)
    if rendered is not None:
        return rendered
    return np.ascontiguousarray(
        _waveform_overlap(clip[:, pad_frames:].T, rate, source_map[:-1], check).T,
        dtype=np.float32,
    )


@lru_cache(maxsize=4)
def _signalsmith_pitch_time_warp_api(path_text):
    api = CDLL(path_text)
    abi_fn = api.vwa_chorus_pitch_abi
    abi_fn.restype = c_int
    abi_fn.argtypes = ()
    if abi_fn() != 1:
        return None
    fn = getattr(api, 'vwa_chorus_pitch_time_warp', None)
    if fn is None:
        return None
    fn.restype = c_int
    fn.argtypes = (
        c_int, c_int, c_int, c_int, c_int,
        _FLOAT_PTR, _DOUBLE_PTR, _DOUBLE_PTR, c_int, c_float, _FLOAT_PTR,
    )
    return fn


def signalsmith_pitch_time_warp_available(root=None, environment=None):
    dll = signalsmith_dll_path(root=root, environment=environment)
    return dll is not None and _signalsmith_pitch_time_warp_api(str(dll)) is not None


def _signalsmith_pitch_time_warp_stream(
    clip, rate, pad_frames, source_map, shift, check,
    *, tonality_hz=_SIGNALSMITH_TONALITY_HZ, formants=True,
):
    dll = signalsmith_dll_path()
    if dll is None:
        return None
    fn = _signalsmith_pitch_time_warp_api(str(dll))
    if fn is None:
        return None
    check()
    channels, in_frames = clip.shape
    out_frames = len(source_map) - 1
    source = np.ascontiguousarray(clip, dtype=np.float32)
    smap = np.ascontiguousarray(source_map, dtype=np.float64)
    ctrl = np.ascontiguousarray(shift, dtype=np.float64) if shift is not None else None
    output = np.empty((channels, out_frames), dtype=np.float32)
    latency = fn(
        int(rate), int(channels), int(in_frames), int(out_frames), int(pad_frames),
        source.ctypes.data_as(_FLOAT_PTR),
        smap.ctypes.data_as(_DOUBLE_PTR),
        ctrl.ctypes.data_as(_DOUBLE_PTR) if ctrl is not None else None,
        1 if formants else 0,
        c_float(float(tonality_hz)),
        output.ctypes.data_as(_FLOAT_PTR),
    )
    check()
    if latency < 0:
        raise RuntimeError(f'Signalsmith 联合修音拉伸返回错误码：{latency}')
    return output


def render_continuous_pitch_time_warp(
    clip, rate, pad_frames, source_map, shift, check,
    *, tonality_hz=_SIGNALSMITH_TONALITY_HZ, formants=True,
):
    """Render time warp and continuous pitch shift in one single phase-coherent STFT pass."""
    channels, _ = clip.shape
    out_frames = len(source_map) - 1
    if out_frames <= 0:
        return np.zeros((channels, 0), dtype=np.float32)
    if shift is None or not np.any(shift):
        return render_continuous_time_warp(clip, rate, pad_frames, source_map, check)
    if len(shift) != out_frames or not np.isfinite(shift).all() or np.max(np.abs(shift)) > 72.:
        raise ValueError('联合修音曲线长度或音高无效。')
    if not np.isfinite(source_map).all() or np.any(np.diff(source_map) < -1e-9):
        raise ValueError('时间拉伸映射包含无效或倒退节点。')
    if rate > 96000:
        work_rate = rate
        while work_rate > 96000:
            work_rate = round(work_rate / 2)
        scale = work_rate / rate
        down = soxr.resample(clip.T, rate, work_rate)
        work_out = max(1, round(out_frames * scale))
        work_pad = min(len(down) - 1, max(0, round(pad_frames * scale)))
        work_map = np.interp(
            np.linspace(0., out_frames, work_out + 1),
            np.arange(out_frames + 1),
            source_map,
        ) * scale
        work_map = np.maximum.accumulate(np.clip(work_map, 0., max(0., len(down) - work_pad - 1.)))
        work_shift = np.interp(np.linspace(0., out_frames - 1, work_out), np.arange(out_frames), shift)
        warped = render_continuous_pitch_time_warp(
            down.T, work_rate, work_pad, work_map, work_shift, check,
            tonality_hz=tonality_hz, formants=formants,
        )
        restored = soxr.resample(warped.T, work_rate, rate).T
        if restored.shape[1] < out_frames:
            tail = np.repeat(restored[:, -1:], out_frames - restored.shape[1], axis=1)
            return np.ascontiguousarray(np.concatenate((restored, tail), axis=1), dtype=np.float32)
        return np.ascontiguousarray(restored[:, :out_frames], dtype=np.float32)
    rendered = _signalsmith_pitch_time_warp_stream(
        clip, rate, pad_frames, source_map, shift, check,
        tonality_hz=tonality_hz, formants=formants,
    )
    if rendered is not None:
        return rendered
    warped = render_continuous_time_warp(clip, rate, pad_frames, source_map, check)
    return render_continuous_pitch(warped, rate, shift, check, engine='auto')


def _signalsmith_stream(clip, rate, shift, check, *, tonality_hz=_SIGNALSMITH_TONALITY_HZ, formants=True):
    dll = signalsmith_dll_path()
    if dll is None:
        raise RuntimeError('未找到 Signalsmith 原生修音组件。')
    stretch_fn = _signalsmith_api(str(dll))
    check()
    channels, frames = clip.shape
    source = np.ascontiguousarray(clip, dtype=np.float32)
    control = np.ascontiguousarray(shift, dtype=np.float64)
    output = np.empty_like(source)
    latency = stretch_fn(
        int(rate), int(channels), int(frames),
        source.ctypes.data_as(_FLOAT_PTR),
        control.ctypes.data_as(_DOUBLE_PTR),
        1 if formants else 0,
        c_float(float(tonality_hz)),
        output.ctypes.data_as(_FLOAT_PTR),
    )
    check()
    if latency < 0:
        raise RuntimeError(f'Signalsmith 修音返回错误码：{latency}')
    return output


def render_signalsmith_pitch(clip, rate, shift, check, *, tonality_hz=_SIGNALSMITH_TONALITY_HZ, formants=True):
    """Render continuous multichannel pitch shift via Signalsmith Stretch (MIT)."""
    if clip.shape[1] != len(shift):
        raise ValueError('修音曲线与音频长度不一致。')
    if not np.any(shift):
        return clip
    if not np.isfinite(shift).all() or np.max(np.abs(shift)) > 72.:
        raise ValueError('修音曲线包含无效音高。')
    if rate <= 96000:
        return _signalsmith_stream(clip, rate, shift, check, tonality_hz=tonality_hz, formants=formants)
    work_rate = rate
    while work_rate > 96000:
        work_rate = round(work_rate / 2)
    down = soxr.resample(clip.T, rate, work_rate)
    control = np.interp(np.arange(len(down)) * (rate / work_rate), np.arange(len(shift)), shift)
    changed = _signalsmith_stream(down.T, work_rate, control, check, tonality_hz=tonality_hz, formants=formants)
    restored = soxr.resample(changed.T, work_rate, rate).T
    if restored.shape[1] < len(shift):
        return np.concatenate((restored, clip[:, restored.shape[1]:]), axis=1)
    return restored[:, :len(shift)]


def resolve_pitch_engine(engine='auto'):
    """Choose the concrete pitch engine independent of auto/manual edit origin."""
    if engine == 'signalsmith':
        return 'signalsmith'
    if engine == 'vwa':
        return 'vwa'
    if engine == 'r3':
        return 'r3'
    return 'signalsmith' if signalsmith_available() else 'vwa'


def render_continuous_pitch(clip, rate, shift, check, *, engine='auto', pedalboard_loader=None):
    """Unified continuous pitch rendering path independent of manual/auto edit flags."""
    if clip.shape[1] != len(shift):
        raise ValueError('修音曲线与音频长度不一致。')
    if not np.any(shift):
        return clip
    resolved = resolve_pitch_engine(engine)
    if resolved == 'signalsmith':
        return render_signalsmith_pitch(clip, rate, shift, check)
    if resolved == 'vwa':
        if engine == 'vwa' or (rate <= 96000 and float(np.max(np.abs(shift))) <= 1.0):
            return render_vocal_pitch(clip, rate, shift, check)
        try:
            if pedalboard_loader is not None:
                pedalboard_loader()
            return render_pitch(clip, rate, shift, check)
        except RuntimeError:
            return render_vocal_pitch(clip, rate, shift, check)
    if pedalboard_loader is not None:
        pedalboard_loader()
    return render_pitch(clip, rate, shift, check)


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
    if not np.isfinite(shift).all() or np.max(np.abs(shift)) > 72.:
        raise ValueError('修音曲线包含无效音高。')
    if rate > 96000:
        work_rate = rate
        while work_rate > 96000:
            work_rate = round(work_rate / 2)
        down = soxr.resample(clip.T, rate, work_rate)
        control = np.interp(np.arange(len(down)) * (rate / work_rate), np.arange(len(shift)), shift)
        changed = render_vocal_pitch(down.T, work_rate, control, check)
        restored = soxr.resample(changed.T, work_rate, rate).T
        if restored.shape[1] < len(shift):
            return np.concatenate((restored, clip[:, restored.shape[1]:]), axis=1)
        return restored[:, :len(shift)]
    check()
    # The inverse rate integrates an original-sample to converted-sample map.
    # Interpolating an oversampled source keeps the time-varying conversion smooth.
    clock = np.r_[0., np.cumsum(np.exp2(-np.asarray(shift, dtype=np.float64) / 12.))]
    target_len = max(1, round(clock[-1]))
    src_frames = np.interp(np.arange(target_len), clock, np.arange(len(clock)))
    expanded = soxr.resample(clip.T, rate, rate * 2, quality='HQ')
    positions = np.minimum(src_frames * 2., len(expanded) - 1.)
    left = positions.astype(np.int64)
    right = np.minimum(left + 1, len(expanded) - 1)
    fraction = (positions - left).astype(np.float32)[:, None]
    converted = expanded[left] * (1. - fraction) + expanded[right] * fraction
    return _waveform_overlap(converted, rate, clock[:-1], check).T


def voiced_tail_end(audio, start, limit, rate):
    """Walk forward from a pitch edit end until continuous voiced audio naturally releases."""
    if limit <= start or start <= 0:
        return start
    from scipy.signal import correlate

    ref_win = round(.040 * rate)
    a0 = max(0, start - ref_win)
    b0 = min(len(audio), start + ref_win)
    if b0 <= a0:
        return start
    channel = int(np.argmax(np.mean(audio[a0:b0] ** 2, axis=0)))
    ref_slice = audio[a0:start, channel].astype(np.float64)
    ref_rms = float(np.sqrt(np.mean(ref_slice ** 2))) if len(ref_slice) else 0.
    if ref_rms < 1.5e-3:
        return start
    floor_rms = max(1.5e-3, .08 * ref_rms)
    hop = max(1, round(.010 * rate))
    radius = max(1, round(.015 * rate))
    min_lag = max(1, rate // 750)
    max_lag = max(min_lag + 1, rate // 75)
    pos = start
    while pos < limit:
        left, right = max(0, pos - radius), min(len(audio), pos + radius)
        frame = audio[left:right, channel].astype(np.float64)
        if len(frame) <= max_lag:
            break
        rms = float(np.sqrt(np.mean(frame ** 2)))
        if rms < floor_rms:
            return min(limit, pos + hop)
        frame -= frame.mean()
        energy = float(np.dot(frame, frame))
        if energy <= 1e-12:
            return min(limit, pos + hop)
        periodic = correlate(frame, frame, mode='full', method='fft')[len(frame) - 1:]
        best = float(np.max(periodic[min_lag:min(max_lag + 1, len(periodic))], initial=0.))
        if best < .45 * energy:
            return min(limit, pos + hop)
        pos += hop
    return limit


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
    """Restore macro vocal level smoothly without injecting 12-90 Hz pitch-period flutter."""
    from scipy.signal import butter, sosfiltfilt

    radius, hop = round(.015 * rate), round(.008 * rate)
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
    gain = np.interp(np.arange(len(mixed)), positions, np.clip(ratio, .78, 1.35))
    delta = mixed * (gain[:, None] - 1.)
    sos = butter(5, 115, 'highpass', fs=rate, output='sos')
    return (mixed + sosfiltfilt(sos, delta, axis=0)).astype(np.float32)


def preserve_low_transients(dry, tuned, rate):
    """Restore low-band energy where pitch rendering has added a short impulse."""
    from scipy.signal import butter, sosfiltfilt

    width = max(1, round(.020 * rate))
    step = max(1, round(.001 * rate))
    if len(tuned) < 2 * width:
        return
    filt = butter(5, 100, 'lowpass', fs=rate, output='sos')
    source, result = (sosfiltfilt(filt, wave, axis=0) for wave in (dry, tuned))
    left = np.arange(0, len(tuned) - width + 1, step)

    def level(audio):
        integral = np.r_[0., np.cumsum(np.mean(audio ** 2, axis=1))]
        return np.sqrt(np.maximum(0., integral[left + width] - integral[left]) / width)

    old, new = level(source), level(result)
    hits = left[(new > np.maximum(.001, 3 * old)) & (new > old + .0015)]
    if not len(hits):
        return
    # Use one shared, gently eased envelope for all channels. Leave the
    # fundamental and upper harmonics of the rest of the performance alone.
    fade = max(1, round(.012 * rate))
    weight = np.zeros(len(tuned), dtype=np.float32)
    for position in hits:
        a, b = max(0, position - fade), min(len(tuned), position + width + fade)
        samples = np.arange(a, b)
        strength = np.minimum(samples - position + fade, position + width + fade - samples) / fade
        weight[a:b] = np.maximum(weight[a:b], np.clip(strength, 0., 1.))
    weight *= weight * (3. - 2. * weight)
    tuned += (source - result) * weight[:, None]
