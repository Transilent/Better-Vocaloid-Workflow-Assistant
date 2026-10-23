"""Round-trip real pitched USTX/VSQX/SVP files and encode actual audio."""
import json
import os
from pathlib import Path
import sys
import tempfile
import shutil
from types import SimpleNamespace
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from common import native_path
os.environ['NUMBA_CACHE_DIR']=native_path(APP/'work/test-numba-cache')
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
    starting_directory=Path.cwd()
    with patch('project_exports.os.chdir',side_effect=AssertionError('Project export changed the global working directory')):
        report=export_job(job,request)
    assert Path.cwd()==starting_directory
    assert len(report['projects']['outputs'])==3 and len(report['audio'])==12
    # Styles are independent SVP candidates over the verified cached baseline.
    # Switching/retrying a style must not reconvert or compound the baseline.
    import copy
    from common import file_hash
    import task_contract
    base_hashes={fmt:file_hash(job/'exports'/('voices.'+fmt)) for fmt in ('ustx','vsqx','svp')}
    baseline=json.loads((job/'exports/voices.svp').read_text(encoding='utf-8-sig').rstrip('\x00'))
    styled={**request,'performance_style':'soft_ballad','style_strength':.6,'style_scope':'lead'}
    with patch('project_exports.library',side_effect=AssertionError('A cached baseline was reconverted')):
        style_report=export_job(job,styled)
    style_item=style_report['styles']['outputs'][0];candidate=job/style_item['file']
    styled_data=json.loads(candidate.read_text(encoding='utf-8'))
    assert styled_data['tracks'][1:]==baseline['tracks'][1:]
    assert styled_data['tracks'][0]['mainGroup']['notes']==baseline['tracks'][0]['mainGroup']['notes']
    assert all(file_hash(job/'exports'/('voices.'+fmt))==digest for fmt,digest in base_hashes.items())
    assert outputs_intact(job,styled)
    # Every algorithm dependency must invalidate candidate reuse even when the
    # verified baseline bytes and user settings remain unchanged.
    import project_exports
    style_path,style_signature=project_exports._style_target(job,styled,{'sha256':base_hashes['svp']})
    for dependency in ('style_presets.py','style_pitch.py','calibration_refinement.py','vocal_expression.py','expression_calibration.py'):
        def updated_hash(path):
            value=file_hash(path)
            return value+'-updated' if Path(path).name==dependency else value
        with patch('project_exports.file_hash',side_effect=updated_hash):
            updated_path,updated_signature=project_exports._style_target(job,styled,{'sha256':base_hashes['svp']})
            assert updated_signature!=style_signature and updated_path!=style_path,dependency
    style_bytes=candidate.read_bytes()
    with patch('style_presets.apply_style',side_effect=AssertionError('Cached style was compounded')):
        export_job(job,styled)
    assert candidate.read_bytes()==style_bytes
    candidate.write_bytes(style_bytes+b' ')
    assert not outputs_intact(job,styled)
    restored=export_job(job,styled)['styles']['outputs'][0]
    assert candidate.read_bytes()==style_bytes and (job/restored['preserved_copy']).is_file()
    all_voices={**styled,'style_scope':'all_vocals'}
    assert not outputs_intact(job,all_voices)
    next_item=export_job(job,all_voices)['styles']['outputs'][0]
    assert next_item['file']!=style_item['file'] and candidate.is_file()
    assert outputs_intact(job,all_voices)
    entries=task_contract.read(job)['entries'].values()
    assert any(row.get('active') is False and row.get('location',{}).get('path')==style_item['file'] for row in entries)
    pitched_request={**styled,'style_pitch':True}
    pitched_item=export_job(job,pitched_request)['styles']['outputs'][0]
    assert pitched_item['file'] not in (style_item['file'],next_item['file'])
    pitch_receipt=pitched_item['adaptation']['pitch']
    assert pitch_receipt['enabled'] and outputs_intact(job,pitched_request)
    source_mode=baseline['tracks'][0]['mainGroup']['parameters']['pitchDelta'].get('mode','cubic')
    if source_mode!='linear':
        assert pitch_receipt['status']=='skipped' and not pitched_item['adaptation']['pitch_changed']
        assert pitch_receipt['tracks'][0]['reason']=='preserved_nonlinear'
    else:assert pitched_item['adaptation']['pitch_changed'],json.dumps(pitch_receipt,ensure_ascii=False)
    assert all(file_hash(job/'exports'/('voices.'+fmt))==digest for fmt,digest in base_hashes.items())
    pitched_path=job/pitched_item['file'];pitched_bytes=pitched_path.read_bytes()
    with patch('style_presets.apply_style',side_effect=AssertionError('Pitch candidate was compounded')):
        export_job(job,pitched_request)
    assert pitched_path.read_bytes()==pitched_bytes
    # Disabling styles retires receipts without deleting either trial; the
    # ordinary original-format output and subsequent tests remain unaffected.
    export_job(job,request)
    assert outputs_intact(job,request) and candidate.is_file() and (job/next_item['file']).is_file()
    assert pitched_path.is_file()
    import task_contract
    task_contract.begin_stage(job,'exports')
    export_job(job,request)
    assert all(row['state']=='verified' for row in task_contract.inspect(job)), 'Cache reuse left committed files stale'
    assert all(item['pitch_points']>10 for item in report['projects']['tracks'])
    # Project export remains independent when the audio-export card is disabled.
    with patch('project_exports.audio_inputs', side_effect=AssertionError('Disabled audio was scanned')):
        projects_only=dict(request,audio_formats=[])
        project_report=export_job(job,projects_only)
        assert len(project_report['projects']['outputs'])==3 and project_report['audio']==[]
        assert outputs_intact(job,projects_only)
    converters,_,SingingTrack,_=library()
    # Import the actual accompaniment after moving the complete task folder.
    with tempfile.TemporaryDirectory(dir=APP/'work',prefix='relocated-') as moved:
        moved_job=Path(moved)/'中文 song';shutil.copytree(job,moved_job)
        for fmt in ('ustx','vsqx','svp'):
            target=moved_job/'exports'/('voices.'+fmt)
            assert str(job) not in target.read_text(encoding='utf-8'),fmt
            loaded=converters[fmt].load(target,{'import_pitch':True,'import_instrumental_track':True})
            audio=[track for track in loaded.track_list if hasattr(track,'audio_file_path')]
            assert len(audio)==1,(fmt,'Missing accompaniment')
            audio_path=Path(audio[0].audio_file_path)
            if not audio_path.is_absolute():audio_path=target.parent/audio_path
            assert audio_path.resolve().is_relative_to(moved_job) and audio_path.is_file(),(fmt,audio_path)
            if fmt=='vsqx':
                info=sf.info(audio_path)
                assert info.subtype=='PCM_16' and info.samplerate==44100 and info.channels==2
    companion=job/'exports/voices.wavparts/instrumental.wav'
    assert companion.is_file()
    old_bytes=companion.read_bytes();companion.unlink()
    assert next(row for row in task_contract.inspect(job) if row['path']==str(job/'exports/voices.vsqx'))['state']=='stale'
    assert not outputs_intact(job,request)
    export_job(job,request)
    assert companion.read_bytes()==old_bytes and outputs_intact(job,request)
    assert next(row for row in task_contract.inspect(job) if row['path']==str(job/'exports/voices.vsqx'))['state']=='verified'
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
    # Disabling pitch must ignore old pitched native caches and remove flat
    # converter defaults. Notes, lyrics, timing and accompaniment still survive.
    notes_only=dict(request,export_pitch=False,audio_formats=['wav'])
    assert not outputs_intact(job,notes_only), 'Pitch toggle reused the previous project'
    plain=export_job(job,notes_only)
    assert all(item['pitch_points']==0 for item in plain['projects']['tracks'])
    import yaml
    import xml.etree.ElementTree as ET
    ustx=yaml.safe_load((job/'exports/voices.ustx').read_text(encoding='utf-8'))
    for part in ustx['voice_parts']:
        assert not any(curve.get('abbr') in ('pitd','pbs') for curve in part.get('curves',[]))
        assert all('pitch' not in note and note['vibrato']=={'length':0} for note in part['notes'])
    svp=json.loads((job/'exports/voices.svp').read_bytes().removesuffix(b'\x00'))
    for track in svp['tracks']:
        group=track['mainGroup']
        assert 'pitchDelta' not in group.get('parameters',{}) and not group.get('pitchControls')
        assert all(not any(key.startswith(('tF0','dF0','pF0')) for key in note.get('attributes',{})) for note in group['notes'])
    assert any(track.get('mainRef',{}).get('isInstrumental') for track in svp['tracks'])
    vsqx=ET.parse(job/'exports/voices.vsqx')
    assert not any(element.get('id') in ('P','S','PIT','PBS','pit','pbs') for element in vsqx.iter())
    for fmt in ('ustx','vsqx','svp'):
        loaded=converters[fmt].load(job/'exports'/('voices.'+fmt),{'import_pitch':False,'import_instrumental_track':False})
        vocal=[track for track in loaded.track_list if isinstance(track,SingingTrack)]
        assert [len(track.note_list) for track in vocal]==[2,2]
        assert [note.lyric for note in vocal[0].note_list]==['你','好']
        assert [(note.start_pos,note.length) for note in vocal[0].note_list]==[(480,864),(1440,864)]
    assert outputs_intact(job,notes_only)
    restored=export_job(job,request)
    assert all(row['pitch_points']>10 for row in restored['projects']['tracks'])
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
    # A same-song redo must retain the fitted controls while keeping revised
    # notes and measured pitch. Losing the source fails before replacing output.
    from calibration_session import project_data
    from calibration_workflow import register_expression,inherit_expression
    from expression_calibration import CONTROLS
    from common import atomic_json,file_hash
    import copy
    sf.write(job/'audio/source.wav',wave,44100,subtype='FLOAT')
    atomic_json(job/'request.json',request)
    tuned=project_data(job/'exports/voices.svp')
    for name,(lo,hi,neutral,step) in CONTROLS.items():
        tuned['tracks'][0]['mainGroup'].setdefault('parameters',{})[name]={
            'mode':'linear','points':[0,neutral,705600000,neutral+step*.3,1411200000,neutral]}
    tuned['tracks'][0]['mainRef']['database']={'name':'Retained Test Voice'}
    original_tuning=job/'exports/fitted.svp';atomic_json(original_tuning,tuned)
    assert register_expression(job,original_tuning)
    # A user can save or refine the same score in SV before using redo.
    tuned['tracks'][0]['mainGroup']['parameters']['tension']['points'][3]=.11
    atomic_json(original_tuning,tuned)
    child=job/'redo';child.mkdir()
    for folder in ('audio','midi'):shutil.copytree(job/folder,child/folder)
    new_request={'voice_mode':'dual','language':'zh','project_formats':['svp'],'audio_formats':['wav'],
                 'export_pitch':True,'tools':request['tools']}
    # First serialize the revised score without parameters as a comparison.
    export_job(child,new_request)
    fresh=project_data(child/'exports/voices.svp')
    inherit_expression(job,child,new_request)
    restored=export_job(child,new_request)
    saved=project_data(child/'exports/voices.svp')
    for current,baseline in zip(saved['tracks'],fresh['tracks']):
        # LibreSVIP assigns fresh UUIDs on every serialization.
        musical_notes=lambda t:[{k:v for k,v in n.items() if k!='uuid'} for n in t.get('mainGroup',{}).get('notes',[])]
        assert musical_notes(current)==musical_notes(baseline)
        assert current.get('mainGroup',{}).get('parameters',{}).get('pitchDelta')==baseline.get('mainGroup',{}).get('parameters',{}).get('pitchDelta')
    for name in CONTROLS:
        curve=saved['tracks'][0]['mainGroup']['parameters'][name]
        prior=tuned['tracks'][0]['mainGroup']['parameters'][name]
        lookup=dict(zip(curve['points'][::2],curve['points'][1::2]))
        assert all(lookup[x]==v for x,v in zip(prior['points'][::2],prior['points'][1::2]))
        assert len(curve['points'])>len(prior['points']) and curve['mode']==prior['mode']
        state=restored['projects']['outputs'][0]['expression_density']['tracks'][0]['controls'][name]
        assert state['original_knots_preserved'] and state['max_changing_gap_ms']<=5.00001
    assert restored['projects']['outputs'][0]['retained_expression']['lead']['new_fit'] is False
    assert outputs_intact(child,new_request)
    # Sampling mode must reach retained automation and change only SVP cache.
    raw_request={**new_request,'expression_density':'original'}
    raw_export=export_job(child,raw_request)
    raw_saved=project_data(child/'exports/voices.svp')
    for name in CONTROLS:
        assert raw_saved['tracks'][0]['mainGroup']['parameters'][name]==tuned['tracks'][0]['mainGroup']['parameters'][name]
    assert raw_export['projects']['outputs'][0]['expression_density']['status']=='preserved_original'
    assert outputs_intact(child,raw_request) and not outputs_intact(child,new_request)
    export_job(child,new_request)
    final_hash=file_hash(child/'exports/voices.svp')
    (child/new_request['retained_expression']['tracks']['lead']['file']).write_text('{}')
    try:export_job(child,new_request)
    except ValueError:pass
    else:raise AssertionError('Missing expression silently became a neutral project')
    assert file_hash(child/'exports/voices.svp')==final_hash
print('PASS: native formats, audio, parameter retention, note/pitch preservation and failed-source protection')
