"""Turn reference performances into portable note-relative style recipes."""

import argparse
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from common import file_hash, native_path
from expression_controls import STYLE_CONTROL_NAMES
from preset_documents import (CONTRACTS, STYLE_PITCH_CAPABILITY, STYLE_PITCH_CONTRACT, STYLE_PITCH_MAX_CENTS,
                              STYLE_ACTION_BOUNDS, STYLE_NOTE_FIELDS, STYLE_PHRASE_FIELDS, STYLE_STRUCTURE_CAPABILITY,
                              STYLE_STRUCTURE_CONTRACT, active_style_action, encode_document,
                              read_regular_bytes, validate_document)
from style_catalog import VISIBLE_STYLES, style_color, style_description, style_label, technique_color
from style_pitch import (CONNECTION_FRACTION, CONNECTION_GAP, CONNECTION_PEAK, EXTERNAL_EDGE_SECONDS,
                         EXTERNAL_MIN_NOTE_SECONDS, EXTERNAL_PEAKS, connection_position, pitch_structure_seed)
from style_presets import (DETAIL_DEFAULTS, LONG_NOTE_SECONDS, PHRASE_GAP, PRESETS, RECIPE_CAPABILITY,
                           _curve, _note_weights, _phrase_weights,
                           freeze_recipe, recipe_has_pitch, structure_definition, time_range_settings)


PHASES = np.linspace(0, 1, 9)
EDGE_FRACTION = .35
MAX_REFERENCE_SECONDS = 120.
MAX_REFERENCE_NOTES = 128
MIN_DYNAMICS_SECONDS = .12
REFERENCE_POSITIONS = ('onset', 'release', 'repeated', 'rising', 'falling', 'sustain')
REFERENCE_CONNECTION_SECONDS = .10
_DYNAMICS = {'vpr': 'dynamics', 'vsqx': 'dynamics', 'ustx': 'dyn', 'acep': 'energy'}
_UNAUTHORED = object()


@dataclass(frozen=True)
class ReferenceNote:
    part: object
    source_index: int
    start: float
    end: float
    key: int
    lyric: str

    @property
    def duration(self):
        return self.end - self.start

    @property
    def identity(self):
        return self.start, self.end, self.key, self.lyric


def _score(path, track_index=None):
    from tuning_workflow import read_source, score_layout

    snapshot = read_source(path)
    score, layout = score_layout(snapshot.data, snapshot.format)
    tracks = [track for track in layout.tracks if track.instrumental is False and track.note_count]
    if not tracks:
        raise ValueError('参考工程中没有可读取的演唱轨。')
    index = tracks[0].index if track_index is None else track_index
    if type(index) is not int or not any(track.index == index for track in tracks):
        raise ValueError('请选择参考工程的演唱轨。')
    return snapshot, score, layout, tracks, index


def _notes(layout, index):
    from tuning_pitch_layer import _singing_lyric

    notes = []
    for placement in layout.tracks[index].placements:
        part = layout.native.parts[index, placement.key]
        for note in placement.notes:
            key = note.pitch
            if type(key) not in (int, float) or not 0 <= key <= 127 or key != int(key):
                continue
            lyric = note.data.get('lyrics', '')
            attr = note.data.get('attributes')
            singing = (isinstance(lyric, str) and _singing_lyric(lyric)
                       and note.data.get('musicalType') != 'rap'
                       and not (isinstance(attr, dict) and attr.get('muted') is True))
            start, end = (layout.clock.to_seconds(tick) for tick in (note.start, note.end))
            if start < end:
                notes.append((ReferenceNote(part, note.index, start, end, int(key), lyric), singing))
    return sorted(notes, key=lambda pair: (pair[0].start, pair[0].end))


def inspect_reference(path):
    snapshot, score, layout, tracks, _ = _score(path)
    choices = []
    for track in tracks:
        notes = [note for note, singing in _notes(layout, track.index) if singing]
        if not notes:
            continue
        first = notes[0].start
        # Start with a short phrase and let the author choose a wider range.
        end = max((note.end for note in notes if note.end <= first + 12), default=notes[0].end)
        choices.append({'index': track.index, 'name': score['tracks'][track.index].get('name') or str(track.index + 1),
                        'start': first, 'end': end, 'last': max(n.end for n in notes)})
    if not choices:
        raise ValueError('参考工程中没有可分析的演唱音符。')
    return {'path': str(snapshot.path), 'sha256': snapshot.sha256, 'format': snapshot.format, 'tracks': choices}


