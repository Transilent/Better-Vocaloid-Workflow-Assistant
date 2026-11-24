"""Source-recording vocal note segmentation, splits, merges and pitch-center edits."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import numpy as np

from chorus_project import Edit, NoteEdit


MIN_NOTE_DURATION = .070
MIN_SPLIT_MARGIN = .030
BRIDGE_GAP_SECONDS = .040
BRIDGE_MAX_SEMITONES = 2.5
HYSTERESIS_SEMITONES = .72
HYSTERESIS_HOLD_SECONDS = .065


@dataclass(frozen=True)
class VocalNote:
    start: float
    end: float
    center_midi: float
    low_midi: float
    high_midi: float
    linked_prev: bool = False
    linked_next: bool = False

    @property
    def duration(self):
        return self.end - self.start


def _voiced_runs(mask):
    edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def _note_stats(midi, step, start, end):
    a = max(0, min(len(midi), round(start / step)))
    b = max(a + 1, min(len(midi), round(end / step)))
    segment = midi[a:b]
    valid = segment[np.isfinite(segment)]
    if not len(valid):
        return None
    # Trim brief onset scoop and release falloff on longer notes when computing
    # the musical pitch center, while keeping full P10/P90 for display & checks.
    dur = end - start
    body = valid
    if dur >= .14:
        trim = min(.035, dur * .15)
        ba = max(a, round((start + trim) / step))
        bb = min(b, round((end - trim) / step))
        if bb > ba:
            inner = midi[ba:bb]
            inner_valid = inner[np.isfinite(inner)]
            if len(inner_valid) >= 3:
                body = inner_valid
    center = float(np.median(body))
    low, high = (float(x) for x in np.percentile(valid, (10, 90)))
    return center, low, high


def _bridge_islands(midi, step):
    """Bridge only very short F0 dropouts within one continuous voiced utterance."""
    raw_runs = _voiced_runs(np.isfinite(midi))
    if not raw_runs:
        return []
    merged = [raw_runs[0]]
    max_gap = max(1, round(BRIDGE_GAP_SECONDS / step))
    for a, b in raw_runs[1:]:
        prev_a, prev_b = merged[-1]
        if a - prev_b <= max_gap:
            left_pitch = float(np.nanmedian(midi[max(prev_a, prev_b - 3):prev_b]))
            right_pitch = float(np.nanmedian(midi[a:min(b, a + 3)]))
            if math.isfinite(left_pitch) and math.isfinite(right_pitch) and abs(right_pitch - left_pitch) <= BRIDGE_MAX_SEMITONES:
                merged[-1] = (prev_a, b)
                continue
        merged.append((a, b))
    return merged


def _segment_island(midi, step, island_start, island_end, min_duration):
    length = island_end - island_start
    if length * step < min_duration:
        return []
    raw = midi[island_start:island_end].astype(np.float64, copy=True)
    # Fill tiny bridged dropouts linearly purely for boundary smoothing.
    finite = np.isfinite(raw)
    if not np.all(finite):
        idx = np.arange(length)
        raw = np.interp(idx, idx[finite], raw[finite])

    # Median smooth (~110 ms) suppresses 5-7 Hz vibrato and isolated pitch spikes
    # without erasing genuine melodic note transitions.
    win = max(3, round(.110 / step) | 1)
    if win > length:
        win = length if length % 2 == 1 else max(1, length - 1)
    padded = np.pad(raw, win // 2, mode='edge')
    smooth = np.median(np.lib.stride_tricks.sliding_window_view(padded, win), axis=1)

    hold_frames = max(2, round(HYSTERESIS_HOLD_SECONDS / step))
    min_frames = max(2, round(min_duration / step))
    cuts = [0]
    seg_start = 0
    cursor = min_frames
    while cursor + hold_frames <= length:
        anchor = float(np.median(smooth[seg_start:cursor]))
        ahead = smooth[cursor:cursor + hold_frames]
        if (np.all(ahead - anchor >= HYSTERESIS_SEMITONES)
                or np.all(anchor - ahead >= HYSTERESIS_SEMITONES)):
            # Place the cut at the steepest slope between steady regions.
            search_left = max(seg_start + min_frames, cursor - max(2, round(.040 / step)))
            search_right = min(length - min_frames, cursor + max(2, round(.025 / step)))
            if search_right > search_left:
                diffs = np.abs(np.diff(smooth[search_left - 1:search_right + 1]))
                best = search_left + int(np.argmax(diffs))
            else:
                best = cursor
            if best - seg_start >= min_frames and length - best >= min_frames:
                cuts.append(best)
                seg_start = best
                cursor = seg_start + min_frames
                continue
        cursor += 1
    cuts.append(length)

    notes = []
    for idx, (a, b) in enumerate(zip(cuts, cuts[1:])):
        start = (island_start + a) * step
        end = (island_start + b) * step
        if end - start < min_duration:
            continue
        stats = _note_stats(midi, step, start, end)
        if stats is None:
            continue
        notes.append(VocalNote(
            start=start, end=end,
            center_midi=stats[0], low_midi=stats[1], high_midi=stats[2],
            linked_prev=(idx > 0), linked_next=(idx + 1 < len(cuts) - 1),
        ))
    if notes:
        notes[0] = replace(notes[0], linked_prev=False)
        notes[-1] = replace(notes[-1], linked_next=False)
    return notes


def _refresh_links(notes):
    if not notes:
        return ()
    out = list(notes)
    for i in range(len(out)):
        lp = i > 0 and out[i].linked_prev and abs(out[i].start - out[i - 1].end) <= BRIDGE_GAP_SECONDS + 1e-4
        ln = i + 1 < len(out) and out[i].linked_next and abs(out[i + 1].start - out[i].end) <= BRIDGE_GAP_SECONDS + 1e-4
        out[i] = replace(out[i], linked_prev=lp, linked_next=ln)
    return tuple(out)


def segment_vocal_notes(midi, step, *, note_edits=(), min_duration=MIN_NOTE_DURATION):
    """Segment a vocal F0 curve into editable notes and apply persisted splits/merges."""
    midi = np.asarray(midi, dtype=np.float32)
    notes = []
    for island_start, island_end in _bridge_islands(midi, step):
        notes.extend(_segment_island(midi, step, island_start, island_end, min_duration))

    for op in note_edits:
        if op.kind == 'split':
            at = op.start
            updated = []
            for note in notes:
                if note.start + MIN_SPLIT_MARGIN <= at <= note.end - MIN_SPLIT_MARGIN:
                    left_stats = _note_stats(midi, step, note.start, at) or (note.center_midi, note.low_midi, note.high_midi)
                    right_stats = _note_stats(midi, step, at, note.end) or (note.center_midi, note.low_midi, note.high_midi)
                    updated.append(VocalNote(
                        note.start, at, left_stats[0], left_stats[1], left_stats[2],
                        linked_prev=note.linked_prev, linked_next=True,
                    ))
                    updated.append(VocalNote(
                        at, note.end, right_stats[0], right_stats[1], right_stats[2],
                        linked_prev=True, linked_next=note.linked_next,
                    ))
                else:
                    updated.append(note)
            notes = updated
        elif op.kind == 'merge':
            covered = [i for i, n in enumerate(notes) if n.end > op.start + 1e-4 and n.start < op.end - 1e-4]
            if len(covered) >= 2:
                first_i, last_i = covered[0], covered[-1]
                start, end = notes[first_i].start, notes[last_i].end
                stats = _note_stats(midi, step, start, end)
                if stats is None:
                    center = float(np.median([n.center_midi for n in notes[first_i:last_i + 1]]))
                    low = min(n.low_midi for n in notes[first_i:last_i + 1])
                    high = max(n.high_midi for n in notes[first_i:last_i + 1])
                else:
                    center, low, high = stats
                merged_note = VocalNote(
                    start, end, center, low, high,
                    linked_prev=notes[first_i].linked_prev,
                    linked_next=notes[last_i].linked_next,
                )
                notes = notes[:first_i] + [merged_note] + notes[last_i + 1:]
    return _refresh_links(notes)


def effective_note_offset(note, pitch_edits):
    """Measure the effective pitch offset (cents) and manual/lock status on one note."""
    overlapping = [e for e in pitch_edits if e.end > note.start + 1e-5 and e.start < note.end - 1e-5]
    if not overlapping:
        return 0., False, False, False
    dur = note.end - note.start
    sample_count = max(5, min(41, round(dur / .008) + 1))
    # Focus on the stable body when sampling offset so edge ramps don't bias the readout.
    trim = min(.020, dur * .12) if dur >= .08 else 0.
    grid = np.linspace(note.start + trim, note.end - trim, sample_count)
    values = np.zeros(sample_count, dtype=np.float64)
    manual = False
    has_curve = False
    locked_all = True
    for edit in overlapping:
        mask = (grid >= edit.start - 1e-6) & (grid <= edit.end + 1e-6)
        if not np.any(mask):
            continue
        if edit.manual:
            manual = True
        if edit.points:
            has_curve = True
            x, y = zip(*edit.points)
            vals = np.interp(grid[mask], x, y)
            values[mask] = vals
            if np.any(np.abs(vals) > 1e-4):
                locked_all = False
        else:
            values[mask] = edit.value
            if abs(edit.value) > 1e-4:
                locked_all = False
        if not edit.manual:
            locked_all = False
    offset_cents = float(np.median(values))
    locked = manual and locked_all and abs(offset_cents) <= 1e-4
    return offset_cents, manual, locked, has_curve


def split_vocal_note(project, track_id, note_start, note_end, split_time):
    """Split one vocal note at take time `split_time`, keeping audio sample-exact."""
    if not (note_start + MIN_SPLIT_MARGIN <= split_time <= note_end - MIN_SPLIT_MARGIN):
        raise ValueError('请在音符内部留出足够长度的位置拆分。')
    # Split any pitch edit straddling `split_time` into two touching clips with
    # identical values/curves so compiled continuous rendering is bit-for-bit identical.
    next_edits = []
    for edit in project.edits:
        if edit.track == track_id and edit.kind == 'pitch' and edit.start < split_time < edit.end:
            next_edits.append(edit.clip(edit.start, split_time))
            next_edits.append(edit.clip(split_time, edit.end))
        else:
            next_edits.append(edit)
    # Remove duplicate split at nearly the same position before appending.
    kept_ops = [op for op in project.note_edits
                if not (op.track == track_id and op.kind == 'split' and abs(op.start - split_time) < .005)]
    kept_ops.append(NoteEdit(track_id, 'split', split_time, split_time))
    return replace(project, edits=tuple(sorted(next_edits, key=lambda e: (e.track, e.kind, e.start))),
                   note_edits=tuple(kept_ops))


def merge_vocal_notes(project, track_id, spans):
    """Merge two or more vocal notes into one editable note."""
    ordered = sorted((float(a), float(b)) for a, b in spans if b > a)
    if len(ordered) < 2:
        raise ValueError('请选择至少两个音符再合并。')
    start, end = ordered[0][0], ordered[-1][1]
    existing = [e for e in project.edits_for(track_id, 'pitch') if e.end > start + 1e-5 and e.start < end - 1e-5]
    # Compute duration-weighted average offset across the merged span.
    weighted_sum = 0.
    total_dur = 0.
    has_active = False
    for edit in existing:
        ov_a, ov_b = max(start, edit.start), min(end, edit.end)
        dur = ov_b - ov_a
        if dur <= 1e-6:
            continue
        if edit.points:
            x, y = zip(*edit.points)
            grid = np.linspace(ov_a, ov_b, max(3, round(dur / .01) + 1))
            val = float(np.mean(np.interp(grid, x, y)))
        else:
            val = float(edit.value)
        if abs(val) > 1e-4:
            has_active = True
        weighted_sum += val * dur
        total_dur += dur

    replacement = ()
    if has_active and total_dur > 0.:
        avg_cents = max(-1200., min(1200., weighted_sum / total_dur))
        replacement = (Edit(track_id, 'pitch', start, end, avg_cents, manual=True, basis='音符合并'),)

    updated = project.replace_region(track_id, 'pitch', start, end, replacement)
    kept_ops = [op for op in updated.note_edits
                if not (op.track == track_id and op.kind == 'split' and start < op.start < end)]
    kept_ops.append(NoteEdit(track_id, 'merge', start, end))
    return replace(updated, note_edits=tuple(kept_ops))


def shift_vocal_notes(project, track_id, spans, delta_cents):
    """Shift one or more vocal notes vertically by `delta_cents`, preserving vibrato & curves."""
    delta = float(delta_cents)
    if not math.isfinite(delta):
        raise ValueError('音高偏移量无效。')
    if abs(delta) < 1e-5:
        return project
    ordered = sorted((float(a), float(b)) for a, b in spans if b > a)
    if not ordered:
        return project

    updated = project
    for start, end in ordered:
        overlapping = [e for e in updated.edits_for(track_id, 'pitch')
                       if e.end > start + 1e-5 and e.start < end - 1e-5]
        # Preserve internal hand-drawn curve shape when shifting vertically.
        if len(overlapping) == 1 and overlapping[0].points and overlapping[0].start <= start + 1e-5 and overlapping[0].end >= end - 1e-5:
            clipped = overlapping[0].clip(start, end)
            shifted_points = tuple((x, max(-1200., min(1200., y + delta))) for x, y in clipped.points)
            new_edit = Edit(track_id, 'pitch', start, end, 0., shifted_points, manual=True, basis='音符拖动')
        elif any(e.points for e in overlapping):
            count = max(3, min(120, round((end - start) / .010) + 1))
            grid = np.linspace(start, end, count)
            vals = np.zeros(count, dtype=np.float64)
            for e in overlapping:
                m = (grid >= e.start - 1e-6) & (grid <= e.end + 1e-6)
                if not np.any(m):
                    continue
                if e.points:
                    x, y = zip(*e.points)
                    vals[m] = np.interp(grid[m], x, y)
                else:
                    vals[m] = e.value
            vals = np.clip(vals + delta, -1200., 1200.)
            pts = tuple((float(t), float(v)) for t, v in zip(grid, vals))
            new_edit = Edit(track_id, 'pitch', start, end, 0., pts, manual=True, basis='音符拖动')
        else:
            # Constant-offset note: preserve exact center + delta (vibrato/glides come from raw F0).
            dummy = VocalNote(start, end, 60., 60., 60.)
            base_cents, _, _, _ = effective_note_offset(dummy, overlapping)
            target = max(-1200., min(1200., base_cents + delta))
            if abs(target) < .25:
                target = 0.
            new_edit = Edit(track_id, 'pitch', start, end, target, manual=True, basis='音符拖动')
        updated = updated.replace_region(track_id, 'pitch', start, end, (new_edit,))
    return updated


def reset_vocal_notes(project, track_id, spans, *, lock_original=False):
    """Restore selected vocal notes to raw vocal pitch (optionally locking original)."""
    ordered = sorted((float(a), float(b)) for a, b in spans if b > a)
    updated = project
    for start, end in ordered:
        replacement = (Edit(track_id, 'pitch', start, end, 0., manual=True, basis='原音保留'),) if lock_original else ()
        updated = updated.replace_region(track_id, 'pitch', start, end, replacement)
    return updated
