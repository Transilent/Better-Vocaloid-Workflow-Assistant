"""Export real VSQX files and check what the editor receives at each boundary."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import atomic_json, file_hash
from pitch_accuracy import absolute_curve, save_pitch
from project_exports import export_job, library, outputs_intact


def ustx(path, notes, *, authored=False, beats=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, {
        'tracks': [{'track_name': path.stem}],
        'time_signatures': [{'bar_position': 0, 'beat_per_bar': beats, 'beat_unit': 4}],
        'tempos': [{'position': 0, 'bpm': 120}, {'position': 1344, 'bpm': 90}],
        'voice_parts': [{'name': path.stem, 'track_no': 0, 'position': 0,
            'notes': [{'position': start, 'duration': duration, 'tone': key, 'lyric': lyric,
                       'vibrato': {'length': 0}} for start, duration, key, lyric in notes],
            'curves': [{'abbr': 'pitd', 'xs': [500, 610, 720], 'ys': [50, 90, 60]}] if authored else []}],
    })


def controls(path):
    root = ET.parse(path).getroot()
    tag = root.tag.split('}')[0] + '}'
    return [[(int(row.findtext(tag + 't')), row.find(tag + 'v').get('id'),
              int(row.findtext(tag + 'v'))) for row in track.findall(f'{tag}vsPart/{tag}cc')]
            for track in root.findall(tag + 'vsTrack')]


def native_cents(rows, ticks):
    """Independent playback of VSQX step controls (defaults P=0, S=2)."""
    def held(name, default):
        events = sorted((x, y) for x, kind, y in rows if kind == name)
        xs, ys = zip(*events) if events else ((), ())
        return np.asarray((default, *ys))[np.searchsorted(xs, ticks, side='right')]
    bend, sensitivity = held('P', 0), held('S', 2)
    return bend * sensitivity * 100 / np.where(bend < 0, 8192, 8191)


def capture(job, voice, f0, native):
    sidecar = native.with_suffix('.pitch.npz')
    measure = SimpleNamespace(midi_pitch=f0, voiced_mask=np.isfinite(f0), time_step_seconds=.01)
    save_pitch(sidecar, measure)
    receipt = {'pitch_source': 'absolute_rmvpe', 'native_sha256': {'ustx': file_hash(native)},
               'output_sha256': {'pitch.npz': file_hash(sidecar)}}
    atomic_json(job / f'midi/{voice}/report.json', receipt)
    return measure, sidecar, receipt


def playback_tick(seconds):
    return round(seconds * 960) if seconds < 1.4 else round(1344 + (seconds - 1.4) * 720)


(APP / 'work').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='vsqx-pitch-') as temp:
    job = Path(temp)
    lead, backing = (job / f'midi/{v}/{v}.ustx' for v in ('lead', 'backing'))
    # The first note ends between F0 frames and its synthesized tail guard.
    notes = [(480, 475, 60, '你'), (1152, 840, 72, '好')]
    ustx(lead, notes)
    ustx(backing, [(480, 475, 67, '啦')], authored=True)
    f0 = np.full(260, np.nan, dtype=np.float32)
    f0[50:100] = 64
    f0[50:54] = [61, 62, 63, 64]  # Intentional attack.
    f0[75] = 70  # Isolated error; the sustained +4 semitone offset is left alone.
    f0[120:230] = 72 + .28 * np.sin(np.arange(110) * .3)
    f0[151:156] = np.nan
    measure, sidecar, receipt = capture(job, 'lead', f0, lead)
    raw_hash = file_hash(sidecar)
    request = {'voice_mode': 'dual', 'language': 'zh', 'project_formats': ['vsqx'],
               'audio_formats': [], 'export_pitch': True, 'include_instrumental': False}

    converters, _, _, _ = library()
    # Isolate the old bend writer with the same corrected source and correct
    # bar clock, so a failing gap cannot be attributed to tempo drift.
    from libresvip.core.tick_counter import skip_tempo_list, shift_tempo_list
    draft = converters['ustx'].load(lead, {'import_pitch': False})
    draft.song_tempo_list = skip_tempo_list(draft.song_tempo_list, 1920)
    absolute_curve(draft.track_list[0], draft, measure)
    # LibreSVIP's generic writer uses the first bar's meter for the curve
    # offset; the shared F0 model always uses four beats.
    curve = draft.track_list[0].edited_params.pitch.points
    curve.root = [point._replace(x=point.x - 480) if 0 <= point.x < 1073741823 else point
                  for point in curve.root]
    draft.song_tempo_list = shift_tempo_list(draft.song_tempo_list, 2 * 1440)
    old = job / 'generic.vsqx'
    converters['vsqx'].dump(old, draft, {})
    old_rows = controls(old)[0]
    assert abs(native_cents(old_rows, [1000])[0]) > 100, 'generic writer no longer reproduces a held bend in the rest'

    # Exercise the same export transaction and cache used by Preparation.
    report = export_job(job, request)['projects']
    output = job / 'exports/voices.vsqx'
    protected, authored = controls(output)
    assert all(0 <= tick < 1992 for tick, _, _ in protected)
    assert [(tick, value) for tick, kind, value in protected if kind == 'S'] == [(0, 4)]
    assert np.all(native_cents(protected, np.arange(955, 1152)) == 0), 'PIT carried through a rest'
    assert np.all(native_cents(protected, np.arange(playback_tick(1.52), playback_tick(1.55))) == 0)
    assert np.allclose(native_cents(protected, [480, 509, 720, 900]), [100, 400, 400, 400], atol=2.2)
    post_tempo = np.arange(164, 218)
    bend = native_cents(protected, [playback_tick(x / 100) for x in post_tempo])
    assert np.max(np.abs(bend - (f0[post_tempo] - 72) * 100)) < 2.7
    assert np.ptp(bend) > 50, 'the real vibrato was flattened'
    policy = report['outputs'][0]['pitch_protection']
    assert policy['readback_verified'] and [row['vocal_track_index'] for row in policy['tracks']] == [0]
    assert policy['tracks'][0]['max_readback_error_cents'] <= 2.1
    assert report['tracks'][0]['pitch_source']['isolated_spike_policy']['corrected_frames'] == 1
    assert report['tracks'][1]['pitch_source'] is None and any(y for _, k, y in authored if k == 'P')
    loaded = converters['vsqx'].load(output, {'import_pitch': True, 'import_instrumental_track': False})
    assert [(t.position, t.bpm) for t in loaded.song_tempo_list] == [(0, 120), (1344, 90)]
    assert loaded.time_signature_list[0].numerator == 3
    assert [(n.start_pos, n.length, n.key_number, n.lyric) for n in loaded.track_list[0].note_list] == notes
    assert file_hash(sidecar) == raw_hash and file_hash(lead) == receipt['native_sha256']['ustx']
    assert outputs_intact(job, request)
    original = output.read_bytes()
    with patch('project_exports.library', side_effect=AssertionError('verified output was rebuilt')):
        assert export_job(job, request)['projects']['outputs'][0]['pitch_protection'] == policy

    # A failed final validation cannot replace or register the prior output.
    atomic_json(job / 'midi/lead/report.json', {**receipt, 'regenerate': True})
    assert not outputs_intact(job, request)
    with patch('vocaloid_pitch._verify_bends', side_effect=ValueError('simulated native readback failure')):
        try:
            export_job(job, request)
        except ValueError as error:
            assert 'native readback failure' in str(error)
        else:
            raise AssertionError('unsafe candidate was accepted')
    assert output.read_bytes() == original and not (job / 'exports/voices.pending.vsqx').exists()

    # Turn the other input into another measured track with no usable F0.
    capture(job, 'backing', np.full(260, np.nan), backing)
    second = export_job(job, request)['projects']['outputs'][0]['pitch_protection']
    assert second['tracks'][1]['status'] == 'neutral' and not controls(output)[1]
    export_job(job, {**request, 'export_pitch': False})
    assert not any(kind in ('P', 'S') for track in controls(output) for _, kind, _ in track)

    # Older verified tasks have only the generated USTX curve. They still
    # get rest protection, without claiming measured unvoiced evidence.
    ustx(lead, notes, authored=True)
    sidecar.unlink()
    atomic_json(job / 'midi/lead/report.json', {'pitch_curve_requested': True,
                'native_sha256': {'ustx': file_hash(lead)}})
    legacy = export_job(job, request)['projects']['outputs'][0]['pitch_protection']['tracks'][0]
    assert legacy['source'] == 'generated_ustx' and legacy['status'] == 'protected'
    assert np.all(native_cents(controls(output)[0], np.arange(955, 1152)) == 0)

    # A sustained source outside the format's 24-semitone range must stay neutral.
    ustx(lead, [*notes, (1992, 300, 74, '啦')])
    ambiguous = f0.copy()
    ambiguous[160:230] = 104
    ambiguous[230:260] = 74.5
    capture(job, 'lead', ambiguous, lead)
    limited = export_job(job, request)['projects']['outputs'][0]['pitch_protection']['tracks'][0]
    assert limited['status'] == 'partial' and limited['protected_overlapping_notes'] == 0
    assert limited['unrepresentable_ticks'] > 100 and limited['protected_unrepresentable_notes'] == 1
    limited_rows = controls(output)[0]
    assert np.all(native_cents(limited_rows, [1170, 1800, 1988]) == 0)
    assert abs(native_cents(limited_rows, [750])[0] - 400) < 2.2
    assert abs(native_cents(limited_rows, [2140])[0] - 50) < 2.2

    # The USTX reader rejects overlaps. Exercise the native writer directly
    # with a part containing overlaps to check that its guard is independent.
    from libresvip.model.base import Note, ParamCurve
    from vocaloid_pitch import write_vsqx_pitch
    overlap = converters['ustx'].load(lead, {'import_pitch': False})
    overlap.song_tempo_list = skip_tempo_list(overlap.song_tempo_list, 1920)
    track = overlap.track_list[0]
    track.note_list.insert(1, Note(start_pos=700, length=200, key_number=62, lyric='啊'))
    model = overlap.model_copy(deep=True)
    model.song_tempo_list = shift_tempo_list(overlap.song_tempo_list, 2 * 1440)
    model.track_list[0].edited_params.pitch = ParamCurve()
    path = job / 'overlap.vsqx'
    converters['vsqx'].dump(path, model, {})
    absolute_curve(track, overlap, measure)
    protected_overlap = write_vsqx_pitch(path, overlap, {0: measure}, cancel=lambda: None)
    assert protected_overlap['tracks'][0]['protected_overlapping_notes'] == 2
    overlap_rows = controls(path)[0]
    assert np.all(native_cents(overlap_rows, np.arange(480, 955)) == 0)
    assert np.ptp(native_cents(overlap_rows, np.arange(1500, 1900))) > 50
    print(f"PASS: generic rest {native_cents(old_rows, [1000])[0]:.1f} cents -> 0; "
          f"protected PIT readback error <= {policy['tracks'][0]['max_readback_error_cents']:.3f} cents")