def _selection(rows, span):
    if not rows:
        raise ValueError('参考轨道没有可分析的音符。')
    start, end = time_range_settings(span or (rows[0][0].start, max(note.end for note, _ in rows)))
    if end - start > MAX_REFERENCE_SECONDS:
        raise ValueError('请选择不超过 120 秒的参考唱段。')
    overlaps = set()
    farthest = -1
    for i, (note, _) in enumerate(rows):
        if farthest >= 0 and note.start < rows[farthest][0].end - 1e-7:
            overlaps.update((i, farthest))
        if farthest < 0 or note.end > rows[farthest][0].end:
            farthest = i
    notes, excluded = [], {'overlap': 0, 'short': 0, 'non_vocal': 0, 'cut': 0}
    for i, (note, singing) in enumerate(rows):
        if note.end <= start + 1e-7 or note.start >= end - 1e-7:
            continue
        if note.start < start - .00051 or note.end > end + .00051:
            excluded['cut'] += 1
        elif not singing:
            excluded['non_vocal'] += 1
        elif i in overlaps:
            excluded['overlap'] += 1
        elif note.duration < MIN_DYNAMICS_SECONDS:
            excluded['short'] += 1
        else:
            notes.append(note)
    if not notes:
        raise ValueError('请选中至少一个完整、非重叠的演唱音符。')
    if len(notes) > MAX_REFERENCE_NOTES:
        raise ValueError('参考唱段最多分析 128 个音符，请缩小范围。')
    return notes, excluded


def _note_positions(rows, notes):
    """Classify selected notes with their real neighbors, even outside the selection."""
    positions = {note.identity: {} for note in notes}
    for i, (note, singing) in enumerate(rows):
        if not singing or note.identity not in positions:
            continue
        kinds = positions[note.identity]
        if note.duration + 1e-9 >= LONG_NOTE_SECONDS:
            kinds['sustain'] = None
        if note.duration < EXTERNAL_MIN_NOTE_SECONDS:
            continue
        previous = rows[i - 1][0] if i and rows[i - 1][1] else None
        following = rows[i + 1][0] if i + 1 < len(rows) and rows[i + 1][1] else None
        if previous is None or note.start - previous.end > PHRASE_GAP + 1e-9:
            kinds['onset'] = None
        elif (note.start - previous.end <= CONNECTION_GAP + 1e-9
              and previous.identity in positions and previous.duration >= EXTERNAL_MIN_NOTE_SECONDS):
            kinds[connection_position(note.key - previous.key)] = previous.identity
        if following is None or following.start - note.end > PHRASE_GAP + 1e-9:
            kinds['release'] = None
    return positions


def _native_gain(snapshot, layout, note, times, cache):
    from tuning_sources import _curve as native_curve

    fmt, part = snapshot.format, note.part.placement
    if fmt not in _DYNAMICS and fmt != 'svp':
        return None
    if note.part.path not in cache:
        if fmt == 'svp':
            params = note.part.raw.get('parameters', {})
            try:
                cache[note.part.path] = _curve(params.get('loudness'), channel='loudness') if isinstance(params, dict) else None
            except ValueError:
                cache[note.part.path] = None
        else:
            data = layout.sequence if fmt == 'vsqx' else snapshot.data
            cache[note.part.path] = native_curve(data, fmt, part, _DYNAMICS[fmt])[1]
    source = cache[note.part.path]
    if source is None:
        return None
    local = np.asarray([(layout.clock.to_position(float(t)) - part.offset) / layout.clock.step for t in times])
    if fmt == 'svp':
        xs, ys = source
        return np.interp(local, xs, ys) if xs else _UNAUTHORED
    if fmt in ('vpr', 'vsqx'):
        from vocaloid_pitch import step_values
        from vpr_tuning import CONTROLLERS, DYN

        if not source['events']:
            return _UNAUTHORED
        default = CONTROLLERS[DYN][0]
        values = step_values(local, source['events'], default)
        return (values - default) / 4. if np.all(values) else None
    if fmt == 'ustx':
        from ustx_tuning import CHANNELS, _native_samples

        if not source['xs']:
            return _UNAUTHORED
        values = _native_samples(np.rint(local).astype(int), source['xs'], source['ys'])
        return values / CHANNELS['dyn'][1] if np.all(values > CHANNELS['dyn'][2]) else None
    # ACE's VWA energy adapter uses a relative linear gain with neutral 1.
    gain = np.ones(len(local))
    authored = np.zeros(len(local), dtype=bool)
    for segment in source:
        indices = np.rint(local).astype(int) - segment['offset']
        active = (indices >= 0) & (indices < len(segment['values']))
        gain[active] = np.asarray(segment['values'], dtype=float)[indices[active]]
        authored |= active
    if not authored.any():
        return _UNAUTHORED
    return 20. * np.log10(gain) if np.all(np.isfinite(gain) & (gain > 0)) else None


