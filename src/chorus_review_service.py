"""Pure Python chorus review, curve projection, MIDI auto-tune & analysis service (Qt-free)."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from chorus_pitch_dsp import signalsmith_available
from chorus_precision import mapped_time, phrase_for


def _check(stop) -> None:
    if stop.is_set():
        raise InterruptedError('操作已取消。')


def _meter(send, phase: str, done: int, total: int, message: str) -> None:
    send({'phase': phase, 'done': done, 'total': total, 'unit': 'items', 'message': message})


@contextmanager
def _analysis_sources(project, rows, cache, features, check, send):
    from chorus_analysis import feature_track
    from chorus_audio import analysis_recording, recording_key

    audio, measured, added = {}, {}, {}
    with TemporaryDirectory(dir=cache, prefix='sources-') as folder:
        for index, track in enumerate(rows):
            check()
            source = analysis_recording(project, track, Path(folder), check)
            key = recording_key(project, track)
            audio[track.id] = source
            if features is None:
                continue
            if key not in features and key not in added:
                _meter(send, 'analyze', index, len(rows), f'分析当前录音 · {track.name}')
                added[key] = feature_track(source, check)
            measured[track.id] = features[key] if key in features else added[key]
        yield audio, measured, added


def display_time_for(track, take_time: float, timing_edits) -> float:
    proj_t = take_time + track.offset
    for edit in timing_edits:
        a, b = edit.source_time(edit.start), edit.source_time(edit.end)
        if a <= proj_t <= b:
            return edit.output_time(proj_t)
    return proj_t


def build_issues_list(project, suggestions, resolved: set[int]) -> list[dict[str, Any]]:
    vocal_names = [t.name for t in project.tracks if t.role in ('vocal', 'guide') and t.name]
    shared_prefix = ''
    if len(vocal_names) >= 2:
        raw_prefix = os.path.commonprefix(vocal_names)
        for sep in (' - ', '_', ' ', '-'):
            cut = raw_prefix.rfind(sep)
            if cut >= 0:
                cand = raw_prefix[:cut + len(sep)]
                if all(len(n[len(cand):].strip()) >= 2 for n in vocal_names):
                    shared_prefix = cand
                    break
    issues = []
    for index, row in enumerate(suggestions):
        if row.review and index not in resolved:
            track = project.track(row.track)
            if track is not None:
                category = row.category or ('timing' if row.kind == 'timing' else 'review')
                severity = row.severity or ('high' if category in ('transpose', 'octave', 'large_shift') else 'medium')
                hint = row.action_hint or (
                    '可点击接受建议自动应用，或跳转钢琴窗试听微调' if row.can_apply
                    else '点击跳转钢琴窗定位该处并试听修正'
                )
                short_name = (track.name[len(shared_prefix):].strip()
                              if shared_prefix and track.name.startswith(shared_prefix) else track.name)
                disp_start = max(0., row.start) if row.end > 0. else row.start
                disp_end = max(disp_start, row.end)
                issues.append({
                    'id': index,
                    'track': track.name,
                    'shortTrack': short_name or track.name,
                    'trackId': track.id,
                    'kind': row.kind,
                    'time': disp_start,
                    'end': disp_end,
                    'reason': row.detail or '需要人工核对',
                    'actionHint': hint,
                    'category': category,
                    'severity': severity,
                    'canApply': bool(row.can_apply),
                    'targetOctave': row.target_octave if row.target_octave is not None else 999,
                })
    issues.sort(key=lambda item: (
        0 if item['category'] == 'transpose' else (1 if item['severity'] == 'high' else 2),
        item['time'],
        item['shortTrack'],
    ))
    return issues


def build_view(
    *,
    project,
    selected_id: str,
    selection: tuple[float, float],
    suggestions,
    resolved: set[int],
    issue_index: int,
    timing_pose_fn: Callable,
    track_row_fn: Callable,
    lyric_row_fn: Callable,
    effective_timing_ref_mode: str,
    gain_view_fn: Callable,
    can_split_fn: Callable,
    score_recommendations_fn: Callable,
    path_str: str,
    dirty: bool,
    batch_strength: dict[str, float],
    view_first: float,
    view_span: float,
    mode: str,
    inspector_open: bool,
    inspector_section: str,
    pitch_draw: bool,
    pitch_tool: str,
    pitch_snap: str,
    show_score_background: bool,
    midi_audition: bool,
    selected_notes: tuple,
    variant: str,
    active_variant: str,
    solo: bool,
    playing: bool,
    loop: bool,
    follow_playhead: bool,
    loading: bool,
    busy: bool,
    notice: str,
    error: bool,
    has_undo: bool,
    has_redo: bool,
    export_str: str,
    prep_scores: list,
    focus_revision: int,
) -> dict[str, Any]:
    selected = project.track(selected_id)
    phrase = phrase_for(project, selection)
    reference = project.reference(selected, phrase)
    middle = sum(selection) / 2
    shift = middle - mapped_time(project, selected, middle) if selected else 0.
    current, timing = timing_pose_fn(selected, middle) if selected else (None, None)
    knots = (current.points if current and current.manual else
             timing.points if timing and timing.points else current.points if current else ())
    if not knots and timing and len(timing.anchors) >= 2:
        knots = ((timing.start, timing.start - (timing.value or 0.)),
                 *((x, y) for _, x, y in timing.anchors if timing.start < x < timing.end),
                 (timing.end, timing.end - (timing.value or 0.)))
    labels = {round(y, 5): label for label, _, y in timing.anchors} if timing else {}
    anchors = [{'label': labels.get(round(y, 5), str(i + 1)), 'time': x, 'source': y}
               for i, (x, y) in enumerate(knots[1:-1])]
    shared, local = ((phrase.lyric(), phrase.lyric(selected_id)) if phrase else (None, None))
    gain = next((edit.value for edit in project.edits_for(selected_id, 'gain')
                 if edit.start <= middle < edit.end), 0.)
    comp = next((edit.source for edit in project.edits_for(selected_id, 'take')
                 if edit.start <= (mapped_time(project, selected, middle) if selected else middle) < edit.end), '')
    score = project.score
    issues = build_issues_list(project, suggestions, resolved)
    from mixdown_contract import MAX_VOCALS

    return {
        'title': project.title, 'path': path_str, 'dirty': dirty,
        'first': project.start, 'last': project.end, 'rate': project.rate,
        'count': len(project.tracks), 'vocals': sum(track.role == 'vocal' for track in project.tracks),
        'hasBacking': any(track.role == 'backing' for track in project.tracks),
        'hasEdits': bool(project.edits),
        'maxMixdownVocals': MAX_VOCALS,
        'guide': project.guide, 'selected': selected_id, 'take': track_row_fn(selected) if selected else {},
        'reference': track_row_fn(reference) if reference else {}, 'shiftMs': shift * 1000,
        'phrase': {'id': phrase.id if phrase else '', 'ref': phrase.reference if phrase else '',
                   'shared': lyric_row_fn(shared), 'local': lyric_row_fn(local)},
        'alignmentEngine': project.alignment_engine,
        'ensembleLanguage': project.ensemble_language,
        'timingRefMode': effective_timing_ref_mode,
        'pitchEngine': project.pitch_engine,
        'signalsmithReady': signalsmith_available(),
        'timingStrength': batch_strength['timing'],
        'pitchStrength': batch_strength['pitch'],
        'anchors': anchors, 'timingRoute': ('手工锚点' if current and current.manual and current.points else
                                           timing.route if timing else current.basis if current else ''),
        'timingDetail': timing.detail if timing else current.basis if current else '', 'gain': gain, 'comp': comp,
        'syllableGain': gain_view_fn(selected, phrase),
        'canApplyTiming': bool(timing and timing.can_apply and not (current and current.manual)
                               and suggestions.index(timing) not in resolved),
        'viewFirst': view_first, 'viewSpan': view_span,
        'rangeFirst': selection[0], 'rangeLast': selection[1], 'mode': mode,
        'inspector': inspector_open, 'inspectorSection': inspector_section,
        'pitchDraw': pitch_draw,
        'pitchTool': pitch_tool,
        'pitchSnap': pitch_snap,
        'showScoreBackground': show_score_background,
        'midiAudition': midi_audition,
        'selectedNoteCount': len(selected_notes),
        'canMergeNotes': len(selected_notes) >= 2,
        'canSplitAtPlayhead': can_split_fn(selected),
        'variant': variant, 'activeVariant': active_variant, 'solo': solo,
        'playing': playing, 'loop': loop, 'followPlayhead': follow_playhead,
        'loading': loading,
        'busy': busy, 'notice': notice, 'error': error,
        'undo': has_undo, 'redo': has_redo, 'export': export_str,
        'score': Path(score.path).name if score else '', 'scoreOffset': score.offset if score else 0.,
        'scoreSourceKind': score.source_kind if score else '',
        'scoreSourceLabel': score.source_label if score else '',
        'scoreConfirmed': score.confirmed if score else True,
        'scoreLanes': [{'id': lane.id, 'name': lane.name, 'notes': len(lane.notes)} for lane in score.lanes]
        if score else [],
        'scoreRecommendations': score_recommendations_fn(selected),
        'prepScores': list(prep_scores),
        'issues': issues, 'issue': issue_index,
        'focusRevision': focus_revision,
        'highIssueCount': sum(1 for item in issues if item['severity'] == 'high'),
        'canAcceptIssue': (0 <= issue_index < len(suggestions)
                           and issue_index not in resolved and suggestions[issue_index].can_apply),
        'suggestions': sum(row.can_apply and index not in resolved
                           for index, row in enumerate(suggestions)),
    }


def _auto_align_score_tracks(working, targets, known, *, need_align: bool):
    from chorus_score_match import estimate_score_alignment

    aligned_info = None
    if need_align or any(not t.score_lane for t in targets):
        aligned_info = estimate_score_alignment(working, known)
        if aligned_info['tracks'] and aligned_info['coverage'] >= .25 and aligned_info['median_err'] <= 2.5:
            if need_align:
                working = replace(working, score=replace(working.score, offset=aligned_info['offset']))
            for tid, info in aligned_info['tracks'].items():
                tr = working.track(tid)
                if tr is not None and (need_align or not tr.score_lane):
                    working = working.update_track(
                        tid,
                        score_lane=tr.score_lane or info['laneId'],
                        score_octave=info['octave'] if (need_align or not tr.score_lane) else tr.score_octave,
                    )
    if len(working.score.lanes) == 1:
        only_lane = working.score.lanes[0].id
        for t in targets:
            tr = working.track(t.id)
            if tr and not tr.score_lane:
                working = working.update_track(t.id, score_lane=only_lane)
    return working, aligned_info


def run_auto_tune_work(
    *,
    project,
    all_vocals,
    targets,
    kind: str,
    selection: tuple[float, float],
    timing_strength: float,
    pitch_strength: float,
    known: dict,
    cached_features: dict,
    cache,
    stop,
    send,
):
    from chorus_analysis import proposals
    from chorus_pitch import estimate_many

    check = lambda: _check(stop)
    with _analysis_sources(project, all_vocals, cache, cached_features, check, send) as (audio, features, added):
        need_align = (abs(project.score.offset) < 1e-4
                      and all(t.score_octave == 0 for t in all_vocals))
        need_tracks = all_vocals if need_align else targets
        missing = tuple(audio[t.id] for t in need_tracks if t.id not in known)
        if missing:
            known.update(estimate_many(missing, cache, check, lambda i, label:
                                       _meter(send, 'analyze', i, len(missing), label)))
        check()
        working, aligned_info = _auto_align_score_tracks(project, targets, known, need_align=need_align)
        active_targets = tuple(working.track(t.id) for t in targets if working.track(t.id))
        _, suggestions = proposals(
            working, active_targets, selection, kind, timing_strength, .60,
            features, known, check,
            lambda i, label: _meter(send, 'analyze', i, len(active_targets), f'对照 MIDI 修音 · {label}'),
            audio=audio, pitch_strength=pitch_strength, use_midi_timing=(kind != 'pitch'),
        )
    check()
    return working, suggestions, known, added, aligned_info


def apply_auto_tune_result(working, suggestions, aligned_info, *, kind: str):
    from chorus_analysis import apply_suggestions

    picked = [(i, row) for i, row in enumerate(suggestions) if row.can_apply and not row.review]
    next_project = working
    pitch_count = 0
    timing_count = 0
    for _, row in picked:
        updated = apply_suggestions(next_project, (row,), manual=False)
        if updated != next_project:
            if row.kind == 'timing':
                timing_count += 1
            else:
                pitch_count += 1
        next_project = updated
    resolved = {i for i, _ in picked}
    extra = (f'（MIDI 偏移 {next_project.score.offset:+.2f}s）'
             if aligned_info and abs(next_project.score.offset) > 1e-4 else '')
    review_cnt = sum(1 for i, r in enumerate(suggestions) if r.review and i not in resolved)
    tail = f'，另有 {review_cnt} 处待核对' if review_cnt else ''
    if kind == 'both':
        msg = f'已对照 MIDI 对齐 {timing_count} 段节奏并修音 {pitch_count} 个音符{extra}{tail}'
    elif kind == 'timing':
        msg = f'已对照 MIDI 对齐 {timing_count} 段节奏{extra}{tail}'
    else:
        msg = f'已对照 MIDI 修音 {pitch_count} 个音符{extra}{tail}'
    return next_project, resolved, (pitch_count or timing_count), msg


def compute_issue_focus(project, row, *, cached_vocal_notes_fn: Callable):
    start, end = row.start, row.end
    track = project.track(row.track)
    if track is not None:
        timing_edits = project.edits_for(track.id, 'timing')
        if row.kind == 'pitch' and timing_edits:
            da = display_time_for(track, row.start - track.offset, timing_edits)
            db = display_time_for(track, row.end - track.offset, timing_edits)
            start, end = (da, db) if db >= da else (db, da)
        if row.category == 'transpose' or (end - start) > 18.0:
            notes = cached_vocal_notes_fn(track)
            if notes:
                first_t = notes[0].start + track.offset
                if timing_edits:
                    first_t = display_time_for(track, notes[0].start, timing_edits)
                start = max(project.start, first_t)
                end = min(project.end, start + 5.0)
    start = max(project.start, start)
    end = max(start + .2, min(project.end, end))
    span = max(3.5, min(8.5, (end - start) + 2.8))
    selected_notes = None
    if track is not None and row.category != 'transpose':
        take_a = row.start - track.offset
        take_b = row.end - track.offset
        notes = cached_vocal_notes_fn(track)
        hit = next((n for n in notes if n.end > take_a + .01 and n.start < take_b - .01), None)
        if hit is not None:
            selected_notes = ((hit.start, hit.end),)
    target_pos = max(project.start, min(project.end, start - .12))
    return start, end, span, selected_notes, target_pos


def build_curve_payload(
    *,
    project,
    identity: str,
    view_first: float,
    view_span: float,
    curves_map: dict,
    variant: str,
    suggestions,
    resolved: set[int],
    selected_notes: tuple,
    cached_vocal_notes_fn: Callable,
    match_cache: dict,
) -> dict[str, Any]:
    from chorus_audio import compiled_pitch_semitones, recording_key
    from chorus_notes import effective_note_offset
    from chorus_score_match import _normalize_token, match_vocal_to_score, shift_score_notes

    track = project.track(identity)
    view_last = view_first + view_span
    score_rows = []
    bound_lane = None
    if track and project.score and track.score_lane:
        score = project.score
        lane = next((item for item in score.lanes if item.id == track.score_lane), None)
        if lane is not None:
            bound_lane = shift_score_notes(
                lane.notes, offset=score.offset, octave_semitones=track.score_octave)
            for note in bound_lane:
                if note.end >= view_first and note.start <= view_last:
                    score_rows.append({
                        'start': note.start, 'end': note.end,
                        'pitch': note.pitch, 'lyric': note.lyric,
                    })

    source = curves_map.get(recording_key(project, track)) if track else None
    if source is None:
        return {'first': view_first, 'step': .01, 'midi': [], 'target': [],
                'notes': [], 'scoreNotes': score_rows, 'trackReview': {}}
    import numpy as np

    midi, step = source
    grid = view_first + np.arange(max(1, int(view_span / step) + 1)) * step
    local = grid - track.offset

    def project_curve(take_time):
        frames = np.rint(take_time / step).astype('int64')
        found = np.full(grid.shape, np.nan, dtype=np.float32)
        valid = (frames >= 0) & (frames < len(midi))
        found[valid] = midi[frames[valid]]
        return found

    original = project_curve(local)
    use_timing = variant in ('timing', 'processed')
    use_pitch = variant in ('pitch', 'processed')
    timing_edits = project.edits_for(identity, 'timing') if use_timing else ()
    moved = local.copy()
    if use_timing:
        for edit in timing_edits:
            covered = (grid >= edit.start) & (grid < edit.end)
            if edit.points:
                x, y = zip(*edit.points)
                moved[covered] = np.interp(grid[covered], x, y) - track.offset
            else:
                moved[covered] -= edit.value
    fixed = project_curve(moved)
    pitch_edits = project.edits_for(identity, 'pitch')
    if use_pitch and pitch_edits:
        shift_semitones = compiled_pitch_semitones(
            pitch_edits, moved, duration=track.duration, voiced_curve=source)
        finite = np.isfinite(fixed)
        fixed[finite] += shift_semitones[finite]

    vocal_notes = cached_vocal_notes_fn(track)
    matches = None
    if bound_lane is not None and vocal_notes:
        all_timing = project.edits_for(identity, 'timing')
        phrase_texts = tuple(
            (p.start, p.end, _normalize_token(line.text))
            for p in project.phrases
            if (line := p.resolved_lyric(identity)) and line.text
        )
        score_obj = project.score
        match_key = (
            recording_key(project, track), project.note_edits_for(identity),
            track.offset, track.score_lane, track.score_octave, score_obj.sha, score_obj.offset,
            score_obj.confirmed, all_timing, phrase_texts,
        )
        matches = match_cache.get(match_key)
        if matches is None:
            def to_output(ta, tb):
                return (display_time_for(track, ta, all_timing),
                        display_time_for(track, tb, all_timing))

            matches = match_vocal_to_score(
                vocal_notes, bound_lane, time_to_output=to_output, phrase_texts=phrase_texts,
                draft=bool(score_obj and not score_obj.confirmed),
            )
            if len(match_cache) > 32:
                match_cache.clear()
            match_cache[match_key] = matches

    unresolved_pitch = [
        (s_idx, row) for s_idx, row in enumerate(suggestions)
        if row.review and s_idx not in resolved and row.track == identity and row.kind == 'pitch'
    ]
    track_review = {}
    for s_idx, row in unresolved_pitch:
        if row.category == 'transpose':
            track_review = {
                'id': s_idx,
                'category': row.category,
                'severity': row.severity or 'high',
                'reason': row.detail,
                'actionHint': row.action_hint,
                'canApply': bool(row.can_apply),
                'targetOctave': row.target_octave if row.target_octave is not None else 999,
            }
            break

    selected_set = {(round(a, 4), round(b, 4)) for a, b in selected_notes}
    note_rows = []
    for idx, note in enumerate(vocal_notes):
        disp_start = display_time_for(track, note.start, timing_edits)
        disp_end = display_time_for(track, note.end, timing_edits)
        if disp_end < view_first - .2 or disp_start > view_last + .2:
            continue
        offset_cents, manual, locked, has_curve = effective_note_offset(note, pitch_edits)
        shown_offset = offset_cents if use_pitch else 0.
        match = matches[idx] if matches is not None else None
        note_a = note.start + track.offset
        note_b = note.end + track.offset
        sug_hit = next(
            ((s_idx, row) for s_idx, row in unresolved_pitch
             if row.category != 'transpose' and row.end > note_a + .01 and row.start < note_b - .01),
            None,
        )
        rev_sev = ''
        rev_cat = ''
        rev_reason = ''
        rev_hint = ''
        issue_id = -1
        if sug_hit is not None:
            issue_id, s_row = sug_hit
            rev_cat = s_row.category or 'review'
            rev_sev = s_row.severity or ('high' if rev_cat in ('octave', 'large_shift') else 'medium')
            rev_reason = s_row.detail
            rev_hint = s_row.action_hint
        elif abs(offset_cents) > 250.:
            rev_sev = 'high'
            rev_cat = 'large_shift'
            rev_reason = f'大幅变调 {offset_cents:+.0f}¢（超过 2.5 半音），易产生音色异常'
            rev_hint = '建议试听后决定是否手动微调或保留原音'
        elif match is not None and (match.review or match.intent == 'review'):
            rev_sev = 'medium'
            rev_cat = 'match_review'
            rev_reason = match.reason or '与背景谱轨匹配存疑'
            rev_hint = '建议对照背景 MIDI 核对并手动拖动音符'
        elif track_review:
            issue_id = track_review['id']
            rev_sev = 'high'
            rev_cat = 'transpose'
            rev_reason = track_review['reason']
            rev_hint = track_review['actionHint']
        note_rows.append({
            'id': f'{note.start:.4f}-{note.end:.4f}',
            'start': disp_start,
            'end': disp_end,
            'takeStart': note.start,
            'takeEnd': note.end,
            'centerMidi': note.center_midi,
            'targetMidi': note.center_midi + shown_offset / 100.,
            'offsetCents': round(offset_cents, 1),
            'lowMidi': note.low_midi,
            'highMidi': note.high_midi,
            'manual': manual,
            'locked': locked,
            'hasCurve': has_curve,
            'linkedPrev': note.linked_prev,
            'linkedNext': note.linked_next,
            'selected': (round(note.start, 4), round(note.end, 4)) in selected_set,
            'scorePitch': match.target_pitch if (match and match.target_pitch is not None) else -1,
            'scoreLyric': match.score_note.lyric if (match and match.score_note is not None) else '',
            'matchReason': match.reason if match is not None else '',
            'matchReview': bool(match.review) if match is not None else False,
            'matchIntent': match.intent if match is not None else '',
            'largeShift': abs(offset_cents) > 250.,
            'reviewSeverity': rev_sev,
            'reviewCategory': rev_cat,
            'reviewReason': rev_reason,
            'reviewHint': rev_hint,
            'issueId': issue_id,
        })

    return {'first': view_first, 'step': step,
            'midi': np.nan_to_num(original, nan=-1.).tolist(),
            'target': np.nan_to_num(fixed, nan=-1.).tolist(),
            'notes': note_rows,
            'scoreNotes': score_rows,
            'trackReview': track_review}


def run_analysis_work(
    *,
    project,
    voice_tracks,
    all_vocals,
    targets,
    selection: tuple[float, float],
    kind: str,
    strength: float,
    pitch_strength: float,
    has_score: bool,
    use_midi_timing: bool,
    language: str,
    cached_features: dict,
    curves: dict,
    speech: dict,
    cache,
    stop,
    send,
):
    from chorus_analysis import proposals
    from chorus_pitch import estimate_many

    check = lambda: _check(stop)
    failure = ''
    working = project
    relevant = {ref.id for track in targets if (ref := project.reference(track))}
    relevant.update(phrase.reference for phrase in project.phrases if phrase.reference
                    and phrase.start < selection[1] and phrase.end > selection[0])
    relevant.update(track.id for track in targets)
    need_score_align = (
        has_score
        and (use_midi_timing or kind != 'timing')
        and abs(project.score.offset) < 1e-4
        and all(t.score_octave == 0 for t in all_vocals)
    )
    with _analysis_sources(project, voice_tracks, cache, cached_features, check, send) as (audio, features, added):
        if need_score_align:
            curve_need = tuple(audio[t.id] for t in all_vocals if t.id not in curves)
        elif use_midi_timing or kind != 'timing':
            curve_need = tuple(
                audio[track.id] for track in voice_tracks if track.id not in curves and (
                    track.id in relevant if kind == 'both' else track in targets
                )
            )
        else:
            curve_need = ()
        if curve_need:
            try:
                curves.update(estimate_many(curve_need, cache, check, lambda i, label:
                                            _meter(send, 'analyze', i, len(curve_need), label)))
            except (OSError, RuntimeError) as error:
                failure = str(error)
                if kind == 'pitch' or use_midi_timing:
                    raise
        if has_score and (use_midi_timing or kind != 'timing'):
            working, _ = _auto_align_score_tracks(working, targets, curves, need_align=need_score_align)
        active_targets = tuple(working.track(t.id) for t in targets if working.track(t.id))
        result = proposals(working, active_targets, selection, kind, strength, .60, features, curves,
                           check, lambda i, label: _meter(send, 'analyze', i, len(active_targets), f'比较唱段 · {label}'),
                           speech=(speech, cache), audio=audio,
                           ensemble_language=language, pitch_strength=pitch_strength,
                           use_midi_timing=use_midi_timing,
                           phone_progress=lambda i, n, label: _meter(send, 'align', i, n, label))
    check()
    return (working, *result, curves, speech, added, failure)


def apply_analysis_result(working, phrases, suggestions, failure: str, *,
                          full: bool, language: str, commit: bool, include_review: bool):
    from chorus_analysis import apply_suggestions
    from chorus_precision import remembered_phrases

    next_project = working
    if full:
        next_project = replace(
            next_project,
            phrases=phrases if language else remembered_phrases(next_project, phrases),
        )
    if commit:
        picked = [(i, row) for i, row in enumerate(suggestions)
                  if row.can_apply and (include_review or not row.review)]
        count = 0
        for _, row in picked:
            updated = apply_suggestions(next_project, (row,), manual=False)
            count += updated != next_project
            next_project = updated
        resolved = {i for i, _ in picked}
        review_cnt = sum(1 for i, r in enumerate(suggestions) if r.review and i not in resolved)
        tail = f'，另有 {review_cnt} 处待核对' if review_cnt else ''
        msg = f'已应用 {count} 项处理{tail}' + (f'。{failure}' if failure else '')
        return next_project, resolved, bool(count), msg
    count = sum(row.can_apply for row in suggestions)
    review_cnt = sum(1 for r in suggestions if r.review)
    tail = f'（含 {review_cnt} 处待核对）' if review_cnt else ''
    msg = f'已生成 {count} 项建议{tail}' + (f'。{failure}' if failure else '')
    return next_project, set(), False, msg


def snap_notes_to_semitone(project, track, parsed, notes):
    from chorus_notes import effective_note_offset, shift_vocal_notes

    pitch_edits = project.edits_for(track.id, 'pitch')
    selected_set = {(round(a, 4), round(b, 4)) for a, b in parsed}
    updated = project
    snapped = 0
    for note in notes:
        if (round(note.start, 4), round(note.end, 4)) not in selected_set:
            continue
        offset_cents, _, _, _ = effective_note_offset(note, pitch_edits)
        target_midi = note.center_midi + offset_cents / 100.
        delta_cents = (round(target_midi) - target_midi) * 100.
        if abs(delta_cents) < .5:
            continue
        updated = shift_vocal_notes(updated, track.id, ((note.start, note.end),), delta_cents)
        snapped += 1
    return updated, snapped


def snap_notes_to_score(project, track, lane, parsed, notes):
    from chorus_notes import effective_note_offset, shift_vocal_notes
    from chorus_score_match import _normalize_token, match_vocal_to_score, shift_score_notes

    score = project.score
    bound_lane = shift_score_notes(lane.notes, offset=score.offset, octave_semitones=track.score_octave)
    all_timing = project.edits_for(track.id, 'timing')
    phrase_texts = tuple(
        (p.start, p.end, _normalize_token(line.text))
        for p in project.phrases
        if (line := p.resolved_lyric(track.id)) and line.text
    )
    matches = match_vocal_to_score(
        notes, bound_lane,
        time_to_output=lambda ta, tb: (display_time_for(track, ta, all_timing),
                                       display_time_for(track, tb, all_timing)),
        phrase_texts=phrase_texts, draft=False,
    )
    pitch_edits = project.edits_for(track.id, 'pitch')
    selected_set = {(round(a, 4), round(b, 4)) for a, b in parsed}
    updated = project
    snapped = 0
    for note, match in zip(notes, matches):
        if (round(note.start, 4), round(note.end, 4)) not in selected_set:
            continue
        if match.target_pitch is None:
            continue
        offset_cents, _, _, _ = effective_note_offset(note, pitch_edits)
        current_midi = note.center_midi + offset_cents / 100.
        delta_cents = (match.target_pitch - current_midi) * 100.
        if abs(delta_cents) < .5:
            continue
        updated = shift_vocal_notes(updated, track.id, ((note.start, note.end),), delta_cents)
        snapped += 1
    return updated, snapped


def find_next_review_target(
    *,
    project,
    selected_id: str,
    suggestions,
    resolved: set[int],
    current_issue: int,
    position: float,
    variant: str,
    cached_vocal_notes_fn: Callable,
):
    from chorus_notes import effective_note_offset
    from chorus_score_match import _normalize_token, match_vocal_to_score, shift_score_notes

    unresolved = [
        (i, r) for i, r in enumerate(suggestions)
        if r.review and i not in resolved and project.track(r.track) is not None
    ]
    if unresolved:
        unresolved.sort(key=lambda item: (
            0 if item[1].category == 'transpose' else (1 if item[1].severity == 'high' else 2),
            item[1].start,
            item[0],
        ))
        ids = [i for i, _ in unresolved]
        if current_issue in ids:
            nxt_id = ids[(ids.index(current_issue) + 1) % len(ids)]
        else:
            nxt_id = next((i for i, r in unresolved if r.start > position + .03), ids[0])
        row = suggestions[nxt_id]
        tr = project.track(row.track)
        msg = f'待核对 ({ids.index(nxt_id) + 1}/{len(ids)}) · {tr.name if tr else ""} · {row.detail}'
        return {'kind': 'issue', 'issue_id': nxt_id, 'message': msg}

    track = project.track(selected_id)
    if not track or track.role != 'vocal':
        return {'kind': 'none'}
    notes = cached_vocal_notes_fn(track)
    if not notes:
        return {'kind': 'message', 'message': '先提取本轨音高再定位待核对音符。'}
    timing_edits = project.edits_for(track.id, 'timing') if variant in ('timing', 'processed') else ()
    pitch_edits = project.edits_for(track.id, 'pitch')
    matches = None
    score = project.score
    if score and track.score_lane:
        lane = next((item for item in score.lanes if item.id == track.score_lane), None)
        if lane is not None:
            bound = shift_score_notes(lane.notes, offset=score.offset, octave_semitones=track.score_octave)
            all_timing = project.edits_for(track.id, 'timing')
            phrase_texts = tuple(
                (p.start, p.end, _normalize_token(line.text))
                for p in project.phrases
                if (line := p.resolved_lyric(track.id)) and line.text
            )
            matches = match_vocal_to_score(
                notes, bound,
                time_to_output=lambda ta, tb: (display_time_for(track, ta, all_timing),
                                               display_time_for(track, tb, all_timing)),
                phrase_texts=phrase_texts, draft=bool(not score.confirmed),
            )
    flagged = []
    for idx, note in enumerate(notes):
        offset_cents, _, _, _ = effective_note_offset(note, pitch_edits)
        match = matches[idx] if matches is not None else None
        is_large = abs(offset_cents) > 250.
        is_rev = bool(match and (match.review or match.intent == 'review'))
        if is_large or is_rev:
            disp_a = display_time_for(track, note.start, timing_edits)
            disp_b = display_time_for(track, note.end, timing_edits)
            reason = '大幅变调 (>2.5 半音)' if is_large else (match.reason if match else '待核对')
            flagged.append((disp_a, disp_b, note, reason))
    if not flagged:
        return {'kind': 'message', 'message': '当前没有高风险或待核对位置。'}
    flagged.sort(key=lambda item: item[0])
    nxt = next((item for item in flagged if item[0] > position + .03), flagged[0])
    disp_a, disp_b, note, reason = nxt
    return {
        'kind': 'note',
        'disp_a': disp_a,
        'disp_b': disp_b,
        'note': note,
        'message': f'已定位待核对音符（共 {len(flagged)} 处）· {reason}',
    }
