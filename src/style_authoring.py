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
                              STYLE_NOTE_FIELDS, STYLE_PHRASE_FIELDS, STYLE_STRUCTURE_CAPABILITY,
                              STYLE_STRUCTURE_CONTRACT, active_style_action, encode_document,
                              read_regular_bytes, validate_document)
from style_catalog import VISIBLE_STYLES, style_color, style_description, style_label, technique_color
from style_pitch import EXTERNAL_EDGE_SECONDS, EXTERNAL_MIN_NOTE_SECONDS, EXTERNAL_PEAKS, pitch_structure_seed
from style_presets import (DETAIL_DEFAULTS, PRESETS, RECIPE_CAPABILITY, _curve, _note_weights, _phrase_weights,
                           freeze_recipe, recipe_has_pitch, structure_definition, time_range_settings)


PHASES = np.linspace(0, 1, 9)
EDGE_FRACTION = .35
MAX_REFERENCE_SECONDS = 120.
MAX_REFERENCE_NOTES = 128
MIN_DYNAMICS_SECONDS = .12
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


def _selection(layout, index, span):
    rows = _notes(layout, index)
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


def _edge_shape(note, sample):
    if note.duration < EXTERNAL_MIN_NOTE_SECONDS:
        return None
    core = note.start + note.duration * np.linspace(.42, .58, 5)
    values = sample(np.r_[_edge_times(note), core])
    if values is None or not np.isfinite(values).all() or np.ptp(values[2:]) > 75:
        return None
    relative = values[:2] - np.median(values[2:])
    if np.any(abs(relative) > 250):
        return None
    return relative


def _dry_measurements(path, notes, offset, pitch, *, cancel, progress):
    from vocal_expression import energy_frames

    if type(offset) not in (int, float) or not math.isfinite(offset):
        raise ValueError('请输入干声 0 秒对应的工程时间。')
    start, end = min(n.start for n in notes) - offset, max(n.end for n in notes) - offset
    if end <= 0:
        raise ValueError('参考唱段在干声起点之前，请核对对齐偏移。')
    window = max(0., start - .12), end + .12
    progress('正在测量对齐干声的力度…')
    audio_times, db, metadata = energy_frames(path, cancel, window=window)
    times = audio_times + offset
    inside = np.zeros(len(times), dtype=bool)
    for note in notes:
        inside |= (times >= note.start) & (times < note.end)
    if not inside.any():
        raise ValueError('参考唱段未落在干声中，请核对对齐偏移。')
    gate = max(-85., float(np.percentile(db[inside], 90)) - 30.)
    usable = db > gate
    loudness, accepted = [], []
    for note in notes:
        lo, hi = np.searchsorted(times, (note.start, note.end))
        # Permit a rounding error at the file end, while requiring the note's whole time span.
        if (note.start < offset - .001 or note.end > offset + metadata['duration_seconds'] + .001
                or hi - lo < 4 or np.mean(usable[lo:hi]) < .55):
            continue
        core = (times[lo:hi] >= note.start + note.duration * .18) & (times[lo:hi] <= note.end - note.duration * .18)
        supported = db[lo:hi][core & usable[lo:hi]]
        if not len(supported):
            continue
        center = np.median(supported)
        # Use in-note edges instead of sampling adjacent notes or quiet gaps.
        curve = np.interp(note.start + PHASES * note.duration, times[lo:hi], db[lo:hi])
        loudness.append(curve - center)
        accepted.append(note)
    if not accepted or len(accepted) < math.ceil(len(notes) * .6):
        raise ValueError('干声与所选音符的有效重合不足，请调整对齐偏移或重选唱段。')
    coverage = float(np.mean(usable[inside]))
    evidence = {'mode': 'manual_audio_zero', 'audio_zero_project_seconds': offset,
                'accepted_notes': len(accepted), 'excluded_notes': len(notes) - len(accepted),
                'in_note_signal_fraction': round(coverage, 3)}
    if np.count_nonzero(~inside) >= 10:
        evidence['rest_signal_fraction'] = round(float(np.mean(usable[~inside])), 3)
    if not pitch:
        return loudness, [], evidence

    from calibration_workflow import _infer_pitch
    result = _infer_pitch(path, cancel, progress, window=window)
    grid = offset + window[0] + np.arange(len(result.midi_pitch)) * result.time_step_seconds
    midi = result.midi_pitch
    edges = []
    for note in accepted:
        cancel()
        def sample(query):
            values = np.interp(query, grid, midi, left=np.nan, right=np.nan)
            if abs(np.median(values[2:]) - note.key) > 1.8:
                return None
            return values * 100.
        if (edge := _edge_shape(note, sample)) is not None:
            edges.append(edge)
    evidence['pitch_matched_notes'] = len(edges)
    return loudness, edges, evidence


