"""Voicing masks, isolated octave errors and safe pitch model input/export."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from v2m_runtime import configure
configure(ROOT / 'dependencies/vocal2midi2')
from inference.API.rmvpe_api import RmvpeResult, RmvpeTranscriber
from inference.API import ustx_api
from pitch_accuracy import prepare_audio, supported_pitch, pitd_curve, install, exclude_unvoiced_pitch

t = np.arange(16000) / 16000
wave = np.sin(t * 2 * np.pi * 220).astype('float32') * 1.8
x = prepare_audio(np.column_stack([wave + .1, -wave - .2]), 16000)
assert abs(x).max() <= .980001 and np.std(x) > .6
assert abs(x.mean()) < 1e-5
assert np.corrcoef(x, wave)[0, 1] > .9999
assert len(prepare_audio(wave, 32000)) == 8000
for invalid in (np.array([np.nan]), np.array([np.inf]), np.zeros((5, 2, 2))):
    try:
        prepare_audio(invalid, 16000)
        raise AssertionError('Invalid pitch audio accepted')
    except ValueError:
        pass

pitch = 60 + .5 * np.sin(np.arange(300) * .01 * 2 * np.pi * 5.5)
pitch = pitch.astype('float32')
mask = np.ones(300, dtype=bool); mask[:10] = False; mask[90:135] = False; mask[-10:] = False
pitch[50] += 12; pitch[160:162] -= 12
before = pitch.copy()
clean = supported_pitch(RmvpeResult(.01, pitch, mask))
assert np.array_equal(before, pitch), 'Pitch input was mutated'
assert np.isnan(clean.midi_pitch[~mask]).all()
assert abs(clean.midi_pitch[50] - (pitch[49] + pitch[51]) / 2) < .0001
assert np.max(abs(clean.midi_pitch[160:162] - 60)) < .5
unchanged = mask.copy(); unchanged[[50, 160, 161]] = False
assert np.array_equal(pitch[unchanged], clean.midi_pitch[unchanged]), 'Vibrato or valid detail was smoothed'
shifted = pitch.copy(); shifted[180:200] += 12
assert np.array_equal(supported_pitch(RmvpeResult(.01, shifted, mask)).midi_pitch[180:200], shifted[180:200]), 'Sustained change was mistaken for a glitch'
# The same isolated inference error can span two octaves. Its real duration,
# rather than just frame count, limits repair to 20 ms; ornaments stay intact.
double=np.full(300,60,dtype='float32');double[190:192]+=24
double_fixed=supported_pitch(RmvpeResult(.01,double,mask))
assert np.max(abs(double_fixed.midi_pitch[190:192]-60))<.6
slow=np.full(100,60,dtype='float32');slow[40:42]+=12
assert np.array_equal(supported_pitch(RmvpeResult(.02,slow)).midi_pitch[40:42],slow[40:42])
ornament=np.full(100,60,dtype='float32');ornament[40:42]+=2
assert np.array_equal(supported_pitch(RmvpeResult(.01,ornament)).midi_pitch,ornament)
out_of_range=np.array([60,128,-1,np.inf,60],dtype='float32')
assert np.isnan(supported_pitch(RmvpeResult(.01,out_of_range)).midi_pitch[1:4]).all()
# Following an onset consonant for legato does not make that consonant's
# spurious f0 a sung pitch. Veto only the aligned phone's interior, while
# retaining ordinary voiced modulation and all unaligned material.
phone_pitch=np.full(100,60.2,dtype='float32');phone_pitch[40:50]=72
phone_result=RmvpeResult(.01,phone_pitch,np.ones(100,bool))
phone_notes=[SimpleNamespace(onset=0.,offset=1.,pitch=60.2)]
guarded,audit=exclude_unvoiced_pitch(phone_result,phone_notes,[{'start':.4,'end':.5,'phone':'s'}])
assert audit['excluded_frames']>=7 and all(row['pitch']==72 for row in audit['excluded'])
assert np.isnan(guarded.midi_pitch[42:48]).all() and np.isfinite(guarded.midi_pitch[[39,50]]).all()
assert np.array_equal(phone_pitch,phone_result.midi_pitch),'Raw pitch was mutated'
unchanged,audit=exclude_unvoiced_pitch(phone_result,phone_notes,[])
assert np.array_equal(unchanged.midi_pitch,phone_pitch) and audit['status']=='unchanged'
normal=RmvpeResult(.01,np.full(100,60.2,dtype='float32'),np.ones(100,bool))
assert exclude_unvoiced_pitch(normal,phone_notes,[{'start':.4,'end':.5,'phone':'s'}])[1]['excluded_frames']==0

notes = [SimpleNamespace(onset=0., offset=3., pitch=60, lyric='a')]
old_x, old_y = ustx_api._build_pitd_curve(notes, RmvpeResult(.01, pitch, mask), 120)
new_x, new_y = pitd_curve(notes, RmvpeResult(.01, pitch, mask), 120)
ticks = lambda second: round(second * 120 * 8)
assert abs(np.interp(ticks(1.1), old_x, old_y)) > 5, 'Expected unvoiced-mask bug did not reproduce'
assert abs(np.interp(ticks(1.1), new_x, new_y)) == 0
assert max(new_y) <= 51 and min(new_y) >= -51, 'Octave glitch remained in exported curve'
assert new_x == sorted(set(new_x))
assert max(new_y) - min(new_y) >= 95, 'Vibrato depth lost'
assert pitd_curve([], clean, 120) == ([], [])
assert pitd_curve(notes, RmvpeResult(.01, pitch, np.zeros(300, dtype=bool)), 120) == ([], [])
# USTX's written note center is integer MIDI. Using the unrounded GAME center
# incorrectly shifts a constant reference by up to half a semitone.
fractional_notes=[SimpleNamespace(onset=0.,offset=1.,pitch=60.4,lyric='a')]
fx,fy=pitd_curve(fractional_notes,RmvpeResult(.01,np.full(100,60.4,dtype='float32'),np.ones(100,bool)),120)
assert abs(np.interp(ticks(.5),fx,fy)-40)<=1

# Install before the pipeline import, as midi_bridge does. Native modules and
# installed source bytes remain untouched; a fake model captures inference input.
original_file = Path(ustx_api.__file__).read_bytes()
captured = []
def model_infer(self, audio, sample_rate, cancel_checker=None):
    captured.append((audio, sample_rate))
    return RmvpeResult(.01, pitch, mask)
with patch.object(RmvpeTranscriber, 'infer', model_infer):
    install(); first = RmvpeTranscriber.infer; install()
    assert RmvpeTranscriber.infer is first
    result = RmvpeTranscriber.__new__(RmvpeTranscriber).infer(np.column_stack([wave, -wave]), 16000)
    assert np.isnan(result.midi_pitch[~mask]).all() and abs(captured[0][0]).max() < 1
    assert captured[0][1] == 16000
    assert ustx_api._build_pitd_curve is pitd_curve
    try:
        RmvpeTranscriber.__new__(RmvpeTranscriber).infer(wave, 16000, lambda: True)
        raise AssertionError('Cancellation ignored')
    except InterruptedError:
        pass
assert Path(ustx_api.__file__).read_bytes() == original_file

print('PASS: unvoiced-mask bug reproduced/fixed, octave spikes removed, vibrato/sustains retained, cancellation, non-clipping input and in-process adapter')

# Absolute f0 must survive the project conversion independently of USTX's
# simulated base pitch, with explicit breaks in rests and unvoiced intervals.
from pitch_accuracy import save_pitch,load_pitch,absolute_curve
from project_exports import library
converters,Project,SingingTrack,InstrumentalTrack=library()
from libresvip.model.base import SongTempo,TimeSignature,Note
from vocal_expression import TempoMap
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='absolute-pitch-') as folder:
    folder=Path(folder)
    f0=np.full(500,np.nan,dtype='float32')
    f0[100:200]=60+.25*np.sin(np.arange(100)*.3)
    f0[300:400]=72+.2*np.sin(np.arange(100)*.3)
    f0[340:365]=np.nan
    reference=RmvpeResult(.01,f0,np.isfinite(f0))
    cache=folder/'voice.pitch.npz';save_pitch(cache,reference);restored=load_pitch(cache)
    assert np.array_equal(restored.midi_pitch,reference.midi_pitch,equal_nan=True)
    score=Project(song_tempo_list=[SongTempo(position=0,bpm=120)],
        time_signature_list=[TimeSignature(bar_index=0,numerator=4,denominator=4)],
        track_list=[SingingTrack(title='Lead',note_list=[Note(start_pos=960,length=960,key_number=60,lyric='你'),
                                                     Note(start_pos=2880,length=960,key_number=72,lyric='好')])])
    report=absolute_curve(score.track_list[0],score,restored)
    assert report['points']==175 and report['runs']==3
    points=score.track_list[0].edited_params.pitch.points.root
    assert all(p.y==-100 or 2880<=p.x<3840 or 4800<=p.x<5760 for p in points)
    destination=folder/'voice.svp';converters['svp'].dump(destination,score,{'down_sample':0,'language_override':'mandarin'})
    data=json.loads(destination.read_bytes().removesuffix(b'\0'))
    rows=np.asarray(data['tracks'][0]['mainGroup']['parameters']['pitchDelta']['points']).reshape(-1,2)
    clock=TempoMap(data['time']['tempo'])
    seconds=np.array([clock.to_seconds(int(row[0])) for row in rows])
    # No multi-octave floor curve is allowed in the intro or between phrases.
    assert not ((abs(rows[:,1])>1)&((seconds<.95)|((seconds>2.05)&(seconds<2.95))|(seconds>4.05))).any()
    assert np.max(abs(rows[:,1]))<100, np.max(abs(rows[:,1]))
    assert np.ptp(rows[(seconds>1.1)&(seconds<1.9),1])>40
    # Exercise the real export cache path and damaged-sidecar recovery, not
    # only the low-level curve builder.
    import soundfile as sf
    from common import atomic_json,file_hash,load_config
    from project_exports import export_job,outputs_intact
    from inference.API.ustx_api import save_ustx
    job=folder/'job';(job/'midi').mkdir(parents=True);(job/'audio').mkdir()
    native=job/'midi/vocals.ustx'
    save_ustx([SimpleNamespace(onset=1.,offset=2.,pitch=60,lyric='你'),
               SimpleNamespace(onset=3.,offset=4.,pitch=72,lyric='好')],native,tempo=120,rmvpe_result=reference)
    sf.write(job/'audio/vocals.wav',np.zeros(24000*5),24000)
    sf.write(job/'audio/instrumental.wav',np.zeros(24000*5),24000)
    sidecar=job/'midi/vocals.pitch.npz';save_pitch(sidecar,reference)
    atomic_json(job/'midi/report.json',{'pitch_source':'absolute_rmvpe',
        'native_sha256':{'ustx':file_hash(native)},'output_sha256':{'pitch.npz':file_hash(sidecar)}})
    request={'voice_mode':'single','language':'zh','project_formats':['svp'],'audio_formats':['wav'],
             'export_pitch':True,'tools':load_config(False)}
    report=export_job(job,request)
    assert report['projects']['tracks'][0]['pitch_source']['source']=='absolute_rmvpe'
    assert outputs_intact(job,request)
    exported=(job/'exports/voices.svp').read_bytes()
    saved=sidecar.read_bytes();sidecar.write_bytes(b'broken')
    assert not outputs_intact(job,request)
    try:export_job(job,request);raise AssertionError('Broken pitch sidecar silently fell back')
    except ValueError as error:assert '连续音高' in str(error)
    assert (job/'exports/voices.svp').read_bytes()==exported
    sidecar.write_bytes(saved)
    # Manually edited native projects keep their own curve instead of being
    # silently replaced by the original measured f0.
    native.write_bytes(native.read_bytes()+b'\n# manual edit\n')
    manual=export_job(job,request)
    assert manual['projects']['tracks'][0]['pitch_source'] is None
    cache.write_bytes(b'broken')
    try:load_pitch(cache);raise AssertionError('Broken pitch cache accepted')
    except (OSError,ValueError):pass
print('PASS: absolute-f0 cache, rest/voicing breaks and real SVP conversion without simulated-pitch artifacts')
