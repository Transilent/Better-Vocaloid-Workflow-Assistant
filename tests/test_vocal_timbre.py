"""Bounded expression controls, confidence gating, real WORLD and optional install."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import numpy as np
import soundfile as sf
import expression_component as component
from vocal_timbre import PARAMETERS, NEUTRAL, settings, map_features, feature_frames, cached_features, analyze, phonetic_context, _feature_code_signature
from vocal_expression import apply_svp, curve_statistics, TempoMap, SV_QUARTER


def raises(kind, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except kind:
        return
    raise AssertionError('Expected ' + kind.__name__)


def value_at(report, seconds):
    points = np.asarray(report['points'])
    return np.interp(seconds, points[:, 0], points[:, 1])


assert settings({}) == ()
assert settings({'reference_tension': True}) == ('tension',)
for value in ('true', None, 1, []):
    raises(ValueError, settings, {'reference_voicing': value})

# Known features separate input validity from mapping. Changes must retain their
# sign, respect parameter units, and return to each parameter's actual neutral.
times = np.arange(0, 12, .02)
frames = np.column_stack([times, 3 * np.sin(times), -15 + 9 * np.sin(times * 1.7),
                         .8 - .32 * np.sin(times * 1.7), np.full(len(times), -20),
                         np.full(len(times), 60), np.ones(len(times))])
intervals = [(1, 4), (4, 7), (9, 11)]
reports = map_features(frames, intervals, .6, PARAMETERS)
for name, result in reports.items():
    assert result['status'] == 'applied', (name, result)
    points = np.asarray(result['points'])
    assert np.isfinite(points).all() and np.all(np.diff(points[:, 0]) > 0)
    assert points[:, 1].min() >= (0 if name == 'voicing' else -1)
    assert points[:, 1].max() <= 1
    for second in (.1, .8, 8, 11.5, 12):
        assert abs(value_at(result, second) - NEUTRAL[name]) < .0001, (name, second)
    softer = map_features(frames, intervals, .3, PARAMETERS)[name]
    for second in (1.5, 2, 5, 6, 9.5, 10.5):
        old = value_at(result, second) - NEUTRAL[name]
        new = value_at(softer, second) - NEUTRAL[name]
        assert abs(new - old * .5) < .012
assert value_at(reports['tension'], 1.6) > value_at(reports['tension'], 4.6)
solo = map_features(frames, intervals, .6, ('voicing',))['voicing']
assert value_at(solo, 1.5) <= value_at(reports['voicing'], 1.5)
# A long unvoiced region and isolated voiced detections must not create controls.
unvoiced = frames.copy(); unvoiced[:, 6] = 0; unvoiced[55:58, 6] = 1
assert all(row['status'] == 'skipped' for row in map_features(unvoiced, intervals, .6, PARAMETERS).values())
constant = frames.copy(); constant[:, 1:4] = [0, -20, .99]
assert all(row['status'] == 'skipped' for row in map_features(constant, intervals, .6, PARAMETERS).values())
# Pure pitch-dependent spectral coloring should be removed where each pitch
# neighborhood has enough evidence, rather than transferred as performance.
colored = constant.copy(); colored[:300, 5] = 60; colored[300:, 5] = 72
colored[:300, 1] = -10; colored[300:, 1] = 10
assert map_features(colored, [(0, 12)], .6, ('tension',))['tension']['status'] == 'skipped'

# Vibrato just across a four-semitone neighborhood boundary must not keep
# switching the harmonic reference between neighboring written pitches.
vibrato = constant.copy()
vibrato[:,5] = np.where(times<4,61.5+.16*np.sin(times*2*np.pi*2),np.where(times<8,59,65))
vibrato[:,1] = np.where(times<4,0,np.where(times<8,-6,6))
sections=[(0,4),(4,8),(8,12)]
moving = map_features(vibrato,sections,.6,('tension',))['tension']
stable = map_features(vibrato,sections,.6,('tension',),pitches=[62,59,65])['tension']
sample_times=np.arange(1,3,.02)
assert np.ptp([value_at(moving,t) for t in sample_times]) > .15
assert np.ptp([value_at(stable,t) for t in sample_times]) < .005
assert stable['pitch_context']=='score'
expressive=vibrato.copy(); expressive[(times>=1)&(times<2),1]+=1.
retained = map_features(expressive,sections,.6,('tension',),pitches=[62,59,65])['tension']
assert value_at(retained,1.5)-value_at(retained,2.5) > .04
for invalid in ([62], [62,59,float('nan')], [62,59,128], [62,59,True]):
    raises(ValueError,map_features,vibrato,sections,.6,('tension',),pitches=invalid)

# Short gestures retain their timing and peak instead of being averaged as a
# long sustain. Neighboring long notes still use the slower smoothing.
short = constant.copy()
short[(times >= 2) & (times < 2.08), 1] = 6
brief = map_features(short, [(0, 2), (2, 2.08), (2.08, 12)], .6, ('tension',))['tension']
slow = map_features(short, [(0, 12)], .6, ('tension',))['tension']
assert brief['status'] == 'applied' and slow['status'] == 'applied'
assert value_at(brief, 2.04) > value_at(slow, 2.04) * 1.3
assert abs(value_at(brief, 1.8)) < .001

# Sustained modest periodicity changes were lost after strength scaling and a
# global 2.5% output gate. They remain small controls; tiny/impulsive noise does
# not become a gesture merely because smoothing widened it.
modest = constant.copy()
first = (times >= 1) & (times < 2)
second = (times >= 3) & (times < 4)
modest[first | second, 3] = .93
modest[second, 2] = -8
alone = map_features(modest, [(0, 12)], .6, ('voicing',))['voicing']
joint = map_features(modest, [(0, 12)], .6, ('breathiness', 'voicing'))['voicing']
softer = map_features(modest, [(0, 12)], .3, ('voicing',))['voicing']
assert all(row['status'] == 'applied' for row in (alone, joint, softer))
assert abs(value_at(alone, 1.5) - value_at(joint, 1.5)) < .001
assert value_at(alone, 3.5) < value_at(joint, 3.5) < 1
assert abs((1-value_at(softer, 1.5)) / (1-value_at(alone, 1.5)) - .5) < .03
assert joint['overlap_reduction_max'] <= .35
impulse = constant.copy(); impulse[100:102, 3] = .5
assert map_features(impulse, [(0, 12)], .6, ('voicing',))['voicing']['status'] == 'skipped'
tiny = constant.copy(); tiny[first, 3] -= .001
assert map_features(tiny, [(0, 12)], .6, ('voicing',))['voicing']['status'] == 'skipped'

# A rejected two-frame Breathiness impulse must not attenuate a real Voicing
# gesture, including when a separate valid Breathiness event exists elsewhere.
rejected = constant.copy()
rejected[(times>=1)&(times<3),3]=.75
rejected[100:102,2]=-8
short_intervals=[(0,2),(2,2.08),(2.08,12)]
independent=map_features(rejected,short_intervals,.6,('voicing',))['voicing']
coupled=map_features(rejected,short_intervals,.6,('breathiness','voicing'))
assert coupled['breathiness']['status']=='skipped'
assert coupled['voicing']['points']==independent['points']
assert coupled['voicing']['overlap_reduction_max']==0
rejected[(times>=5)&(times<6),2]=-8
elsewhere=map_features(rejected,short_intervals,.6,('breathiness','voicing'))
reversed_order=map_features(rejected,short_intervals,.6,('voicing','breathiness'))
assert elsewhere['breathiness']['status']=='applied'
assert elsewhere['voicing']['points']==independent['points']
assert elsewhere==reversed_order
# A curve below the selected strength's export resolution is also absent.
low_strength=constant.copy(); low_strength[first,3]=.75; low_strength[first,2]=-18.8
quiet_b=map_features(low_strength,[(0,12)],.1,('breathiness','voicing'))
assert quiet_b['breathiness']['reason']=='below_output_resolution'
assert quiet_b['voicing']['points']==map_features(low_strength,[(0,12)],.1,('voicing',))['voicing']['points']

# Bridge only tiny detection holes between same-sign supported controls. The
# missing spectral measurements must never become evidence or control values.
sustain=constant.copy(); body=(times>=1)&(times<3)
sustain[body,1]=4; sustain[body,2]=-8; sustain[body,3]=.75
unbroken=map_features(sustain,[(0,12)],.6,PARAMETERS)
for gap_size in (1,2):
    gapped=sustain.copy(); gapped[100:100+gap_size,6]=0
    gapped[100:100+gap_size,1:4]=[1e4,1e4,.01]
    continuous=map_features(gapped,[(0,12)],.6,PARAMETERS)
    for name in PARAMETERS:
        row=continuous[name]
        assert row['bridged_gaps']==1 and row['bridged_seconds']==gap_size*.02
        assert abs(row['reliable_seconds']-(unbroken[name]['reliable_seconds']-gap_size*.02))<1e-6
        assert abs(value_at(row,2.02)-value_at(unbroken[name],2.02))<.001
        assert abs(value_at(row,1.6)-value_at(unbroken[name],1.6))<.001
        assert value_at(row,3.5)==NEUTRAL[name]
    for change in ('long_gap','quiet','nonfinite','score_rest','subframe_rest','register_jump'):
        blocked=sustain.copy(); blocked[100:100+(3 if change=='long_gap' else gap_size),6]=0
        score=[(0,12)]; pitches=None
        if change=='quiet':blocked[100:100+gap_size,4]=-100
        elif change=='nonfinite':blocked[100:100+gap_size,1]=float('nan')
        elif change=='score_rest':score=[(0,2),(2+gap_size*.02,12)]
        elif change=='subframe_rest':score=[(0,2.005),(2.015,12)]
        elif change=='register_jump':score=[(0,2),(2,12)]; pitches=[60,72]
        rows=map_features(blocked,score,.6,PARAMETERS,pitches=pitches)
        for name in PARAMETERS:
            assert rows[name]['bridged_gaps']==0,(change,name,rows[name])
            assert abs(value_at(rows[name],2)-NEUTRAL[name])<.001,(change,name)
opposite=sustain.copy(); opposite[times>=2,1]*=-1; opposite[100:102,6]=0
opposing=map_features(opposite,[(0,12)],.6,('tension',))['tension']
assert opposing['bridged_gaps']==0 and abs(value_at(opposing,2.02))<.001
unsupported=sustain.copy(); unsupported[:,1]=0; unsupported[98:100,1]=4
unsupported[(times>=2.04)&(times<3),1]=4; unsupported[100:102,6]=0
one_sided=map_features(unsupported,[(0,12)],.6,('tension',))['tension']
assert one_sided['bridged_gaps']==0 and abs(value_at(one_sided,2.02))<.001

# A measured, still-voiced dip just below the evidence gate must not chatter
# between two genuine same-sign gestures. Weak evidence alone remains invalid.
weak_dip=constant.copy();weak_dip[body,1]=.6
weak_dip[100:102,1]=.24  # Tension .02: below .025 but above the local hold gate.
held=map_features(weak_dip,[(0,12)],.6,('tension',))['tension']
assert held['held_evidence_gaps']==1 and held['held_evidence_seconds']==.04
assert value_at(held,2.02)>.018 and held['bridged_gaps']==0
weak_alone=constant.copy();weak_alone[body,1]=.24
assert map_features(weak_alone,[(0,12)],.6,('tension',))['tension']['status']=='skipped'
for kind in ('long','unvoiced','reversed','nearly_neutral'):
    separate=weak_dip.copy()
    if kind=='long':separate[100:104,1]=.24
    elif kind=='unvoiced':separate[100:102,6]=0
    elif kind=='reversed':separate[100:102,1]=-.24
    else:separate[100:102,1]=.01
    row=map_features(separate,[(0,12)],.6,('tension',))['tension']
    assert row['held_evidence_gaps']==0,(kind,row)

# One genuine gesture must not validate unrelated short/noisy excursions in
# another part of the song. This previously happened after a track-wide gate.
mixed = constant.copy(); mixed[first, 1] = 4; mixed[350:352, 1] = 7
localized = map_features(mixed, [(0, 12)], .6, ('tension',))['tension']
assert localized['status'] == 'applied' and value_at(localized, 1.5) > .1
assert abs(value_at(localized, 7.02)) < .001
assert localized['gesture_count'] == 1 and localized['gesture_seconds'] < 1.3

# Compare independently repeated vowels at the same pitch across phrases. The
# bounded correction should reduce a vowel-only bias, retaining a real gesture
# between two occurrences of the same vowel instead of flattening every note.
vowel_intervals = [(float(i + phrase*5), float(i+1+phrase*5)) for phrase in range(3) for i in range(4)]
vowels = ['mandarin:a','mandarin:i'] * 6
vowel_times = np.arange(0, 15, .02)
vowel_frames = np.tile(constant[0],(len(vowel_times),1)); vowel_frames[:,0]=vowel_times
for (start, end), vowel in zip(vowel_intervals, vowels):
    vowel_frames[(vowel_times>=start)&(vowel_times<end),1] = 2. if vowel.endswith(':a') else -2.
unconditioned = map_features(vowel_frames,vowel_intervals,.6,('tension',))['tension']
conditioned = map_features(vowel_frames,vowel_intervals,.6,('tension',),nuclei=vowels)['tension']
gap_before = value_at(unconditioned,.5)-value_at(unconditioned,1.5)
gap_after = value_at(conditioned,.5)-value_at(conditioned,1.5)
assert .35 < gap_after/gap_before < .45
assert conditioned['vowel_conditioning']['status']=='applied'
assert conditioned['vowel_conditioning']['max_correction_db'] <= 1.8
changed_vowels=vowel_frames.copy(); changed_vowels[(vowel_times>=5)&(vowel_times<6),1]+=1.
gesture = map_features(changed_vowels,vowel_intervals,.6,('tension',),nuclei=vowels)['tension']
assert value_at(gesture,5.5)-value_at(gesture,.5) > .04
# Insufficient independent phrases, unknown text and a long melisma must not
# supply a guessed vowel profile, even when there are many note fragments.
one_phrase = [(float(i),float(i+1)) for i in range(12)]
confounded = map_features(vowel_frames,one_phrase,.6,('tension',),nuclei=vowels)['tension']
assert confounded['vowel_conditioning']['status']=='insufficient_context'
melisma = map_features(vowel_frames,vowel_intervals,.6,('tension',),
                      nuclei=['mandarin:a','-','-','-']*3)['tension']
assert melisma['vowel_conditioning']['status']=='insufficient_context'
unknown = map_features(vowel_frames,vowel_intervals,.6,('tension',),nuclei=[None]*12)['tension']
assert unknown['points']==unconditioned['points']
raises(ValueError,map_features,vowel_frames,vowel_intervals,.6,('tension',),nuclei=['mandarin:a'])

# Use already bundled dictionaries. Reading a nucleus must not change lyrics,
# insert a fallback 'la' or mistake a multi-syllable token for a known vowel.
context_notes=[{'lyrics':text} for text in ('ma','mi','-','br','nonexistent','你好','+')]
saved_notes=copy.deepcopy(context_notes)
assert phonetic_context(context_notes,'mandarin') == ['mandarin:a','mandarin:i','-',None,None,None,None]
ja_notes=[{'lyrics':text} for text in ('ka','ki','きゃ','-','nonexistent','br','+')]
assert phonetic_context(ja_notes,'japanese') == ['japanese:a','japanese:i','japanese:a','-',None,None,None]
assert phonetic_context([{'lyrics':'ka','languageOverride':'english'}],'japanese') == [None]
assert phonetic_context([{'lyrics':'ka','phonemes':'s a'}],'japanese') == ['japanese:a']
assert phonetic_context(context_notes,None) == [None]*len(context_notes)
assert context_notes==saved_notes

# Report absent and constant curves honestly. An applied result must have real
# variation in the serialized project; reject dropped/flat/invalid output.
assert curve_statistics({},'tension')['point_count']==0
assert not curve_statistics({'tension':{'points':[0,0,100,0]}},'tension')['has_variation']
for points in ([],[0,0,100,0],[0,0,0,.1],[0,0,100,float('nan')],[0,0,100,2],[0,0,100]):
    raises(ValueError,curve_statistics,{'tension':{'points':points}},'tension',require_variation=True)
assert curve_statistics({'tension':{'points':[0.,0,100.5,.1]}},'tension',require_variation=True)['point_count']==2

with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='vocal-timbre-') as folder:
    folder = Path(folder)
    clock = TempoMap([{'position': 0, 'bpm': 120}, {'position': SV_QUARTER * 4, 'bpm': 60}])
    offset = SV_QUARTER
    score = {'time': {'tempo': [{'position': 0, 'bpm': 120}, {'position': SV_QUARTER * 4, 'bpm': 60}]},
             'tracks': [{'name': 'Lead', 'mainRef': {'blickOffset': offset},
                         'mainGroup': {'notes': [{'onset': clock.to_position(a) - offset,
                           'duration': clock.to_position(b) - clock.to_position(a), 'pitch': 60} for a, b in intervals],
                         'parameters': {'pitchDelta': {'mode': 'linear', 'points': [0, 20, SV_QUARTER, -20]}}}},
                        {'name': 'Instrumental', 'mainRef': {'isInstrumental': True, 'audio': 'test.wav'}}]}
    path = folder / 'test.svp'; original = json.dumps(score).encode() + b'\0'; path.write_bytes(original)
    metadata = {'analyzer': 'test', 'voicebank_calibrated': False}
    with patch('vocal_timbre.feature_frames', return_value=(frames, metadata)), patch('vocal_expression.analyze', side_effect=AssertionError('Loudness was disabled')):
        report = apply_svp(path, [{'voice': 'lead', 'path': folder / 'not-read.wav'}], .6, dynamics=False, timbre=PARAMETERS)
    data = json.loads(path.read_bytes().removesuffix(b'\0'))
    assert report['parameters'] == list(PARAMETERS)
    for name in PARAMETERS:
        state=report['tracks'][0]['parameter_status'][name]
        assert state['requested'] and state['status']=='applied' and state['has_variation']
    assert report['tracks'][0]['parameter_status']['loudness']['status']=='disabled'
    assert data['tracks'][1] == score['tracks'][1]
    assert data['tracks'][0]['mainGroup']['notes'] == score['tracks'][0]['mainGroup']['notes']
    actual = data['tracks'][0]['mainGroup']['parameters']
    assert 'loudness' not in actual and actual['pitchDelta'] == score['tracks'][0]['mainGroup']['parameters']['pitchDelta']
    for name in PARAMETERS:
        points = np.asarray(actual[name]['points']).reshape(-1, 2)
        for seconds in (1.2, 1.8, 2., 2.4, 5., 8.):
            value = np.interp(clock.to_position(seconds) - offset, points[:, 0], points[:, 1])
            assert abs(value - value_at(report['tracks'][0]['timbre']['parameters'][name], seconds)) < .009
    before = path.read_bytes()
    raises(InterruptedError, apply_svp, path, [{'voice': 'lead', 'path': folder / 'x.wav'}], .6,
           lambda: (_ for _ in ()).throw(InterruptedError()), dynamics=False, timbre=PARAMETERS)
    assert before == path.read_bytes()
    # A single-control export is explicitly reported, without enabling other
    # controls behind the user's back. A broken serialization leaves the file.
    path.write_bytes(original)
    with patch('vocal_timbre.feature_frames',return_value=(frames,metadata)):
        single=apply_svp(path,[{'voice':'lead','path':folder/'not-read.wav'}],.6,dynamics=False,timbre=('voicing',))
    states=single['tracks'][0]['parameter_status']
    assert states['voicing']['status']=='applied'
    assert all(states[name]['status']=='disabled' for name in ('tension','breathiness'))
    assert all(states[name]['point_count']==0 for name in ('tension','breathiness'))
    path.write_bytes(original)
    with patch('vocal_timbre.feature_frames',return_value=(frames,metadata)), patch('vocal_expression.automation',return_value={'mode':'linear','points':[]}):
        raises(ValueError,apply_svp,path,[{'voice':'lead','path':folder/'not-read.wav'}],.6,dynamics=False,timbre=PARAMETERS)
    assert path.read_bytes()==original
    # A mapping-only edit can reuse expensive WORLD features. An extraction or
    # analyzer-loader change must invalidate them, even if the audio is the same.
    source=(APP/'src/vocal_timbre.py').read_text(encoding='utf-8-sig')
    extractor_identity=_feature_code_signature(source)
    assert _feature_code_signature(source.replace('ALGORITHM_VERSION = 6','ALGORITHM_VERSION = 999'))==extractor_identity
    assert _feature_code_signature(source.replace('centered_tilt / 12','centered_tilt / 13'))==extractor_identity
    assert _feature_code_signature(source.replace('f0_floor=65','f0_floor=60'))!=extractor_identity
    # Cached WORLD features can be remapped at another strength or with other
    # controls. Changed input/notes and corruption must trigger analysis again.
    reference = folder/'cache-source.wav'; reference.write_bytes(b'original reference fixture')
    cache = folder/'features/lead.npz'
    note_data = [(1., 4., 60), (4., 7., 60), (9., 11., 60)]
    with patch('vocal_timbre.feature_frames', return_value=(frames, metadata)) as extractor:
        first = analyze(reference, note_data, .6, PARAMETERS, cache=cache)
        again = analyze(reference, note_data, .3, ('tension',), cache=cache)
        assert extractor.call_count == 1 and not first['cache_hit'] and again['cache_hit']
        assert again['parameters']['tension']['status'] == 'applied'
        assert cache.stat().st_size < frames.nbytes
        # Extended installation paths must support both cold feature writes and
        # warm cache reads, including the atomic temporary filename.
        from common import native_path
        import shutil
        long_root = folder/'extended-cache-case'
        long_cache = long_root/('nested-'*10)/('folder-'*10)/'lead.npz'
        assert len(str(long_cache)) > 300
        try:
            assert not analyze(reference, note_data, .6, PARAMETERS, cache=long_cache)['cache_hit']
            assert analyze(reference, note_data, .6, PARAMETERS, cache=long_cache)['cache_hit']
        finally:
            assert long_root.resolve().is_relative_to(folder.resolve())
            shutil.rmtree(native_path(long_root))
        with patch('vocal_timbre.ALGORITHM_VERSION',999):
            assert analyze(reference,note_data,.6,PARAMETERS,cache=cache)['cache_hit']
        with patch('vocal_timbre._feature_code_signature',return_value='changed extractor'):
            assert not analyze(reference,note_data,.6,PARAMETERS,cache=cache)['cache_hit']
        assert not analyze(reference,note_data,.6,PARAMETERS,cache=cache)['cache_hit']
        def changed_loader(path):
            return 'changed analyzer loader' if Path(path).name=='expression_component.py' else hashlib.sha256(Path(path).read_bytes()).hexdigest()
        with patch('common.file_hash',side_effect=changed_loader):
            assert not analyze(reference,note_data,.6,PARAMETERS,cache=cache)['cache_hit']
        assert not analyze(reference,note_data,.6,PARAMETERS,cache=cache)['cache_hit']
        reference.write_bytes(b'changed reference fixture')
        assert not analyze(reference, note_data, .6, PARAMETERS, cache=cache)['cache_hit']
        changed_notes = [(.9, 4., 60), *note_data[1:]]
        assert not analyze(reference, changed_notes, .6, PARAMETERS, cache=cache)['cache_hit']
        cache.write_bytes(b'corrupt cache')
        assert not analyze(reference, changed_notes, .6, PARAMETERS, cache=cache)['cache_hit']
    saved = cache.read_bytes()
    with patch('vocal_timbre.feature_frames', side_effect=InterruptedError('cancelled')):
        raises(InterruptedError, cached_features, reference, note_data, cache)
    assert cache.read_bytes() == saved and not list(cache.parent.glob('*.pending'))
    # A matching signature is not sufficient when the feature payload is invalid.
    with np.load(cache, allow_pickle=False) as stored:
        signature, saved_metadata = stored['signature'], stored['metadata']
    corrupt = frames.copy(); corrupt[0, 1] = np.nan
    np.savez_compressed(cache, signature=signature, metadata=saved_metadata, frames=corrupt)
    with patch('vocal_timbre.feature_frames', return_value=(frames, metadata)) as extractor:
        assert not cached_features(reference, changed_notes, cache)[1]['cache_hit']
        assert extractor.call_count == 1
    # Install uses the same verified/resumable downloader; test a local wheel,
    # cancellation and incomplete installs without reaching the network.
    fake = folder / 'fake.whl'
    import zipfile
    with zipfile.ZipFile(fake, 'w') as archive:
        archive.writestr('testpackage/__init__.py', 'VALUE=1\n')
        archive.writestr('testpackage-1.dist-info/LICENSE', 'Test fixture\n')
    fake_spec = dict(component.SPEC, sha256=hashlib.sha256(fake.read_bytes()).hexdigest(), bytes=fake.stat().st_size)
    fake_probe = type('Probe', (), {'returncode': 0})()
    with patch.object(component, 'SPEC', fake_spec), patch('optional_components.download', return_value=fake), patch('expression_component.subprocess.run', return_value=fake_probe):
        installed = component.install(root=folder / 'component')
        assert installed['ready'] and component.status(folder / 'component')['ready']
        required = folder / 'component/current/packages/testpackage/__init__.py'
        required.unlink()
        assert not component.status(folder / 'component')['ready']
    assert not component.status(folder / 'missing')['ready']
    # The real optional analyzer is exercised when present, with no auto-download.
    if component.status()['ready']:
        rate = 24000; t = np.arange(rate * 10) / rate
        phase = 2 * np.pi * 220 * t
        upper = np.where(t < 5, .03, .22)
        noise = np.random.default_rng(31).normal(size=len(t)) * np.where((t > 6) & (t < 8), .025, .0001)
        wave = .2 * np.sin(phase) + upper * np.sin(phase * 3) + noise
        wave[(t < .5) | ((t >= 4) & (t < 4.5)) | (t >= 9.5)] = 0
        mono = folder / 'mono.wav'; stereo = folder / 'stereo.wav'
        sf.write(mono, wave, rate, subtype='FLOAT')
        sf.write(stereo, np.column_stack([wave * 2 + .02, -wave * 2 - .02]), rate, subtype='FLOAT')
        notes = [(.5, 4, 57), (4.5, 9.5, 57)]
        first, meta = feature_frames(mono, notes)
        second, _ = feature_frames(stereo, notes)
        assert first.shape == second.shape and np.isfinite(first).all()
        assert np.all(np.diff(first[:, 0]) > 0)
        valid = first[:, 6].astype(bool) & second[:, 6].astype(bool)
        assert valid.sum() > 200
        assert np.median(abs(first[valid, 1:4] - second[valid, 1:4])) < .01
        assert np.percentile(abs(first[valid, 5] - 57), 90) < .1
        mapped = map_features(first, [(a, b) for a, b, _ in notes], .6, PARAMETERS)
        assert mapped['tension']['status'] == 'applied'
        assert value_at(mapped['tension'], 5.5) > value_at(mapped['tension'], 2) + .03
        raises(InterruptedError, feature_frames, mono, notes, lambda: (_ for _ in ()).throw(InterruptedError()))
    else:
        print('SKIP: optional WORLD native analysis (not installed; no network requested)')

print('PASS: timbre range/neutral/gap/strength, evidence gating, pitch conditioning, SVP time mapping, isolation, component transaction and WORLD')