def _pitch_samples(snapshot, layout, note, times, cache, *, include_default=False):
    from tuning_native_pitch import _collect, sample_authored_pitch

    part = note.part.placement
    if snapshot.format == 'svp' and part.ref.get('systemPitchDelta'):
        return None
    if snapshot.format == 'vpr' and note.part.notes[note.source_index].get('directPitches'):
        return None
    if note.part.path not in cache:
        data = layout.sequence if snapshot.format == 'vsqx' else snapshot.data
        if snapshot.format == 'vsqx':
            from tuning_native import at_path
            raw = at_path(data, note.part.path)
        else:
            raw = note.part.raw
        cache[note.part.path] = _collect(data, snapshot.format, raw)
    curves = cache[note.part.path]
    if not curves and include_default and snapshot.format == 'svp':
        params = note.part.raw.get('parameters', {})
        if (isinstance(params, dict) and not note.part.raw.get('pitchControls')
                and 'pitchDelta' not in params):
            return np.zeros(len(times))
    coords = np.asarray([(layout.clock.to_position(float(t)) - part.offset) / layout.clock.step for t in times])
    return sample_authored_pitch(curves, coords, note.key)


def _edge_times(note):
    width = min(EXTERNAL_EDGE_SECONDS, EDGE_FRACTION * note.duration)
    return np.asarray([note.start + EXTERNAL_PEAKS['onset'] * width,
                       note.end - (1 - EXTERNAL_PEAKS['release']) * width])


def _relative_pitch(note, sample, times):
    if note.duration < EXTERNAL_MIN_NOTE_SECONDS:
        return None
    core = note.start + note.duration * np.linspace(.42, .58, 5)
    values = sample(np.r_[times, core])
    if values is None or not np.isfinite(values).all() or np.ptp(values[-5:]) > 75:
        return None
    relative = values[:-5] - np.median(values[-5:])
    if np.any(abs(relative) > 250):
        return None
    return relative


def _measure_pitch(note, sample, kinds, edges, localized):
    identity = note.identity
    edge = _relative_pitch(note, sample, _edge_times(note))
    if edge is not None:
        edges[identity] = edge
    for kind in kinds:
        if kind == 'sustain':
            continue
        if kind in EXTERNAL_PEAKS:
            width = min(EXTERNAL_EDGE_SECONDS, EDGE_FRACTION * note.duration)
            peak = EXTERNAL_PEAKS[kind]
            instant = note.start + peak * width if kind == 'onset' else note.end - (1 - peak) * width
            relative = edge[0 if kind == 'onset' else 1] if edge is not None else None
        else:
            width = min(REFERENCE_CONNECTION_SECONDS, CONNECTION_FRACTION * note.duration)
            instant = note.start + CONNECTION_PEAK * width
            relative = None
        if relative is None:
            values = _relative_pitch(note, sample, [instant])
            if values is None:
                continue
            relative = values[0]
        localized.setdefault(identity, {})[kind] = (float(relative), width)


def _sustain_amount(note, values):
    # Fit only the long-note arc, with a free level and slope over its middle half.
    phases = PHASES[2:7]
    timing = {'onset': .14, 'release': .22, 'sustain_arc': {'loudness': 1.}}
    basis = _note_weights(phases * note.duration, [(0., note.duration)], (), (0., 0., 0.),
                          timing, {}, DETAIL_DEFAULTS, 'loudness')
    model = np.column_stack((np.ones(len(phases)), phases, basis))
    return float(np.linalg.lstsq(model, values[2:7], rcond=None)[0][-1])


