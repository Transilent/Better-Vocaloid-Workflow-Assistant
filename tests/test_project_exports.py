"""Round-trip real pitched USTX/VSQX/SVP files and encode actual audio."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
os.environ['NUMBA_CACHE_DIR']=str(APP/'work/test-numba-cache')
from v2m_runtime import configure
configure(APP/'dependencies/vocal2midi2')
import numpy as np
import soundfile as sf
from inference.API.ustx_api import save_ustx
from inference.API.rmvpe_api import RmvpeResult
from project_exports import export_job,library,outputs_intact
from common import load_config

with tempfile.TemporaryDirectory(dir=APP/'work',prefix='project-formats-') as temp:
    job=Path(temp)
    (job/'audio').mkdir()
    wave=(.2*np.sin(np.arange(44100*3)*2*np.pi*440/44100)).astype('float32')
    for voice in ('lead','backing','instrumental'):
        sf.write(job/'audio'/(voice+'.wav'),wave,44100,subtype='PCM_16')
    for voice,base in (('lead',60),('backing',64)):
        out=job/'midi'/voice
        out.mkdir(parents=True)
        notes=[SimpleNamespace(onset=.5,offset=1.4,pitch=base,lyric='你'),
               SimpleNamespace(onset=1.5,offset=2.4,pitch=base+2,lyric='好')]
        pitch=np.zeros(300,dtype='float32')
        pitch[50:140]=base+.3*np.sin(np.arange(90)*.3)
        pitch[150:240]=base+2+.3*np.sin(np.arange(90)*.3)
        rmvpe=RmvpeResult(.01,pitch,pitch>0)
        save_ustx(notes,out/(voice+'.ustx'),tempo=120,rmvpe_result=rmvpe)
    request={'voice_mode':'dual','language':'zh','project_formats':['ustx','vsqx','svp'],
             'audio_formats':['wav','flac','mp3','ogg'],'export_pitch':True,'tools':load_config(False)}
    report=export_job(job,request)
    assert len(report['projects']['outputs'])==3 and len(report['audio'])==12
    assert all(item['pitch_points']>10 for item in report['projects']['tracks'])
    converters,_,SingingTrack,_=library()
    for fmt in ('ustx','vsqx','svp'):
        project=converters[fmt].load(job/'exports'/('voices.'+fmt),{'import_pitch':True,'import_instrumental_track':False})
        tracks=[track for track in project.track_list if isinstance(track,SingingTrack)]
        assert len(tracks)==2 and [len(track.note_list) for track in tracks]==[2,2]
        assert [note.key_number for note in tracks[0].note_list]==[60,62]
        assert abs(tracks[0].note_list[0].start_pos-480)<=1
        assert abs(tracks[0].note_list[0].length-864)<=1
        assert [note.lyric for note in tracks[0].note_list]==['你','好']
        for track in tracks:
            points=[point for point in track.edited_params.pitch.points.root if point.y!=-100]
            assert len(points)>10, (fmt,'Lost pitch curve')
            assert len({point.y for point in points})>3, (fmt,'Pitch flattened')
    for voice in ('lead','backing','instrumental'):
        info=sf.info(job/'exports/audio'/(voice+'.flac'))
        assert info.frames==len(wave) and info.samplerate==44100
    assert outputs_intact(job,request)
    originals={item['file']:(job/item['file']).stat().st_mtime_ns for item in report['projects']['outputs']+report['audio']}
    with patch('pipeline.run_process',side_effect=AssertionError('Unnecessary audio encoding')), \
         patch('project_exports.library',side_effect=AssertionError('Unnecessary project conversion')):
        export_job(job,request)
    (job/'exports/audio/backing.flac').write_bytes(b'broken file')
    assert not outputs_intact(job,request)
    from pipeline import run_process as real_run_process
    commands=[]
    def encode(command,*args,**kwargs):
        commands.append(command)
        return real_run_process(command,*args,**kwargs)
    with patch('pipeline.run_process',side_effect=encode):
        export_job(job,request)
    assert len(commands)==1 and 'backing.pending.flac' in commands[0][-1]
    assert outputs_intact(job,request)
    assert all((job/name).stat().st_mtime_ns==modified for name,modified in originals.items() if name!='exports/audio/backing.flac')
    added=dict(request,audio_formats=['wav','flac','mp3'])
    with patch('pipeline.run_process',side_effect=AssertionError('Previously encoded format lost')):
        export_job(job,added)
    # A user can tune and save the generated SVP in place. Regeneration must
    # preserve those bytes, while leaving unrelated generated outputs alone.
    edited=converters['svp'].load(job/'exports/voices.svp',{'import_pitch':True,'import_instrumental_track':False})
    next(track for track in edited.track_list if isinstance(track,SingingTrack)).note_list[0].lyric='改'
    converters['svp'].dump(job/'exports/voices.svp',edited,{'down_sample':0,'language_override':'mandarin'})
    edited_bytes=(job/'exports/voices.svp').read_bytes()
    kept_stamps={item['file']:(job/item['file']).stat().st_mtime_ns for item in report['projects']['outputs']+report['audio'] if item['file']!='exports/voices.svp'}
    repaired=export_job(job,request)
    svp=next(item for item in repaired['projects']['outputs'] if item['file']=='exports/voices.svp')
    assert (job/svp['preserved_copy']).read_bytes()==edited_bytes,'In-place tuning was lost'
    assert all((job/name).stat().st_mtime_ns==stamp for name,stamp in kept_stamps.items())
    backup_count=len(list((job/'backups').iterdir()))
    export_job(job,request)
    assert len(list((job/'backups').iterdir()))==backup_count,'Intact generated files created redundant backups'
    # A silent backing part still needs an editable named track in native files.
    save_ustx([],job/'midi/backing/backing.ustx',tempo=120)
    request['audio_formats']=['wav']
    empty_report=export_job(job,request)
    assert [track['notes'] for track in empty_report['projects']['tracks']]==[2,0]
    for fmt in ('ustx','vsqx','svp'):
        text=(job/'exports'/('voices.'+fmt)).read_text(encoding='utf-8')
        assert 'Backing Vocal' in text, (fmt,'Lost empty voice track')
    # Real full-song feedback: minimum-duration padding must not create
    # overlaps between adjacent, sub-10-tick notes in an editor project.
    short=[SimpleNamespace(onset=1.,offset=1.008,pitch=69,lyric='你'),
           SimpleNamespace(onset=1.008,offset=1.016,pitch=69,lyric='好'),
           SimpleNamespace(onset=1.016,offset=1.5,pitch=69,lyric='呀')]
    save_ustx(short,job/'midi/lead/lead.ustx',tempo=120)
    short_report=export_job(job,request)
    assert [row['notes'] for row in short_report['projects']['tracks']]==[3,0]
    for fmt in ('ustx','vsqx','svp'):
        project=converters[fmt].load(job/'exports'/('voices.'+fmt),{'import_pitch':True,'import_instrumental_track':False})
        track=next(track for track in project.track_list if isinstance(track,SingingTrack) and track.note_list)
        assert [(note.start_pos,note.length,note.key_number,note.lyric) for note in track.note_list]==[
            (960,8,69,'你'),(968,7,69,'好'),(975,465,69,'呀')],fmt
        assert all(a.end_pos<=b.start_pos for a,b in zip(track.note_list,track.note_list[1:]))
    # Float separation stems can legitimately exceed full scale. Integer FLAC
    # must preserve their shape with one shared gain instead of hard clipping.
    from common import file_hash
    for voice,scale in (('lead',10),('backing',8),('instrumental',12)):
        sf.write(job/'audio'/(voice+'.wav'),wave*scale,44100,subtype='FLOAT')
    before={voice:file_hash(job/'audio'/(voice+'.wav')) for voice in ('lead','backing','instrumental')}
    request['audio_formats']=['wav','flac']
    peak_report=export_job(job,request)
    gain=peak_report['audio_encoding']['gain']
    assert .39 < gain < .40
    for voice in before:
        original,_=sf.read(job/'audio'/(voice+'.wav'),dtype='float32')
        encoded,_=sf.read(job/'exports/audio'/(voice+'.flac'),dtype='float32')
        assert file_hash(job/'audio'/(voice+'.wav'))==before[voice]
        assert encoded.shape==original.shape and np.max(np.abs(encoded))<=.950001
        assert np.max(np.abs(encoded-original*gain))<2e-7,'Integer export clipped or changed stem balance'
    assert outputs_intact(job,request)
    encoded_stamps={item['file']:(job/item['file']).stat().st_mtime_ns for item in peak_report['audio']}
    with patch('pipeline.run_process',side_effect=AssertionError('Stable gain triggered redundant encoding')):
        export_job(job,request)
    assert all((job/name).stat().st_mtime_ns==stamp for name,stamp in encoded_stamps.items())
print('PASS: two tracks, note timing/pitches/lyrics, non-flat pitch in USTX/VSQX/SVP, WAV reuse and FLAC/MP3/OGG encoding')
