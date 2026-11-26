"""Measure phrase timing and target stable notes from each independent take."""
from dataclasses import dataclass, replace
import numpy as np
import soxr

from chorus_notes import (FRAME, Suggestion, _pitch_runs, _project_slice, _runs, quiet_timing,
                          timing_suggestion, valley_timing)
from chorus_project import Edit, Phrase
from chorus_precision import (analysis_phrases, fit_timing, fit_translation, note_anchors, phone_anchors,
                              phone_requests, phoneme_cache)
from i18n import tr


FEATURE_RATE = 8000


@dataclass
class Features:
    energy: np.ndarray
    shape: np.ndarray
    active: np.ndarray
    peaks: list
    side_mid_db: float = -120.0
    wet_warning: bool = False


_NOTE_NAMES = ('C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B')


def _midi_note_name(midi_val):
    nearest = int(round(float(midi_val)))
    return f'{_NOTE_NAMES[nearest % 12]}{(nearest // 12) - 1}'


def _mono(samples):
    if samples.shape[1] == 1:
        return samples[:, 0]
    mean = samples.mean(axis=1)
    powers = np.mean(samples * samples, axis=0)
    if np.mean(mean * mean) < max(powers, default=0.) * .2:
        return samples[:, int(np.argmax(powers))]
    return mean


def _smooth(values, radius):
    x = np.arange(-radius, radius + 1)
    kernel = np.exp(-.5 * (x / (radius / 2)) ** 2)
    return np.convolve(np.pad(values, radius, mode='edge'), kernel / kernel.sum(), mode='valid')