def _position_summary(positions, notes, loudness, localized):
    counts = {kind: 0 for kind in REFERENCE_POSITIONS}
    samples_by_position = {kind: [] for kind in REFERENCE_POSITIONS}
    for note in notes:
        identity = note.identity
        for kind in positions[identity]:
            counts[kind] += 1
            if kind == 'sustain' and identity in loudness:
                samples_by_position[kind].append((_sustain_amount(note, loudness[identity]), note.duration))
            elif kind in localized.get(identity, {}):
                samples_by_position[kind].append(localized[identity][kind])
    rows = []
    for kind, count in counts.items():
        samples = samples_by_position[kind]
        result = {'id': kind, 'count': count, 'observed': len(samples), 'adoptable': False}
        if samples:
            amounts, widths = np.asarray(samples).T
            median = float(np.median(amounts))
            threshold = .30 if kind == 'sustain' else 4.
            agreement = int(np.count_nonzero(amounts * np.sign(median) >= threshold))
            result.update(median=round(median, 2), minimum=round(float(amounts.min()), 2),
                          maximum=round(float(amounts.max()), 2), seconds=float(np.median(widths)),
                          adoptable=abs(median) >= threshold and 3 * agreement >= 2 * len(samples))
        rows.append(result)
    return rows


def _dry_measurements(path, notes, offset, pitch, positions, localized, *, review_only=False, cancel, progress):
    import soundfile as sf
    from vocal_expression import energy_frames

    if type(offset) not in (int, float) or not math.isfinite(offset):
        raise ValueError('请输入干声 0 秒对应的工程时间。')
    info = sf.info(str(path))
    if info.samplerate <= 0 or info.frames <= 0 or info.format not in ('WAV', 'WAVEX', 'RF64'):
        raise ValueError('请选择可读取且包含有效采样的 WAV 干声。')
    duration = info.frames / info.samplerate
    start, end = min(n.start for n in notes) - offset, max(n.end for n in notes) - offset
    window = max(0., start - .12), min(duration, end + .12)
    times, db = np.empty(0), np.empty(0)
    if window[0] < window[1]:
        progress('正在核对干声的音符覆盖…')
        audio_times, db, _ = energy_frames(path, cancel, window=window)
        times = audio_times + offset
    inside = np.zeros(len(times), dtype=bool)
    for note in notes:
        inside |= (times >= note.start) & (times < note.end)
    gate = max(-85., float(np.percentile(db[inside], 90)) - 30.) if inside.any() else -85.
    usable = db > gate
    loudness, accepted, checked = {}, [], []
    for note in notes:
        cancel()
        lo, hi = np.searchsorted(times, (note.start, note.end))
        fraction = float(np.mean(usable[lo:hi])) if hi > lo else 0.
        entry = {'start': note.start, 'end': note.end, 'lyric': note.lyric,
                 'active_fraction': round(fraction, 3), 'state': 'covered'}
        checked.append(entry)
        # Permit a rounding error at the file end, while requiring the note's whole time span.
        if note.start < offset - .001 or note.end > offset + duration + .001:
            entry['state'] = 'outside_audio'
            continue
        if hi - lo < 4 or fraction < .55:
            entry['state'] = 'low_activity'
            continue
        core = (times[lo:hi] >= note.start + note.duration * .18) & (times[lo:hi] <= note.end - note.duration * .18)
        supported = db[lo:hi][core & usable[lo:hi]]
        if not len(supported):
            entry['state'] = 'low_activity'
            continue
        center = np.median(supported)
        # Use in-note edges instead of sampling adjacent notes or quiet gaps.
        curve = np.interp(note.start + PHASES * note.duration, times[lo:hi], db[lo:hi])
        loudness[note.identity] = curve - center
        accepted.append(note)
    adequate = len(accepted) >= math.ceil(len(notes) * .6)
    coverage = float(np.mean(usable[inside])) if inside.any() else 0.
    evidence = {'mode': 'manual_audio_zero', 'audio_zero_project_seconds': offset,
                'audio_duration_seconds': duration, 'samplerate': info.samplerate, 'channels': info.channels,
                'accepted_notes': len(accepted), 'excluded_notes': len(notes) - len(accepted),
                'in_note_signal_fraction': round(coverage, 3), 'adequate': adequate, 'notes': checked}
    if np.count_nonzero(~inside) >= 10:
        evidence['rest_signal_fraction'] = round(float(np.mean(usable[~inside])), 3)
    if review_only:
        return loudness, {}, evidence
    if not adequate:
        raise ValueError('干声与所选音符的有效重合不足，请调整对齐偏移或重选唱段。')
    if not pitch:
        return loudness, {}, evidence

    from calibration_workflow import _infer_pitch
    result = _infer_pitch(path, cancel, progress, window=window)
    grid = offset + window[0] + np.arange(len(result.midi_pitch)) * result.time_step_seconds
    midi = np.where(result.midi_pitch > 0., result.midi_pitch, np.nan)
    edges = {}
    for note in accepted:
        cancel()
        def sample(query):
            values = np.interp(query, grid, midi, left=np.nan, right=np.nan)
            if abs(np.median(values[-5:]) - note.key) > 1.8:
                return None
            return values * 100.
        kinds = (kind for kind, predecessor in positions[note.identity].items()
                 if predecessor is None or predecessor in loudness)
        _measure_pitch(note, sample, kinds, edges, localized)
    evidence['pitch_matched_notes'] = len(edges.keys() | localized.keys())
    return loudness, edges, evidence


