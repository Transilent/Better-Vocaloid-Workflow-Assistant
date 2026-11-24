"""Measure and edit vocal strength at the voiced body of each syllable."""
from dataclasses import dataclass, replace
from math import floor, log10

import numpy as np
import soundfile as sf
import soxr

from chorus_project import Edit, SYLLABLE_GAIN


_VOWELS = {
    'ja': frozenset('a i u e o'.split()),
    'en': frozenset('aa ae ah ao aw ax ay eh er ey ih iy ow oy uh uw'.split()),
}
_MAX_GAIN = 6.
_HEADROOM = .98
_STEP = .01


def vowel_phone(label, language):
    phone = label.rsplit('/', 1)[-1].casefold()
    if language in _VOWELS:
        return phone.rstrip('012') in _VOWELS[language]
    return language in ('zh', 'yue') and phone.startswith(tuple('aeiouv') + ('yu', 'y0'))


def time_sections(project, track, selection):
    """Partition an output selection into dry and time-mapped pieces."""
    first, last = selection
    cursor = first
    for edit in sorted(project.edits_for(track.id, 'timing'), key=lambda row: row.start):
        left, right = max(cursor, edit.start), min(last, edit.end)
        if left >= right:
            continue
        if cursor < left:
            yield cursor, left, None
        yield left, right, edit
        cursor = right
    if cursor < last:
        yield cursor, last, None


def take_spans(project, track, selection):
    spans = []
    for start, end, edit in time_sections(project, track, selection):
        a, b = (edit.source_time(start), edit.source_time(end)) if edit else (start, end)
        left, right = max(0., a - track.offset), min(track.duration, b - track.offset)
        if left < right:
            spans.append((left, right))
    merged = []
    for left, right in sorted(spans):
        if merged and left <= merged[-1][1] + 1e-6:
            merged[-1] = merged[-1][0], max(merged[-1][1], right)
        else:
            merged.append((left, right))
    return tuple(merged)


def gain_time(project, track, selection, take_time):
    time = track.offset + take_time
    for start, end, edit in time_sections(project, track, selection):
        output = edit.output_time(time) if edit else time
        if start <= output < end and (not edit or edit.source_time(start) <= time < edit.source_time(end)):
            return output
    return time


def clear_syllables(project, identity, spans):
    edits = tuple(edit for edit in project.edits if not (
        edit.kind == SYLLABLE_GAIN and edit.track == identity
        and any(a < edit.end and b > edit.start for a, b in spans)))
    return replace(project, edits=edits)


@dataclass(frozen=True)
class SyllableGain:
    label: str
    first: float
    last: float
    measured_db: float
    limit: float
    db: float
    shape: tuple

    def edit(self, identity):
        return Edit(identity, SYLLABLE_GAIN, self.first, self.last, self.db,
                    tuple((time, weight * self.db) for time, weight in self.shape),
                    basis=self.label, manual=True)


def apply_syllables(project, identity, rows):
    spans = tuple((row.first, row.last) for row in rows)
    cleared = clear_syllables(project, identity, spans)
    edits = (*cleared.edits, *(row.edit(identity) for row in rows if row.db > 0.))
    return replace(cleared, edits=tuple(sorted(edits, key=lambda row: (row.track, row.kind, row.start))))


