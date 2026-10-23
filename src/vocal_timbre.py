"""Conservative relative timbre cues, not a voicebank's inverse synthesis model."""
import math

ALGORITHM_VERSION = 6
PARAMETERS = ('tension', 'breathiness', 'voicing')
NEUTRAL = {'tension': 0., 'breathiness': 0., 'voicing': 1.}


def settings(options):
    chosen = []
    for name in PARAMETERS:
        value = options.get('reference_' + name, False)
        if type(value) is not bool:
            raise ValueError('参考音色参数的开关需要为布尔值。')
        if value:
            chosen.append(name)
    return tuple(chosen)


def select_channels(block, mode='standard'):
    """SV extra channels are interleaved: main-L, asp-L, main-R, asp-R."""
    if mode=='standard':
        if block.shape[1] not in (1,2):raise ValueError('普通人声需要单／双声道；请明确选择 SV 气声输出方式。')
        return block
    if mode not in ('sv-extra','sv-isolated') or block.shape[1] not in (2,4):
        raise ValueError('追加／隔离气声模式需要 2 或 4 声道的 SV 分轨文件。')
    main=block[:,::2]
    return main if mode=='sv-extra' else main+block[:,1::2]


def feature_frames(path, notes, cancel=lambda: None, *, channel_mode='standard'):
    """Analyze bounded 8 s blocks at 24 kHz, retaining only score-supported f0."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from expression_component import load
    pw = load()
    collected = []
    with sf.SoundFile(str(path)) as audio:
        rate, length = audio.samplerate, audio.frames
        if rate < 8000 or not length:
            raise ValueError('参考人声音频需要至少 8 kHz 采样率和有效采样。')
        for first in range(0, length, 8 * rate):
            cancel()
            left, right = max(0, first - rate // 5), min(length, first + 8 * rate + rate // 5)
            audio.seek(left)
            block = audio.read(right - left, dtype='float64', always_2d=True)
            if len(block) != right - left or not np.isfinite(block).all():
                raise ValueError('参考人声音频包含无效采样或文件不完整。')
            block = select_channels(block,channel_mode)
            block -= block.mean(axis=0)
            # Strongest-channel analysis avoids destroying opposite-polarity stereo.
            x = block[:, int(np.argmax(np.mean(block * block, axis=0)))]
            divisor = math.gcd(rate, 24000)
            x = np.ascontiguousarray(resample_poly(x, 24000 // divisor, rate // divisor), dtype=np.float64)
            peak = float(np.max(abs(x), initial=0))
            if peak > 0:
                x /= peak  # Spectral ratios are independent of recording gain.
            if len(x) < 4800:
                x = np.pad(x, (0, 4800 - len(x)))
            f0, local = pw.dio(x, 24000, f0_floor=65, f0_ceil=1400, frame_period=20.)
            absolute = local + left / rate
            expected = np.full(len(local), np.nan)
            for start, end, pitch in notes:
                expected[(absolute >= start) & (absolute < end)] = pitch
            midi = 69 + 12 * np.log2(np.maximum(f0, 1e-9) / 440)
            # Correct only an octave decision supported by the existing score.
            octave = np.rint((expected - midi) / 12)
            octave = np.nan_to_num(np.clip(octave, -1, 1))
            f0 = f0 * (2. ** octave)
            f0 = pw.stonemask(x, np.ascontiguousarray(f0), local, 24000)
            midi = 69 + 12 * np.log2(np.maximum(f0, 1e-9) / 440)
            valid = np.isfinite(expected) & (f0 >= 65) & (f0 <= 1400) & (abs(midi - expected) < 1.5)
            f0[~valid] = 0
            sp = pw.cheaptrick(x, f0, local, 24000)
            ap = pw.d4c(x, f0, local, 24000)
            if not np.isfinite(sp).all() or not np.isfinite(ap).all():
                raise ValueError('WORLD 分析返回无效数值，请检查参考音频。')
            frequency = np.linspace(0, 12000, sp.shape[1])
            noise = sp * np.clip(ap, 0, 1) ** 2
            harmonic = np.maximum(0, sp - noise)
            high = (frequency >= 1500) & (frequency <= 7000)
            band = (frequency >= 100) & (frequency <= 7000)
            breath = 10 * np.log10(np.maximum(noise[:, high].sum(axis=1), 1e-20) /
                                      np.maximum(sp[:, high].sum(axis=1), 1e-20))
            periodic = harmonic[:, band].sum(axis=1) / np.maximum(sp[:, band].sum(axis=1), 1e-20)
            # Compare upper harmonics with the first two; pitch-conditioned
            # centering below reduces the influence of changing melody.
            order = frequency[None, :] / np.maximum(f0[:, None], 1)
            low = (order >= .7) & (order < 2.5)
            upper = (order >= 2.5) & (order <= 10.5) & (frequency[None, :] <= 7000)
            tilt = 10 * np.log10(np.maximum((harmonic * upper).sum(axis=1), 1e-20) /
                                     np.maximum((harmonic * low).sum(axis=1), 1e-20))
            level = 10 * np.log10(np.maximum(sp[:, band].sum(axis=1), 1e-20)) + 20 * math.log10(max(peak, 1e-12))
            keep = (absolute >= first / rate) & (absolute < min(length, first + 8 * rate) / rate)
            collected.append(np.column_stack([absolute, tilt, breath, periodic, level, midi, valid])[keep])
    result = np.concatenate(collected)
    # Quantize the time grid to avoid duplicate joins caused by float addition.
    result[:, 0] = np.round(result[:, 0], 6)
    return result, {'samplerate': rate, 'duration_seconds': length / rate, 'hop_seconds': .02,
                    'analyzer': 'WORLD 0.3.5', 'voicebank_calibrated': False}


def _feature_code_signature(source=None):
    """Fingerprint extraction independently of control mapping and formatting."""
    import ast
    import hashlib
    from pathlib import Path
    if source is None:
        source = Path(__file__).read_text(encoding='utf-8-sig')
    extractors = [node for node in ast.parse(source).body
                  if isinstance(node, ast.FunctionDef) and node.name in ('feature_frames','select_channels')]
    return hashlib.sha256(''.join(ast.dump(node,include_attributes=False) for node in extractors).encode()).hexdigest()


def cached_features(path, notes, cache=None, cancel=lambda: None):
    """Keep one verified feature file per voice; strength/toggles do not affect it."""
    if cache is None:
        return feature_frames(path, notes, cancel)
    import hashlib
    import json
    from pathlib import Path
    import os
    import tempfile
    import zipfile
    import numpy as np
    from common import file_hash, native_path
    from expression_component import SPEC, __file__ as loader_file
    cancel()
    source_hash = file_hash(path)
    identity = {'schema': 2, 'audio': source_hash, 'analyzer': SPEC['sha256'],
                'extractor': _feature_code_signature(), 'loader': file_hash(Path(loader_file)),
                'notes': [[float(a), float(b), float(p)] for a, b, p in notes]}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    cache = Path(cache)
    try:
        with np.load(native_path(cache), allow_pickle=False) as stored:
            matching = str(stored['signature']) == signature
            frames = stored['frames'] if matching else None
            metadata = json.loads(str(stored['metadata'])) if matching else {}
        if (isinstance(metadata, dict) and frames is not None and frames.dtype.kind == 'f'
                and frames.ndim == 2 and frames.shape[1] == 7 and len(frames)
                and np.isfinite(frames).all() and np.all(np.diff(frames[:, 0]) > 0)):
            cancel()
            return frames, {**metadata, 'cache_hit': True, 'cache_bytes': Path(native_path(cache)).stat().st_size}
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        pass
    frames, metadata = feature_frames(path, notes, cancel)
    cancel()
    if source_hash != file_hash(path):
        raise ValueError('分析期间参考音频发生变化，请重试。')
    Path(native_path(cache.parent)).mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=native_path(cache.parent), prefix='.timbre-',
                                         suffix='.pending', delete=False) as stream:
            pending = Path(stream.name)
            np.savez_compressed(stream, signature=signature, frames=frames,
                                metadata=json.dumps(metadata, ensure_ascii=False, allow_nan=False))
        cancel()
        os.replace(pending, native_path(cache))
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
    return frames, {**metadata, 'cache_hit': False, 'cache_bytes': Path(native_path(cache)).stat().st_size}


def _smooth_runs(values, valid, short=None):
    import numpy as np
    from scipy.ndimage import median_filter, uniform_filter1d
    result = np.zeros(len(values))
    transitions = np.diff(np.r_[False, valid, False].astype(int))
    for left, right in zip(np.where(transitions == 1)[0], np.where(transitions == -1)[0]):
        block = values[left:right]
        slow = uniform_filter1d(median_filter(block, size=5, mode='nearest'), size=7, mode='nearest')
        if short is None or not short[left:right].any():
            result[left:right] = slow
        else:
            fast = uniform_filter1d(median_filter(block, size=3, mode='nearest'), size=3, mode='nearest')
            weight = uniform_filter1d(short[left:right].astype(float), size=3, mode='nearest')
            result[left:right] = slow + weight * (fast - slow)
    return result


def phonetic_context(notes, language):
    """Read syllable nuclei for analysis only; never rewrite project pronunciation.

    Reuse the exporter's dictionaries. Unknown lyrics stay unknown rather than
    becoming its fallback syllable. A hyphen is resolved against audio-time gaps
    by the mapper, and a multi-syllable '+' is deliberately left unknown.
    """
    if language not in ('mandarin', 'japanese'):
        return [None] * len(notes)
    from project_exports import library
    library()
    from libresvip.core.lyric_phoneme.chinese import get_pinyin_series
    from libresvip.core.lyric_phoneme.japanese import to_romaji
    from libresvip.plugins.svp.phoneme_utils import phoneme_dictionary, get_phoneme_categories
    lyrics = [note.get('lyrics', '') for note in notes]
    graphemes = (get_pinyin_series(lyrics, filter_non_chinese=False) if language == 'mandarin'
                 else [to_romaji(lyric) for lyric in lyrics])
    result = []
    for note, lyric, grapheme in zip(notes, lyrics, graphemes):
        # A per-note language override needs its own converter; don't guess it.
        override = note.get('languageOverride') or note.get('attributes', {}).get('languageOverride')
        if override and override != language:
            result.append(None)
            continue
        if lyric.strip() == '-':
            result.append('-')
            continue
        phones = (note.get('phonemes') or phoneme_dictionary[language].get(grapheme.strip(), '')).split()
        categories = get_phoneme_categories(phones, language)
        nuclei = [phone for phone, kind in zip(phones, categories) if kind in ('vowel', 'diphthong')]
        result.append(language + ':' + nuclei[0] if len(nuclei) == 1 else None)
    return result


def _vowel_bias(times, tilt, active, intervals, nuclei, bins):
    """Bounded nuisance correction from repeated vowels at comparable pitches.

    Each independent syllable contributes one median; long notes and melismas
    cannot dominate. Require both vowel groups in at least two phrases, avoiding
    a correction inferred from just one expressive phrase or one held vowel.
    This is a conservative heuristic, not a formant/source separation model.
    """
    import numpy as np
    bias = np.zeros(len(times))
    report = {'status': 'insufficient_context', 'cells': 0, 'syllables': 0,
              'corrected_seconds': 0., 'max_correction_db': 0.}
    if nuclei is None:
        report['status'] = 'no_lyric_context'
        return bias, report
    if len(nuclei) != len(intervals):
        raise ValueError('音色分析的歌词与音符数量不一致。')
    cells, regions, previous, end, phrase = {}, [], None, -1., -1
    for index, ((start, stop), nucleus) in enumerate(zip(intervals, nuclei)):
        continuation = nucleus == '-'
        if start - end > .18:
            phrase += 1
            previous = None
        if continuation:
            nucleus = previous
        left, right = np.searchsorted(times, [start, stop])
        if nucleus:
            regions.append((left, right, nucleus))
            # Analyze the voiced body, not the consonant transition. Even short
            # notes keep a useful interior; this margin doesn't shorten notes.
            margin = min(.06, (stop-start) * .2)
            first, last = np.searchsorted(times, [start+margin, stop-margin])
            body = active[first:last].copy()
            if body.sum() >= 3 and not continuation:
                pitch_bin = float(np.median(bins[first:last][body]))
                # A moving pitch crossing a neighborhood isn't a clean anchor.
                body &= bins[first:last] == pitch_bin
                if body.sum() >= 3:
                    cells.setdefault(pitch_bin, {}).setdefault(nucleus, []).append(
                        (index, phrase, float(np.median(tilt[first:last][body]))))
        previous, end = nucleus, max(end, stop)
    corrections = {}
    for pitch_bin, groups in cells.items():
        groups = {name: rows for name, rows in groups.items()
                  if len(rows) >= 3 and len({row[1] for row in rows}) >= 2}
        if len(groups) < 2:
            continue
        centers = {name: float(np.median([row[2] for row in rows])) for name, rows in groups.items()}
        center = float(np.median(list(centers.values())))
        for name, rows in groups.items():
            # Partial subtraction leaves room for real singing differences that
            # happen to correlate with vowels. Never flatten each note to zero.
            corrections[pitch_bin, name] = float(np.clip(centers[name]-center, -3., 3.)) * .6
            report['syllables'] += len(rows)
    for left, right, nucleus in regions:
        for (pitch_bin, name), correction in corrections.items():
            if name == nucleus:
                local = bias[left:right]
                local[bins[left:right] == pitch_bin] = correction
    report.update(status='applied' if corrections else 'insufficient_context', cells=len(corrections),
                  corrected_seconds=round(int((active & (abs(bias) > 1e-8)).sum()) * .02, 3),
                  max_correction_db=round(float(np.max(abs(bias), initial=0)), 4))
    return bias, report


def _gesture_support(meaningful, active):
    """Require local evidence; an event elsewhere cannot validate a noise spike."""
    import numpy as np
    support = np.zeros(len(meaningful))
    runs = np.diff(np.r_[False, meaningful, False].astype(int))
    count = 0
    for left, right in zip(np.where(runs == 1)[0], np.where(runs == -1)[0]):
        if right - left < 3:
            continue
        support[left:right] = 1.
        count += 1
        # At most 40 ms of decay, bounded by valid signal and phrase gaps.
        for step, weight in ((1, 2/3), (2, 1/3)):
            for index, between in ((left-step, slice(left-step, left)),
                                   (right-1+step, slice(right, right+step))):
                if 0 <= index < len(support) and active[between].all():
                    support[index] = max(support[index], weight)
    return support, count


def _control_gaps(times, active, inside, audible, finite, reference_pitch, intervals):
    """Find tiny detection gaps inside continuous score coverage, not score rests."""
    import numpy as np
    # Even a rest shorter than a frame must separate two score regions.
    regions = []
    for start, end in sorted(intervals):
        if regions and start <= regions[-1][1] + 1e-7:
            regions[-1][1] = max(end, regions[-1][1])
        else:
            regions.append([start, end])
    coverage = np.full(len(times), -1, dtype=int)
    for index, (start, end) in enumerate(regions):
        coverage[(times >= start) & (times < end)] = index
    gaps = []
    runs = np.diff(np.r_[False, ~active, False].astype(int))
    for left, right in zip(np.where(runs == 1)[0], np.where(runs == -1)[0]):
        if (0 < left < right < len(times) and right-left <= 2
                and times[right]-times[left-1] <= .060001
                and inside[left:right].all() and audible[left:right].all() and finite[left:right].all()
                and coverage[left-1] >= 0 and coverage[left-1] == coverage[right]
                and abs(reference_pitch[right]-reference_pitch[left-1]) <= 4):
            gaps.append((left, right))
    return gaps


def _hold_evidence(support, measured, candidate, active, floor):
    """Retain measured weak dips between two locally validated gesture cores."""
    import numpy as np
    result = support.copy()
    core = support == 1
    runs = np.diff(np.r_[False, ~core, False].astype(int))
    count = frames = 0
    for left, right in zip(np.where(runs == 1)[0], np.where(runs == -1)[0]):
        if not (0 < left < right < len(core) and right-left <= 3 and active[left:right].all()):
            continue
        sign = np.sign(measured[left-1])
        if (sign and sign == np.sign(measured[right])
                and np.all(measured[left:right] * sign >= floor * .5)
                and np.all(candidate[left:right] * sign >= floor * .5)):
            result[left:right] = 1.
            count += 1
            frames += right-left
    return result, count, frames


def _bridge_control(delta, support, active, gaps):
    """Interpolate controls only between same-sign, independently supported ends."""
    import numpy as np
    result, valid = delta.copy(), active.copy()
    count = frames = 0
    for left, right in gaps:
        if support[left-1] < 1 or support[right] < 1 or delta[left-1] * delta[right] <= 0:
            continue
        result[left:right] = np.linspace(delta[left-1], delta[right], right-left+2)[1:-1]
        valid[left:right] = True
        count += 1
        frames += right-left
    return result, valid, count, frames


def map_features(frames, intervals, strength, chosen, *, nuclei=None, pitches=None, preserve_samples=False):
    """Neutral gaps and small, bounded changes; unreliable regions stay neutral."""
    import numpy as np
    from vocal_expression import simplify
    times, tilt, breath, periodic, level, pitch, voiced = np.asarray(frames).T
    inside = np.zeros(len(times), dtype=bool)
    short = np.zeros(len(times), dtype=bool)
    for start, end in intervals:
        part = (times >= start) & (times < end)
        inside |= part
        if end - start <= .35:
            short |= part
    finite = np.isfinite(frames).all(axis=1)
    active = voiced.astype(bool) & finite & inside
    gate = max(-85., float(np.percentile(level[active], 90)) - 35) if active.any() else -85.
    active &= (level > gate) & (periodic > .4)
    # Three frames retain a short vowel; isolated 20–40 ms detections stay excluded.
    transitions = np.diff(np.r_[False, active, False].astype(int))
    for left, right in zip(np.where(transitions == 1)[0], np.where(transitions == -1)[0]):
        if right - left < 3:
            active[left:right] = False
    reliable = int(active.sum())
    reports = {}
    if reliable < 12:
        return {name: {'status': 'skipped', 'reason': 'insufficient_voiced_signal', 'points': []} for name in chosen}
    # Use global centering as fallback for sparse pitch neighborhoods.
    centered_tilt = tilt - np.median(tilt[active])
    # A vocal's measured f0 can cross a neighborhood boundary on every vibrato
    # cycle. Use the written note as context; it does not change its reference
    # spectral baseline just because the singer bends the pitch within a note.
    reference_pitch = pitch.copy()
    if pitches is not None:
        if len(pitches) != len(intervals) or any(
                type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 127
                for value in pitches):
            raise ValueError('音色分析的音高与音符数量或范围不一致。')
        for (start, end), value in zip(intervals, pitches):
            reference_pitch[(times >= start) & (times < end)] = value
    bins = np.floor((np.rint(reference_pitch) + 2) / 4)
    for value in np.unique(bins[active]):
        mask = active & (bins == value)
        if mask.sum() >= 25:
            centered_tilt[mask] = tilt[mask] - np.median(tilt[mask])
    bias, context_report = _vowel_bias(times, tilt, active, intervals, nuclei, bins)
    centered_tilt -= bias
    noise_excess = breath - np.median(breath[active])
    lost_periodicity = np.maximum(0, np.median(periodic[active]) - periodic)
    candidates = {
        'tension': np.clip(centered_tilt / 12, -.35, .35),
        'breathiness': np.clip(noise_excess / 24, -.30, .30),
        'voicing': -np.clip(lost_periodicity * .5, 0, .18),
    }
    gaps = _control_gaps(times, active, inside, level > gate, finite, reference_pitch, intervals)
    profiles = {}
    for name in chosen:
        measured = _smooth_runs(candidates[name], active, short)
        # Eligibility is measured before strength/coupling. A user's softer
        # setting must not make a real gesture disappear at a fixed 2.5% gate.
        floor = .012 if name == 'voicing' else .025
        meaningful = active & (abs(candidates[name]) >= floor) & (abs(measured) >= floor * .7)
        support, gesture_count = _gesture_support(meaningful, active)
        support, held_count, held_frames = _hold_evidence(support, measured, candidates[name], active, floor)
        delta, control_active, gap_count, gap_frames = _bridge_control(measured * support, support, active, gaps)
        report = {'status': 'skipped', 'reason': 'constant_or_unreliable', 'points': [],
                  'reliable_seconds': round(reliable * .02, 3), 'gate_db': round(gate, 3),
                  'evidence_seconds': round(int(meaningful.sum()) * .02, 3), 'evidence_floor': floor,
                  'short_note_seconds': round(int((short & active).sum()) * .02, 3),
                  'gesture_count': gesture_count,
                  'gesture_seconds': round(int((support > 0).sum()) * .02, 3),
                  'bridged_gaps': gap_count, 'bridged_seconds': round(int(gap_frames) * .02, 3),
                  'held_evidence_gaps': held_count, 'held_evidence_seconds': round(int(held_frames) * .02, 3),
                  'pitch_context': 'score' if pitches is not None else 'measured_f0',
                  'overlap_reduction_max': 0.}
        if name == 'tension':
            report['vowel_conditioning'] = context_report
        if not gesture_count:
            report['reason'] = 'insufficient_sustained_variation'
            reports[name] = report
            continue
        # Fade only the control contribution at evidence boundaries, not the note/audio.
        transitions = np.diff(np.r_[False, control_active, False].astype(int))
        for left, right in zip(np.where(transitions == 1)[0], np.where(transitions == -1)[0]):
            taper = np.minimum(1., np.minimum(np.arange(1, right-left+1), np.arange(right-left, 0, -1)) / 3)
            delta[left:right] *= taper
        # Build eligible profiles before coupling. A rejected Breathiness event
        # must not weaken Voicing; the selected controls' order is irrelevant.
        peak = float(np.max(abs(delta))) * strength
        if peak < (.003 if name == 'voicing' else .008):
            report['reason'] = 'below_output_resolution'
            reports[name] = report
            continue
        profiles[name] = delta, report
    for name in chosen:
        if name not in profiles:
            continue
        delta, report = profiles[name]
        neutral = NEUTRAL[name]
        if name == 'voicing' and 'breathiness' in profiles:
            breath_delta = profiles['breathiness'][0]
            # Couple only supported, positive Breathiness where Voicing also
            # changes. Tapered shoulders and interpolated gaps keep their scale.
            overlap = np.clip(breath_delta / .18, 0, 1) * np.clip(-delta / .08, 0, 1)
            report['overlap_reduction_max'] = round(float(overlap.max()) * .35, 4)
            delta = delta * (1 - .35 * overlap)
        values = neutral + delta * strength
        values = np.clip(values, 0 if name == 'voicing' else -1, 1)
        peak = float(np.max(abs(values - neutral)))
        if peak < (.003 if name == 'voicing' else .008):
            report['reason'] = 'below_output_resolution'
            reports[name] = report
            continue
        raw = list(zip(times.tolist(), values.tolist()))
        # Preserve continuous gestures across adjacent notes. Only phrase gaps reset.
        phrases = []
        for start, end in sorted(intervals):
            if phrases and start - phrases[-1][1] <= .1:
                phrases[-1][1] = max(end, phrases[-1][1])
            else:
                phrases.append([start, end])
        for start, end in phrases:
            for boundary in (start, end):
                raw.append((max(0., boundary), neutral))
        raw.append((max(float(times[-1]) + .02, max(b for a, b in intervals)), neutral))
        samples = sorted(dict(raw).items())
        points = samples if preserve_samples else simplify(samples, tolerance=max(.001, min(.008, peak * .1)))
        report.update(status='applied', reason=None, points=[[round(t, 6), round(v, 4)] for t, v in points],
                      value_min=round(float(values.min()), 4), value_max=round(float(values.max()), 4),
                      neutral=neutral, point_count=len(points), samples_preserved=preserve_samples)
        reports[name] = report
    return reports


def analyze(path, notes, strength, chosen, cancel=lambda: None, *, cache=None, nuclei=None, preserve_samples=False):
    if any(name not in PARAMETERS for name in chosen) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('参考音色参数无效。')
    report = {'algorithm_version': ALGORITHM_VERSION, 'strength': strength, 'parameters': {}}
    if not notes or strength == 0 or not chosen:
        report['parameters'] = {name: {'status': 'skipped', 'reason': 'no_notes_or_zero_strength', 'points': []} for name in chosen}
        return report
    frames, metadata = cached_features(path, notes, cache, cancel)
    cancel()
    report.update(metadata)
    report['parameters'] = map_features(frames, [(a, b) for a, b, _ in notes], strength, chosen,
                                        nuclei=nuclei, pitches=[pitch for _, _, pitch in notes], preserve_samples=preserve_samples)
    return report
