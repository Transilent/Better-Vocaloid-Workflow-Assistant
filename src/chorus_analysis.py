"""Measure phrase timing and target stable notes from each independent take."""
from dataclasses import dataclass
import numpy as np
import soxr

from chorus_project import Edit, Phrase


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

    @property
    def can_apply(self):
        return self.value is not None and abs(self.value) > 1e-6


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
    for a, b in bounds:
        a, b = max(first, a - .07), min(last, b + .09)
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


def _pitch_runs(midi, step):
    for start, stop in _runs(np.isfinite(midi)):
        if (stop - start) * step < .08:
            continue
        part = midi[start:stop]
        window = max(3, round(.08 / step) | 1)
        windows = np.lib.stride_tricks.sliding_window_view(np.pad(part, window // 2, mode='edge'), window)
        smooth = np.median(windows, axis=1)
        centers = np.rint(smooth)
        edges = np.r_[0, np.flatnonzero(np.diff(centers)) + 1, len(centers)]
        for left, right in zip(edges, edges[1:]):
            if (right - left) * step < .075:
                continue
            raw = part[left:right]
            yield (start + left) * step, (start + right) * step, float(np.median(raw))


def _pitch_target(start, end, notes):
    overlap = sorted(
        ((min(end, note.end) - max(start, note.start), note.pitch) for note in notes
         if note.start < end and start < note.end),
        reverse=True)
    if not overlap or overlap[0][0] < (end - start) * .30:
        return None, False
    ambiguous = len(overlap) > 1 and overlap[0][1] != overlap[1][1] and overlap[1][0] >= overlap[0][0] * .76
    return overlap[0][1], ambiguous


def proposals(project, targets, selection, kind, strength, radius, features, curves, check, progress):
    begin, end = selection
    generated = []
    reference = project.reference()
    phrases = phrases_from_reference(reference, features.get(reference.id) if reference else None,
                                    begin, end, project.score)
    score = project.score
    notes_by_lane = {lane.id: tuple(type(note)(note.start + score.offset, note.end + score.offset,
                                             note.pitch, note.lyric) for note in lane.notes)
                     for lane in score.lanes} if score else {}
    for index, track in enumerate(targets):
        check()
        progress(index, track.name)
        if kind != 'pitch':
            ref = project.reference(track)
            if ref is not None and ref.id != track.id:
                ref_phrases = (phrases if ref == reference else phrases_from_reference(
                    ref, features[ref.id], begin, end, score))
                for phrase in ref_phrases:
                    check()
                    movement, source, review = _timing_match(ref, track, features[ref.id],
                                                            features[track.id], phrase, radius)
                    generated.append(Suggestion(track.id, 'timing', phrase.start, phrase.end,
                                                None if movement is None else movement * strength,
                                                source, review))
        if kind == 'timing' or track.id not in curves:
            continue
        midi, step = curves[track.id]
        lane = notes_by_lane.get(track.score_lane)
        timing = [row for row in generated if row.track == track.id and row.kind == 'timing']
        applied_timing = project.edits_for(track.id, 'timing')
        for a, b, actual in _pitch_runs(midi, step):
            start, finish = max(begin, track.offset + a), min(end, track.offset + b)
            if finish - start < .05:
                continue
            if lane is None:
                target, uncertain = round(actual), False
            else:
                center = (start + finish) / 2
                suggested = next((row.value for row in timing
                                  if row.value is not None and row.start <= center < row.end and not row.review), None)
                delay = suggested if suggested is not None else next(
                    (row.value for row in applied_timing if row.start <= center < row.end), 0.)
                target, uncertain = _pitch_target(start + delay, finish + delay, lane)
            if target is None:
                generated.append(Suggestion(track.id, 'pitch', start, finish, None,
                                            '本声部乐谱在这里没有匹配音符', True, True))
                continue
            cents = (target - actual) * 100
            if abs(cents) >= 5:
                uncertain = uncertain or abs(cents) > 240
                generated.append(Suggestion(track.id, 'pitch', start, finish, cents * strength,
                                            '本声部乐谱' if lane is not None else '本轨稳定音',
                                            uncertain, lane is not None))
    return phrases, tuple(generated)


def apply_suggestions(project, rows):
    """Commit chosen candidates against their project and take coordinates."""
    for row in rows:
        if row.value is None or abs(row.value) < 1e-6:
            continue
        track = project.track(row.track)
        if track is None:
            continue
        bias = track.offset if row.kind == 'pitch' else 0.
        start, end = row.start - bias, row.end - bias
        edit = Edit(track.id, row.kind, start, end, row.value)
        project = project.replace_region(track.id, row.kind, start, end, (edit,))
    return project
