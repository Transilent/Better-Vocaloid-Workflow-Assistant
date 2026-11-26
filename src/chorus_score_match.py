"""Ordered multi-part score matching, lane recommendation and preparation score handoff."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np

from chorus_project import SCORE_SUFFIXES


@dataclass(frozen=True)
class ScoreMatch:
    score_note: object | None
    target_pitch: int | None
    reason: str
    review: bool = False
    confidence: float = 0.
    intent: str = 'melody'


_CONSONANT_HARMONY_INTERVALS = (-12, -9, -8, -7, -5, -4, -3, 3, 4, 5, 7, 8, 9, 12)


def detect_harmony_phrases(vocal_notes, score_notes, *, time_to_output=None, phrase_gap=.28):
    """Detect phrases where a singer steadily holds a consonant harmony interval vs lead MIDI.

    Returns a dict mapping vocal note index -> (best_score_note, label) for notes belonging
    to protected harmony phrases (e.g. -12 st octave, +7 st fifth, ±3/±4 st diatonic thirds).
    """
    if not vocal_notes or not score_notes:
        return {}

    mapped = []
    for idx, v in enumerate(vocal_notes):
        if time_to_output is not None:
            a, b = time_to_output(v.start, v.end)
            if b < a:
                a, b = b, a
        else:
            a, b = v.start, v.end
        # Find best-overlapping score note in time (independent of pitch!).
        best_s = None
        best_ov = 0.
        for s in score_notes:
            if s.end <= a - .04:
                continue
            if s.start >= b + .04:
                break
            ov = min(b, s.end) - max(a, s.start)
            if ov > best_ov:
                best_ov = ov
                best_s = s
        mapped.append((idx, a, b, v.center_midi, best_s, best_ov))

    # Group vocal notes into breath-bounded phrases.
    phrases = [[mapped[0]]]
    for item in mapped[1:]:
        if item[1] - phrases[-1][-1][2] >= phrase_gap:
            phrases.append([item])
        else:
            phrases[-1].append(item)

    protected = {}
    for group in phrases:
        paired = [(idx, a, b, c, s) for idx, a, b, c, s, ov in group
                  if s is not None and ov >= .045 and (b - a) >= .07]
        total_dur = sum(b - a for _, a, b, _, _ in paired)
        if len(paired) < 3 or total_dur < .45:
            continue
        diffs = np.array([c - s.pitch for _, _, _, c, s in paired], dtype=np.float64)
        # Never classify a phrase as harmony if >= 35% of its notes are near unison.
        if float(np.mean(np.abs(diffs) <= 1.45)) >= .35:
            continue

        chosen_label = ''
        best_fit = (0., 99., 0)
        for interval in _CONSONANT_HARMONY_INTERVALS:
            err = np.abs(diffs - interval)
            hit_ratio = float(np.mean(err <= 1.25))
            med_err = float(np.median(err))
            if hit_ratio >= .72 and med_err <= .75:
                if (hit_ratio, -med_err) > (best_fit[0], -best_fit[1]):
                    best_fit = (hit_ratio, med_err, interval)
        if best_fit[0] >= .72:
            interval = best_fit[2]
            chosen_label = f'和声声部保护（{interval:+d} 半音）'
        else:
            # Diatonic moving harmony (e.g. alternating +3/+4 st upper thirds or -3/-4 st lower thirds).
            # Reject dissonant tritone bands (5.4..6.6 st) which indicate wrong-key singing rather than harmony.
            med_diff = float(np.median(diffs))
            if not (5.4 <= abs(med_diff) <= 6.6):
                pos_mask = (diffs >= 2.2) & (diffs <= 9.6)
                neg_mask = (diffs <= -2.2) & (diffs >= -9.6)
                for mask, side_name in ((pos_mask, '上和声'), (neg_mask, '下和声')):
                    if float(np.mean(mask)) >= .78 and float(np.std(diffs[mask])) <= 1.35:
                        chosen_label = f'和声声部保护（{side_name}）'
                        break
        if chosen_label:
            for idx, _a, _b, _c, s, _ov in group:
                protected[idx] = (s, chosen_label)
    return protected


_PART_HINTS = (
    ('lead', ('lead', 'main', 'melody', 'vocal', '主唱', '主旋律', '旋律')),
    ('high', ('high', 'upper', 'soprano', 'sop', 'top', '上和声', '高声部', '高和声', '高音')),
    ('low', ('low', 'lower', 'alto', 'bass', 'bottom', '下和声', '低声部', '低和声', '低音')),
    ('harmony', ('harmony', 'harm', 'backing', 'chorus', '和声', '副歌', '合唱')),
)


def _normalize_token(text):
    return re.sub(r'[\s\-_+.,!?。，！？、・「」『』]+', '', str(text or '').casefold())


def _polyphonic_indices(score_notes):
    """Identify score notes that significantly overlap another note of different pitch."""
    poly = set()
    for i, a in enumerate(score_notes):
        dur_a = max(1e-6, a.end - a.start)
        for j in range(i + 1, len(score_notes)):
            b = score_notes[j]
            if b.start >= a.end:
                break
            if b.pitch == a.pitch:
                continue
            ov = min(a.end, b.end) - max(a.start, b.start)
            dur_b = max(1e-6, b.end - b.start)
            if ov >= .30 * min(dur_a, dur_b):
                poly.add(i)
                poly.add(j)
    return poly


def _pair_score(vo_start, vo_end, v_center, s_note, phrase_text=''):
    """Score pairing one vocal note (in output seconds) with one score note."""
    v_dur = max(.02, vo_end - vo_start)
    s_dur = max(.02, s_note.end - s_note.start)
    v_mid = .5 * (vo_start + vo_end)
    s_mid = .5 * (s_note.start + s_note.end)
    overlap = max(0., min(vo_end, s_note.end) - max(vo_start, s_note.start))
    gap = max(0., max(vo_start - s_note.end, s_note.start - vo_end))
    center_diff = abs(v_mid - s_mid)
    onset_diff = abs(vo_start - s_note.start)
    if gap > .45 or center_diff > .95:
        return None

    ov_ratio = overlap / min(v_dur, s_dur)
    time_score = 1.65 * ov_ratio - 1.20 * min(center_diff, 1.0) - .45 * min(onset_diff, .8)

    pitch_diff = abs(v_center - s_note.pitch)
    octave_err = abs(pitch_diff - 12.) <= 1.8
    if pitch_diff <= .65:
        pitch_score = 2.85 - .60 * pitch_diff
    elif pitch_diff <= 1.4:
        pitch_score = 2.20 - .60 * pitch_diff
    elif pitch_diff <= 2.4:
        pitch_score = 1.36 - 1.40 * (pitch_diff - 1.4)
    elif pitch_diff <= 5.5:
        pitch_score = -.04 - .45 * (pitch_diff - 2.4)
    elif octave_err:
        pitch_score = -1.6
    else:
        pitch_score = -2.4 - .25 * (pitch_diff - 5.5)

    lyric_bonus = 0.
    s_tok = _normalize_token(s_note.lyric)
    if s_tok and phrase_text and s_tok in phrase_text:
        lyric_bonus = .55

    return time_score + pitch_score + lyric_bonus, overlap, pitch_diff, octave_err


def match_vocal_to_score(vocal_notes, score_notes, *, time_to_output=None, phrase_texts=(), draft=False):
    """Order-preserving DP matching from vocal notes to a bound score lane.

    Handles fast passages, skipped/missed notes, repeated notes, melismas/splits,
    polyphonic score collisions and octave ambiguity with explicit review reasons.
    """
    m, n = len(vocal_notes), len(score_notes)
    if not m:
        return ()
    if not n:
        return tuple(ScoreMatch(None, None, '本声部乐谱在这里没有匹配音符', True, 0., intent='review')
                     for _ in vocal_notes)

    harmony_map = detect_harmony_phrases(vocal_notes, score_notes, time_to_output=time_to_output)
    poly = _polyphonic_indices(score_notes)
    mapped = []
    for v in vocal_notes:
        if time_to_output is not None:
            a, b = time_to_output(v.start, v.end)
            if b < a:
                a, b = b, a
            if b - a < 1e-4:
                b = a + max(.02, v.end - v.start)
        else:
            a, b = v.start, v.end
        mid = .5 * (a + b)
        p_text = ''
        for p_start, p_end, norm_text in phrase_texts:
            if p_start <= mid <= p_end:
                p_text = norm_text
                break
        mapped.append((a, b, v.center_midi, p_text))

    # Candidate score indices for each vocal note (bounded time window keeps DP O(m * k)).
    candidates = []
    for a, b, center, p_text in mapped:
        row = []
        for j, s in enumerate(score_notes):
            if s.end < a - .48:
                continue
            if s.start > b + .48:
                break
            scored = _pair_score(a, b, center, s, p_text)
            if scored is not None:
                row.append((j, *scored))
        candidates.append(row)

    # DP over vocal notes i = 0..m-1, tracking last matched score index j (or -1 for unmatched).
    # State dict maps last_j -> (total_score, backpointer_list_index).
    skip_v_penalty = -0.35
    skip_s_penalty = 0.08
    melisma_penalty = 0.30

    # dp[i]: dict[j_state -> (score, prev_state, assigned_j_or_None)]
    dp = [{-1: (0., None, None)}]
    for i in range(m):
        prev = dp[-1]
        cur = {}

        def relax(state, score, prev_state, assigned):
            existing = cur.get(state)
            if existing is None or score > existing[0]:
                cur[state] = (score, prev_state, assigned)

        # Option 1: skip vocal note i (ornament / extra vocalization / no score note)
        for pj, (pscore, _, _) in prev.items():
            relax(pj, pscore + skip_v_penalty, pj, None)

        # Option 2: match vocal note i to candidate score note j
        for j, base, overlap, pitch_diff, _oct in candidates[i]:
            s_j = score_notes[j]
            v_center = mapped[i][2]
            for pj, (pscore, _, _) in prev.items():
                if pj > j:
                    continue
                if pj == j:
                    # Melisma / split note sharing one score note: require actual time overlap.
                    if overlap <= .015:
                        continue
                    trans = -melisma_penalty
                else:
                    skipped = (j - pj - 1) if pj >= 0 else 0
                    trans = -skip_s_penalty * min(skipped, 4)
                    # Contour bonus when consecutive matched notes move in the same direction.
                    if pj >= 0 and i > 0:
                        dv = v_center - mapped[i - 1][2]
                        ds = s_j.pitch - score_notes[pj].pitch
                        if abs(dv) >= .6 and abs(ds) >= 1 and (dv * ds > 0):
                            trans += .35 if abs(dv - ds) <= 2.2 else .15
                relax(j, pscore + base + trans, pj, j)

        # Keep top states to bound worst-case complexity on huge songs.
        if len(cur) > 32:
            top = sorted(cur.items(), key=lambda kv: kv[1][0], reverse=True)[:32]
            cur = dict(top)
        dp.append(cur)

    # Backtrack best sequence
    best_state = max(dp[-1].items(), key=lambda kv: kv[1][0])[0]
    assigned = [None] * m
    state = best_state
    for i in range(m, 0, -1):
        _, prev_state, j_pick = dp[i][state]
        assigned[i - 1] = j_pick
        state = prev_state

    results = []
    for i, j in enumerate(assigned):
        if i in harmony_map:
            h_note, h_label = harmony_map[i]
            results.append(ScoreMatch(h_note, None, h_label, False, .85, intent='harmony'))
            continue
        if j is None:
            results.append(ScoreMatch(None, None, '本声部乐谱在这里没有匹配音符', True, 0., intent='review'))
            continue
        s_note = score_notes[j]
        a, b, center, _ = mapped[i]
        v_dur = max(.02, b - a)
        overlap = max(0., min(b, s_note.end) - max(a, s_note.start))
        pitch_diff = abs(center - s_note.pitch)
        octave_err = abs(pitch_diff - 12.) <= 1.8
        is_poly = j in poly

        if octave_err:
            results.append(ScoreMatch(s_note, s_note.pitch, '疑似错八度，请核对', True, .25, intent='review'))
        elif is_poly:
            results.append(ScoreMatch(s_note, s_note.pitch, '同轨重叠和弦，请确认声部', True, .35, intent='review'))
        elif draft:
            results.append(ScoreMatch(s_note, s_note.pitch, '导唱提取草稿，请先核对', True, .60, intent='review'))
        elif overlap < .20 * v_dur or pitch_diff > 4.5 or (pitch_diff > 2.4 and overlap < .35 * min(v_dur, max(.02, s_note.end - s_note.start))):
            results.append(ScoreMatch(s_note, s_note.pitch, '谱轨匹配待确认', True, .45, intent='review'))
        else:
            conf = max(.5, min(1., .65 + .35 * min(1., overlap / v_dur) - .08 * pitch_diff))
            intent = 'review' if pitch_diff > 2.5 else 'melody'
            results.append(ScoreMatch(s_note, s_note.pitch, '有序谱轨匹配', False, round(conf, 3), intent=intent))
    return tuple(results)


def shift_score_notes(notes, *, offset=0., octave_semitones=0):
    """Project score notes by global time offset and per-track octave transposition."""
    off = float(offset or 0.)
    oct_shift = int(octave_semitones or 0)
    if not off and not oct_shift:
        return tuple(notes)
    return tuple(
        type(note)(note.start + off, note.end + off, max(0, min(127, int(note.pitch) + oct_shift)), note.lyric)
        for note in notes
    )


def expand_vocal_notes_for_score(vocal_notes, score_notes, *, time_to_output=None, curve=None,
                                 min_sub_dur=.095, min_overlap=.095):
    """Sub-split a long slurred vocal note ONLY when the vocal pitch itself moves across MIDI pitches.

    Steady held vowels are never chopped by passing MIDI notes; real melismas are cut at
    the vocal F0's actual steepest inflection point (within ±120 ms of the MIDI boundary)
    so transitions stay natural without double-step hiccups.
    """
    if not vocal_notes or not score_notes:
        return tuple(vocal_notes)
    from dataclasses import replace as _replace
    from chorus_notes import _note_stats

    expanded = []
    for v in vocal_notes:
        if v.duration < .20:
            expanded.append(v)
            continue
        # Gate 1: Do not chop a steady held vowel just because MIDI has a passing note,
        # UNLESS there is an actual internal unvoiced dropout (consonant break) inside a long note.
        has_internal_dropout = False
        dropout_times = ()
        if curve is not None:
            midi, step = curve
            trim = min(.030, v.duration * .12)
            ia = max(0, min(len(midi), round((v.start + trim) / step)))
            ib = max(ia + 1, min(len(midi), round((v.end - trim) / step)))
            seg_all = midi[ia:ib]
            nan_mask = ~np.isfinite(seg_all)
            if v.duration >= .30 and np.any(nan_mask):
                n_edges = np.flatnonzero(np.diff(np.r_[False, nan_mask, False]))
                drops = []
                for da, db in zip(n_edges[::2], n_edges[1::2]):
                    dt = (ia + .5 * (da + db)) * step
                    if dt - v.start >= min_sub_dur and v.end - dt >= min_sub_dur:
                        drops.append(dt)
                if drops:
                    has_internal_dropout = True
                    dropout_times = tuple(drops)
            seg_f = seg_all[np.isfinite(seg_all)]
            body_spread = float(np.percentile(seg_f, 90) - np.percentile(seg_f, 10)) if len(seg_f) >= 6 else (v.high_midi - v.low_midi)
            if body_spread < .85 and not has_internal_dropout:
                expanded.append(v)
                continue
        if time_to_output is not None:
            oa, ob = time_to_output(v.start, v.end)
            if ob < oa:
                oa, ob = ob, oa
        else:
            oa, ob = v.start, v.end
        out_dur = max(1e-6, ob - oa)
        # Collect overlapping score notes grouped into contiguous runs of equal pitch,
        # ignoring isolated bass/breath artifact notes >5 st below the vocal note.
        runs = []
        for s in score_notes:
            if s.end <= oa + .01:
                continue
            if s.start >= ob - .01:
                break
            if abs(s.pitch - v.center_midi) > 5.0 and (s.end - s.start) <= .25:
                continue
            ov_a = max(oa, s.start)
            ov_b = min(ob, s.end)
            if ov_b - ov_a < .025:
                continue
            if runs and runs[-1][2] == s.pitch and ov_a - runs[-1][1] <= .06:
                runs[-1] = (runs[-1][0], max(runs[-1][1], ov_b), s.pitch)
            else:
                runs.append((ov_a, ov_b, s.pitch))
        # Filter to meaningful distinct-pitch runs covering inside this vocal note.
        # When an internal unvoiced consonant dropout separates syllables inside a long note,
        # allow shorter edge runs (>= 65 ms) so a brief initial/final note is not swallowed.
        eff_min_ov = .065 if has_internal_dropout else min_overlap
        significant = [r for r in runs if (r[1] - r[0]) >= eff_min_ov]
        if len(significant) < 2 or len({r[2] for r in significant}) < 2:
            expanded.append(v)
            continue
        # Derive internal cut positions snapped to an internal consonant dropout or steepest vocal inflection.
        cuts = [v.start]
        cut_dirs = []
        cut_is_drop = []
        for prev_r, next_r in zip(significant, significant[1:]):
            if prev_r[2] == next_r[2]:
                continue
            out_cut = .5 * (prev_r[1] + next_r[0]) if next_r[0] >= prev_r[1] else next_r[0]
            frac = max(0., min(1., (out_cut - oa) / out_dur))
            take_cut = v.start + frac * v.duration
            direction = 1. if next_r[2] > prev_r[2] else -1.
            snapped_drop = False
            if dropout_times:
                near_drop = min(dropout_times, key=lambda dt: abs(dt - take_cut))
                if abs(near_drop - take_cut) <= .16:
                    take_cut = near_drop
                    snapped_drop = True
            if not snapped_drop and curve is not None:
                midi, step = curve
                lo_t = max(cuts[-1] + min_sub_dur, take_cut - .12)
                hi_t = min(v.end - min_sub_dur, take_cut + .12)
                if hi_t > lo_t + 2. * step:
                    ga = max(0, round((lo_t - .03) / step))
                    gb = min(len(midi), round((hi_t + .03) / step))
                    raw = midi[ga:gb].astype(np.float64, copy=True)
                    fin = np.isfinite(raw)
                    if np.count_nonzero(fin) >= 5:
                        idx_a = np.arange(len(raw))
                        if not np.all(fin):
                            raw = np.interp(idx_a, idx_a[fin], raw[fin])
                        # 50 ms smoothing before finding steepest directional pitch change.
                        k = max(3, round(.050 / step) | 1)
                        sm = np.convolve(np.pad(raw, k // 2, mode='edge'), np.ones(k) / k, mode='valid')
                        slopes = np.diff(sm) * direction
                        s_lo = max(0, round(lo_t / step) - ga)
                        s_hi = min(len(slopes), round(hi_t / step) - ga)
                        if s_hi > s_lo and float(np.max(slopes[s_lo:s_hi])) > .035:
                            best_i = s_lo + int(np.argmax(slopes[s_lo:s_hi]))
                            take_cut = (ga + best_i + .5) * step
            if take_cut - cuts[-1] >= min_sub_dur and v.end - take_cut >= min_sub_dur:
                cuts.append(take_cut)
                cut_dirs.append(direction)
                cut_is_drop.append(snapped_drop)
        cuts.append(v.end)
        if len(cuts) <= 2:
            expanded.append(v)
            continue
        # Gate 2: Keep cuts where adjacent vocal sub-segments actually move in the MIDI direction
        # (or are separated by a genuine internal unvoiced consonant break).
        if curve is not None:
            valid_cuts = [cuts[0]]
            for c_idx, (direction, is_drop) in enumerate(zip(cut_dirs, cut_is_drop)):
                left_t = valid_cuts[-1]
                mid_t = cuts[c_idx + 1]
                right_t = cuts[c_idx + 2]
                st_l = _note_stats(curve[0], curve[1], left_t, mid_t)
                st_r = _note_stats(curve[0], curve[1], mid_t, right_t)
                if st_l is not None and st_r is not None:
                    dv = (st_r[0] - st_l[0]) * direction
                    if dv >= .55 or (is_drop and dv >= .25):
                        valid_cuts.append(mid_t)
            valid_cuts.append(cuts[-1])
            cuts = valid_cuts
        if len(cuts) <= 2:
            expanded.append(v)
            continue
        for idx, (ca, cb) in enumerate(zip(cuts, cuts[1:])):
            stats = _note_stats(curve[0], curve[1], ca, cb) if curve is not None else None
            center, low, high = stats if stats is not None else (v.center_midi, v.low_midi, v.high_midi)
            expanded.append(_replace(
                v, start=ca, end=cb, center_midi=center, low_midi=low, high_midi=high,
                linked_prev=(v.linked_prev if idx == 0 else True),
                linked_next=(v.linked_next if idx == len(cuts) - 2 else True),
            ))
    return tuple(expanded)


def estimate_phrase_score_shift(midi_curve, step, score_notes, phrase_start, phrase_end, *,
                                track_offset=0., max_shift=.36):
    """Estimate un-aliased phrase time shift (seconds) via pitch-contour cross-correlation vs MIDI.

    Positive return value means the vocal entered early and should move LATER to match MIDI.
    Avoids 1-note phase slips in fast 8th-note passages where nearest-in-time matching fails.
    """
    if not score_notes or phrase_end - phrase_start < .25:
        return 0., 99., 99., False
    midi = np.asarray(midi_curve, dtype=np.float32)
    ia = max(0, int(round((phrase_start - track_offset) / step)))
    ib = min(len(midi), int(round((phrase_end - track_offset) / step)))
    if ib - ia < 15:
        return 0., 99., 99., False
    v_sl = midi[ia:ib]
    v_fin = np.isfinite(v_sl)
    if int(np.sum(v_fin)) < 15:
        return 0., 99., 99., False
    # Need real melodic movement (not a single flat pitch) to correlate pitch contour reliably.
    v_valid = v_sl[v_fin]
    if float(np.percentile(v_valid, 90) - np.percentile(v_valid, 10)) < 1.2:
        return 0., 99., 99., False

    pad_s = max_shift + .20
    grid_origin = phrase_start - pad_s
    grid_len = max(1, int(round((phrase_end - phrase_start + 2. * pad_s) / step)) + 4)
    m_grid = np.full(grid_len, np.nan, dtype=np.float32)
    for n in score_notes:
        if n.end < grid_origin:
            continue
        if n.start > phrase_end + pad_s:
            break
        ga = max(0, int(round((n.start - grid_origin) / step)))
        gb = min(grid_len, int(round((n.end - grid_origin) / step)))
        if gb > ga:
            m_grid[ga:gb] = float(n.pitch)

    base_idx = int(round((track_offset + ia * step - grid_origin) / step))
    max_lag = int(round(max_shift / step))
    n_len = len(v_sl)

    def eval_lag(lag):
        ja = base_idx + lag
        jb = ja + n_len
        if ja < 0 or jb > grid_len:
            return -1e9, 99., 0
        m_sl = m_grid[ja:jb]
        both = v_fin & np.isfinite(m_sl)
        cnt = int(np.sum(both))
        if cnt < max(12, int(.30 * int(np.sum(v_fin)))):
            return -1e9, 99., cnt
        diff = np.abs(v_sl[both] - m_sl[both])
        sc = float(np.sum(np.maximum(0., 2.2 - diff)) - .35 * np.sum(diff > 2.8)) - .015 * abs(lag)
        return sc, float(np.median(diff)), cnt

    sc0, err0, _ = eval_lag(0)
    results = []
    for lag in range(-max_lag, max_lag + 1):
        sc, err, cnt = eval_lag(lag)
        results.append((lag, sc, err, cnt))
    peak_sc = max(r[1] for r in results)
    if peak_sc <= -1e8:
        return 0., err0, err0, False
    tied = [r for r in results if r[1] >= peak_sc - 1e-3]
    best_lag, best_sc, best_err, best_cnt = tied[len(tied) // 2]
    # Require a strong, unambiguous contour improvement AND plausible melody match (<= 2.20 st)
    # so harmony phrases (e.g. +5/+7 st) and noisy short phrases never get falsely shifted,
    # while genuinely flat + early lead phrases (e.g. 1.4 st flat, +200 ms early) still lock cleanly.
    min_gain = max(8.0, .12 * best_cnt)
    strong_gain = max(18.0, .25 * best_cnt)
    improved = (
        best_err <= 2.20
        and best_sc > sc0 + min_gain
        and (best_err <= err0 - .14 or best_sc > sc0 + strong_gain)
    )
    if abs(best_lag * step) > .05 and not improved:
        return 0., err0, err0, False
    return round(best_lag * step, 3), err0, best_err, improved


def detect_track_key_mismatch(midi_curve, step, raw_score_notes, *,
                              score_offset=0., track_offset=0., current_octave=0):
    """Detect when a whole vocal track is transposed off-key relative to MIDI (e.g. -6 st tritone).

    Returns a diagnostic dict when a non-octave transposition `3 <= |shift| <= 9` drops median
    pitch error dramatically (from >2.6 st down to <=1.25 st), else `None`.
    """
    if not raw_score_notes:
        return None
    midi = np.asarray(midi_curve, dtype=np.float32)
    valid_idx = np.flatnonzero(np.isfinite(midi))
    if len(valid_idx) < 30:
        return None
    first_t = track_offset + float(valid_idx[0]) * step
    last_t = track_offset + float(valid_idx[-1]) * step
    raw_med = float(np.median(midi[valid_idx]))

    # Compare raw vocal curve against score notes at current_octave and nearby octaves.
    base_notes = shift_score_notes(raw_score_notes, offset=score_offset, octave_semitones=0)
    m_grid = np.full(len(midi), np.nan, dtype=np.float32)
    for n in base_notes:
        a = max(0, int(round((n.start - track_offset) / step)))
        b = min(len(midi), int(round((n.end - track_offset) / step)))
        if b > a:
            m_grid[a:b] = float(n.pitch)
    both = np.isfinite(midi) & np.isfinite(m_grid)
    if int(np.sum(both)) < 30:
        return None
    v_b = midi[both]
    m_b = m_grid[both]
    cur_err = float(np.median(np.abs(v_b - (m_b + current_octave))))
    if cur_err <= 2.6:
        return None

    # Find best integer semitone shift `k` making `v_b + k` match `m_b + target_octave`.
    best = None
    for target_oct in (-24, -12, 0, 12, 24):
        m_oct = m_b + target_oct
        # Prefer smaller absolute shift; near a +/-6 st tritone tie, favor shifting UP
        # for higher/female voices (raw_med >= 59) to avoid dark baritone timbre.
        for k in range(-9, 10):
            if abs(k) < 3:
                continue
            err = float(np.median(np.abs((v_b + k) - m_oct)))
            within = float(np.mean(np.abs((v_b + k) - m_oct) <= 1.25))
            if err <= 1.25 and within >= .45:
                pref_penalty = .08 * abs(k) + (.15 if (k < 0 and raw_med >= 59. and abs(k) >= 5) else 0.)
                score_val = err + pref_penalty
                if best is None or score_val < best[0]:
                    best = (score_val, err, k, target_oct, within)
    if best is None:
        return None
    _, best_err, shift_st, target_oct, _ = best
    if best_err > cur_err - 2.2:
        return None
    direction = '偏低' if shift_st > 0 else '偏高'
    oct_label = '原八度' if target_oct == 0 else f'{target_oct:+d} 半音八度'
    return {
        'mismatch': True,
        'current_err': round(cur_err, 2),
        'best_err': round(best_err, 2),
        'shift_semitones': int(shift_st),
        'target_octave': int(target_oct),
        'first_time': round(first_t, 3),
        'last_time': round(last_t, 3),
        'reason': (f'整轨疑似唱偏调：相对 MIDI（{oct_label}）整体{direction}约 {abs(shift_st)} 半音'
                   f'（移调 {shift_st:+d} 半音后中位误差可由 {cur_err:.1f} 降至 {best_err:.2f} 半音）'),
        'action_hint': f'点「接受建议」可一键切至 {oct_label} 并移调修音对齐 MIDI；若属特殊和声请点「保留原样」',
    }


def midi_timing_anchors(vocal_notes, score_notes, *, track_offset=0., radius=.60, coarse_shift=0.):
    """Extract vowel-onset timing anchors pairing vocal notes to their matched MIDI note onsets.

    `coarse_shift` pre-aligns vocal notes to MIDI during DP matching so fast passages
    shifted by >120 ms never phase-alias onto the adjacent MIDI note.
    """
    if not vocal_notes or not score_notes:
        return ()
    to_proj = lambda a, b: (track_offset + a + coarse_shift, track_offset + b + coarse_shift)
    matches = match_vocal_to_score(vocal_notes, score_notes, time_to_output=to_proj, draft=False)
    anchors = []
    last_score = None
    for v, m in zip(vocal_notes, matches):
        if m.score_note is None:
            continue
        if v.duration < .085:
            continue
        if m.score_note is last_score:
            continue
        if m.review and m.intent != 'harmony':
            continue
        v_onset = track_offset + v.start
        s_onset = float(m.score_note.start)
        if abs(s_onset - v_onset) > radius:
            continue
        s_dur = max(.05, m.score_note.end - m.score_note.start)
        if v_onset + coarse_shift > m.score_note.start + .65 * s_dur and (v_onset + coarse_shift) - s_onset > .18:
            continue
        label = (m.score_note.lyric or '♪')[:4]
        anchors.append((label, s_onset, v_onset))
        last_score = m.score_note
    return tuple(anchors)


def _best_octave_for_register(vocal_median, lane_median):
    """Choose the nearest octave shift in (-24, -12, 0, +12, +24) aligning lane to vocal."""
    if vocal_median is None or lane_median is None:
        return 0
    return min((-24, -12, 0, 12, 24), key=lambda oct_s: abs((lane_median + oct_s) - vocal_median))


def _score_offset_grid(lane_notes, step, max_duration):
    length = max(1, int(round(max_duration / step)) + 2)
    grid = np.full(length, np.nan, dtype=np.float32)
    for note in lane_notes:
        a = max(0, int(round(note.start / step)))
        b = min(length, int(round(note.end / step)))
        if b > a:
            grid[a:b] = float(note.pitch)
    return grid


def estimate_score_alignment(project, curves, *, track_ids=None, max_search_seconds=25.):
    """Estimate global score time offset and per-track (lane_id, octave_shift) from vocal F0 curves.

    Uses pitch-contour cross-correlation across vocal tracks (coarse 50 ms + fine 10 ms)
    so single-lane or multi-lane MIDIs with arbitrary intro padding or male/female octave
    differences snap accurately onto the recording.
    """
    score = project.score
    if not score or not score.lanes:
        return {'offset': 0., 'tracks': {}, 'confidence': 0., 'coverage': 0., 'median_err': 99.}
    wanted = set(track_ids) if track_ids is not None else None
    vocals = [t for t in project.tracks
              if t.role == 'vocal' and t.id in curves and (wanted is None or t.id in wanted)]
    if not vocals:
        return {'offset': score.offset, 'tracks': {}, 'confidence': 0., 'coverage': 0., 'median_err': 99.}

    step = float(curves[vocals[0].id][1])
    # Prepare per-track lane + octave candidates (prefer existing bound lane, else top recommended).
    recs = recommend_score_lanes(project, curves)
    lane_map = {lane.id: lane for lane in score.lanes}
    track_specs = []
    max_dur = max(
        max((t.offset + t.duration for t in vocals), default=10.),
        max((n.end for lane in score.lanes for n in lane.notes), default=10.),
    ) + 2.
    lane_grids = {}
    for track in vocals:
        midi, t_step = curves[track.id]
        # Resample onto common step if needed
        if abs(t_step - step) > 1e-6:
            grid_t = np.arange(0., track.duration, step)
            src_t = np.arange(len(midi)) * t_step
            finite = np.isfinite(midi)
            if np.count_nonzero(finite) < 5:
                continue
            v_arr = np.full(len(grid_t), np.nan, dtype=np.float32)
            idx = np.rint(grid_t / t_step).astype(np.int64)
            ok = (idx >= 0) & (idx < len(midi))
            v_arr[ok] = midi[idx[ok]]
        else:
            v_arr = np.asarray(midi, dtype=np.float32)
        # Shift vocal curve into project coordinates by track.offset
        pad_frames = int(round(track.offset / step))
        if pad_frames > 0:
            v_proj = np.concatenate((np.full(pad_frames, np.nan, dtype=np.float32), v_arr))
        elif pad_frames < 0:
            v_proj = v_arr[-pad_frames:]
        else:
            v_proj = v_arr
        valid = v_proj[np.isfinite(v_proj)]
        if len(valid) < 20:
            continue
        v_med = float(np.median(valid))
        lane_id = track.score_lane if track.score_lane in lane_map else ''
        if not lane_id:
            if len(score.lanes) == 1:
                lane_id = score.lanes[0].id
            elif recs.get(track.id):
                lane_id = recs[track.id][0]['laneId']
        if not lane_id or lane_id not in lane_map:
            continue
        lane = lane_map[lane_id]
        if lane_id not in lane_grids:
            lane_grids[lane_id] = _score_offset_grid(lane.notes, step, max_dur)
        l_med = float(np.median([n.pitch for n in lane.notes]))
        oct_primary = _best_octave_for_register(v_med, l_med)
        oct_candidates = tuple(dict.fromkeys((oct_primary, 0, -12, 12)))
        # Compute approximate start difference hint so offsets beyond +/-25s are still found.
        first_v = float(np.flatnonzero(np.isfinite(v_proj))[0]) * step
        first_s = lane.notes[0].start
        track_specs.append((track, v_proj, lane_id, oct_candidates, first_v - first_s))

    if not track_specs:
        return {'offset': score.offset, 'tracks': {}, 'confidence': 0., 'coverage': 0., 'median_err': 99.}

    def eval_offset(shift_frames):
        total_score = 0.
        per_track_oct = {}
        matched_frames = 0
        total_voiced = 0
        abs_errs = []
        for track, v_proj, lane_id, oct_cands, _ in track_specs:
            m_grid = lane_grids[lane_id]
            if shift_frames >= 0:
                v_sl = v_proj[shift_frames:]
                m_sl = m_grid[:len(v_sl)]
            else:
                m_sl = m_grid[-shift_frames:]
                v_sl = v_proj[:len(m_sl)]
            n_cmp = min(len(v_sl), len(m_sl))
            if n_cmp < 20:
                per_track_oct[track.id] = (lane_id, oct_cands[0])
                continue
            va = v_sl[:n_cmp]
            ma = m_sl[:n_cmp]
            both = np.isfinite(va) & np.isfinite(ma)
            cnt = int(np.sum(both))
            total_voiced += int(np.sum(np.isfinite(v_proj)))
            if cnt < 15:
                per_track_oct[track.id] = (lane_id, oct_cands[0])
                continue
            va_b = va[both]
            ma_b = ma[both]
            best_t = (-1e9, oct_cands[0], None)
            for oct_s in oct_cands:
                diff = np.abs(va_b - (ma_b + oct_s))
                sc = float(np.sum(np.maximum(0., 2.2 - diff)) - .20 * np.sum(diff > 3.5))
                if sc > best_t[0]:
                    best_t = (sc, oct_s, diff)
            total_score += best_t[0]
            per_track_oct[track.id] = (lane_id, best_t[1])
            matched_frames += cnt
            abs_errs.append(best_t[2])
        return total_score, per_track_oct, matched_frames, max(1, total_voiced), abs_errs

    # Build coarse search grid (50 ms step) around 0, current offset, and start-diff hints.
    coarse_step = max(1, int(round(.05 / step)))
    radius = int(round(max_search_seconds / step))
    centers = {0, int(round(score.offset / step))}
    for _, _, _, _, hint in track_specs:
        if abs(hint) > max_search_seconds * .8 and abs(hint) < 180.:
            centers.add(int(round(hint / step)))
    candidate_frames = set()
    for c in centers:
        span = radius if c == 0 else min(radius, int(round(8. / step)))
        for f in range(c - span, c + span + 1, coarse_step):
            candidate_frames.add(f)

    best_score = -1e12
    best_frame = int(round(score.offset / step))
    for sf in sorted(candidate_frames):
        sc, _, _, _, _ = eval_offset(sf)
        if sc > best_score:
            best_score = sc
            best_frame = sf

    # Fine search (+/- 200 ms at 10 ms resolution) around best coarse peak.
    # Center ties across a flat plateau so offset does not bias toward the left edge.
    fine_radius = max(1, int(round(.20 / step)))
    fine_results = []
    for sf in range(best_frame - fine_radius, best_frame + fine_radius + 1):
        fine_results.append((sf, eval_offset(sf)))
    peak_sc = max(res[0] for _, res in fine_results)
    tied = [item for item in fine_results if item[1][0] >= peak_sc - 1e-3]
    best_frame, best_detail = tied[len(tied) // 2]

    sc, per_track_oct, matched, total_voiced, err_chunks = best_detail
    all_err = np.concatenate(err_chunks) if err_chunks else np.array([99.], dtype=np.float32)
    med_err = float(np.median(all_err))
    within_1st = float(np.mean(all_err <= 1.2))
    cov = float(matched) / float(total_voiced)
    conf = max(0., min(1., .5 * min(1., cov / .55) + .5 * within_1st))
    return {
        'offset': round(best_frame * step, 3),
        'tracks': {tid: {'laneId': lid, 'octave': int(oct_s)} for tid, (lid, oct_s) in per_track_oct.items()},
        'confidence': round(conf, 3),
        'coverage': round(cov, 3),
        'median_err': round(med_err, 3),
    }


def _part_bucket(text):
    low = str(text or '').casefold()
    for bucket, keywords in _PART_HINTS:
        if any(kw in low for kw in keywords):
            return bucket
    return ''


def recommend_score_lanes(project, curves):
    """Rank score lanes for each vocal track by register, active overlap and part name."""
    if not project.score or not project.score.lanes:
        return {}
    offset = project.score.offset
    recommendations = {}
    for track in project.tracks:
        if track.role != 'vocal':
            continue
        curve = curves.get(track.id)
        vocal_median = None
        vocal_times = None
        if curve is not None:
            midi, step = curve
            valid = np.isfinite(midi)
            if np.any(valid):
                vocal_median = float(np.median(midi[valid]))
                vocal_times = (np.flatnonzero(valid) * step) + track.offset
        track_bucket = _part_bucket(f'{track.part} {track.name}')
        ranked = []
        for lane in project.score.lanes:
            if not lane.notes:
                continue
            pitches = np.array([n.pitch for n in lane.notes], dtype=np.float64)
            lane_median = float(np.median(pitches))
            score = 0.
            reasons = []
            best_oct = 0
            if vocal_median is not None:
                best_oct = _best_octave_for_register(vocal_median, lane_median)
                dist = abs(vocal_median - (lane_median + best_oct))
                score += max(-4., 4. - .45 * dist)
                if dist <= 2.5:
                    reasons.append('音区接近' if best_oct == 0 else f'音区接近（{best_oct:+d}半音）')
                elif dist >= 7.:
                    reasons.append(f'音区差 {dist:.1f} 半音')
            if vocal_times is not None and len(vocal_times):
                starts = np.array([n.start + offset for n in lane.notes])
                ends = np.array([n.end + offset for n in lane.notes])
                # Subsample vocal times for fast coverage check.
                sample = vocal_times[::max(1, len(vocal_times) // 250)]
                covered = np.any((sample[:, None] >= starts[None, :] - .08) &
                                 (sample[:, None] <= ends[None, :] + .08), axis=1)
                cov = float(np.mean(covered))
                score += 2.5 * cov
                if cov >= .55:
                    reasons.append(f'时间覆盖 {round(cov * 100)}%')
            lane_bucket = _part_bucket(lane.name)
            if track_bucket and lane_bucket and track_bucket == lane_bucket:
                score += 1.6
                reasons.append('声部名称匹配')
            ranked.append({
                'laneId': lane.id,
                'name': lane.name,
                'notes': len(lane.notes),
                'medianMidi': round(lane_median, 1),
                'recommendedOctave': int(best_oct),
                'score': round(score, 3),
                'reason': ' · '.join(reasons) if reasons else f'{len(lane.notes)} 个音符',
            })
        ranked.sort(key=lambda row: row['score'], reverse=True)
        recommendations[track.id] = ranked
    return recommendations


def preparation_score_candidates(check=None):
    """List verified MIDI and vocal editor projects from VWA preparation tasks."""
    import task_contract
    import task_store

    candidates = []
    for record in task_store.records(check=check):
        if check is not None and check():
            raise InterruptedError('已取消。')
        job = Path(record['path']).resolve()
        title = str(record.get('title') or job.name)
        try:
            rows = task_contract.inspect(job, verify=True, check=check)
        except Exception:
            continue
        for row in rows:
            if row.get('state') != 'verified':
                continue
            role = str(row.get('role') or '')
            if role not in ('midi', 'editor_project', 'tuned_project'):
                continue
            path_text = str(row.get('path') or '')
            if not path_text:
                continue
            path = Path(path_text)
            if path.suffix.casefold() not in SCORE_SUFFIXES or not path.is_file():
                continue
            producer = str(row.get('producer') or '')
            is_draft = (role == 'midi') or (producer in ('midi', 'exports') and role != 'tuned_project')
            candidates.append({
                'jobId': str(job),
                'jobTitle': title,
                'artifactId': str(row.get('id') or ''),
                'path': str(path.resolve()),
                'fileName': path.name,
                'role': role,
                'producer': producer,
                'sourceKind': 'preparation_draft' if is_draft else 'external',
                'draft': is_draft,
                'label': f'{title} · {path.name}',
            })
    return candidates