def _voiced_frames(pcm, rate, center_samples, levels):
    voice_rate = 4000
    strongest = np.argmax(np.mean(pcm ** 2, axis=0))
    wave = soxr.resample(pcm[:, strongest], rate, voice_rate)
    half = round(.024 * voice_rate)
    views = np.lib.stride_tricks.sliding_window_view(np.pad(wave, half), 2 * half)
    centers = np.clip(np.rint(center_samples * (voice_rate / rate)).astype(int), 0, len(wave))
    fft_size = 512
    bins = np.fft.rfftfreq(fft_size, 1. / voice_rate)
    voice_band = (bins >= 70.) & (bins <= 1700.)
    checked = np.flatnonzero(levels > -65.)
    voiced = np.zeros(len(levels), dtype=bool)
    for first in range(0, len(checked), 1024):
        indices = checked[first:first + 1024]
        frames = views[centers[indices]]
        power = np.abs(np.fft.rfft(frames - frames.mean(axis=1, keepdims=True), n=fft_size, axis=1)) ** 2
        power[:, ~voice_band] = 0.
        periodic = np.fft.irfft(power, n=fft_size, axis=1)
        scan = periodic[:, voice_rate // 950 - 1:voice_rate // 70 + 2]
        center = scan[:, 1:-1]
        peaks = (center > scan[:, :-2]) & (center >= scan[:, 2:])
        repeat = np.max(np.where(peaks, center, 0.), axis=1)
        energy = periodic[:, 0]
        # Reject aperiodic air, sibilance and low rumble around the vowels.
        voiced[indices] = (repeat >= .60 * energy) & (energy / (2 * half) >= .12 * 10. ** (levels[indices] / 10.))
    return voiced


def _frame_levels(recording, first, last):
    centers = np.arange(max(0, floor(first / _STEP)), int(last / _STEP) + 1) * _STEP
    if not len(centers):
        return None
    rate = recording.rate
    samples = np.rint(centers * rate).astype(np.int64)
    half = max(1, round(.012 * rate))
    pad = round(.050 * rate)
    a, b = max(0, samples[0] - pad), min(recording.frames, samples[-1] + pad)
    recording.check_source()
    with sf.SoundFile(recording.path) as stream:
        stream.seek(a)
        pcm = stream.read(b - a, dtype='float32', always_2d=True)
    recording.check_source()
    if len(pcm) != b - a or not np.isfinite(pcm).all():
        raise ValueError('录音采样无效，无法分析音节力度。')
    energy = np.r_[0., np.cumsum(np.mean(np.square(pcm, dtype=np.float64), axis=1))]
    low, high = np.clip(samples - half - a, 0, len(pcm)), np.clip(samples + half - a, 0, len(pcm))
    db = 10. * np.log10(np.maximum(1e-12, (energy[high] - energy[low]) / np.maximum(1, high - low)))
    return centers, db, _voiced_frames(pcm, rate, samples - a, db), pcm, a


def _shape(centers, gate, first, last):
    edges = np.flatnonzero(np.diff(np.r_[False, gate, False]))
    # Short dropouts in voicing stay inside a sustained vowel. Longer gaps
    # are left alone, including breaths near the end of an aligned vowel.
    for left, right in zip(edges[1:-1:2], edges[2::2]):
        if (right - left) * _STEP <= .021:
            gate[left:right] = True
    edges = np.flatnonzero(np.diff(np.r_[False, gate, False]))
    points = []
    for left, right in zip(edges[::2], edges[1::2]):
        a = float(max(first, centers[left] - _STEP * .5))
        b = float(min(last, centers[right - 1] + _STEP * .5))
        if b - a < .044:
            continue
        fade = min(.035, (b - a) * .4)
        # Eased attack and release sampled in dB, including a neutral seam.
        for x, db in ((0., 0.), (.25, .15625), (.75, .84375), (1., 1.)):
            points.append((a + x * fade, db))
        for x, db in ((1., 1.), (.75, .84375), (.25, .15625), (0., 0.)):
            points.append((b - x * fade, db))
    return tuple(points)


def suggest_syllables(clip, spans, phones):
    recording = clip.track
    context = _frame_levels(recording, max(0., spans[0][0] - 18.),
                            min(recording.duration, spans[-1][1] + 18.))
    if context is None:
        return ()
    centers, levels, voiced, pcm, sample_first = context
    # Estimate the singer's locally supported level. Leave a few dB for
    # natural emphasis and keep the authority of each suggested boost low.
    target = float(np.quantile(levels[voiced], .76)) - 3.5 if voiced.sum() * _STEP >= .5 else -65.
    origin = clip.frames[0] / recording.rate
    rows = []
    for label, start, end in phones:
        if not vowel_phone(label, clip.language):
            continue
        center = origin + (start + end) * .5
        span = next((span for span in spans if span[0] <= center < span[1]), None)
        if span is None:
            continue
        # Respect phone boundaries even if the voicing model covers a neighbouring consonant.
        a = max(span[0], origin + start + .008, rows[-1].last if rows else 0.)
        b = min(span[1], origin + end - .008)
        region = (centers >= a) & (centers <= b)
        supported = region & voiced
        if supported.sum() * _STEP < .04 or supported.sum() < region.sum() * .55:
            continue
        level = float(np.quantile(levels[supported], .70))
        shape = _shape(centers, supported & (levels >= level - 9.), a, b)
        if not shape:
            continue
        a, b = shape[0][0], shape[-1][0]
        left, right = (round(t * recording.rate) - sample_first for t in (a, b))
        peak = float(np.max(np.abs(pcm[left:right]), initial=0.))
        limit = floor(gain_limit(peak) * 2.) / 2.
        if limit <= 0.:
            continue
        gain = min(4., limit, max(0., round((target - level) * .8 * 2.) / 2.))
        rows.append(SyllableGain(label.rsplit('/', 1)[-1], a, b, level, limit, gain, shape))
    return tuple(rows)


def gain_limit(peak):
    return min(_MAX_GAIN, max(0., 20. * log10(_HEADROOM / max(1e-12, peak))))


def render_syllable_gains(audio, rate, edits, check, dry=None):
    for edit in edits:
        check()
        start, end = max(0, round(edit.start * rate)), min(len(audio), round(edit.end * rate))
        if end <= start or not edit.value:
            continue
        view = audio[start:end]
        peak = float(np.max(np.abs(view), initial=0.))
        if dry is not None:
            peak = max(peak, float(np.max(np.abs(dry[start:end]), initial=0.)))
        limit = gain_limit(peak)
        db = np.interp(np.arange(start, end) / rate, *zip(*edit.points))
        envelope = np.power(10., db * (min(1., limit / edit.value) / 20.))[:, None].astype(np.float32)
        view *= envelope
        if dry is not None:
            dry[start:end] *= envelope