def _comparison_notes(layout, index, prior_layout, prior_index, span):
    start, end = span
    left, right = layout.clock, prior_layout.clock
    boundaries = {start, end, *(t for clock in (left, right) for t in clock.seconds if start < t < end)}
    if any(abs(left.to_position(t) - right.to_position(t)) > 1 for t in boundaries):
        raise ValueError('两份工程在选区内的速度或播放时间轴不同。')

    def signature(geometry, track):
        return sorted((note.start, note.end, note.key, str(note.lyric), singing)
                      for note, singing in _notes(geometry, track) if note.start < end and note.end > start)

    first, second = signature(layout, index), signature(prior_layout, prior_index)
    if first != second:
        raise ValueError('两份工程在选区内的音符时间、音高、歌词或演唱状态不同。')
    return len(first)


def analyze_reference(path, track_index=None, span=None, *, audio=None, audio_zero=0., pitch=False,
                      before=None, before_track=None, expected_sha256=None, expected_before_sha256=None,
                      expected_audio_sha256=None, review_only=False, cancel=lambda: None, progress=lambda _s: None):
    """Read authored automation or measure a dry vocal against its aligned score."""
    cancel()
    progress('正在读取参考工程和演唱音符…')
    snapshot, _, layout, _, index = _score(path, track_index)
    if expected_sha256 is not None and snapshot.sha256 != expected_sha256:
        raise ValueError('参考工程已变化，请重新载入。')
    rows = _notes(layout, index)
    notes, excluded = _selection(rows, span)
    positions = _note_positions(rows, notes)
    localized = {}
    sources = {snapshot.path: snapshot.sha256}
    compared = None
    if audio and before:
        raise ValueError('请在对齐干声和原参数工程差值中选择一种。')
    if audio:
        audio = Path(audio).resolve()
        digest = file_hash(audio, check=cancel)
        if expected_audio_sha256 is not None and digest != expected_audio_sha256:
            raise ValueError('参考干声已变化，请重新选择并核对。')
        sources[audio] = digest
        loudness, edges, align = _dry_measurements(audio, notes, audio_zero, pitch, positions, localized,
                                                   review_only=review_only,
                                                   cancel=cancel, progress=progress)
        mode = 'aligned_dry_vocal'
    else:
        prior, prior_layout, previous = None, None, {}
        if before:
            prior, _, prior_layout, _, prior_index = _score(before, before_track)
            if expected_before_sha256 is not None and prior.sha256 != expected_before_sha256:
                raise ValueError('对照底稿已变化，请重新载入。')
            if prior.format != snapshot.format:
                raise ValueError('参考与对照底稿需要使用相同的工程格式。')
            compared = _comparison_notes(layout, index, prior_layout, prior_index,
                                         span or (notes[0].start, max(note.end for note in notes)))
            prior_notes, _ = _selection(_notes(prior_layout, prior_index), span)
            if [n.identity for n in notes] != [n.identity for n in prior_notes]:
                raise ValueError('两份工程的可提取音符范围不同。')
            previous = {note.identity: old for note, old in zip(notes, prior_notes)}
            sources[prior.path] = prior.sha256
        loudness, edges, align = {}, {}, None
        gain_cache, before_cache, pitch_cache, prior_pitch_cache = {}, {}, {}, {}
        for note in notes:
            cancel()
            times = note.start + PHASES * note.duration
            gain = _native_gain(snapshot, layout, note, times, gain_cache)
            old = previous.get(note.identity)
            baseline = _native_gain(prior, prior_layout, old, times, before_cache) if old else _UNAUTHORED
            if gain is not None and baseline is not None and (gain is not _UNAUTHORED or baseline is not _UNAUTHORED):
                loudness[note.identity] = ((0. if gain is _UNAUTHORED else gain)
                                           - (0. if baseline is _UNAUTHORED else baseline))
            if pitch:
                def sample(query):
                    values = _pitch_samples(snapshot, layout, note, query, pitch_cache, include_default=old is not None)
                    if values is None or old is None:
                        return values
                    base = _pitch_samples(prior, prior_layout, old, query, prior_pitch_cache, include_default=True)
                    return None if base is None else values - base
                _measure_pitch(note, sample, positions[note.identity], edges, localized)
        mode = 'native_difference' if before else 'authored_project'
    for source, sha in sources.items():
        if file_hash(source, check=cancel) != sha:
            raise ValueError('分析期间参考素材已改变，请重新提取。')
    points = []
    if loudness:
        values = np.median(list(loudness.values()), axis=0)
        points = [[float(x), round(float(y), 3)] for x, y in zip(PHASES, np.clip(values, -12, 12))]
    gestures = []
    if edges:
        values = np.clip(np.median(list(edges.values()), axis=0), -STYLE_PITCH_MAX_CENTS, STYLE_PITCH_MAX_CENTS)
        gestures = [{'kind': kind, 'cents': round(float(value), 2), 'fraction': EDGE_FRACTION}
                    for kind, value in zip(('onset', 'release'), values) if abs(value) >= 4]
    position_rows = [] if review_only else _position_summary(positions, notes, loudness, localized)
    pitch_count = len(edges.keys() | localized.keys())
    return {'points': points, 'gestures': gestures, 'positions': position_rows,
            'audio_sha256': sources[audio] if audio else '',
            'report': {
        'source': mode, 'format': snapshot.format, 'track_index': index, 'notes': len(notes),
        'range_seconds': [notes[0].start, max(note.end for note in notes)],
        'typical_note_seconds': float(np.median([note.duration for note in notes])),
        'excluded': excluded, 'loudness_notes': len(loudness), 'pitch_requested': pitch,
        'pitch_notes': pitch_count, 'alignment': align, 'comparison_notes': compared,
        'usable': align['adequate'] if align else bool(points or pitch_count)}}


