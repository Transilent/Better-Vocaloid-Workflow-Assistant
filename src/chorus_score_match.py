"""Ordered multi-part score matching, lane recommendation and preparation score handoff."""
from __future__ import annotations

from dataclasses import dataclass
import math
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
    time_score = 2.6 * ov_ratio - 2.2 * min(center_diff, 1.0) - .8 * min(onset_diff, .8)

    pitch_diff = abs(v_center - s_note.pitch)
    octave_err = abs(pitch_diff - 12.) <= 1.8
    if pitch_diff <= 2.4:
        pitch_score = 2.2 - .55 * pitch_diff
    elif pitch_diff <= 5.5:
        pitch_score = .85 - .35 * (pitch_diff - 2.4)
    elif octave_err:
        pitch_score = -1.1
    else:
        pitch_score = -2.2 - .25 * (pitch_diff - 5.5)

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
        return tuple(ScoreMatch(None, None, '本声部乐谱在这里没有匹配音符', True, 0.) for _ in vocal_notes)

    poly = _polyphonic_indices(score_notes)
    mapped = []
    for v in vocal_notes:
        if time_to_output is not None:
            a, b = time_to_output(v.start, v.end)
        else:
            a, b = v.start, v.end
        if b <= a:
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
        if j is None:
            results.append(ScoreMatch(None, None, '本声部乐谱在这里没有匹配音符', True, 0.))
            continue
        s_note = score_notes[j]
        a, b, center, _ = mapped[i]
        v_dur = max(.02, b - a)
        overlap = max(0., min(b, s_note.end) - max(a, s_note.start))
        pitch_diff = abs(center - s_note.pitch)
        octave_err = abs(pitch_diff - 12.) <= 1.8
        is_poly = j in poly

        if octave_err:
            results.append(ScoreMatch(s_note, s_note.pitch, '疑似错八度，请核对', True, .25))
        elif is_poly:
            results.append(ScoreMatch(s_note, s_note.pitch, '同轨重叠和弦，请确认声部', True, .35))
        elif draft:
            results.append(ScoreMatch(s_note, s_note.pitch, '导唱提取草稿，请先核对', True, .60))
        elif overlap < .20 * v_dur or pitch_diff > 2.4:
            results.append(ScoreMatch(s_note, s_note.pitch, '谱轨匹配待确认', True, .45))
        else:
            conf = max(.5, min(1., .65 + .35 * min(1., overlap / v_dur) - .10 * pitch_diff))
            results.append(ScoreMatch(s_note, s_note.pitch, '有序谱轨匹配', False, round(conf, 3)))
    return tuple(results)


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
            if vocal_median is not None:
                dist = abs(vocal_median - lane_median)
                score += max(-4., 4. - .45 * dist)
                if dist <= 2.5:
                    reasons.append('音区接近')
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
