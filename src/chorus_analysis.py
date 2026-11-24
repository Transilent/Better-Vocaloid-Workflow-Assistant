"""Measure phrase timing and target stable notes from each independent take."""
from dataclasses import dataclass, replace
import numpy as np
import soxr

from chorus_project import Edit, Phrase
from chorus_precision import (analysis_phrases, fit_timing, fit_translation, note_anchors, phone_anchors,
                              phone_requests, phoneme_cache)
from i18n import tr


FRAME = .01
FEATURE_RATE = 8000


@dataclass
class Features:
    energy: np.ndarray
    shape: np.ndarray
    active: np.ndarray
    peaks: list


@dataclass(frozen=True)
class Suggestion:
    track: str
    kind: str
    start: float
    end: float
    value: float | None
    detail: str
    review: bool = False
    from_score: bool = False
    points: tuple = ()
    anchors: tuple = ()
    route: str = ''
    timing_residual_ms: int | None = None
    restore_timing: bool = False

    @property
    def can_apply(self):
        return self.restore_timing or bool(self.points) or (self.value is not None and abs(self.value) > 1e-6)


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
    track.check_source()
    return Features(energy, shape, active, peaks.round(4).tolist())


def _runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return zip(edges[::2], edges[1::2])


def phrases_from_reference(reference, features, first, last, score=None):
    if reference is None or features is None:
        return (Phrase(first, last),) if first < last else ()
    bounds = []
    for a, b in _runs(features.active):
        if (b - a) * FRAME < .075:
            continue
        start, end = reference.offset + a * FRAME, reference.offset + b * FRAME
        if bounds and start - bounds[-1][1] < .40:
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


def _project_slice(values, offset, indices):
    local = indices - round(offset / FRAME)
    valid = (local >= 0) & (local < len(values))
    output = values[np.clip(local, 0, len(values) - 1)].copy()
    output[~valid] = 0
    return output


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
        return None, '没有足够的发音可配对', True
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


def quiet_timing(track, features):
    """Return a predicate for guarded cuts on this track's project clock."""
    def quiet(first, last):
        grid = np.arange(round(first / FRAME), round(last / FRAME))
        return not _project_slice(features.active, track.offset, grid).any()

    return quiet


def timing_suggestion(track, span, fit, route, source, review_reason=''):
    if fit.movement is None:
        method = tr('唱段边界过近，可扩展选区后再试')
    elif fit.local:
        method = tr('局部拉伸 · 基准 {0} ms', f'{fit.movement * 1000:+.0f}')
    elif fit.movement:
        method = tr('整句平移 {0} ms', f'{fit.movement * 1000:+.0f}')
    else:
        method = tr('原录音时序已接近参照' if fit.residual_ms <= 25 else '该力度下时序调整很小')
    detail = f'{tr(route)} · {source} · {method}'
    if fit.anchors:
        detail += ' · ' + tr('{0} 个边界 · 中位余差 {1} ms', len(fit.anchors), fit.residual_ms)
    if review_reason:
        detail += ' · ' + tr(review_reason)
    elif fit.review and fit.movement is not None:
        detail += ' · ' + tr('边界意见不一致，建议试听')
    return Suggestion(track, 'timing', *span, fit.movement, detail, fit.review or bool(review_reason),
                      points=fit.points, anchors=fit.anchors, route=route,
                      timing_residual_ms=fit.residual_ms if fit.anchors else None)


def _pitch_runs(midi, step, *, note_edits=()):
    from chorus_notes import segment_vocal_notes

    for note in segment_vocal_notes(midi, step, note_edits=note_edits):
        yield note.start, note.end, note.center_midi


def _pitch_target(start, end, notes):
    overlap = sorted(
        ((min(end, note.end) - max(start, note.start), note.pitch) for note in notes
         if note.start < end and start < note.end),
        reverse=True)
    if not overlap or overlap[0][0] < (end - start) * .30:
        return None, False
    ambiguous = len(overlap) > 1 and overlap[0][1] != overlap[1][1] and overlap[1][0] >= overlap[0][0] * .76
    return overlap[0][1], ambiguous


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
        fit = fit_timing(anchors, first, last, strength, radius, quiet)
        if fit is None and track.id in curves and ref.id in curves:
            for item in (ref, track):
                if item.id not in note_runs:
                    note_runs[item.id] = tuple((item.offset + a, item.offset + b, pitch)
                                               for a, b, pitch in _pitch_runs(*curves[item.id]))
            notes = note_anchors(note_runs[ref.id], note_runs[track.id], phrase, movement)
            fit = fit_timing(notes, first, last, strength, radius, quiet)
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
            generated.append(Suggestion(track.id, 'timing', first, last, None, detail, True, route='需核对'))
        else:
            fit = fit_translation(first, last, movement * strength, quiet)
            reason = detail if review else ('仅靠工程参照推断的大位移，建议先试听'
                                            if not explicit and abs(movement) > .100 else '')
            generated.append(timing_suggestion(track.id, (first, last), fit, '声学起音', source, reason))
    return generated