def make_recipe(name, points, gestures=(), *, strength=.6, identifier=None):
    """Save just transferable parameters, with no local source material."""
    controls = [{'channel': 'loudness', 'operation': 'add', 'points': points}] if points else []
    actions = list(gestures)
    has_pitch = bool(actions)
    return validate_document({
        'format': 'vwa.preset', 'format_version': 1, 'id': identifier or 'user.' + uuid.uuid4().hex,
        'type': 'style_recipe', 'version': '1.0.0', 'metadata': {'name': name.strip()},
        'requires': {'preset_api': 1,
                     'engine_contract': STYLE_PITCH_CONTRACT if has_pitch else CONTRACTS['style_recipe'],
                     'capabilities': [RECIPE_CAPABILITY, *([STYLE_PITCH_CAPABILITY] if has_pitch else [])]},
        'binding_slots': [],
        'payload': {'role': 'style', 'coordinate_system': 'normalized_note_phase', 'default_strength': strength,
                    'controls': controls, 'pitch': {'enabled': has_pitch, 'gestures': actions,
                                                    **({'unit': 'cents'} if has_pitch else {})}},
    })


def structure_templates():
    return [{'id': name, 'label': style_label(name, card=True), 'description': style_description(name, card=True),
             'color': style_color(name) or technique_color(name)} for name in (*VISIBLE_STYLES, 'chinese_opera') if name != 'none']


_EMPTY_ACTIONS = {
    'onset': {'cents': 0., 'seconds': .12, 'peak': .22},
    'release': {'cents': 0., 'seconds': .20, 'peak': .72},
    'connection': {'cents_per_semitone': 0., 'limit': 32., 'seconds': .10,
                   'direction': 'settle', 'repeat_cents': 0., 'anticipate_descent': False},
    'repeated': {'cents': 0., 'seconds': REFERENCE_CONNECTION_SECONDS},
    'rising': {'cents': 0., 'seconds': REFERENCE_CONNECTION_SECONDS},
    'falling': {'cents': 0., 'seconds': REFERENCE_CONNECTION_SECONDS},
    'vibrato': {'depth': 0., 'rate': 5.3, 'delay': .30, 'minimum_note': .85},
}


