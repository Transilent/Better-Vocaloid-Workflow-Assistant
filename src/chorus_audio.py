"""One audio render plan for native audition and sample-aligned stem delivery."""
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf
import soxr

if __name__ == '__main__':
    extra = os.environ.get('VWA_MIXDOWN_EXTRA_PATHS', '').split(os.pathsep)
    sys.path[:0] = [str(Path(__file__).resolve().parent), *(path for path in extra if path)]

from chorus_project import Recording, SYLLABLE_GAIN, VARIANTS, sha256 as source_digest


def recording_key(project, track):
    selections = project.edits_for(track.id, 'take')
    if not selections:
        return track.sha
    sources = tuple((edit, take.sha, take.offset) for edit in selections
                    if (take := project.track(edit.source)) is not None)
    return track.sha, track.offset, sources


def monitor_tracks(project, solo):
    return tuple(track for track in project.tracks
                 if (track.id == solo if solo else not track.muted and track.role != 'take'))


def monitor_sources(project, solo):
    rows = monitor_tracks(project, solo)
    visible = {track.id for track in rows}
    takes = {edit.source for edit in project.edits if edit.track in visible and edit.kind == 'take'}
    return (*rows, *(track for track in project.tracks if track.id in takes))


def render_key(project, variant, solo=''):
    rendered = tuple((track.id, recording_key(project, track), track.offset)
                     for track in monitor_tracks(project, solo))
    ids = {row[0] for row in rendered}
    edits = tuple(edit for edit in project.edits
                  if edit.track in ids and edit.kind != 'take'
                  and (variant == 'processed' or edit.kind == variant))
    engine = project.pitch_engine if variant in ('pitch', 'processed') else ''
    return (project.first_frame, project.frames, project.rate, rendered, variant, edits, engine)


def _read_take(track, sample_rate, check):
    track.check_source()
    data, rate = sf.read(track.path, dtype='float32', always_2d=True)
    if not np.isfinite(data).all():
        raise ValueError(f'音频包含无效采样：{track.name}')
    check()
    if rate != sample_rate:
        data = soxr.resample(data, rate, sample_rate)
    track.check_source()
    return np.ascontiguousarray(data, dtype=np.float32)


def _window(samples, start, length):
    result = np.zeros((length, samples.shape[1]), dtype=np.float32)
    left, right = max(0, start), min(len(samples), start + length)
    if left < right:
        result[left - start:right - start] = samples[left:right]
    return result


def _crossfade(destination, source, frames):
    if frames <= 0:
        destination[:] = source
        return
    weight = np.ones((len(destination), 1), dtype=np.float32)
    ramp = np.linspace(0., 1., frames, endpoint=False, dtype=np.float32)
    weight[:frames, 0], weight[-frames:, 0] = ramp, ramp[::-1]
    destination[:] = destination * (1 - weight) + source * weight