def analyze_reference(path, track_index=None, span=None, *, audio=None, audio_zero=0., pitch=False,
                      before=None, before_track=None, expected_sha256=None, expected_before_sha256=None,
                      cancel=lambda: None, progress=lambda _s: None):
    """Read authored automation or measure a dry vocal against its aligned score."""
    cancel()
    progress('正在读取参考工程和演唱音符…')
    snapshot, _, layout, _, index = _score(path, track_index)
    if expected_sha256 is not None and snapshot.sha256 != expected_sha256:
        raise ValueError('参考工程已变化，请重新载入。')
    notes, excluded = _selection(layout, index, span)
    sources = {snapshot.path: snapshot.sha256}
    if audio and before:
        raise ValueError('请在对齐干声和原参数工程差值中选择一种。')
    if audio:
        audio = Path(audio).resolve()
        sources[audio] = file_hash(audio, check=cancel)
        loudness, edges, align = _dry_measurements(audio, notes, audio_zero, pitch, cancel=cancel, progress=progress)
        mode = 'aligned_dry_vocal'
    else:
        prior, prior_layout, previous = None, None, {}
        if before:
            prior, _, prior_layout, _, prior_index = _score(before, before_track)
            if expected_before_sha256 is not None and prior.sha256 != expected_before_sha256:
                raise ValueError('对照底稿已变化，请重新载入。')
            prior_notes, _ = _selection(prior_layout, prior_index, span)
            if prior.format != snapshot.format or [n.identity for n in notes] != [n.identity for n in prior_notes]:
                raise ValueError('原参数工程需要同格式、同音符和同一播放时间轴。')
            previous = {note.identity: old for note, old in zip(notes, prior_notes)}
            sources[prior.path] = prior.sha256
        loudness, edges, align = [], [], None
        gain_cache, before_cache, pitch_cache, prior_pitch_cache = {}, {}, {}, {}
        for note in notes:
            cancel()
            times = note.start + PHASES * note.duration
            gain = _native_gain(snapshot, layout, note, times, gain_cache)
            old = previous.get(note.identity)
            baseline = _native_gain(prior, prior_layout, old, times, before_cache) if old else _UNAUTHORED
            if gain is not None and baseline is not None and (gain is not _UNAUTHORED or baseline is not _UNAUTHORED):
                loudness.append((0. if gain is _UNAUTHORED else gain) - (0. if baseline is _UNAUTHORED else baseline))
            if pitch:
                def sample(query):
                    values = _pitch_samples(snapshot, layout, note, query, pitch_cache, include_default=old is not None)
                    if values is None or old is None:
                        return values
                    base = _pitch_samples(prior, prior_layout, old, query, prior_pitch_cache, include_default=True)
                    return None if base is None else values - base
                if (edge := _edge_shape(note, sample)) is not None:
                    edges.append(edge)
        mode = 'native_difference' if before else 'authored_project'
    for source, sha in sources.items():
        if file_hash(source, check=cancel) != sha:
            raise ValueError('分析期间参考素材已改变，请重新提取。')
    points = []
    if loudness:
        values = np.median(loudness, axis=0)
        points = [[float(x), round(float(y), 3)] for x, y in zip(PHASES, np.clip(values, -12, 12))]
    gestures = []
    if edges:
        values = np.clip(np.median(edges, axis=0), -STYLE_PITCH_MAX_CENTS, STYLE_PITCH_MAX_CENTS)
        gestures = [{'kind': kind, 'cents': round(float(value), 2), 'fraction': EDGE_FRACTION}
                    for kind, value in zip(('onset', 'release'), values) if abs(value) >= 4]
    if not points and not gestures:
        raise ValueError('所选唱段没有可用的力度或受限音高动作。')
    return {'points': points, 'gestures': gestures, 'report': {
        'source': mode, 'format': snapshot.format, 'track_index': index, 'notes': len(notes),
        'range_seconds': [notes[0].start, max(note.end for note in notes)],
        'typical_note_seconds': float(np.median([note.duration for note in notes])),
        'excluded': excluded, 'loudness_notes': len(loudness), 'pitch_requested': pitch,
        'pitch_notes': len(edges), 'alignment': align}}


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
            document = make_recipe(args.name, value['points'], value['gestures'])
            with open(native_path(args.output), 'xb') as stream:
                stream.write(encode_document(document))
            payload = value['report']
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