def structure_draft(template='none', document=None):
    """Start from the actual built-in recipe or detach a library copy for editing."""
    source = PRESETS[template]
    envelope = source.get('note_envelope', {'onset': .14, 'release': .22, 'sustain_arc': {}})
    controls = {}
    for channel in STYLE_CONTROL_NAMES:
        onset, sustain, release = source['controls'].get(channel, (0., 0., 0.))
        controls[channel] = {
            'note': dict(zip(STYLE_NOTE_FIELDS, (onset, sustain, release, envelope['sustain_arc'].get(channel, 0.)))),
            'phrase': {key: source.get('phrase_envelope', {}).get(channel, {}).get(key, 0.)
                       for key in STYLE_PHRASE_FIELDS},
        }
    result = {'timing': {key: envelope[key] for key in ('onset', 'release')}, 'controls': controls,
              'pitch': {**deepcopy(_EMPTY_ACTIONS), **pitch_structure_seed(template)}, 'strength': .6,
              'color': style_color(template) or technique_color(template)}
    if document is not None:
        payload = document['payload']
        result.update(timing=dict(payload['timing']), strength=payload['default_strength'],
                      color=document['metadata'].get('color'))
        for row in payload['controls']:
            controls[row['channel']] = {'note': dict(row['note']), 'phrase': dict(row['phrase'])}
        result['pitch'].update({key: dict(value) for key, value in payload['pitch'].items() if key != 'enabled'})
    return result


def structure_contents(draft):
    controls = [{'channel': channel, **row} for channel, row in draft['controls'].items()
                if any((*row['note'].values(), *row['phrase'].values()))]
    actions = {kind: action for kind, action in draft['pitch'].items() if active_style_action(kind, action)}
    return controls, actions


def make_structure_recipe(name, draft, *, identifier=None):
    controls, actions = structure_contents(draft)
    metadata = {'name': name.strip()}
    if draft['color']:
        metadata['color'] = draft['color']
    return validate_document({
        'format': 'vwa.preset', 'format_version': 1, 'id': identifier or 'user.' + uuid.uuid4().hex,
        'type': 'style_recipe', 'version': '1.0.0', 'metadata': metadata,
        'requires': {'preset_api': 1, 'engine_contract': STYLE_STRUCTURE_CONTRACT,
                     'capabilities': [STYLE_STRUCTURE_CAPABILITY]},
        'binding_slots': [],
        'payload': {'role': 'style', 'coordinate_system': 'note_and_phrase',
                    'timing': draft['timing'], 'controls': controls, 'default_strength': draft['strength'],
                    'pitch': {'enabled': bool(actions), **actions}},
    })


def structure_preview(draft, channel):
    """Illustrate the executable envelopes on independent note and phrase axes."""
    row = {'channel': channel, **draft['controls'][channel]}
    definition = structure_definition(draft['timing'], [row])
    steps = np.linspace(0., 1., 81)
    guide = draft.get('loudness_guide') if channel == 'loudness' else None
    duration = guide['seconds'] if guide else 1.2
    note = _note_weights(steps * duration, [(0., duration)], (), definition['controls'][channel],
                         definition['note_envelope'], {}, DETAIL_DEFAULTS, channel)
    phrase = _phrase_weights(steps * 3., (0., 3.), channel, definition['phrase_envelope'], DETAIL_DEFAULTS)
    strength = draft['strength']
    result = {'note': (note * strength).tolist(), 'phrase': (phrase * strength).tolist(),
              'note_seconds': duration}
    if guide:
        phases, values = np.asarray(guide['points']).T
        result['guide'] = (np.interp(steps, phases, values) * strength).tolist()
        estimate = _note_weights(phases * duration, [(0., duration)], (), definition['controls'][channel],
                                 definition['note_envelope'], {}, DETAIL_DEFAULTS, channel)
        result['guide_rmse'] = float(np.sqrt(np.mean((estimate - values)**2)))
    return result


def adopt_reference_part(draft, measured, part):
    """Take one measured layer into a fresh recipe, preserving the other design choices."""
    duration = max(EXTERNAL_MIN_NOTE_SECONDS, measured['report']['typical_note_seconds'])
    if part == 'loudness' and measured['points']:
        points = measured['points']
        phases, values = np.asarray(points).T
        times = phases * duration
        basis = []
        for active in STYLE_NOTE_FIELDS:
            anchors = {key: float(key == active) for key in STYLE_NOTE_FIELDS}
            definition = structure_definition(draft['timing'], [{
                'channel': 'loudness', 'note': anchors, 'phrase': {},
            }])
            basis.append(_note_weights(times, [(0., duration)], (), definition['controls']['loudness'],
                                       definition['note_envelope'], {}, DETAIL_DEFAULTS, 'loudness'))
        # A small ridge keeps poorly observed attack/release coefficients stable.
        model = np.vstack((np.asarray(basis).T, .18 * np.eye(len(basis))))
        estimate = np.linalg.lstsq(model, np.append(values, np.zeros(len(basis))), rcond=None)[0]
        draft['controls']['loudness']['note'] = {
            key: round(float(value), 3) for key, value in zip(STYLE_NOTE_FIELDS, np.clip(estimate, -12., 12.))
        }
        draft['loudness_guide'] = {'points': deepcopy(points), 'seconds': duration}
        return
    if part == 'pitch' and measured['gestures']:
        for gesture in measured['gestures']:
            kind = gesture['kind']
            draft['pitch'][kind].update(cents=gesture['cents'], peak=EXTERNAL_PEAKS[kind],
                                       seconds=max(.025, min(EXTERNAL_EDGE_SECONDS, duration * gesture['fraction'])))
        return
    raise ValueError('请先取得这一项可用的参考数据。')


