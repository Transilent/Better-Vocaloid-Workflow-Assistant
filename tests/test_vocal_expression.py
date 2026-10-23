"""Measured dynamics, timeline conversion and real SVP export/cache integration."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import numpy as np
import soundfile as sf
from vocal_expression import analyze, apply_svp, energy_frames, settings, simplify, TempoMap, SV_QUARTER


def raises(exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except exception:
        return
    raise AssertionError(f'Expected {exception.__name__}')


def value_at(report, seconds):
    points = np.asarray(report['points'])
    return float(np.interp(seconds, points[:, 0], points[:, 1]))


assert settings({}) == (False, .6)
for strength in (True, float('nan'), float('inf'), -.1, 1.1, '0.6'):
    raises(ValueError, settings, {'dynamics_strength': strength})
raises(ValueError, settings, {'reference_dynamics': 'true'})
clock = TempoMap([{'position': 0, 'bpm': 120}, {'position': 4 * SV_QUARTER, 'bpm': 60}])
assert clock.to_seconds(4 * SV_QUARTER) == 2
assert clock.to_seconds(6 * SV_QUARTER) == 4
for seconds in np.linspace(0, 12, 125):
    assert abs(clock.to_seconds(clock.to_position(float(seconds))) - seconds) < 1e-8
for tempos in ([], [{'position': 1, 'bpm': 120}], [{'position': 0, 'bpm': 0}],
               [{'position': 0, 'bpm': 120}, {'position': 0, 'bpm': 60}]):
    raises(ValueError, TempoMap, tempos)

with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='reference-dynamics-') as folder:
    job = Path(folder)
    rate = 44100
    times = np.arange(rate * 12) / rate
    amplitude_db = np.full(len(times), -120.)
    for start, end, low, high in [(1., 4., -23., -11.), (4.5, 7., -16., -20.), (9., 11., -13., -29.)]:
        selected = (times >= start) & (times < end)
        amplitude_db[selected] = np.linspace(low, high, selected.sum())
    wave = (10 ** (amplitude_db / 20) * np.sin(times * 2 * np.pi * 220)).astype('float32')
    source = job / '参考人声.wav'
    sf.write(source, wave, rate, subtype='FLOAT')
    intervals = [(1., 4.), (4.5, 7.), (9., 11.)]
    result = analyze(source, intervals)
    assert result['status'] == 'applied' and result['reliable_fraction'] > .95
    assert result['point_count'] < result['frames_analyzed'] / 4, result['point_count']
    assert value_at(result, 3.5) - value_at(result, 1.5) > 4
    assert value_at(result, 9.5) - value_at(result, 10.7) > 4
    assert abs(value_at(result, 8.)) < .001, 'No neutral reset in the phrase gap'
    assert -.001 <= result['points'][0][0] < 1
    assert -.001 <= result['points'][-1][1] <= .001
    assert all(a[0] < b[0] for a, b in zip(result['points'], result['points'][1:]))
    assert min(row[1] for row in result['points']) >= -7.201
    assert max(row[1] for row in result['points']) <= 3.601

    # Gain and DC offsets must not turn into a different expression envelope;
    # averaging opposite-polarity channels must not erase a valid vocal.
    stereo = job / 'anti-phase.wav'
    sf.write(stereo, np.column_stack([wave * 3 + .02, -wave * 3 - .04]), rate, subtype='FLOAT')
    other = analyze(stereo, intervals)
    assert other['status'] == 'applied'
    assert abs(other['baseline_db'] - result['baseline_db'] - 20 * math.log10(3)) < .01
    for seconds in np.linspace(1.2, 10.8, 70):
        assert abs(value_at(result, seconds) - value_at(other, seconds)) < .02

    # Check the streaming batch boundary against independent sample-domain RMS.
    frame_times, db, info = energy_frames(source)
    half = round(rate * .06 / 2)
    for seconds in (1.5, 6., 9.94, 9.98, 10., 10.02, 10.08):
        index = int(np.argmin(abs(frame_times - seconds)))
        center = round(frame_times[index] * rate)
        samples = wave[max(0, center - half):min(len(wave), center + half + 1)].astype('float64')
        expected = 10 * np.log10(np.mean((samples - samples.mean()) ** 2))
        assert abs(expected - db[index]) < 1e-6, (seconds, expected, db[index])

    pure = job / 'constant.wav'
    sf.write(pure, .2 * np.sin(times * 2 * np.pi * 220), rate, subtype='FLOAT')
    assert analyze(pure, intervals)['reason'] == 'constant_level'
    silent = job / 'silent.wav'
    sf.write(silent, np.zeros(rate), rate)
    assert analyze(silent, [(0, 1)])['reason'] == 'insufficient_signal'
    assert analyze(silent, [(2, 3)])['reason'] == 'notes_outside_audio'
    with patch('vocal_expression.energy_frames', side_effect=AssertionError('Disabled analysis ran')):
        assert analyze(source, [])['reason'] == 'no_notes'
        assert analyze(source, intervals, 0)['reason'] == 'zero_strength'
    invalid = job / 'invalid.wav'
    bad = np.zeros(rate, dtype='float32'); bad[123] = float('nan')
    sf.write(invalid, bad, rate, subtype='FLOAT')
    raises(ValueError, analyze, invalid, [(0, 1)])
    raises(InterruptedError, energy_frames, source, lambda: (_ for _ in ()).throw(InterruptedError()))
    dense = [(float(t), float(np.sin(t) + .05 * np.sin(t * 30))) for t in np.linspace(0, 4, 500)]
    reduced = np.asarray(simplify(dense))
    error = abs(np.asarray(dense)[:, 1] - np.interp(np.asarray(dense)[:, 0], reduced[:, 0], reduced[:, 1]))
    assert error.max() <= .120001 and len(reduced) < len(dense) / 10

    # A group offset and a tempo change must still sample the original audio at
    # its absolute timestamps, while writing group-local control positions.
    offset = SV_QUARTER
    test_svp = {'time': {'tempo': [{'position': 0, 'bpm': 120}, {'position': 4 * SV_QUARTER, 'bpm': 60}]},
        'tracks': [{'name': 'Lead', 'mainRef': {'blickOffset': offset},
                    'mainGroup': {'notes': [{'onset': SV_QUARTER, 'duration': 4 * SV_QUARTER}], 'parameters': {}}}]}
    svp = job / 'offset.svp'
    svp.write_bytes(json.dumps(test_svp).encode() + b'\x00')
    mapped = apply_svp(svp, [{'voice': 'lead', 'path': source}])
    assert mapped['tracks'][0]['samples_preserved']
    data = json.loads(svp.read_bytes().removesuffix(b'\x00'))
    points = np.asarray(data['tracks'][0]['mainGroup']['parameters']['loudness']['points']).reshape(-1, 2)
    for second in (1.4, 1.8, 2., 2.4, 2.8):
        actual = np.interp(clock.to_position(second) - offset, points[:, 0], points[:, 1])
        assert abs(actual - value_at(mapped['tracks'][0], second)) < .13
    assert svp.read_bytes().endswith(b'\x00')
    original = svp.read_bytes()
    raises(InterruptedError, apply_svp, svp, [{'voice': 'lead', 'path': source}], .6,
           lambda: (_ for _ in ()).throw(InterruptedError()))
    assert svp.read_bytes() == original

    from v2m_runtime import configure
    configure(APP / 'dependencies/vocal2midi2')
    from inference.API.ustx_api import save_ustx
    from inference.API.rmvpe_api import RmvpeResult
    from project_exports import export_job, outputs_intact
    from common import load_config
    (job / 'audio').mkdir()
    sf.write(job / 'audio/lead.wav', wave, rate, subtype='FLOAT')
    sf.write(job / 'audio/backing.wav', np.zeros_like(wave), rate, subtype='FLOAT')
    sf.write(job / 'audio/instrumental.wav', wave * .2, rate, subtype='FLOAT')
    notes = [SimpleNamespace(onset=a, offset=b, pitch=60 + i, lyric=lyric)
             for i, ((a, b), lyric) in enumerate(zip(intervals, ['你', '好', '呀']))]
    midi = job / 'midi/lead'; midi.mkdir(parents=True)
    pitch = np.zeros(1200, dtype='float32')
    for i, (start, end) in enumerate(intervals):
        left, right = round(start * 100), round(end * 100)
        pitch[left:right] = 60 + i + .2 * np.sin(np.arange(right - left) * .3)
    save_ustx(notes, midi / 'lead.ustx', tempo=120, rmvpe_result=RmvpeResult(.01, pitch, pitch > 0))
    (job / 'midi/backing').mkdir()
    save_ustx([], job / 'midi/backing/backing.ustx', tempo=120)
    request = {'voice_mode': 'dual', 'language': 'zh', 'project_formats': ['ustx', 'vsqx', 'svp'],
               'audio_formats': ['wav'], 'export_pitch': True, 'tools': load_config(False)}
    baseline = export_job(job, request)
    plain = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    native_stamps = {file: file.stat().st_mtime_ns for file in (job / 'midi').rglob('*.ustx')}
    other_stamps = {fmt: (job / f'exports/voices.{fmt}').stat().st_mtime_ns for fmt in ('ustx', 'vsqx')}
    request.update(reference_dynamics=True, dynamics_strength=.6)
    assert not outputs_intact(job, request)
    enriched = export_job(job, request)
    expression = enriched['projects']['outputs'][-1]['reference_dynamics']
    assert [row['status'] for row in expression['tracks']] == ['applied', 'skipped']
    data = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    for old, new in zip(plain['tracks'], data['tracks']):
        assert old['mainGroup']['parameters'].get('pitchDelta') == new['mainGroup']['parameters'].get('pitchDelta')
        assert [{key: val for key, val in note.items() if key != 'uuid'} for note in old['mainGroup']['notes']] == [
               {key: val for key, val in note.items() if key != 'uuid'} for note in new['mainGroup']['notes']]
    assert data['tracks'][0]['mainGroup']['parameters']['loudness']['points']
    assert not data['tracks'][1]['mainGroup']['parameters']['loudness']['points']
    assert any(track['mainRef'].get('isInstrumental') for track in data['tracks'])
    assert all((job / f'exports/voices.{fmt}').stat().st_mtime_ns == stamp for fmt, stamp in other_stamps.items())
    assert outputs_intact(job, request)
    stamp = (job / 'exports/voices.svp').stat().st_mtime_ns
    with patch('project_exports.library', side_effect=AssertionError('Intact expression export recomputed')):
        export_job(job, request)
    assert (job / 'exports/voices.svp').stat().st_mtime_ns == stamp
    request['dynamics_strength'] = .3
    assert not outputs_intact(job, request)
    softer = export_job(job, request)['projects']['outputs'][-1]['reference_dynamics']['tracks'][0]
    assert abs(value_at(softer, 3.5) / value_at(expression['tracks'][0], 3.5) - .5) < .03
    # Disabling reference dynamics removes generated Loudness while retaining F0.
    request['reference_dynamics'] = False
    export_job(job, request)
    restored = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    assert not restored['tracks'][0]['mainGroup']['parameters']['loudness']['points']
    assert restored['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']
    assert all(file.stat().st_mtime_ns == modified for file, modified in native_stamps.items())
    assert all((job / f'exports/voices.{fmt}').stat().st_mtime_ns == modified for fmt, modified in other_stamps.items())
    # Exporting dynamics with pitch disabled must not restore any pitch presets.
    request.update(reference_dynamics=True, export_pitch=False, dynamics_strength=.6)
    export_job(job, request)
    no_pitch = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    parameters = no_pitch['tracks'][0]['mainGroup']['parameters']
    assert 'pitchDelta' not in parameters and 'vibratoEnv' not in parameters
    assert parameters['loudness']['points']
    before = (job / 'exports/voices.svp').stat().st_mtime_ns
    # A changed reference audio invalidates the expression output even when the
    # native score is identical. An absolute gain shift remains expression-neutral.
    sf.write(job / 'audio/lead.wav', wave * .5, rate, subtype='FLOAT')
    assert not outputs_intact(job, request)
    export_job(job, request)
    assert (job / 'exports/voices.svp').stat().st_mtime_ns != before
    assert outputs_intact(job, request)
    from pipeline import create_job
    raises(ValueError, create_job, local_music=source, reference_dynamics=True, remember=False)
    created = create_job(local_music=source, parent=job / 'requests', reference_dynamics=True,
                         dynamics_strength=.45, project_formats=['svp'], remember=False)
    recorded = json.loads((created / 'request.json').read_text(encoding='utf-8'))
    assert recorded['reference_dynamics'] and recorded['dynamics_strength'] == .45
    # Combined-vocal mode uses a different native path and must map its single
    # voice to the correct audio, without assuming a lead/backing pair exists.
    single = job / 'single'
    (single / 'audio').mkdir(parents=True)
    (single / 'midi').mkdir()
    sf.write(single / 'audio/vocals.wav', wave, rate, subtype='FLOAT')
    sf.write(single / 'audio/instrumental.wav', wave * .1, rate, subtype='FLOAT')
    save_ustx(notes, single / 'midi/vocals.ustx', tempo=120)
    single_request = dict(request, voice_mode='single', project_formats=['svp'], export_pitch=False)
    single_report = export_job(single, single_request)['projects']['outputs'][0]['reference_dynamics']
    assert [track['voice'] for track in single_report['tracks']] == ['vocals']
    assert single_report['tracks'][0]['status'] == 'applied'
    assert outputs_intact(single, single_request)
    # Timbre options are independently cached and do not affect MIDI or the
    # other project formats. Exercise the complete converter/receipt path.
    from project_exports import project_signatures
    for name in ('tension', 'breathiness', 'voicing'):
        altered = dict(request, **{'reference_'+name: True})
        base_signatures = project_signatures(job, request)
        new_signatures = project_signatures(job, altered)
        assert new_signatures['svp'] != base_signatures['svp']
        assert new_signatures['ustx'] == base_signatures['ustx']
        assert new_signatures['vsqx'] == base_signatures['vsqx']
    request.update(reference_dynamics=False, reference_tension=True)
    synthetic = np.column_stack([np.arange(600)*.02, 5*np.sin(np.arange(600)*.03),
                    np.full(600,-20), np.full(600,.95), np.full(600,-10), np.full(600,60), np.ones(600)])
    with patch('vocal_timbre.feature_frames', return_value=(synthetic, {'analyzer':'test'})):
        rich = export_job(job, request)
    richer = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    assert richer['tracks'][0]['mainGroup']['parameters']['tension']['points']
    assert not richer['tracks'][0]['mainGroup']['parameters']['loudness']['points']
    assert 'pitchDelta' not in richer['tracks'][0]['mainGroup']['parameters']
    assert outputs_intact(job, request)
    stamp = (job / 'exports/voices.svp').stat().st_mtime_ns
    with patch('vocal_timbre.feature_frames', side_effect=AssertionError('Cached timbre reran')):
        export_job(job, request)
    assert (job / 'exports/voices.svp').stat().st_mtime_ns == stamp
    request['dynamics_strength'] = .3
    with patch('vocal_timbre.feature_frames', side_effect=AssertionError('Changing strength reran WORLD')):
        remapped = export_job(job, request)
    output = next(item for item in remapped['projects']['outputs'] if item['file'].endswith('.svp'))
    assert output['reference_dynamics']['tracks'][0]['timbre']['cache_hit']
    request['reference_tension'] = False
    export_job(job, request)
    reset = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    assert not reset['tracks'][0]['mainGroup']['parameters']['tension']['points']

    # Complete combinations must survive the converter, request switches and
    # receipt path in BOTH supported languages. This complements the isolated
    # control test above and rejects a missing/flat timbre curve in the result.
    from vocal_expression import curve_statistics
    grid=np.arange(600)*.02
    combination_frames=np.column_stack([grid,4*np.sin(grid),-16+6*np.sin(grid*1.4),
        .86-.12*np.sin(grid*1.4),np.full(600,-10),np.full(600,60),np.ones(600)])
    for language,syllables in (('ja',['ka','ki','ku']),('zh',['你','好','呀'])):
        native=[SimpleNamespace(onset=a,offset=b,pitch=60+i,lyric=lyric)
                for i,((a,b),lyric) in enumerate(zip(intervals,syllables))]
        save_ustx(native,single/'midi/vocals.ustx',tempo=120)
        combination=dict(single_request,language=language,reference_dynamics=True,
            reference_tension=True,reference_breathiness=True,reference_voicing=True,dynamics_strength=.6)
        with patch('vocal_timbre.feature_frames',return_value=(combination_frames,{'analyzer':'test'})):
            combined=export_job(single,combination)
        generated=combined['projects']['outputs'][0]['reference_dynamics']
        density=combined['projects']['outputs'][0]['expression_density']
        for row in density['tracks']:
            assert all(c.get('max_changing_gap_ms',0)<=5.00001 for c in row['controls'].values())
        assert generated['expression_density']['step_seconds']==.005
        assert generated['parameters']==['loudness','tension','breathiness','voicing']
        assert generated['tracks'][0]['samples_preserved']
        assert generated['tracks'][0]['timbre']['parameters']['tension']['samples_preserved']
        statuses=generated['tracks'][0]['parameter_status']
        assert all(state['requested'] and state['status']=='applied' and state['has_variation'] for state in statuses.values())
        actual=json.loads((single/'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
        params=actual['tracks'][0]['mainGroup']['parameters']
        for name in ('loudness','tension','breathiness','voicing'):
            assert curve_statistics(params,name,require_variation=True)['point_count']>=2
        assert generated['tracks'][0]['timbre']['parameters']['tension']['pitch_context']=='score'
        assert outputs_intact(single,combination)

print('PASS: bounded RMS, gain/DC/stereo invariance, releases, gaps, sparse curves, SVP time/offsets, cancellation and export cache isolation')