def proposals(project, targets, selection, kind, strength, radius, features, curves, check, progress,
              *, speech=None, audio=None, phone_progress=lambda *_: None, ensemble_language='', pitch_strength=None):
    begin, end = selection
    audio = audio or {track.id: track for track in project.tracks}
    if ensemble_language and speech is not None and kind != 'pitch':
        from chorus_ensemble import ensemble_timing

        phrases, generated = ensemble_timing(project, targets, selection, features, audio, ensemble_language,
                                             strength, radius, speech, check, phone_progress)
    else:
        reference = project.reference()
        phrases = phrase_regions(project, reference, features.get(reference.id) if reference else None, selection)
        generated = (_timing_candidates(project, targets, selection, strength, radius, features, curves, check,
                                        phone_progress, speech, audio) if kind != 'pitch' else [])
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
    notes_by_lane = {lane.id: tuple(type(note)(note.start + score.offset, note.end + score.offset,
                                             note.pitch, note.lyric) for note in lane.notes)
                     for lane in score.lanes} if score else {}
    from chorus_notes import segment_vocal_notes
    from chorus_score_match import _normalize_token, match_vocal_to_score

    for index, track in enumerate(targets):
        check()
        progress(index, track.name)
        if kind == 'timing' or track.id not in curves:
            continue
        midi, step = curves[track.id]
        lane = notes_by_lane.get(track.score_lane)
        timing = [Edit(track.id, 'timing', row.start, row.end, row.value, row.points)
                  for row in generated if row.track == track.id and row.kind == 'timing'
                  and row.can_apply and not row.review]
        applied_timing = project.edits_for(track.id, 'timing')
        time_edits = [row for row in applied_timing if row.manual] + timing + [row for row in applied_timing
                                                                                 if not row.manual]
        vocal_notes = segment_vocal_notes(midi, step, note_edits=project.note_edits_for(track.id))
        in_range = [note for note in vocal_notes
                    if min(end, track.offset + note.end) - max(begin, track.offset + note.start) >= .05]
        if lane is not None:
            def to_output(take_a, take_b):
                sa, sb = track.offset + take_a, track.offset + take_b
                center = (sa + sb) / 2.
                mapping = next((row for row in time_edits
                                if row.source_time(row.start) <= center < row.source_time(row.end)), None)
                if mapping:
                    return mapping.output_time(sa), mapping.output_time(sb)
                return sa, sb

            phrase_texts = tuple(
                (p.start, p.end, _normalize_token(line.text))
                for p in project.phrases
                if (line := p.resolved_lyric(track.id)) and line.text
            )
            matches = match_vocal_to_score(
                in_range, lane, time_to_output=to_output, phrase_texts=phrase_texts,
                draft=bool(score and not score.confirmed),
            )
            for note, match in zip(in_range, matches):
                start, finish = max(begin, track.offset + note.start), min(end, track.offset + note.end)
                if match.target_pitch is None:
                    generated.append(Suggestion(track.id, 'pitch', start, finish, None,
                                                match.reason, True, True))
                    continue
                cents = (match.target_pitch - note.center_midi) * 100.
                if abs(cents) >= 5.:
                    uncertain = match.review or abs(cents) > 240.
                    generated.append(Suggestion(track.id, 'pitch', start, finish, cents * pitch_strength,
                                                match.reason, uncertain, True))
        else:
            for note in in_range:
                start, finish = max(begin, track.offset + note.start), min(end, track.offset + note.end)
                if finish - start < .115:
                    continue
                if note.high_midi - note.low_midi > 1.25:
                    continue
                target = round(note.center_midi)
                cents = (target - note.center_midi) * 100.
                if abs(cents) >= 6.:
                    generated.append(Suggestion(track.id, 'pitch', start, finish, cents * pitch_strength,
                                                '本轨稳定音', abs(cents) > 240., False))
    return phrases, tuple(generated)


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