def feature_track(track, check):
    """Decode once, measuring real peaks separately from the normalized cues."""
    import soundfile as sf

    check()
    track.check_source()
    audio, rate = sf.read(track.path, dtype='float32', always_2d=True)
    if not np.isfinite(audio).all():
        raise ValueError(f'音频包含非有限采样：{track.name}')
    bins = min(3000, max(24, round(track.duration * 35)))
    chunk = (len(audio) + bins - 1) // bins
    bins = (len(audio) + chunk - 1) // chunk
    edges = np.arange(bins) * chunk
    peaks = np.maximum(np.abs(np.maximum.reduceat(audio, edges, axis=0)),
                       np.abs(np.minimum.reduceat(audio, edges, axis=0))).max(axis=1)
    mono = _mono(audio)
    mono = soxr.resample(mono, rate, FEATURE_RATE)
    check()
    size, hop = 320, 80
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(mono, (size // 2, size // 2)), size)[::hop]
    energy = np.sqrt(np.mean(windows * windows, axis=1))
    spectrum = np.abs(np.fft.rfft(windows * np.hanning(size), axis=1))
    bands = np.column_stack((spectrum[:, 3:20].mean(axis=1),
                             spectrum[:, 20:65].mean(axis=1), spectrum[:, 65:].mean(axis=1)))
    power = float(np.percentile(energy, 90))
    # Match phrasing cues on separately normalized tracks, independent of key.
    level = np.log1p(energy * 5 / max(.0005, power))
    edge = np.maximum(0., np.diff(_smooth(level, 2), prepend=0.))
    scaled = np.log1p(bands / np.maximum(np.percentile(bands, 90, axis=0), .001))
    shape = np.column_stack((_smooth(level, 3), _smooth(edge, 4),
                             _smooth(scaled[:, 1], 3), _smooth(scaled[:, 2], 3)))
    active = _smooth(energy, 4) > max(.00035, power * .08)
    side_mid_db = -120.0
    wet_warning = False
    if audio.shape[1] >= 2:
        mid = .5 * (audio[:, 0].astype(np.float64) + audio[:, 1].astype(np.float64))
        side = .5 * (audio[:, 0].astype(np.float64) - audio[:, 1].astype(np.float64))
        # Measure stereo side/mid ratio over active vocal body (ignoring silent floor).
        stride = max(1, len(audio) // 160000)
        m_sub, s_sub = mid[::stride], side[::stride]
        thr = float(np.percentile(np.abs(m_sub), 90)) * .08
        voiced = np.abs(m_sub) > max(1e-4, thr)
        if np.count_nonzero(voiced) >= 64:
            m_rms = float(np.sqrt(np.mean(m_sub[voiced] ** 2)))
            s_rms = float(np.sqrt(np.mean(s_sub[voiced] ** 2)))
            side_mid_db = round(20.0 * float(np.log10(max(s_rms, 1e-12) / max(m_rms, 1e-12))), 1)
            wet_warning = (track.role == 'vocal' and side_mid_db > -32.0)
    track.check_source()
    return Features(energy, shape, active, peaks.round(4).tolist(), side_mid_db, wet_warning)


def phrases_from_reference(reference, features, first, last, score=None):
    if reference is None or features is None:
        return (Phrase(first, last),) if first < last else ()
    bounds = []
    for a, b in _runs(features.active):
        if (b - a) * FRAME < .075:
            continue
        start, end = reference.offset + a * FRAME, reference.offset + b * FRAME
        if bounds and start - bounds[-1][1] < .22:
            bounds[-1] = (bounds[-1][0], end)
        else:
            bounds.append((start, end))
    if not bounds:
        return (Phrase(first, last),) if first < last else ()
    phrases = []
    lyric_lanes = (next((lane for lane in score.lanes if lane.id == reference.score_lane), None)
                   if score else None)
    segments = []
    for a, b in bounds:
        # Keep recognition and manual review on phrases even when a legato
        # passage has no long breath. Cut at a measured energy minimum.
        while b - a > 15:
            left = round((a + 8. - reference.offset) / FRAME)
            right = min(len(features.energy), round((a + 13. - reference.offset) / FRAME))
            if right <= left:
                break
            cut = reference.offset + (left + int(np.argmin(features.energy[left:right]))) * FRAME
            segments.append((a, cut))
            a = cut
        segments.append((a, b))
    for index, (a, b) in enumerate(segments):
        a, b = max(first, a - .07), min(last, b + .09)
        if index:
            a = max(a, (segments[index - 1][1] + segments[index][0]) / 2.)
        if index + 1 < len(segments):
            b = min(b, (segments[index][1] + segments[index + 1][0]) / 2.)
        if a >= b:
            continue
        label = ''
        if lyric_lanes:
            text = [note.lyric for note in lyric_lanes.notes
                    if a <= note.start + score.offset < b and note.lyric and note.lyric not in ('-', '+')]
            label = ''.join(text)
        phrases.append(Phrase(a, b, label))
    return tuple(phrases)


def _timing_match(reference, target, source, take, phrase, radius):
    span = np.arange(round(phrase.start / FRAME), round(phrase.end / FRAME))
    if len(span) < 8:
        return None, '唱段太短，可手动移动', True
    step = max(1, round(radius / FRAME))
    lags = np.arange(-step, step + 1)
    origin = _project_slice(source.shape, reference.offset, span)
    vocal = _project_slice(source.active, reference.offset, span)
    clips = _project_slice(take.shape, target.offset, span[None, :] + lags[:, None])
    presence = _project_slice(take.active, target.offset, span[None, :] + lags[:, None])
    if vocal.sum() < 7 or presence.sum(axis=1).max(initial=0) < 6:
        # Either the reference or this singer is silent in this phrase (e.g. solo verse / rest / intro);
        # silence is NOT a rhythm alignment error and should not flood the review list.
        return None, '', False
    origin -= origin.mean(axis=0)
    clips -= clips.mean(axis=1, keepdims=True)
    denom = np.sqrt((origin * origin).sum(axis=0) * (clips * clips).sum(axis=1))
    similarity = np.divide((clips * origin).sum(axis=1), denom,
                           out=np.zeros_like(denom), where=denom > 1e-8)
    correlation = (similarity * [.44, .28, .16, .12]).sum(axis=1)
    shared = (presence & vocal).sum(axis=1) / max(1, vocal.sum())
    coverage = np.minimum(shared * 1.4, 1.)
    metric = correlation * coverage - .045 * np.abs(lags) / step
    pick = int(np.argmax(metric))
    score = float(correlation[pick])
    lag = int(lags[pick])
    competing = metric[np.abs(lags - lag) > 8]
    separated = float(metric[pick] - competing.max()) if len(competing) else 1.
    if min(score, float(coverage[pick])) < .13:
        return None, '没有明确的起音匹配，可手动移动', True
    review = score < .47 or coverage[pick] < .72 or separated < .05 or abs(lag) >= step - 1
    return -lag * FRAME, ('起音候选接近，建议试听' if review else '声学起音'), review


def phrase_regions(project, reference, features, selection):
    first, last = selection
    inferred = phrases_from_reference(reference, features, first, last, project.score)
    return analysis_phrases(project.phrases, inferred, first, last)


def _timing_candidates(project, targets, selection, strength, radius, features, curves, check, progress,
                       speech, audio):
    pending = []
    note_runs = {}
    for track in targets:
        provisional = project.reference(track)
        ref = provisional if provisional and provisional.id != track.id else None
        origin = ref or track
        for phrase in phrase_regions(project, origin, features[origin.id], selection):
            check()
            if track.id == phrase.reference:
                continue
            reference = project.reference(track, phrase)
            if reference is None or reference.id == track.id or reference.role == 'backing':
                continue
            movement, detail, review = _timing_match(reference, track, features[reference.id],
                                                     features[track.id], phrase, radius)
            clips = phone_requests(phrase, reference, track, movement, audio) if speech is not None else ()
            pending.append((track, reference, phrase, movement, detail, review, clips))
    facts, failure = (phoneme_cache((row[-1] for row in pending), *speech, check, progress,
                                    project.alignment_engine) if speech is not None else ({}, ''))
    generated = []
    for track, ref, phrase, movement, detail, review, clips in pending:
        check()
        evidence = facts.get(tuple(clip.key for clip in clips), {})
        route, anchors = phone_anchors(clips, evidence)
        first, last = max(selection[0], phrase.start), min(selection[1], phrase.end)
        quiet = quiet_timing(track, features[track.id])
        snap = valley_timing(track, features[track.id])
        fit = fit_timing(anchors, first, last, strength, radius, quiet, snap=snap)
        if fit is None and track.id in curves and ref.id in curves:
            for item in (ref, track):
                if item.id not in note_runs:
                    note_runs[item.id] = tuple(
                        (item.offset + a, item.offset + b, pitch)
                        for a, b, pitch in _pitch_runs(*curves[item.id], note_edits=project.note_edits_for(item.id))
                    )
            notes = note_anchors(note_runs[ref.id], note_runs[track.id], phrase, movement)
            fit = fit_timing(notes, first, last, strength, radius, quiet, snap=snap)
            route = '音符起音'
        explicit = ref.id in (phrase.reference, track.timing_ref)
        source = tr('指定参照 {0}' if explicit else '工程参照 {0}', ref.name)
        if fit is not None:
            reason = ('仅靠工程参照推断的大位移，建议先试听'
                      if not explicit and abs(fit.movement or 0.) > .100
                      else '音符起音匹配存疑，建议先试听' if review and route == '音符起音' else '')
            generated.append(timing_suggestion(track.id, (first, last), fit, route, source, reason))
            continue
        if clips:
            reason = next((evidence[clip.key].get('detail') for clip in clips
                           if evidence.get(clip.key, {}).get('detail')), '')
            detail += f' · {reason or failure or "音素边界不足"}'
        if movement is None:
            if review and detail and last > 0.05:
                generated.append(Suggestion(track.id, 'timing', max(0., first), last, None, detail, True, route='需核对'))
        else:
            fit = fit_translation(first, last, movement * strength, quiet)
            reason = detail if review else ('仅靠工程参照推断的大位移，建议先试听'
                                            if not explicit and abs(movement) > .100 else '')
            generated.append(timing_suggestion(track.id, (first, last), fit, '声学起音', source, reason))
    return generated


_DIATONIC_SCALES = tuple(
    (root, frozenset((root + degree) % 12 for degree in (0, 2, 4, 5, 7, 9, 11)))
    for root in range(12)
)


def _note_body_span(midi, step, note):
    """Measure pitch spread inside the trimmed musical body of a note (ignoring onset/release scoops)."""
    dur = note.end - note.start
    trim = min(.035, dur * .15) if dur >= .14 else 0.
    a = max(0, min(len(midi), round((note.start + trim) / step)))
    b = max(a + 1, min(len(midi), round((note.end - trim) / step)))
    seg = midi[a:b]
    valid = seg[np.isfinite(seg)]
    if len(valid) < 3:
        return note.high_midi - note.low_midi
    low, high = np.percentile(valid, (10, 90))
    return float(high - low)


def _estimate_ensemble_scale(notes_by_track, *, guide_id=''):
    """Estimate a high-confidence diatonic pitch-class set from guide/chorus vocal notes."""
    def _histogram(track_ids):
        hist = np.zeros(12, dtype=np.float64)
        count = 0
        for tid in track_ids:
            for note in notes_by_track.get(tid, ()):
                dur = note.end - note.start
                if dur < .075 or note.high_midi - note.low_midi > 2.4:
                    continue
                hist[round(note.center_midi) % 12] += dur
                count += 1
        return hist, count

    def _pick_scale(hist, count):
        total = float(hist.sum())
        if count < 12 or total < 4.0:
            return None
        scores = sorted(
            ((float(sum(hist[pc] for pc in scale)) / total,
              float(hist[root] + hist[(root + 7) % 12]) / total,
              scale) for root, scale in _DIATONIC_SCALES),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        best_cov, best_td, best_scale = scores[0]
        second_cov, second_td, second_scale = scores[1] if len(scores) > 1 else (0., 0., frozenset())
        if best_cov >= .78 and best_cov - second_cov >= .03:
            return best_scale
        # Tie-break two fifth-related scales both >=82% by tonic+dominant weight
        # (e.g. Eb+Bb=47% picks 7-note Eb major over Ab major).
        if best_cov >= .82 and second_cov >= .82 and len(best_scale | second_scale) == 8:
            if abs(best_td - second_td) >= .04:
                return best_scale if best_td > second_td else second_scale
            return best_scale | second_scale
        return None

    if guide_id and guide_id in notes_by_track:
        guide_scale = _pick_scale(*_histogram((guide_id,)))
        if guide_scale is not None:
            return guide_scale
    return _pick_scale(*_histogram(tuple(notes_by_track.keys())))


def _scale_aware_target(center_midi, dur, scale):
    """Snap to nearest semitone, steering out-of-key singing back into the detected diatonic scale."""
    nearest = round(center_midi)
    if scale is None or (nearest % 12) in scale:
        return nearest
    # Keep long, dead-center chromatic accidentals untouched.
    if dur >= .45 and abs(center_midi - nearest) <= .12:
        return nearest
    candidates = [
        int(np.floor(center_midi)) + delta
        for delta in range(-2, 3)
        if (int(np.floor(center_midi)) + delta) % 12 in scale
    ]
    return min(candidates, key=lambda pitch: abs(pitch - center_midi)) if candidates else nearest


def _score_scale_set(score_notes):
    """Infer the diatonic pitch-class scale from a MIDI score lane for harmony protection."""
    if not score_notes:
        return None
    hist = np.zeros(12, dtype=np.float64)
    for n in score_notes:
        hist[int(n.pitch) % 12] += max(.05, float(n.end - n.start))
    total = float(hist.sum())
    if total <= 1e-6:
        return None
    scores = sorted(
        ((float(sum(hist[pc] for pc in scale)) / total,
          float(hist[root] + hist[(root + 7) % 12]) / total,
          scale) for root, scale in _DIATONIC_SCALES),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    return scores[0][2] if scores[0][0] >= .78 else None


def _local_score_scale(score_notes, t_start, t_end, *, window=16.0, fallback=None):
    """Infer local diatonic scale around `[t_start, t_end]` so key modulations never pollute earlier verses."""
    if not score_notes:
        return fallback
    lo, hi = t_start - window, t_end + window
    local = [n for n in score_notes if n.end >= lo and n.start <= hi]
    if len(local) < 10:
        return fallback or _score_scale_set(score_notes)
    return _score_scale_set(local) or fallback or _score_scale_set(score_notes)


def _build_natural_pitch_curve(midi, step, note, base_cents, pitch_strength, *, drift_strength=.55, clip_span=None):
    """3-layer pitch curve: center + slow drift (<2 Hz) stabilization while preserving vibrato (4.5-7.5 Hz).

    Returns `()` for short or steady notes (staying scalar constant shift), or a smooth
    `(t, shift_cents)` knot tuple in take seconds when slow internal pitch drift >= 28 cents.
    """
    dur = note.end - note.start
    if dur < .22 or pitch_strength <= .05:
        return ()
    a = max(0, min(len(midi) - 1, round(note.start / step)))
    b = max(a + 1, min(len(midi), round(note.end / step)))
    if b - a < 12:
        return ()
    raw = midi[a:b].astype(np.float64, copy=True)
    finite = np.isfinite(raw)
    if np.count_nonzero(finite) < max(8, int(.65 * len(raw))):
        return ()
    idx = np.arange(len(raw))
    if not np.all(finite):
        raw = np.interp(idx, idx[finite], raw[finite])

    # Separate slow pitch drift (<~1.8 Hz) from 4.5-7.5 Hz vibrato using a zero-phase
    # Gaussian low-pass filter (sigma = 105 ms suppresses >=4.5 Hz vibrato by >96%).
    sigma = max(1.5, .105 / step)
    rad = max(2, int(round(2.5 * sigma)))
    gx = np.arange(-rad, rad + 1, dtype=np.float64)
    g = np.exp(-.5 * (gx / sigma) ** 2)
    g /= g.sum()
    dev = raw - note.center_midi
    slow = np.convolve(np.pad(dev, rad, mode='edge'), g, mode='valid')

    trim = min(max(2, round(.040 / step)), len(slow) // 4)
    body = slow[trim:len(slow) - trim] if len(slow) > 2 * trim + 2 else slow
    drift_span_cents = float(np.ptp(body)) * 100.0
    if drift_span_cents < 28.0:
        return ()

    # Center the slow drift around its body median so overall center stays exact.
    slow_centered = slow - float(np.median(body))
    # Taper drift correction smoothly at note onset/release (38 ms) to keep natural scoops/releases.
    times = (a + idx) * step
    taper = min(.038, dur * .22)
    env = np.minimum(
        np.clip((times - note.start) / max(1e-4, taper), 0., 1.),
        np.clip((note.end - times) / max(1e-4, taper), 0., 1.),
    )
    env = env * env * (3. - 2. * env)
    drift_corr = np.clip(-slow_centered * 100.0 * (drift_strength * pitch_strength), -110., 110.) * env
    total_curve = np.clip(base_cents + drift_corr, -1200., 1200.)

    span_a, span_b = (float(note.start), float(note.end)) if clip_span is None else (float(clip_span[0]), float(clip_span[1]))
    if span_b - span_a < .04:
        return ()
    # Subsample onto ~25 ms knots strictly inside [span_a, span_b].
    knot_step = max(1, round(.025 / step))
    picks = list(range(0, len(times), knot_step))
    if picks[-1] != len(times) - 1:
        picks.append(len(times) - 1)
    y_a = float(np.interp(span_a, times, total_curve))
    y_b = float(np.interp(span_b, times, total_curve))
    pts = [(span_a, y_a)]
    for p in picks:
        t_p = float(times[p])
        if span_a + 1e-4 < t_p < span_b - 1e-4:
            pts.append((t_p, float(total_curve[p])))
    pts.append((span_b, y_b))
    return tuple(pts)


def _midi_timing_spans(track, features, selection):
    """Return `(vocal_start, vocal_end, padded_start, padded_end)` breath-bounded sub-phrases."""
    first, last = selection
    if features is None:
        return ((first, last, first, last),) if first < last else ()
    bounds = []
    for a, b in _runs(features.active):
        if (b - a) * FRAME < .075:
            continue
        start, end = track.offset + a * FRAME, track.offset + b * FRAME
        if end <= first or start >= last:
            continue
        if bounds and start - bounds[-1][1] < .14:
            bounds[-1] = (bounds[-1][0], end)
        else:
            bounds.append((start, end))
    if not bounds:
        return ()
    out = []
    for idx, (va, vb) in enumerate(bounds):
        pa, pb = max(first, va - .05), min(last, vb + .06)
        if idx:
            pa = max(pa, .5 * (bounds[idx - 1][1] + bounds[idx][0]))
        if idx + 1 < len(bounds):
            pb = min(pb, .5 * (bounds[idx][1] + bounds[idx + 1][0]))
        if pb - pa >= .18:
            out.append((max(first, va), min(last, vb), pa, pb))
    return tuple(out)


def _midi_timing_candidates(project, targets, selection, strength, radius, features, curves, check):
    """MIDI-guided hierarchical timing: group-aware 1.00x pure translation first, tight-bounded local warp rarely."""
    from chorus_notes import segment_vocal_notes
    from chorus_precision import TimingFit
    from chorus_score_match import estimate_phrase_score_shift, midi_timing_anchors, shift_score_notes

    score = project.score
    if not score or not score.lanes or not targets:
        return []
    raw_lanes = {lane.id: lane.notes for lane in score.lanes}
    generated = []
    for track in targets:
        check()
        if track.id not in curves or not track.score_lane or track.score_lane not in raw_lanes:
            continue
        lane = shift_score_notes(raw_lanes[track.score_lane], offset=score.offset, octave_semitones=track.score_octave)
        midi_curve, step = curves[track.id]
        vocal_notes = segment_vocal_notes(midi_curve, step, note_edits=project.note_edits_for(track.id))
        feat = features.get(track.id)
        if feat is None:
            continue
        quiet = quiet_timing(track, feat)
        snap = valley_timing(track, feat)
        raw_spans = _midi_timing_spans(track, feat, selection)
        if not raw_spans:
            continue

        # Pass 1: Measure each sub-phrase's un-aliased shift relative to MIDI.
        measured = []
        for va, vb, first, last in raw_spans:
            check()
            coarse_shift, _err0, _err_best, contour_ok = estimate_phrase_score_shift(
                midi_curve, step, lane, first, last, track_offset=track.offset, max_shift=min(.36, radius))
            p_notes = tuple(v for v in vocal_notes
                            if track.offset + v.end > first - .04 and track.offset + v.start < last + .04)
            p_anchors = midi_timing_anchors(
                p_notes, lane, track_offset=track.offset, radius=radius, coarse_shift=coarse_shift)
            if not p_anchors and not contour_ok:
                measured.append({'va': va, 'vb': vb, 'first': first, 'last': last, 'shift': 0.,
                                 'anchors': (), 'mad_ms': 0, 'spread_ms': 0, 'contour_ok': False, 'active': False})
                continue
            if p_anchors:
                offsets = np.array([ref_t - src_t for _, ref_t, src_t in p_anchors], dtype=np.float64)
                med_off = float(np.median(offsets))
                mad_ms = round(1000. * float(np.median(np.abs(offsets - med_off))))
                spread_ms = round(1000. * float(np.ptp(np.percentile(offsets, (10, 90))))) if len(offsets) >= 4 else mad_ms
                if contour_ok and abs(coarse_shift) > .08 and len(p_anchors) < 4:
                    med_off = .5 * (med_off + coarse_shift)
            else:
                med_off = coarse_shift
                mad_ms = 25
                spread_ms = 25
            in_deadzone = (abs(med_off) < .035 and mad_ms <= 45)
            measured.append({
                'va': va, 'vb': vb, 'first': first, 'last': last,
                'shift': 0. if in_deadzone else med_off * strength,
                'raw_off': 0. if in_deadzone else med_off,
                'anchors': p_anchors, 'mad_ms': mad_ms, 'spread_ms': spread_ms,
                'contour_ok': contour_ok, 'active': not in_deadzone,
            })

        # Pass 2: Merge adjacent phrases that shift in the same direction by a similar amount
        # (<= 65 ms diff across <= 0.45 s breath) to eliminate interior accordion speedups/slowdowns!
        merged = []
        for item in measured:
            if (merged and item['active'] and merged[-1]['active']
                    and item['va'] - merged[-1]['vb'] <= .45
                    and abs(item['shift'] - merged[-1]['shift']) <= .065):
                prev = merged[-1]
                comb_anchors = prev['anchors'] + item['anchors']
                if comb_anchors:
                    offs = np.array([ref_t - src_t for _, ref_t, src_t in comb_anchors], dtype=np.float64)
                    med = float(np.median(offs))
                    mad = round(1000. * float(np.median(np.abs(offs - med))))
                    spr = round(1000. * float(np.ptp(np.percentile(offs, (10, 90))))) if len(offs) >= 4 else mad
                else:
                    med = .5 * (prev['raw_off'] + item['raw_off'])
                    mad, spr = 25, 25
                prev['vb'] = item['vb']
                prev['last'] = item['last']
                prev['anchors'] = comb_anchors
                prev['raw_off'] = med
                prev['shift'] = med * strength
                prev['mad_ms'] = mad
                prev['spread_ms'] = spr
                prev['contour_ok'] = prev['contour_ok'] or item['contour_ok']
            else:
                merged.append(dict(item))

        # Pass 3: Group-aware collision-free pure 1.00x translation (takes neighbor shifts into account
        # so consecutive phrases moving right by +195 ms and +153 ms cut cleanly in silence with ZERO warp!).
        last_out_end = selection[0]
        for idx, item in enumerate(merged):
            if not item['active']:
                continue
            s = item['shift']
            va, vb = item['va'], item['vb']
            out_va, out_vb = va + s, vb + s
            prev_out_vb = (merged[idx - 1]['vb'] + merged[idx - 1]['shift']) if idx > 0 else selection[0]
            next_out_va = (merged[idx + 1]['va'] + merged[idx + 1]['shift']) if idx + 1 < len(merged) else selection[1]
            # Check if shifted vocal fits with >= 30 ms breath clearance on both sides.
            # Also require local stretching ONLY for long phrases (>= 3.0 s) with large genuine drift.
            want_local = (len(item['anchors']) >= 6 and (vb - va) >= 3.0
                          and item['mad_ms'] > 55 and item['spread_ms'] > 110)
            pure_ok = (not want_local and out_va >= prev_out_vb + .025 and out_vb <= next_out_va - .025)
            if pure_ok:
                # Place pure-translation edit boundaries in silent breath gaps (both output & source quiet!).
                prev_src_vb = merged[idx - 1]['vb'] if idx > 0 else selection[0]
                next_src_va = merged[idx + 1]['va'] if idx + 1 < len(merged) else selection[1]
                # Output cut `out_a` must satisfy: out_a >= last_out_end, out_a <= out_va - .018,
                # and source read `out_a - s` >= prev_src_vb + .010 (doesn't pull previous phrase!).
                min_a = max(selection[0], last_out_end, prev_out_vb + .010, prev_src_vb + s + .010)
                max_a = min(out_va - .015, item['first'] if s >= 0 else out_va - .015)
                out_a = min(max_a, max(min_a, .5 * (min_a + max_a))) if min_a <= max_a else max(selection[0], last_out_end, out_va - .015)
                # Output cut `out_b` must satisfy: out_b >= out_vb + .018,
                # out_b <= next_out_va - .010, and source read `out_b - s` <= next_src_va - .010.
                min_b = out_vb + .015
                max_b = min(selection[1], next_out_va - .010, next_src_va + s - .010)
                out_b = max(min_b, min(max_b, .5 * (min_b + max_b))) if min_b <= max_b else min(selection[1], out_vb + .015)
                if out_b > out_a + .12 and out_a >= last_out_end - 1e-4:
                    out_a = max(out_a, last_out_end)
                    matched = tuple((lbl, float(src + s), float(src)) for lbl, _, src in item['anchors'])
                    fit = TimingFit(s, (), matched, item['mad_ms'], local=False, review=False)
                    last_out_end = out_b
                    limit_ms = .320 if item['contour_ok'] else .200
                    reason = 'MIDI 大位移，建议试听' if abs(s) > limit_ms else ''
                    generated.append(timing_suggestion(track.id, (out_a, out_b), fit, 'MIDI 整句平移', 'MIDI 谱轨', reason))
                    continue

            # Fallback: tightly rate-bounded warp (speed strictly inside ~0.85x..1.18x, NEVER 2.50x!).
            first, last = max(last_out_end, item['first']), item['last']
            if last - first < .18:
                continue
            fit = fit_timing(
                item['anchors'], first, last, strength, radius, quiet, snap=snap,
                max_slope=.10, shoulder_rate=.24, allow_local=want_local,
            )
            if fit is None:
                fit = fit_translation(first, last, s, quiet, shoulder_rate=.24)
                route = 'MIDI 整句平移'
            else:
                route = 'MIDI 局部对齐' if fit.local else 'MIDI 整句平移'
            if fit.movement is not None:
                last_out_end = last
            limit_ms = .320 if item['contour_ok'] else .200
            reason = 'MIDI 大位移，建议试听' if abs(fit.movement or 0.) > limit_ms else ''
            generated.append(timing_suggestion(track.id, (first, last), fit, route, 'MIDI 谱轨', reason))
    return generated


def proposals(project, targets, selection, kind, strength, radius, features, curves, check, progress,
              *, speech=None, audio=None, phone_progress=lambda *_: None, ensemble_language='', pitch_strength=None,
              use_midi_timing=False):
    begin, end = selection
    audio = audio or {track.id: track for track in project.tracks}
    if ensemble_language and speech is not None and kind != 'pitch' and not use_midi_timing:
        from chorus_ensemble import ensemble_timing

        phrases, generated = ensemble_timing(project, targets, selection, features, audio, ensemble_language,
                                             strength, radius, speech, check, phone_progress)
    else:
        reference = project.reference()
        phrases = phrase_regions(project, reference, features.get(reference.id) if reference else None, selection)
        if kind != 'pitch':
            if use_midi_timing:
                generated = _midi_timing_candidates(project, targets, selection, strength, radius, features, curves, check)
            else:
                generated = _timing_candidates(project, targets, selection, strength, radius, features, curves, check,
                                               phone_progress, speech, audio)
                # Also provide MIDI-guided timing for tracks bound to a score lane that had no
                # separate vocal/guide reference (e.g. single-track MIDI tuning workflow).
                covered_tracks = {row.track for row in generated if row.kind == 'timing'}
                midi_only = tuple(t for t in targets if t.id not in covered_tracks and t.score_lane and t.id in curves)
                if midi_only:
                    generated.extend(_midi_timing_candidates(
                        project, midi_only, selection, strength, radius, features, curves, check))
        else:
            generated = []
    if strength:
        for index, row in enumerate(generated):
            # A fresh, closely matched original can supersede older automatic
            # movement. Mere absence of evidence or strength zero may not.
            if (row.kind == 'timing' and row.value == 0. and not row.points and not row.review and len(row.anchors) >= 4
                    and row.timing_residual_ms is not None and row.timing_residual_ms <= 25
                    and any(not edit.manual and edit.start < row.end and row.start < edit.end
                            for edit in project.edits_for(row.track, 'timing'))):
                generated[index] = replace(row, restore_timing=True, detail=row.detail + ' · ' + tr('恢复原录音时序'))
    pitch_strength = strength if pitch_strength is None else pitch_strength
    score = project.score
    raw_lanes = {lane.id: lane.notes for lane in score.lanes} if score else {}
    from chorus_notes import segment_vocal_notes
    from chorus_score_match import (_normalize_token, detect_track_key_mismatch,
                                    expand_vocal_notes_for_score, match_vocal_to_score, shift_score_notes)

    vocal_notes_by_track = {}
    if kind != 'timing':
        for track_id, (curve_midi, curve_step) in curves.items():
            vocal_notes_by_track[track_id] = segment_vocal_notes(
                curve_midi, curve_step, note_edits=project.note_edits_for(track_id))
    guide_track = project.reference()
    ensemble_scale = (
        _estimate_ensemble_scale(vocal_notes_by_track, guide_id=guide_track.id if guide_track else '')
        if kind != 'timing' else None
    )

    for index, track in enumerate(targets):
        check()
        progress(index, track.name)
        if kind == 'timing' or track.id not in curves:
            continue
        midi, step = curves[track.id]
        raw_lane = raw_lanes.get(track.score_lane)
        lane = (shift_score_notes(raw_lane, offset=score.offset, octave_semitones=track.score_octave)
                if raw_lane is not None and score is not None else None)
        timing = [Edit(track.id, 'timing', row.start, row.end, row.value, row.points)
                  for row in generated if row.track == track.id and row.kind == 'timing'
                  and row.can_apply and not row.review]
        applied_timing = project.edits_for(track.id, 'timing')
        time_edits = [row for row in applied_timing if row.manual] + timing + [row for row in applied_timing
                                                                                 if not row.manual]
        vocal_notes = vocal_notes_by_track[track.id]

        def to_output(take_a, take_b):
            sa, sb = track.offset + take_a, track.offset + take_b
            center = (sa + sb) / 2.
            mapping = next((row for row in time_edits
                            if row.source_time(row.start) <= center < row.source_time(row.end)), None)
            if mapping:
                oa, ob = mapping.output_time(sa), mapping.output_time(sb)
                return (oa, ob) if ob > oa else (ob, oa)
            return sa, sb

        in_range = [note for note in vocal_notes
                    if min(end, to_output(note.start, note.end)[1]) - max(begin, to_output(note.start, note.end)[0]) >= .05]
        if lane is not None:
            # Whole-track wrong-key detector (e.g. singer recorded -6 st off-key vs MIDI):
            # emit ONE clear high-priority review card with one-click transposition instead of spamming notes.
            key_mismatch = detect_track_key_mismatch(
                midi, step, raw_lane,
                score_offset=score.offset, track_offset=track.offset, current_octave=track.score_octave,
            )
            if key_mismatch is not None and key_mismatch['first_time'] < end and key_mismatch['last_time'] > begin:
                generated.append(Suggestion(
                    track.id, 'pitch',
                    max(begin, key_mismatch['first_time']), min(end, key_mismatch['last_time']),
                    key_mismatch['shift_semitones'] * 100.0,
                    key_mismatch['reason'], True, True,
                    category='transpose', severity='high',
                    action_hint=key_mismatch['action_hint'],
                    target_octave=key_mismatch['target_octave'],
                ))
                continue
            score_scale = _score_scale_set(lane) or ensemble_scale
            in_range = expand_vocal_notes_for_score(
                in_range, lane, time_to_output=to_output, curve=(midi, step))
            phrase_texts = tuple(
                (p.start, p.end, _normalize_token(line.text))
                for p in project.phrases
                if (line := p.resolved_lyric(track.id)) and line.text
            )
            matches = match_vocal_to_score(
                in_range, lane, time_to_output=to_output, phrase_texts=phrase_texts,
                draft=bool(score and not score.confirmed),
            )
            for n_idx, (note, match) in enumerate(zip(in_range, matches)):
                start, finish = max(begin, track.offset + note.start), min(end, track.offset + note.end)
                dur = finish - start
                local_scale = _local_score_scale(lane, start, finish, fallback=score_scale)
                if match.intent == 'harmony':
                    # Harmony protection: keep intentional harmony intervals intact with gentle
                    # local-key diatonic semitone tuning instead of pulling back to the lead melody.
                    if dur < .085 or _note_body_span(midi, step, note) > 2.20:
                        continue
                    target = _scale_aware_target(note.center_midi, dur, local_scale)
                    cents = (target - note.center_midi) * 100.
                    if abs(cents) >= 6.:
                        base = cents * pitch_strength * .85
                        pts = _build_natural_pitch_curve(
                            midi, step, note, base, pitch_strength * .85,
                            clip_span=(start - track.offset, finish - track.offset))
                        generated.append(Suggestion(track.id, 'pitch', start, finish,
                                                    0. if pts else base, match.reason, False, True, points=pts))
                    continue
                if match.target_pitch is None:
                    generated.append(Suggestion(
                        track.id, 'pitch', start, finish, None,
                        f'此处 MIDI 无对应音符（实测 {_midi_note_name(note.center_midi)}）', True, True,
                        category='pitch_unmatched', severity='medium',
                        action_hint='可能是自由加花、尾音或 MIDI 偏移未对齐；建议试听，满意则点「保留原样」或手动拖动音符',
                    ))
                    continue
                cents = (match.target_pitch - note.center_midi) * 100.
                src_name = _midi_note_name(note.center_midi)
                dst_name = _midi_note_name(match.target_pitch)
                # Musical variation protection: if singer is >2.3 st away from MIDI (e.g. held
                # melodic variation or MIDI differs from original vocal) but already sits close
                # to a valid local-key diatonic scale note, tune gently to that scale note.
                # If >4.5 st away (e.g. wrong-key / tritone note), mark review instead of auto-forcing.
                if abs(cents) > 450.:
                    generated.append(Suggestion(
                        track.id, 'pitch', start, finish, cents * pitch_strength,
                        f'偏离 MIDI 过大（{cents / 100.:+.1f} 半音：{src_name} → {dst_name}），疑似唱偏或错八度',
                        True, True, category='pitch_large', severity='high',
                        action_hint=f'建议试听确认：若确实唱偏可点「接受建议」拉至 {dst_name}，若属和声/加花请点「保留原样」',
                    ))
                    continue
                if abs(cents) > 230. and local_scale is not None:
                    scale_target = _scale_aware_target(note.center_midi, dur, local_scale)
                    if abs(scale_target - note.center_midi) <= .65 and scale_target != match.target_pitch:
                        s_cents = (scale_target - note.center_midi) * 100.
                        if abs(s_cents) >= 6.:
                            base = s_cents * pitch_strength * .85
                            pts = _build_natural_pitch_curve(
                                midi, step, note, base, pitch_strength * .85,
                                clip_span=(start - track.offset, finish - track.offset))
                            generated.append(Suggestion(track.id, 'pitch', start, finish,
                                                        0. if pts else base, '旋律变奏保护 · 调内轻修',
                                                        False, True, points=pts))
                        continue
                # Monotonic passing-glide & onset-scoop protection:
                is_monotonic_glide = (
                    note.linked_prev and note.linked_next
                    and 0 < n_idx < len(in_range) - 1
                    and ((in_range[n_idx - 1].center_midi > note.center_midi + .35
                          and note.center_midi > in_range[n_idx + 1].center_midi + .35)
                         or (in_range[n_idx - 1].center_midi + .35 < note.center_midi
                             and note.center_midi + .35 < in_range[n_idx + 1].center_midi))
                )
                if is_monotonic_glide and dur < .105 and abs(cents) > 100.:
                    continue
                same_prev = (note.linked_prev and n_idx > 0
                             and matches[n_idx - 1].target_pitch == match.target_pitch)
                same_next = (note.linked_next and n_idx + 1 < len(matches)
                             and matches[n_idx + 1].target_pitch == match.target_pitch)
                is_edge_scoop = (note.linked_prev != note.linked_next)
                if (note.linked_prev or note.linked_next) and abs(cents) > 135.:
                    if dur < .090 and (is_edge_scoop or not (same_prev and same_next)):
                        continue
                    if dur < .115 and (is_edge_scoop or not (same_prev or same_next)):
                        damped = max(-95., min(95., cents * .35 * pitch_strength))
                        if abs(damped) >= 5.:
                            generated.append(Suggestion(track.id, 'pitch', start, finish, damped,
                                                        '过渡音轻修保护', False, True))
                        continue
                if abs(cents) >= 5.:
                    uncertain = match.review or abs(cents) > 260. or (dur < .15 and abs(cents) > 200.)
                    base = cents * pitch_strength
                    pts = _build_natural_pitch_curve(
                        midi, step, note, base, pitch_strength,
                        clip_span=(start - track.offset, finish - track.offset))
                    detail_txt = match.reason
                    cat, sev, hint = '', '', ''
                    if uncertain:
                        if '错八度' in match.reason:
                            detail_txt = f'疑似错八度（实测 {src_name} vs MIDI {dst_name}，差 {cents / 100.:+.1f} 半音）'
                            cat, sev = 'pitch_large', 'high'
                            hint = '请先在右侧「音轨」或 MIDI 菜单核对本轨八度参照（0 / -12 / +12 半音）'
                        elif '和弦' in match.reason:
                            detail_txt = f'MIDI 同轨存在重叠和弦（{src_name} → {dst_name}）'
                            cat, sev = 'pitch_large', 'medium'
                            hint = '请试听确认本轨应唱哪条声部，必要时手动拖拽音符至目标音高'
                        elif '草稿' in match.reason:
                            detail_txt = f'导唱提取草稿待核对（{src_name} → {dst_name}，{cents:+.0f}¢）'
                            cat, sev = 'score_draft', 'medium'
                            hint = '确认乐谱起点与目标音高无误后，点顶栏「确认乐谱」即可批量自动修音'
                        elif abs(cents) > 200.:
                            detail_txt = f'修音幅度较大（{cents / 100.:+.1f} 半音：{src_name} → {dst_name}），防误拉暂未自动应用'
                            cat = 'pitch_jump' if dur < .15 else 'pitch_large'
                            sev = 'high' if abs(cents) > 280. else 'medium'
                            hint = f'建议试听：若确实唱偏可点「接受建议」修到 {dst_name}，若觉得拉完太生硬请点「保留原样」'
                        else:
                            detail_txt = f'{match.reason}（{src_name} → {dst_name}，{cents:+.0f}¢）'
                            cat, sev = 'pitch_large', 'medium'
                            hint = f'音符与 MIDI 时间重合度偏低，建议试听后再决定接受至 {dst_name} 或保留原样'
                    generated.append(Suggestion(track.id, 'pitch', start, finish,
                                                0. if pts else base, detail_txt, uncertain, True, points=pts,
                                                category=cat, severity=sev, action_hint=hint))
        else:
            min_dur = .075 if ensemble_scale is not None else .115
            for note in in_range:
                start, finish = max(begin, track.offset + note.start), min(end, track.offset + note.end)
                dur = finish - start
                if dur < (min_dur if not (note.linked_prev or note.linked_next) else .075):
                    continue
                if _note_body_span(midi, step, note) > 2.20:
                    continue
                target = _scale_aware_target(note.center_midi, dur, ensemble_scale)
                cents = (target - note.center_midi) * 100.
                if abs(cents) >= 6.:
                    detail = '本轨调内音' if ensemble_scale is not None else '本轨稳定音'
                    base = cents * pitch_strength
                    pts = _build_natural_pitch_curve(
                        midi, step, note, base, pitch_strength,
                        clip_span=(start - track.offset, finish - track.offset))
                    uncertain = abs(cents) > 240.
                    generated.append(Suggestion(
                        track.id, 'pitch', start, finish, 0. if pts else base,
                        (f'本轨大偏移待核对（{cents / 100.:+.1f} 半音：{_midi_note_name(note.center_midi)} → {_midi_note_name(target)}）'
                         if uncertain else detail),
                        uncertain, False, points=pts,
                        category='pitch_large' if uncertain else '',
                        severity='medium' if uncertain else '',
                        action_hint='建议试听确认是否需要大幅修正，否则点「保留原样」' if uncertain else '',
                    ))
    return phrases, tuple(generated)


def transpose_track_to_score(project, track, curve, *, target_octave=0, shift_hint=0., pitch_strength=.90):
    """One-click whole-track transposition & MIDI pitch alignment for an off-key vocal track.

    Uses ``shift_hint`` (e.g. +6 st) solely to match each raw vocal note to its intended MIDI note
    in ``target_octave``, then writes natural vibrato-preserving pitch edits from raw F0 to MIDI.
    """
    from chorus_notes import VocalNote, segment_vocal_notes
    from chorus_score_match import expand_vocal_notes_for_score, match_vocal_to_score, shift_score_notes

    score = project.score
    if score is None or not track.score_lane or curve is None:
        return project, 0
    raw_lane = next((lane.notes for lane in score.lanes if lane.id == track.score_lane), None)
    if not raw_lane:
        return project, 0
    updated = project.update_track(track.id, score_octave=int(target_octave))
    kept_edits = tuple(e for e in updated.edits if not (e.track == track.id and e.kind == 'pitch' and not e.manual))
    updated = replace(updated, edits=kept_edits)
    tr = updated.track(track.id)
    midi, step = curve
    lane = shift_score_notes(raw_lane, offset=score.offset, octave_semitones=int(target_octave))
    time_edits = updated.edits_for(tr.id, 'timing')

    def to_output(take_a, take_b):
        sa, sb = tr.offset + take_a, tr.offset + take_b
        center = (sa + sb) / 2.
        mapping = next((row for row in time_edits
                        if row.source_time(row.start) <= center < row.source_time(row.end)), None)
        if mapping:
            oa, ob = mapping.output_time(sa), mapping.output_time(sb)
            return (oa, ob) if ob > oa else (ob, oa)
        return sa, sb

    raw_notes = segment_vocal_notes(midi, step, note_edits=updated.note_edits_for(tr.id))
    if not raw_notes:
        return updated, 0
    # Shift curve & note centers by shift_hint so score sub-splitting and matching align to target key.
    shifted_midi = midi + np.float32(shift_hint)
    raw_notes = expand_vocal_notes_for_score(
        raw_notes, lane, time_to_output=to_output, curve=(shifted_midi, step))
    # Recompute raw center_midi for each expanded note from the original unshifted curve.
    rebuilt_raw = []
    shifted_notes = []
    for n in raw_notes:
        a = max(0, round(n.start / step))
        b = min(len(midi), max(a + 1, round(n.end / step)))
        seg = midi[a:b]
        fin = seg[np.isfinite(seg)]
        raw_center = float(np.median(fin)) if len(fin) else (n.center_midi - float(shift_hint))
        raw_lo = float(np.percentile(fin, 10)) if len(fin) else (n.low_midi - float(shift_hint))
        raw_hi = float(np.percentile(fin, 90)) if len(fin) else (n.high_midi - float(shift_hint))
        rebuilt_raw.append(VocalNote(n.start, n.end, raw_center, raw_lo, raw_hi, n.linked_prev, n.linked_next))
        shifted_notes.append(VocalNote(
            n.start, n.end, raw_center + float(shift_hint),
            raw_lo + float(shift_hint), raw_hi + float(shift_hint),
            n.linked_prev, n.linked_next,
        ))

    matches = match_vocal_to_score(shifted_notes, lane, time_to_output=to_output, draft=False)
    strength = max(0., min(1., float(pitch_strength)))
    sugs = []
    for note, match in zip(rebuilt_raw, matches):
        if match.target_pitch is None:
            continue
        cents = (match.target_pitch - note.center_midi) * 100.
        if abs(cents) < 5. or abs(cents) > 1050.:
            continue
        start = max(updated.start, tr.offset + note.start)
        finish = min(updated.end, tr.offset + note.end)
        if finish - start < .045:
            continue
        # Apply 100% of the integer-semitone key transposition so the singer lands in key,
        # while scaling only the fine sub-semitone intonation residual by pitch_strength.
        coarse = round(cents / 100.) * 100.
        base = coarse + (cents - coarse) * strength
        pts = _build_natural_pitch_curve(
            midi, step, note, base, strength,
            clip_span=(start - tr.offset, finish - tr.offset))
        sugs.append(Suggestion(
            tr.id, 'pitch', start, finish, 0. if pts else base,
            f'整轨移调对齐 MIDI（{cents / 100.:+.1f} 半音）', False, True, points=pts,
        ))
    if sugs:
        updated = apply_suggestions(updated, sugs, manual=False)
    return updated, len(sugs)


def apply_suggestions(project, rows, *, manual=False):
    """Commit chosen candidates against their project and take coordinates."""
    for row in rows:
        if not row.can_apply:
            continue
        track = project.track(row.track)
        if track is None:
            continue
        bias = track.offset if row.kind == 'pitch' else 0.
        start, end = row.start - bias, row.end - bias
        edit = (None if row.restore_timing and not manual else
                Edit(track.id, row.kind, start, end, row.value, row.points, manual=manual, basis=row.route))
        blocked = () if manual else tuple(region for region in project.edits_for(track.id, row.kind) if region.manual)
        cursor = start
        for lock in blocked:
            if lock.end <= cursor or lock.start >= end:
                continue
            if lock.start > cursor:
                project = project.replace_region(track.id, row.kind, cursor, lock.start,
                                                 (edit.clip(cursor, lock.start),) if edit else ())
            cursor = max(cursor, lock.end)
        if cursor < end:
            project = project.replace_region(track.id, row.kind, cursor, end,
                                             (edit.clip(cursor, end),) if edit else ())
    return project
