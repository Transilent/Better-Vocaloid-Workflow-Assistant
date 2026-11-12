"""Encode protected measured F0 as part-local VOCALOID pitch controllers."""

from itertools import groupby
import math
import xml.etree.ElementTree as ET

PIT_SPEC = (0, -8192, 8191)
PBS_SPEC = (2, 1, 24)
REPLAY_TOLERANCE_CENTS = 2.0


def step_values(grid, events, default):
    """VOCALOID holds a controller value until its next event."""
    import numpy as np

    values = np.asarray([default] + [event['value'] for event in events], dtype=np.int64)
    index = np.searchsorted([event['pos'] for event in events], grid, side='right')
    return values[index]


def _independent_notes(notes):
    """Keep whole overlapping notes out of a shared pitch controller."""
    safe, cluster = [], []
    end, skipped = -1, 0
    for note in sorted(notes, key=lambda note: note.start_pos):
        if note.start_pos >= end:
            if len(cluster) == 1:
                safe.extend(cluster)
            elif cluster:
                skipped += len(cluster)
            cluster = []
        cluster.append(note)
        end = max(end, note.end_pos)
    if len(cluster) == 1:
        safe.extend(cluster)
    else:
        skipped += len(cluster)
    return safe, skipped


def _bend_runs(track, project, measure, cancel):
    import numpy as np
    from libresvip.core.time_sync import TimeSynchronizer
    from libresvip.model.pitch_simulator import PitchSimulator
    from libresvip.model.portamento import PortamentoPitch
    from pitch_accuracy import ABSOLUTE_F0_PITCH_AXIS_OFFSET_TICKS

    safe, overlap_notes = _independent_notes(track.note_list)
    if not safe:
        return [], overlap_notes, 0, 0
    clock = TimeSynchronizer(project.song_tempo_list)
    base = PitchSimulator(clock, PortamentoPitch.vocaloid_portamento(), safe, project.time_signature_list)
    starts = np.array([note.start_pos for note in safe])
    ends = np.array([note.end_pos for note in safe])
    axis = ABSOLUTE_F0_PITCH_AXIS_OFFSET_TICKS
    # The shared F0 curve adds short extrapolated guards around each run.
    # Taper only those extra ticks; preserve the first and last measured frames.
    measured = np.empty(0, dtype=np.int64)
    if measure is not None:
        frames = np.flatnonzero(measure.voiced_mask) * measure.time_step_seconds
        measured = np.fromiter((round(clock.get_actual_ticks_from_secs(time)) for time in frames),
                               dtype=np.int64, count=len(frames))
    runs, rejected, rejected_notes = [], 0, set()
    for voiced, points in groupby(track.edited_params.pitch.points.root, key=lambda point: point.y != -100):
        if not voiced:
            continue
        cancel()
        knots = {point.x - axis: point.y for point in points if point.x >= axis}
        if not knots:
            continue
        xs = sorted(knots)
        ticks = np.arange(xs[0], xs[-1] + 1, dtype=np.int64)
        owners = np.searchsorted(starts, ticks, side='right') - 1
        selected = (owners >= 0) & (ticks < ends[owners])
        delta = np.zeros(ticks.size)
        if selected.any():
            inside = ticks[selected]
            target = np.interp(inside, xs, [knots[x] for x in xs])
            baseline = np.asarray(base.pitch_at_ticks_batch(inside.tolist()), dtype=float)
            raw = target - baseline
            representable = np.isfinite(raw) & (np.abs(raw) <= PBS_SPEC[2] * 100)
            rejected += int((~representable).sum())
            rejected_notes.update(owners[selected][~representable].tolist())
            delta[selected] = np.where(representable, raw, 0)
        lo, hi = np.searchsorted(measured, [xs[0], xs[-1] + 1])
        if lo < hi:
            head, tail = measured[lo], measured[hi - 1]
            before, after = ticks < head, ticks > tail
            delta[before] *= (ticks[before] - xs[0] + 1) / (head - xs[0] + 1)
            delta[after] *= (xs[-1] - ticks[after] + 1) / (xs[-1] - tail + 1)
        runs.append((int(ticks[0]), delta, owners))
    if rejected_notes:
        # Clipping, or repeatedly jumping between full bend and neutral,
        # would generate a new glitch on a note the format cannot reproduce.
        blocked = sorted(rejected_notes)
        for _, delta, owners in runs:
            delta[np.isin(owners, blocked)] = 0
    return [(start, delta) for start, delta, _ in runs], overlap_notes, rejected, len(rejected_notes)


def _encode_bends(runs, end, cancel):
    """Use one sensitivity per part, and reset PIT at every unsupported span."""
    import numpy as np

    maximum = max((float(abs(delta).max()) for _, delta in runs), default=0)
    sensitivity = max(PBS_SPEC[0], math.ceil(maximum / 100))
    _, low, high = PIT_SPEC
    scale = 100 * sensitivity
    threshold = int(REPLAY_TOLERANCE_CENTS * high / scale)
    events = {}
    for start, delta in runs:
        cancel()
        # The signed range has one more negative value than positive.
        desired = (delta * np.where(delta < 0, -low, high) / scale).round().astype(int)
        last = 0
        for tick, value in enumerate(desired.tolist(), start):
            if tick >= end:
                break
            if abs(value - last) > threshold or (value == 0 and last != 0):
                events[tick] = value
                last = value
        stop = start + len(delta)
        if last and stop < end:
            events[stop] = 0
    if events:
        events.setdefault(0, 0)
    return sensitivity, events


