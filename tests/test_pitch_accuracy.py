"""Voicing masks, isolated octave errors and safe pitch model input/export."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from v2m_runtime import configure
configure(Path(os.environ.get(
    'VWA_PITCH_TEST_RUNTIME', ROOT / 'dependencies/vocal2midi2')))
from inference.API.rmvpe_api import RmvpeResult, RmvpeTranscriber
from inference.API import ustx_api
from pitch_accuracy import (
    assign_note_owners, build_absolute_f0_curve, exclude_unvoiced_pitch,
    install, pitd_curve, pitch_note_descriptors, prepare_audio,
    stabilize_isolated_pitch_spikes, supported_pitch,
)

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

# Owner ambiguity is sticky: a third overlapping note must not reclaim a frame
# already marked -2 by the first overlap.
clock=lambda seconds: round(seconds*960)
inverse_clock=lambda ticks: ticks/960.
owner_times=np.arange(0.,1.01,.01)
owner=assign_note_owners(owner_times,[(.1,.8),(.2,.7),(.3,.6)])
assert np.all(owner[(owner_times>=.3)&(owner_times<.6)]==-2)

# A voiced marker with an invalid F0 is rejected before spike smoothing. A
# false marker may still carry an arbitrary sentinel because it is not audio.
valid_owner=assign_note_owners(np.arange(60)*.01,[(0.,.6)])
for bad in (np.nan,np.inf,-1.,128.):
    invalid_pitch=np.full(60,60.,dtype=np.float32);invalid_pitch[30]=bad
    try:
        stabilize_isolated_pitch_spikes(
            invalid_pitch,np.ones(60,dtype=bool),valid_owner,.01)
        raise AssertionError('Invalid voiced F0 reached spike smoothing')
    except ValueError as error:
        assert '有声 F0' in str(error)

# The shared builder may create a declared derived correction, but it must
# never mutate the measured input arrays.
source_pitch=np.full(120,60.,dtype=np.float32);source_pitch[60]=65.
source_mask=np.ones(120,dtype=bool)
source_copy=source_pitch.copy();mask_copy=source_mask.copy()
curve=build_absolute_f0_curve(
    source_pitch,source_mask,.01,[(0.,1.2)],clock,inverse_clock)
assert np.array_equal(source_pitch,source_copy) and np.array_equal(source_mask,mask_copy)
assert curve['isolated_spike_policy']['corrected_frames']==1
assert dict(curve['points'])[clock(.60)+1920]==6000

# Sustained note-center review is independent of adjacent-frame jumps. The
# drift below rises only ~3 cents/frame, but remains >300 cents from the
# written note center for well over 160 ms. It must stay in the exported F0 and
# produce a read-only warning tied to the source SHA and note identity.
review_note=SimpleNamespace(start_pos=0,end_pos=2400,length=2400,key_number=60,lyric='戏')
review_intervals,review_descriptors=pitch_note_descriptors(
    [review_note],inverse_clock,vocal_track_index=0)
drift=np.full(250,60.,dtype=np.float32)
drift_mask=np.ones(250,dtype=bool)
drift[:8]=66.       # Head evidence stays inside the Chinese-opera guard.
drift[8:140]=np.linspace(60.,64.,132)
drift[140:242]=64.
drift[242:]=54.     # Tail evidence stays inside the Chinese-opera guard.
drift_copy=drift.copy()
assert np.max(np.abs(np.diff(drift[8:140])))*100<300
review_curve=build_absolute_f0_curve(
    drift,drift_mask,.01,review_intervals,clock,inverse_clock,
    note_descriptors=review_descriptors,source_sha256='a'*64,
    vocal_track_index=0)
offset_audit=review_curve['pitch_offset_diagnostics']
assert offset_audit['status']=='review_required'
assert offset_audit['warning_count']==1
assert offset_audit['warnings'][0]['note']['note_id'].startswith('vocal-0:note-0:')
assert offset_audit['warnings'][0]['review_target']=='note_pitch_or_f0_curve'
assert offset_audit['warnings'][0]['interval']['duration_seconds']>=.16
assert offset_audit['warnings'][0]['source_sha256']=='a'*64
assert offset_audit['edge_guard_suppressed_frames']>=8
assert offset_audit['truth_determined'] is False
assert offset_audit['automatic_change'] is False
assert dict(review_curve['points'])[clock(1.5)+1920]==6400
assert np.array_equal(drift,drift_copy)

# A normal sub-300-cent vibrato does not become a sustained-offset warning.
vibrato_note=SimpleNamespace(start_pos=0,end_pos=1200,length=1200,key_number=60,lyric='啊')
vibrato_intervals,vibrato_descriptors=pitch_note_descriptors(
    [vibrato_note],inverse_clock,vocal_track_index=0)
vibrato_pitch=(60+.2*np.sin(np.arange(120)*.45)).astype(np.float32)
vibrato_curve=build_absolute_f0_curve(
    vibrato_pitch,np.ones(120,dtype=bool),.01,vibrato_intervals,clock,inverse_clock,
    note_descriptors=vibrato_descriptors,source_sha256='b'*64,vocal_track_index=0)
assert vibrato_curve['pitch_offset_diagnostics']['warning_count']==0
assert vibrato_curve['pitch_offset_diagnostics']['truth_determined'] is False

# A brief expressive excursion can cross 300 cents without becoming a
# sustained-offset warning. This keeps fast ornaments and transition details
# in review evidence rather than treating them as errors by threshold alone.
brief_pitch=np.full(120,60.,dtype=np.float32)
brief_pitch[50:53]=63.5
brief_curve=build_absolute_f0_curve(
    brief_pitch,np.ones(120,dtype=bool),.01,vibrato_intervals,clock,inverse_clock,
    note_descriptors=vibrato_descriptors,source_sha256='c'*64,vocal_track_index=0)
brief_audit=brief_curve['pitch_offset_diagnostics']
assert brief_audit['warning_count']==0
assert brief_audit['short_offset_candidates']>=1

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
    f0[150]=65.
    f0[300:400]=72+.2*np.sin(np.arange(100)*.3)
    f0[340:365]=np.nan
    raw_f0=f0.copy()
    reference=RmvpeResult(.01,f0,np.isfinite(f0))
    cache=folder/'voice.pitch.npz';save_pitch(cache,reference);restored=load_pitch(cache)
    assert np.array_equal(restored.midi_pitch,reference.midi_pitch,equal_nan=True)
    assert restored.source_sha256==hashlib.sha256(cache.read_bytes()).hexdigest()
    score=Project(song_tempo_list=[SongTempo(position=0,bpm=120)],
        time_signature_list=[TimeSignature(bar_index=0,numerator=4,denominator=4)],
        track_list=[SingingTrack(title='Lead',note_list=[Note(start_pos=960,length=960,key_number=60,lyric='你'),
                                                     Note(start_pos=2880,length=960,key_number=72,lyric='好')])])
    report=absolute_curve(score.track_list[0],score,restored)
    assert report['algorithm']=='absolute-f0-v2'
    assert report['points']==175 and report['runs']==3
    assert np.array_equal(f0,raw_f0,equal_nan=True)
    assert report['isolated_spike_policy']['corrected_frames']==1
    assert report['isolated_spike_policy']['corrections'][0]['frame']==150
    points=score.track_list[0].edited_params.pitch.points.root
    # Source points stay inside each note. The bounded two-tick head guard and
    # four-tick tail guard belong to the immediately adjacent edge only; they
    # must never fill the long rest between the phrases.
    near_edges = all(p.y==-100 or 2878<=p.x<3844 or 4798<=p.x<5764 for p in points)
    assert near_edges, [(p.x,p.y) for p in points if not (p.y==-100 or 2878<=p.x<3844 or 4798<=p.x<5764)]
    assert not any(p.y!=-100 and 3844<=p.x<4798 for p in points)
    assert not any(p.y!=-100 and p.x<2878 for p in points)
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