def adopt_reference_position(draft, measured, position):
    row = next((item for item in measured['positions'] if item['id'] == position and item['adoptable']), None)
    if row is None:
        raise ValueError('请先取得这一项可用的参考数据。')
    if position == 'sustain':
        draft['controls']['loudness']['note']['arc'] = float(np.clip(row['median'], -12., 12.))
        return
    bounds = STYLE_ACTION_BOUNDS[position]
    action = draft['pitch'][position]
    action.update(cents=float(np.clip(row['median'], *bounds['cents'])),
                  seconds=float(np.clip(row['seconds'], *bounds['seconds'])))
    if position in EXTERNAL_PEAKS:
        action['peak'] = EXTERNAL_PEAKS[position]


def brush_for_recipe(recipe, strength=None):
    if strength is None:
        strength = recipe['document']['payload']['default_strength']
    return {'preset': 'none', 'strength': strength, 'pitch': recipe_has_pitch(recipe), 'style_recipe': recipe}


def main():
    import json
    from tuning_timeline import ProjectNotes
    from tuning_workflow import apply_snapshot, read_source

    parser = argparse.ArgumentParser(prog='vwa-devtools')
    commands = parser.add_subparsers(dest='command', required=True)
    extract = commands.add_parser('extract')
    extract.add_argument('project', type=Path)
    extract.add_argument('output', type=Path)
    extract.add_argument('--track', type=int)
    extract.add_argument('--range', type=float, nargs=2, metavar=('START', 'END'))
    extract.add_argument('--name', required=True)
    extract.add_argument('--audio', type=Path)
    extract.add_argument('--audio-zero', type=float, default=0.)
    extract.add_argument('--before', type=Path)
    extract.add_argument('--before-track', type=int)
    extract.add_argument('--pitch', action='store_true')
    extract.add_argument('--position', choices=REFERENCE_POSITIONS, action='append',
                         help='Adopt the specified reference position into a structural recipe; may be repeated.')
    preview = commands.add_parser('preview')
    preview.add_argument('project', type=Path)
    preview.add_argument('recipe', type=Path)
    preview.add_argument('--track', type=int, required=True)
    preview.add_argument('--range', type=float, nargs=2, metavar=('START', 'END'), required=True)
    preview.add_argument('--strength', type=float)
    args = parser.parse_args()
    try:
        if args.command == 'extract':
            value = analyze_reference(args.project, args.track, args.range, audio=args.audio,
                                      audio_zero=args.audio_zero, pitch=args.pitch, before=args.before,
                                      before_track=args.before_track, progress=lambda s: print(s, file=sys.stderr, flush=True))
            if args.position:
                draft = structure_draft()
                for position in args.position:
                    adopt_reference_position(draft, value, position)
                document = make_structure_recipe(args.name, draft)
            elif value['points'] or value['gestures']:
                document = make_recipe(args.name, value['points'], value['gestures'])
            else:
                raise ValueError('整体包络暂无可用变化。可用 --position 选取按位置提取的结果。')
            with open(native_path(args.output), 'xb') as stream:
                stream.write(encode_document(document))
            payload = {**value['report'], 'positions': value['positions']}
        else:
            recipe = freeze_recipe(read_regular_bytes(args.recipe))
            project = ProjectNotes.load(args.project)
            track = next((key for key in project.editable if project.tracks[key]['index'] == args.track), '')
            region = project.region(track, *args.range, brush_for_recipe(recipe, args.strength))
            if region is None:
                raise ValueError('目标选区没有可试套的音符。')
            snapshot = read_source(args.project)
            if snapshot.sha256 != project.sha256:
                raise ValueError('目标工程已变化，请重新载入。')
            _, payload = apply_snapshot(snapshot, {'track_indices': [args.track], 'style_regions': [region['recipe']],
                                                  'expression_density': 'original'},
                                        cancel=lambda: None, progress=lambda _s: None)
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