def _comped_audio(project, track, rate, check):
    audio = _read_take(track, rate, check)
    origin = round(track.offset * rate)
    alternatives = {}
    for choice in project.edits_for(track.id, 'take'):
        take = project.track(choice.source)
        if take is None or take.role != 'take':
            continue
        if take.id not in alternatives:
            alternatives[take.id] = _read_take(take, rate, check)
        start = max(0, round(choice.start * rate) - origin)
        end = min(len(audio), round(choice.end * rate) - origin)
        if end <= start:
            continue
        alternate = _window(alternatives[take.id], origin + start - round(take.offset * rate), end - start)
        if alternate.shape[1] != audio.shape[1]:
            alternate = alternate.mean(axis=1, keepdims=True)
        _crossfade(audio[start:end], alternate, min(round(.016 * rate), (end - start) // 2))
    return audio


def analysis_recording(project, track, folder, check):
    """Speech and F0 read the same pre-effect take selection as the monitor."""
    key = recording_key(project, track)
    if isinstance(key, str):
        return track
    track.check_source()
    for choice in project.edits_for(track.id, 'take'):
        alternate = project.track(choice.source)
        if alternate:
            alternate.check_source()
    path = folder / ('take-' + sha256(repr(key).encode()).hexdigest() + '.wav')
    if not path.is_file():
        folder.mkdir(exist_ok=True, parents=True)
        audio = _comped_audio(project, track, track.rate, check)
        with TemporaryDirectory(dir=folder) as scratch:
            staged = Path(scratch) / 'comp.wav'
            sf.write(staged, audio, track.rate, subtype='FLOAT')
            check()
            staged.replace(path)
    derived = Recording.read(path, track.role, identity=track.id)
    return replace(derived, name=track.name, part=track.part, offset=track.offset,
                   score_lane=track.score_lane, timing_ref=track.timing_ref)


def _pedalboard():
    """Load the already managed optional DSP component via its public API."""
    from mixdown_runtime import cached_optional_packages, component_manifest, optional_packages

    root, spec, _ = component_manifest()
    packages = cached_optional_packages() or optional_packages(root, spec)
    if packages:
        path = str(packages)
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        import pedalboard
    except (ImportError, OSError) as error:
        raise RuntimeError('修音需要混音效果组件（Pedalboard）。可先试听原声或仅对齐。') from error
    return pedalboard


PITCH_CONTEXT_S = .09
PITCH_MAX_GAP_S = .095
PITCH_SMOOTH_S = .028
PITCH_FADE_S = .020
PITCH_TAIL_MAX_S = 1.50


def _pitch_active(region):
    return any(y for _, y in region.points) if region.points else bool(region.value)


def _voiced_curve_gap(voiced_curve, start, end, rate):
    midi, step = voiced_curve
    a = max(0, round((start / rate) / step))
    b = min(len(midi), max(a + 1, round((end / rate) / step)))
    if b <= a:
        return False
    return float(np.mean(np.isfinite(midi[a:b]))) >= .65


def compile_pitch_spans(regions, rate, size, samples=None, *, voiced_curve=None):
    """Compile active pitch edits into independent continuous voiced spans.

    Auto, manual, split and hand-drawn edits share one compilation rule so that
    equal curves render identically, splits preserve PCM bit-for-bit, and editing
    one span never perturbs any non-continuous neighbor.
    """
    from chorus_pitch_dsp import voiced_short_gap, voiced_tail_end

    context = round(PITCH_CONTEXT_S * rate)
    max_gap = round(PITCH_MAX_GAP_S * rate)
    window = max(1, round(PITCH_SMOOTH_S * rate))
    half_smooth = window // 2
    fade = round(PITCH_FADE_S * rate)
    edge_margin = half_smooth + fade
    tail_max = round(PITCH_TAIL_MAX_S * rate)
    untouched = tuple(
        (max(0, round(edit.start * rate)), min(size, round(edit.end * rate)))
        for edit in regions if edit.manual and not _pitch_active(edit)
    )
    active = []
    for region in sorted((row for row in regions if _pitch_active(row)),
                         key=lambda edit: (edit.start, edit.end)):
        start = max(0, round(region.start * rate))
        end = min(size, round(region.end * rate))
        if end > start:
            active.append((start, end, region))
    if not active:
        return ()

    # Partition strictly by acoustic continuity and explicit keep-original locks.
    grouped = [[active[0]]]
    for item in active[1:]:
        start, end, _ = item
        prev_end = max(stop for _, stop, _ in grouped[-1])
        blocked = any(a < start and prev_end < b for a, b in untouched)
        if not blocked and start <= prev_end + 1:
            grouped[-1].append(item)
        elif not blocked and 0 < start - prev_end <= max_gap:
            bridged = (voiced_short_gap(samples, prev_end, start, rate, PITCH_MAX_GAP_S)
                       if samples is not None else
                       _voiced_curve_gap(voiced_curve, prev_end, start, rate)
                       if voiced_curve is not None else False)
            if bridged:
                grouped[-1].append(item)
            else:
                grouped.append([item])
        else:
            grouped.append([item])

    # When actual PCM is present, keep phase-shifted voiced vowels inside one
    # continuous stream until the voice naturally releases to silence/unvoiced
    # audio instead of crossfading phase-rotated wet audio back into dry mid-note.
    if samples is not None:
        merged = [list(grouped[0])]
        for nxt in grouped[1:]:
            prev_end = max(stop for _, stop, _ in merged[-1])
            nxt_start = nxt[0][0]
            blocked = any(a < nxt_start and prev_end < b for a, b in untouched)
            if not blocked and nxt_start - prev_end <= tail_max:
                tail = voiced_tail_end(samples, prev_end, nxt_start, rate)
                if tail >= nxt_start - edge_margin:
                    merged[-1].extend(nxt)
                    continue
            merged.append(list(nxt))
        grouped = merged

    compiled = []
    prev_span_end = 0
    for idx, members in enumerate(grouped):
        raw_start = members[0][0]
        raw_end = max(end for _, end, _ in members)
        if samples is not None:
            lock_before = max((b for a, b in untouched if b <= raw_start), default=0)
            span_start = max(prev_span_end, lock_before, raw_start - edge_margin)
            next_raw_start = grouped[idx + 1][0][0] if idx + 1 < len(grouped) else size
            lock_after = min((a for a, b in untouched if a >= raw_end), default=size)
            upper = min(size, lock_after, max(raw_end, next_raw_start - edge_margin))
            min_end = min(upper, raw_end + edge_margin)
            tail_limit = min(upper, raw_end + tail_max)
            tail_end = voiced_tail_end(samples, raw_end, tail_limit, rate)
            span_end = min(upper, max(min_end, tail_end + fade))
        else:
            span_start = raw_start
            span_end = raw_end
        prev_span_end = span_end
        left = max(0, span_start - context)
        right = min(size, span_end + context)
        shift = np.zeros(right - left, dtype=np.float64)
        cursor = None
        for start, end, region in members:
            cents = region.value
            if region.points:
                x, y = zip(*region.points)
                cents = np.interp(np.arange(start, end) / rate, x, y)
            shift[start - left:end - left] = np.asarray(cents, dtype=np.float64) / 100.
            if cursor is not None and 0 < start - cursor <= max_gap:
                shift[cursor - left:start - left] = np.linspace(
                    shift[cursor - left - 1], shift[start - left], start - cursor + 2)[1:-1]
            cursor = max(cursor, end) if cursor is not None else end
        edges = np.pad(shift, (window // 2, (window - 1) // 2), mode='edge')
        prefix = np.r_[0., np.cumsum(edges)]
        smoothed = (prefix[window:] - prefix[:-window]) / window
        # Adaptive legato S-curve smoothing ONLY at shift-step boundaries
        # (|Δshift| > 0.70 st). Splits with Δshift == 0 skip this path completely,
        # preserving bit-exact split invariance while making melisma turns silky.
        if len(shift) > window:
            diffs = np.abs(np.diff(shift))
            jump_idx = np.flatnonzero(diffs > .70)
            if len(jump_idx):
                # Merge nearby step indices belonging to the same transition edge.
                clusters = []
                for pos in jump_idx:
                    if not clusters or pos - clusters[-1][1] > max(4, window // 3):
                        clusters.append([int(pos), int(pos), float(diffs[pos])])
                    else:
                        clusters[-1][1] = int(pos)
                        clusters[-1][2] = max(clusters[-1][2], float(diffs[pos]))
                for idx_c, (c_a, c_b, jump) in enumerate(clusters):
                    center = (c_a + c_b) // 2
                    left_val = float(shift[max(0, c_a)])
                    right_val = float(shift[min(len(shift) - 1, c_b + 1)])
                    opposing = (left_val * right_val < -0.04)
                    # Widen S-curve smoothly up to 95 ms on large opposing shift reversals (>=1.3 st)
                    # or >=1.8 st jumps while capping to 65% of neighbor spacing so short notes keep center.
                    if opposing and jump >= 1.3:
                        target_ms = min(.095, .048 + .024 * (jump - 1.3))
                    elif jump >= 1.8:
                        target_ms = min(.090, .050 + .022 * (jump - 1.8))
                    else:
                        target_ms = min(.075, PITCH_SMOOTH_S + .020 * (jump - .70))
                    prev_edge = clusters[idx_c - 1][1] if idx_c > 0 else 0
                    next_edge = clusters[idx_c + 1][0] if idx_c + 1 < len(clusters) else len(smoothed) - 1
                    max_half = max(window // 2 + 1, int(.65 * min(center - prev_edge, next_edge - center)))
                    wide = min(max(window + 2, round(target_ms * rate)), max(window + 2, 2 * max_half + 1))
                    if wide % 2 == 0:
                        wide += 1
                    half = wide // 2
                    seg_a = max(0, center - half)
                    seg_b = min(len(smoothed), center + half + 1)
                    if seg_b - seg_a < 5:
                        continue
                    # Smooth local neighborhood with raised-cosine kernel & blend edges cleanly.
                    local_pad = np.pad(smoothed[seg_a:seg_b], (half, half), mode='edge')
                    t_k = np.linspace(-1., 1., wide, dtype=np.float64)
                    k = .5 * (1. + np.cos(np.pi * t_k))
                    k /= k.sum()
                    blurred = np.convolve(local_pad, k, mode='valid')[:seg_b - seg_a]
                    n_loc = seg_b - seg_a
                    blend = np.sin(np.pi * np.arange(n_loc, dtype=np.float64) / max(1, n_loc - 1)) ** 2
                    smoothed[seg_a:seg_b] = smoothed[seg_a:seg_b] * (1. - blend) + blurred * blend
        shift = smoothed
        compiled.append((span_start, span_end, left, right, shift))
    return tuple(compiled)


def compiled_pitch_semitones(regions, times, *, duration, voiced_curve=None, sample_rate=1000):
    """Evaluate the exact compiled continuous pitch shift (in semitones) at take times."""
    times = np.asarray(times, dtype=np.float64)
    result = np.zeros(times.shape, dtype=np.float64)
    if not regions or duration <= 0.:
        return result
    size = max(1, round(duration * sample_rate))
    spans = compile_pitch_spans(regions, sample_rate, size, voiced_curve=voiced_curve)
    if not spans:
        return result
    indices = np.rint(times * sample_rate).astype(np.int64)
    for span_start, span_end, left, _right, shift in spans:
        inside = (indices >= span_start) & (indices < span_end)
        if np.any(inside):
            result[inside] = shift[indices[inside] - left]
    return result


def _shift_pitch(samples, rate, regions, check, *, engine='auto'):
    from chorus_cache import load_pitch_span, save_pitch_span, span_cache_key
    from chorus_pitch_dsp import (
        blend_pitch, bypass_unvoiced_sibilants, preserve_level, render_continuous_pitch,
        resolve_pitch_engine, signalsmith_dll_path,
    )

    spans = compile_pitch_spans(regions, rate, len(samples), samples)
    if not spans:
        return
    resolved_engine = resolve_pitch_engine(engine)
    engine_tag = f'{engine}->{resolved_engine}:vocal-v2'
    if resolved_engine == 'signalsmith':
        dll = signalsmith_dll_path()
        if dll is not None:
            st = dll.stat()
            engine_tag = f'{engine_tag}:{st.st_size}:{st.st_mtime_ns}'
    elif resolved_engine == 'vwa' and engine == 'auto':
        # Distinguish whether Pedalboard R3 is available so installing Pedalboard
        # later invalidates fallback cache entries for >1 st shifts.
        try:
            _pedalboard()
            engine_tag = f'{engine_tag}:r3ok'
        except RuntimeError:
            engine_tag = f'{engine_tag}:vwaonly'
    # Snapshot dry input so each independent voiced span reads immutable audio
    # and never perturbs a non-continuous neighbor.
    dry_source = samples.copy()
    for span_start, span_end, left, right, shift in spans:
        check()
        dry = dry_source[left:right]
        interval = slice(span_start - left, span_end - left)
        expected_shape = (span_end - span_start, dry.shape[1])
        cache_key = span_cache_key(
            dry, rate, shift, f'{engine_tag}:{span_start - left}:{span_end - left}')
        cached = load_pitch_span(cache_key, expected_shape)
        if cached is not None:
            samples[span_start:span_end] = cached
            continue
        clip = np.ascontiguousarray(dry.T)
        edited = render_continuous_pitch(
            clip, rate, shift, check, engine=engine, pedalboard_loader=_pedalboard).T
        if edited.shape != dry.shape or not np.isfinite(edited).all():
            raise ValueError('修音组件返回的长度、声道或采样无效。')
        edited = bypass_unvoiced_sibilants(dry, edited, rate, shift)
        mixed = dry.copy()
        fade = min(round(PITCH_FADE_S * rate), (span_end - span_start) // 2)
        blend_pitch(mixed[interval], edited[interval], fade)
        level = preserve_level(dry, mixed, rate)
        _crossfade(mixed[interval], level[interval], min(round(.006 * rate), (span_end - span_start) // 2))
        rendered_interval = np.ascontiguousarray(mixed[interval], dtype=np.float32)
        save_pitch_span(cache_key, rendered_interval)
        samples[span_start:span_end] = rendered_interval


def _render_knots(points, tolerance):
    """Join nearly affine runs without erasing the project's editable anchors."""
    kept = [points[0]]
    last = 0
    for i in range(2, len(points)):
        x, y = points[last]
        right, finish = points[i]
        speed = (finish - y) / (right - x)
        if any(abs(y + speed * (at - x) - value) > tolerance for at, value in points[last + 1:i]):
            kept.append(points[i - 1])
            last = i - 1
    return (*kept, points[-1])


def _compiled_shift_curve(regions, rate, size, samples):
    """Build a dense take-time semitone shift curve matching compile_pitch_spans."""
    curve = np.zeros(size, dtype=np.float64)
    for span_start, span_end, left, _right, shift in compile_pitch_spans(regions, rate, size, samples):
        curve[span_start:span_end] = shift[span_start - left:span_end - left]
    return curve


def _warp_audio(placed, edit, origin, rate, check, *, pitch_curve=None):
    """Fit segments between source knots with one continuous phase-coherent time stretch."""
    from chorus_pitch_dsp import render_continuous_pitch_time_warp, render_continuous_time_warp

    knots = _render_knots(edit.points, .00025 if edit.manual else .004)
    output, source = zip(*knots)
    outs = [round(time * rate) - origin for time in output]
    ins = [round(time * rate) - origin for time in source]
    out_total = outs[-1] - outs[0]
    in_total = ins[-1] - ins[0]
    if out_total <= 0:
        return np.zeros((0, placed.shape[1]), dtype=np.float32)
    # Preserve untouched dry PCM when all segments stay within 0.5 ms of 1:1 speed
    # and no joint pitch curve needs rendering.
    near_identity = all(
        (ins[i + 1] - ins[i]) <= 0 or abs((ins[i + 1] - ins[i]) - (outs[i + 1] - outs[i])) <= round(.0005 * rate)
        for i in range(len(outs) - 1)
    )
    if near_identity and abs(in_total - out_total) <= round(.001 * rate) and pitch_curve is None:
        return _window(placed, ins[0], out_total)
    rel_outs = np.asarray(outs, dtype=np.float64) - outs[0]
    rel_ins = np.maximum.accumulate(np.asarray(ins, dtype=np.float64) - ins[0])
    source_map = np.interp(np.arange(out_total + 1, dtype=np.float64), rel_outs, rel_ins)
    if len(knots) > 2:
        # Ease rate transitions across interior knots without drifting endpoints.
        slopes = np.diff(source_map)
        rate_span = float(np.max(slopes) - np.min(slopes)) if len(slopes) else 0.
        smooth_s = .030 if rate_span > .15 else .015
        smooth_width = max(1, round(smooth_s * rate))
        if smooth_width < len(slopes):
            kernel = np.ones(smooth_width, dtype=np.float64) / smooth_width
            pad_left = smooth_width // 2
            pad_right = smooth_width - 1 - pad_left
            eased = np.convolve(np.pad(slopes, (pad_left, pad_right), mode='edge'), kernel, mode='valid')
            eased = np.maximum(0., eased)
            total = float(np.sum(eased))
            if total > 1e-9 and source_map[-1] > 0.:
                eased *= source_map[-1] / total
                source_map = np.r_[0., np.cumsum(eased)]
    pad = round(.15 * rate)
    audio = _window(placed, ins[0] - pad, max(1, in_total) + pad * 2)
    if pitch_curve is not None:
        src_pos = ins[0] + source_map[:-1]
        in_bounds = (src_pos >= 0.) & (src_pos < len(pitch_curve))
        out_shift = np.zeros(out_total, dtype=np.float64)
        if np.any(in_bounds):
            idx = np.clip(np.rint(src_pos[in_bounds]).astype(np.int64), 0, len(pitch_curve) - 1)
            out_shift[in_bounds] = pitch_curve[idx]
        warped = render_continuous_pitch_time_warp(
            np.ascontiguousarray(audio.T), rate, pad, source_map, out_shift, check).T
    else:
        warped = render_continuous_time_warp(
            np.ascontiguousarray(audio.T), rate, pad, source_map, check).T
    if warped.shape != (out_total, placed.shape[1]) or not np.isfinite(warped).all():
        raise ValueError('时间拉伸组件返回了无效采样。')
    return np.ascontiguousarray(warped, dtype=np.float32)


def _timed_slice(placed, edit, origin, rate, start, end, check, *, pitch_curve=None):
    if not edit.points:
        return _window(placed, start - round(edit.value * rate), end - start)
    warped = _warp_audio(placed, edit, origin, rate, check, pitch_curve=pitch_curve)
    return _window(warped, start + origin - round(edit.start * rate), end - start)


def render_track(project, track, variant, check=lambda: None):
    """Render the take into the project's common half-open sample interval."""
    if variant not in VARIANTS:
        raise ValueError('试听版本无效。')
    rate = project.rate
    original = _comped_audio(project, track, rate, check)
    origin = project.first_frame
    offset = origin - round(track.offset * rate)
    timing = project.edits_for(track.id, 'timing') if variant in ('timing', 'processed') else ()
    syllables = project.edits_for(track.id, SYLLABLE_GAIN) if variant == 'processed' else ()
    dry_reference = None
    joint_dry_placed = None
    joint_pitch_curve = None
    if variant in ('pitch', 'processed'):
        regions = project.edits_for(track.id, 'pitch')
        active = tuple(row for row in regions if _pitch_active(row))
        if active:
            dry_pitch = original.copy() if (timing or syllables) else None
            # When warped timing edits and Signalsmith pitch both apply without
            # intermediate syllable gain, keep dry PCM + dense pitch curve so
            # warped phrases render in ONE single STFT pass (no double vocoding).
            if (variant == 'processed' and not syllables and any(edit.points for edit in timing)
                    and dry_pitch is not None):
                from chorus_pitch_dsp import resolve_pitch_engine, signalsmith_pitch_time_warp_available
                if resolve_pitch_engine(project.pitch_engine) == 'signalsmith' and signalsmith_pitch_time_warp_available():
                    take_curve = _compiled_shift_curve(regions, rate, len(dry_pitch), dry_pitch)
                    joint_dry_placed = _window(dry_pitch, offset, project.frames)
                    placed_curve = np.zeros(project.frames, dtype=np.float64)
                    left = max(0, offset)
                    right = min(len(take_curve), offset + project.frames)
                    if left < right:
                        placed_curve[left - offset:right - offset] = take_curve[left:right]
                    joint_pitch_curve = placed_curve
            _shift_pitch(original, rate, regions, check, engine=project.pitch_engine)
            if timing and dry_pitch is not None:
                from chorus_pitch_dsp import preserve_low_transients

                # Suppress pitch-induced low-band impulses once in take-time so timed
                # phrases do not run a redundant second Pedalboard time_stretch on dry audio.
                preserve_low_transients(dry_pitch, original, rate)
            if syllables and dry_pitch is not None:
                dry_reference = dry_pitch
    if syllables:
        from chorus_gain import render_syllable_gains

        render_syllable_gains(original, rate, syllables, check, dry_reference)
    placed = _window(original, offset, project.frames)
    rendered = placed.copy() if timing else placed
    if timing:
        from chorus_pitch_dsp import blend_pitch
    for edit in timing:
        check()
        start = max(0, round(edit.start * rate) - project.first_frame)
        end = min(project.frames, round(edit.end * rate) - project.first_frame)
        shift = round(edit.value * rate)
        if start >= end or (not edit.points and not shift):
            continue
        if edit.points and joint_dry_placed is not None and joint_pitch_curve is not None:
            source = _timed_slice(joint_dry_placed, edit, origin, rate, start, end, check,
                                  pitch_curve=joint_pitch_curve)
        else:
            source = _timed_slice(placed, edit, origin, rate, start, end, check)
        fade = min(round(.018 * rate), (end - start) // 2)
        blend_pitch(rendered[start:end], source, fade)
    if variant == 'processed':
        for edit in project.edits_for(track.id, 'gain'):
            start = max(0, round(edit.start * rate) - project.first_frame)
            end = min(project.frames, round(edit.end * rate) - project.first_frame)
            if start < end and edit.value:
                view = rendered[start:end]
                _crossfade(view, view * 10 ** (edit.value / 20.), min(round(.018 * rate), (end - start) // 2))
    return rendered


def _stereo_channels(data):
    return data if data.shape[1] <= 2 else data.mean(axis=1, keepdims=True)


def balance_vocal_level(data, *, target_dbfs=-18.0, max_gain_db=9.0, peak_ceiling=.891):
    """Apply gentle constant macro gain so multi-singer vocal stems start balanced for mixdown.

    Measures active vocal RMS (ignoring silence) and applies one constant linear gain with
    peak protection — zero dynamic pumping or coloration. Returns `(balanced_data, gain_db)`.
    """
    arr = np.asarray(data, dtype=np.float32)
    if arr.size == 0:
        return arr, 0.0
    mono = arr.mean(axis=1) if arr.ndim == 2 else arr
    abs_m = np.abs(mono)
    p95 = float(np.percentile(abs_m, 95))
    if p95 < 1e-4:
        return arr, 0.0
    active = mono[abs_m >= max(1e-4, .06 * p95)]
    if len(active) < 64:
        return arr, 0.0
    rms = float(np.sqrt(np.mean(active.astype(np.float64) ** 2)))
    if rms < 1e-6:
        return arr, 0.0
    cur_dbfs = 20.0 * float(np.log10(rms))
    gain_db = max(-max_gain_db, min(max_gain_db, target_dbfs - cur_dbfs))
    gain = float(10.0 ** (gain_db / 20.0))
    peak = float(np.max(np.abs(arr)))
    if peak * gain > peak_ceiling and peak > 1e-6:
        gain = peak_ceiling / peak
        gain_db = 20.0 * float(np.log10(max(gain, 1e-6)))
    if abs(gain_db) < .25:
        return arr, 0.0
    return np.ascontiguousarray(arr * np.float32(gain), dtype=np.float32), round(gain_db, 2)


def render_score_guide(project, track=None, *, gain_db=-14.0):
    """Synthesize a soft monitor-only electric-piano MIDI reference track (never exported)."""
    from chorus_score_match import shift_score_notes

    frames = project.frames
    rate = project.rate
    if frames <= 0 or not project.score or not project.score.lanes:
        return np.zeros((max(0, frames), 2), dtype=np.float32)
    lane = None
    octave = 0
    if track is not None and track.score_lane:
        lane = next((item for item in project.score.lanes if item.id == track.score_lane), None)
        octave = int(track.score_octave or 0)
    if lane is None:
        lane = project.score.lanes[0]
    notes = shift_score_notes(lane.notes, offset=project.score.offset, octave_semitones=octave)
    mono = np.zeros(frames, dtype=np.float32)
    origin = project.first_frame
    amp = float(10.0 ** (gain_db / 20.0)) * .34
    attack_n = max(1, round(.008 * rate))
    release_n = max(1, round(.018 * rate))
    two_pi = 2.0 * np.pi
    for note in notes:
        a = max(0, round(note.start * rate) - origin)
        b = min(frames, round(note.end * rate) - origin)
        n = b - a
        if n < 16:
            continue
        freq = 440.0 * (2.0 ** ((note.pitch - 69.0) / 12.0))
        t = np.arange(n, dtype=np.float64) / rate
        wave = (np.sin(two_pi * freq * t)
                + .22 * np.sin(two_pi * 2.0 * freq * t)
                + .06 * np.sin(two_pi * 3.0 * freq * t))
        env = np.exp(-t / .70)
        att = min(attack_n, n // 2)
        rel = min(release_n, n - att)
        if att > 0:
            env[:att] *= .5 - .5 * np.cos(np.pi * np.arange(att) / att)
        if rel > 0:
            env[-rel:] *= .5 + .5 * np.cos(np.pi * np.arange(rel) / rel)
        mono[a:b] += (wave * env * amp).astype(np.float32)
    peak = float(np.max(np.abs(mono), initial=0.))
    if peak > .45:
        mono *= .45 / peak
    return np.column_stack((mono, mono)).astype(np.float32)


def render_mix(project, variant, solo, check, progress):
    rows = monitor_tracks(project, solo)
    stereo = np.zeros((project.frames, 2), dtype=np.float32)
    gain = 1. / max(1, len(rows))
    for index, track in enumerate(rows):
        check()
        progress(index, len(rows), track.name)
        audio = render_track(project, track, variant, check)
        stereo += _stereo_channels(audio) * gain
    peak = float(np.max(np.abs(stereo), initial=0.))
    if peak > .98:
        stereo *= .98 / peak
    stereo.setflags(write=False)
    return stereo


def render_monitor(project, variant, solo, cache, check, progress):
    """Generate pitch-corrected PCM away from the active output thread."""
    rows = monitor_tracks(project, solo)
    audible = {track.id for track in rows}
    pitch = variant in ('pitch', 'processed') and any(
        row.kind == 'pitch' and row.track in audible and _pitch_active(row) for row in project.edits)
    if not pitch:
        return render_mix(project, variant, solo, check, progress)

    from chorus_model_worker import model_batch

    spec = _render_spec(project, variant, solo=solo)
    labels = [track.name for track in rows]
    with model_batch(__file__, spec, cache, check, lambda i, name: progress(i, len(rows), name),
                     labels, '修音试听渲染失败') as folder:
        audio = np.load(folder / 'monitor.npy', allow_pickle=False)
    if audio.dtype != np.float32 or audio.shape != (project.frames, 2) or not np.isfinite(audio).all():
        raise RuntimeError('修音试听返回的采样或时间范围无效。')
    audio.setflags(write=False)
    return audio


def _render_spec(project, variant, **options):
    return {'title': project.title, 'id': project.id, 'rate': project.rate, 'variant': variant,
            'pitch_engine': project.pitch_engine,
            'tracks': [asdict(track) for track in project.tracks], 'edits': [asdict(edit) for edit in project.edits],
            **options}


def _render_process(request):
    from chorus_project import ChorusProject, Edit

    spec = json.loads(request.read_text(encoding='utf-8'))
    tracks = tuple(Recording(**(track | {'stamp': tuple(track['stamp'])})) for track in spec['tracks'])
    edits = tuple(Edit(**(edit | {'points': tuple(map(tuple, edit['points']))})) for edit in spec['edits'])
    project = ChorusProject(spec['title'], spec['id'], spec['rate'], tracks, edits=edits,
                            pitch_engine=spec.get('pitch_engine', 'auto'))

    def progress(index, _total, _name):
        (request.parent / 'progress.txt').write_text(str(index), encoding='utf-8')

    if spec.get('export'):
        destination, receipt = export_stems(project, spec['folder'], spec['variant'], spec['selection'],
                                           lambda: None, progress, include_mix=spec['include_mix'],
                                           balance_vocals=bool(spec.get('balance_vocals', False)))
        (request.parent / 'export.json').write_text(json.dumps({'folder': destination.name, 'receipt': receipt},
                                                                ensure_ascii=False), encoding='utf-8')
    else:
        mix = render_mix(project, spec['variant'], spec['solo'], lambda: None, progress)
        np.save(request.parent / 'monitor.npy', mix, allow_pickle=False)


def _stem_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)[:80].rstrip(' .') or '音轨'


def _publish_stems(stage, folder, title):
    base = folder / (_stem_name(title) + ' - 分轨')
    number = 1
    while True:
        destination = base if number == 1 else base.with_name(f'{base.name} {number}')
        if not destination.exists():
            try:
                stage.rename(destination)
                return destination
            except FileExistsError:
                pass
        number += 1


def export_delivery(project, folder, variant, selection, cache, check, progress, *,
                    include_mix=False, balance_vocals=False):
    """Render delivery in a child process so the desktop stays responsive."""
    from chorus_model_worker import model_batch

    folder = Path(folder).expanduser().resolve(strict=True)
    rows = [track.name for track in project.tracks if track.role == 'vocal']
    if include_mix:
        rows.append('合唱干声')
    if variant == 'original':
        return export_stems(project, folder, variant, selection, check, progress,
                            include_mix=include_mix, balance_vocals=balance_vocals)
    # The parent owns this directory. Cancellation removes everything here
    # even if the render process was terminated while writing a stem.
    with TemporaryDirectory(dir=folder, prefix='.vwa-delivery-') as directory:
        stage = Path(directory).resolve()
        spec = _render_spec(project, variant, export=True, folder=str(stage), selection=selection,
                            include_mix=include_mix, balance_vocals=bool(balance_vocals))
        with model_batch(__file__, spec, cache, check, lambda i, text: progress(i, len(rows), text), rows,
                         '分轨渲染失败') as worker:
            result = json.loads((worker / 'export.json').read_text(encoding='utf-8'))
        source = (stage / result['folder']).resolve(strict=True)
        if source.parent != stage or not source.is_dir():
            raise RuntimeError('分轨渲染返回的目录无效。')
        check()
        return _publish_stems(source, folder, project.title), result['receipt']


def export_stems(project, folder, variant, selection, check, progress, *,
                 include_mix=False, balance_vocals=False):
    """Publish only the completed directory, without replacing an export."""
    folder = Path(folder).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('请选择已有的分轨导出目录。')
    rows = tuple(track for track in project.tracks if track.role == 'vocal')
    if not rows:
        raise ValueError('至少需要一条待处理人声音轨。')
    first = max(project.first_frame, round(selection[0] * project.rate))
    last = min(project.first_frame + project.frames, round(selection[1] * project.rate))
    if last <= first:
        raise ValueError('导出时间范围为空。')
    start, stop = first - project.first_frame, last - project.first_frame
    total = len(rows) + int(include_mix)
    receipt = {'schema': 'vwa-chorus-stems/1', 'project': project.id, 'title': project.title,
               'variant': variant, 'sample_rate': project.rate, 'origin_frame': first,
               'origin_seconds': first / project.rate, 'frames': stop - start,
               'balance_vocals': bool(balance_vocals), 'tracks': []}
    with TemporaryDirectory(dir=folder, prefix='.vwa-chorus-') as scratch:
        stage = Path(scratch) / 'stems'
        stage.mkdir()
        vocal_mix = np.zeros((stop - start, 2), dtype=np.float32) if include_mix else None
        for index, track in enumerate(rows, 1):
            check()
            progress(index - 1, total, track.name)
            data = render_track(project, track, variant, check)[start:stop]
            balance_gain_db = 0.0
            if balance_vocals:
                data, balance_gain_db = balance_vocal_level(data)
            filename = f'{index:02d} {_stem_name(track.name)}.wav'
            destination = stage / filename
            sf.write(destination, data, project.rate, format='WAV', subtype='FLOAT')
            if vocal_mix is not None:
                vocal_mix += _stereo_channels(data)
            takes = [{'source_sha256': source.sha, 'name': source.name, 'start': edit.start, 'end': edit.end}
                     for edit in project.edits_for(track.id, 'take')
                     if (source := project.track(edit.source)) is not None]
            receipt['tracks'].append({'id': track.id, 'name': track.name, 'part': track.part,
                                      'source_sha256': track.sha, 'file': filename,
                                      'channels': track.channels, 'sha256': source_digest(destination),
                                      'balance_gain_db': balance_gain_db,
                                      'timing_edits': len(project.edits_for(track.id, 'timing')),
                                      'pitch_edits': len(project.edits_for(track.id, 'pitch')),
                                      'gain_edits': len(project.edits_for(track.id, 'gain')),
                                      'syllable_gain_edits': len(project.edits_for(track.id, SYLLABLE_GAIN)),
                                      'take_selections': takes})
        if vocal_mix is not None:
            check()
            progress(len(rows), total, '合唱干声')
            peak = float(np.max(np.abs(vocal_mix), initial=0.))
            gain = .98 / peak if peak > .98 else 1.
            vocal_mix *= gain
            destination = stage / '合唱干声.wav'
            sf.write(destination, vocal_mix, project.rate, format='WAV', subtype='FLOAT')
            receipt['vocal_mix'] = {'file': destination.name, 'channels': 2, 'gain': gain,
                                    'sha256': source_digest(destination)}
        view = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)
        (stage / '分轨说明.json').write_text(view + '\n', encoding='utf-8')
        check()
        return _publish_stems(stage, folder, project.title), receipt


def prepare_mixdown_handoff(project, destination, receipt, selection, check, *, merged_vocals=False):
    """Build a same-origin handoff payload from exported chorus stems into Simple Mixdown."""
    from mixdown_contract import MAX_VOCALS
    from mixdown_workspace_state import AUDIO_SUFFIXES

    destination = Path(destination).expanduser().resolve(strict=True)
    vocal_rows = []
    if merged_vocals:
        mix_info = receipt.get('vocal_mix')
        if not isinstance(mix_info, dict) or not mix_info.get('file'):
            raise RuntimeError('缺少合并人声文件，无法以合轨模式送入简易混音。')
        vocal_rows.append({
            'label': f'{project.title} · 人声合轨',
            'path': str((destination / mix_info['file']).resolve()),
            'muted': False,
        })
    else:
        for entry in receipt.get('tracks', ()):
            track = project.track(entry.get('id', ''))
            part = str(entry.get('part') or '').strip()
            name = str(entry.get('name') or '').strip() or '人声'
            label = f'{name} ({part})' if part and part not in name else name
            vocal_rows.append({
                'label': label,
                'path': str((destination / entry['file']).resolve()),
                'muted': bool(track.muted) if track is not None else False,
            })
        if len(vocal_rows) > MAX_VOCALS:
            raise ValueError(f'分轨数量（{len(vocal_rows)} 路）超过简易混音上限（{MAX_VOCALS} 路），请改用“合轨人声”模式送入。')
    if not vocal_rows:
        raise ValueError('没有可送入简易混音的人声分轨。')

    backing_payload = None
    backing_track = next((t for t in project.tracks if t.role == 'backing' and not t.muted), None)
    if backing_track is None:
        backing_track = next((t for t in project.tracks if t.role == 'backing'), None)
    if backing_track is not None:
        check()
        origin_frame = int(receipt.get('origin_frame', project.first_frame))
        origin_seconds = float(receipt.get('origin_seconds', origin_frame / project.rate))
        frames = int(receipt.get('frames', project.frames))
        full_span = (origin_frame == project.first_frame and frames == project.frames)
        src_path = Path(backing_track.path).expanduser().resolve()
        if (full_span and abs(backing_track.offset - origin_seconds) < 1e-4
                and src_path.is_file() and src_path.suffix.casefold() in AUDIO_SUFFIXES):
            backing_payload = {'label': backing_track.name or '伴奏', 'path': str(src_path)}
        else:
            start = max(0, origin_frame - project.first_frame)
            stop = min(project.frames, start + frames)
            rendered = render_track(project, backing_track, 'original', check)[start:stop]
            backing_file = destination / f'00 伴奏 {_stem_name(backing_track.name)}.wav'
            sf.write(backing_file, rendered, project.rate, format='WAV', subtype='FLOAT')
            backing_payload = {'label': backing_track.name or '伴奏', 'path': str(backing_file.resolve())}

    return {
        'title': project.title,
        'variant': receipt.get('variant', 'processed'),
        'folder': str(destination),
        'merged': bool(merged_vocals),
        'vocals': vocal_rows,
        'backing': backing_payload,
    }


def export_mixdown_handoff(project, folder, variant, selection, cache, check, progress, *,
                           merged_vocals=False, balance_vocals=True):
    """Render chorus stems and build a ready-to-load payload for Simple Mixdown."""
    from mixdown_contract import MAX_VOCALS

    vocal_count = sum(track.role == 'vocal' for track in project.tracks)
    if vocal_count <= 0:
        raise ValueError('至少需要一条待处理人声音轨。')
    if not merged_vocals and vocal_count > MAX_VOCALS:
        raise ValueError(f'当前共有 {vocal_count} 路人声，超过简易混音分轨上限（{MAX_VOCALS} 路），请选择“合轨人声”模式送入。')
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    destination, receipt = export_delivery(
        project, folder, variant, selection, cache, check, progress,
        include_mix=bool(merged_vocals), balance_vocals=bool(balance_vocals))
    return prepare_mixdown_handoff(project, destination, receipt, selection, check, merged_vocals=bool(merged_vocals))


class AuditionPCM:
    """Adapter for the existing native output session and its audio clock."""

    channels = 2

    def __init__(self, rate, audio, position=0):
        self.sample_rate = rate
        self.duration_frames = len(audio)
        self.position_frame = position
        self.volume = .85
        self.audio = audio
        self.previous = None
        self.fade_remaining = 0

    def set_parameters(self, request):
        audio = request['pcm'].audio
        if audio.shape != self.audio.shape or audio.dtype != np.float32:
            raise ValueError('试听快照与工程时间轴不一致。')
        if audio is not self.audio:
            self.previous, self.audio = self.audio, audio
            self.fade_remaining = max(1, round(.012 * self.sample_rate))

    def set_volume(self, value):
        self.volume = float(value) / 100.

    def seek(self, frame):
        self.position_frame = frame

    def render(self, frames):
        start = self.position_frame
        end = start + frames
        data = self.audio[start:end] * self.volume
        if self.previous is not None and self.fade_remaining:
            length = min(len(data), self.fade_remaining)
            if length > 0:
                duration = max(1, round(.012 * self.sample_rate))
                weight = ((duration - self.fade_remaining + np.arange(length)) / duration)[:, None]
                data[:length] = data[:length] * weight + self.previous[start:start + length] * self.volume * (1. - weight)
                self.fade_remaining -= length
            if not self.fade_remaining or length < frames:
                self.previous = None
                self.fade_remaining = 0
        self.position_frame = end
        return np.ascontiguousarray(data, dtype=np.float32)

    def close(self):
        self.previous = None


@dataclass(frozen=True)
class MonitorBuffer:
    audio: np.ndarray

    def __deepcopy__(self, _memo):
        # PCM is published read-only. Reusing the reference avoids copying
        # the entire song on the Qt thread during an A/B switch.
        return self


if __name__ == '__main__':
    _render_process(Path(sys.argv[1]))