def _verify_bends(runs, end, bends, sensitivity):
    """Evaluate the decoded step values inside runs and at the start of gaps."""
    import numpy as np

    ticks, targets, gap_starts = [], [], []
    after = 0
    for start, delta in runs:
        stop = min(start + len(delta), end)
        if start >= stop:
            break
        if after < start:
            gap_starts.append(after)
        ticks.append(np.arange(start, stop))
        targets.append(delta[:stop - start])
        after = stop
    if after < end:
        gap_starts.append(after)
    if gap_starts:
        ticks.append(np.asarray(gap_starts))
        targets.append(np.zeros(len(gap_starts)))
    if not ticks:
        return 0.0
    expected = np.concatenate(targets)
    values = step_values(np.concatenate(ticks), bends, PIT_SPEC[0])
    _, low, high = PIT_SPEC
    cents = values / np.where(values < 0, -low, high) * (sensitivity * 100)
    error = float(np.max(np.abs(cents - expected), initial=0.))
    if np.any(values[expected == 0] != 0) or error > REPLAY_TOLERANCE_CENTS + sensitivity * 50 / high + 1e-6:
        raise ValueError('VSQX 音高回读未达到休止归零或控制精度要求，未交付候选工程。')
    return round(error, 4)


def write_vsqx_pitch(path, project, generated_tracks, *, cancel):
    """Install only generated PIT/PBS, then verify the serialized native events."""
    from libresvip.plugins.vsqx.models.vsqx4 import VSQ4_NS, ParameterNames

    tree = ET.parse(path)
    root = tree.getroot()
    tag = '{' + VSQ4_NS + '}'
    if root.tag != tag + 'vsq4':
        raise ValueError('音高保护需要 VWA 生成的 VSQX4 工程。')
    ET.register_namespace('', VSQ4_NS)
    pit_id, pbs_id = ParameterNames.PIT.value, ParameterNames.PBS.value
    tracks = [track for track in project.track_list if hasattr(track, 'note_list')]
    parts = root.findall(tag + 'vsTrack')
    if len(tracks) != len(parts):
        raise ValueError('VSQX 音高保护无法匹配声部。')
    prefix = round(project.time_signature_list[0].bar_length())
    receipts, plans = [], {}

    def controller_rows(part):
        rows = []
        for control in part.findall(tag + 'cc'):
            value = control.find(tag + 'v')
            if value is not None and value.get('id') in (pit_id, pbs_id):
                rows.append((int(control.findtext(tag + 't')), value.get('id'), int(value.text)))
        return rows

    for index, measure in generated_tracks.items():
        cancel()
        track = tracks[index]
        voice_parts = parts[index].findall(tag + 'vsPart')
        if not track.note_list and not voice_parts:
            receipts.append({'vocal_track_index': index, 'status': 'no_notes'})
            continue
        if len(voice_parts) != 1:
            raise ValueError('VSQX 音高保护需要每个生成声部只有一个片段。')
        part = voice_parts[0]
        native_notes = [tuple(int(note.findtext(tag + key)) for key in ('t', 'dur', 'n'))
                        for note in part.findall(tag + 'note')]
        if int(part.findtext(tag + 't')) != prefix or native_notes != [
                (note.start_pos, note.length, note.key_number) for note in track.note_list]:
            raise ValueError('VSQX 音高保护的片段时间或音符与来源不一致。')
        end = int(part.findtext(tag + 'playTime'))
        runs, overlaps, rejected, rejected_notes = _bend_runs(track, project, measure, cancel)
        sensitivity, bends = _encode_bends(runs, end, cancel)
        # The converter must not write another pitch layer on this track.
        if controller_rows(part):
            raise ValueError('VSQX 转换器为受保护声部重复写入了音高。')
        rows = [(0, pbs_id, sensitivity)] if bends and sensitivity != PBS_SPEC[0] else []
        rows.extend((tick, pit_id, value) for tick, value in sorted(bends.items()))
        controls = []
        for tick, name, value in rows:
            control = ET.Element(tag + 'cc')
            ET.SubElement(control, tag + 't').text = str(tick)
            ET.SubElement(control, tag + 'v', {'id': name}).text = str(value)
            controls.append(control)
        existing = part.findall(tag + 'cc')
        for control in existing:
            part.remove(control)
        controls.extend(existing)
        controls.sort(key=lambda control: int(control.findtext(tag + 't')))
        insertion = list(part).index(part.find(tag + 'note'))
        part[insertion:insertion] = controls
        receipt = {'vocal_track_index': index,
                   'source': 'absolute_rmvpe' if measure is not None else 'generated_ustx',
                   'status': 'partial' if overlaps or rejected else 'protected' if bends else 'neutral',
                   'pit_events': len(bends), 'pbs': sensitivity,
                   'protected_overlapping_notes': overlaps, 'unrepresentable_ticks': rejected,
                   'protected_unrepresentable_notes': rejected_notes}
        plans[index] = rows, runs, end, receipt
        receipts.append(receipt)

    tree.write(path, encoding='utf-8', xml_declaration=True)
    readback = ET.parse(path).getroot().findall(tag + 'vsTrack')
    for index, (rows, runs, end, receipt) in plans.items():
        cancel()
        part = readback[index].find(tag + 'vsPart')
        actual = controller_rows(part)
        if actual != rows:
            raise ValueError('VSQX 音高控制事件回读不一致，未交付候选工程。')
        receipt['max_readback_error_cents'] = _verify_bends(runs, end,
            [{'pos': pos, 'value': value} for pos, name, value in actual if name == pit_id],
            next((value for _, name, value in actual if name == pbs_id), PBS_SPEC[0]))
    return {'policy': 'vocaloid-guarded-pit-pbs-v1', 'readback_verified': True, 'tracks': receipts}
