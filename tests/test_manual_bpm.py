"""Exercise fractional tempo through the real bridge, merge and editor exporters."""
import json,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(APP),str(APP/'tools/research')]
import numpy as np
import soundfile as sf
import mido
from common import atomic_json,file_hash,midi_signature
from music_timing import validate_bpm,effective_bpm
from pipeline import create_job
from workflow import validate_options
from midi_bridge import run
from midi_merge import merge_voices
from project_exports import export_job,library
from note_benchmark import read_midi
from subtitles import midi_cues
from v2m_runtime import configure
configure(APP/'dependencies/vocal2midi2')

assert validate_bpm(None) is None and effective_bpm({})==120.
for value in (True,False,'120',0,-1,19.99,400.01,float('nan'),float('inf'),[],{}):
    try:
        validate_bpm(value)
        raise AssertionError(('Invalid BPM accepted',value))
    except ValueError:
        pass
for value in (20.,137.5,400.):
    assert validate_options({'bpm':value})['bpm']==value
assert validate_options({})['bpm'] is None

with tempfile.TemporaryDirectory(dir=APP/'work',prefix='manual-bpm-') as temp:
    root=Path(temp);source=root/'source.wav'
    wave=(.15*np.sin(np.arange(44100*3)*2*np.pi*440/44100)).astype('float32')
    sf.write(source,wave,44100,subtype='FLOAT')
    original_hash=file_hash(source)
    comparisons=[]
    for chosen in (None,72.,137.5,200.,123.45):
        language='ja' if chosen==123.45 else 'zh'
        labels=('no','on') if language=='ja' else ('你','好')
        job=create_job(local_music=source,parent=root/'jobs',remember=False,
            language=language,midi_backend='v2',midi_device='cpu',cpu_threads=4,bpm=chosen,
            project_formats=['ustx','vsqx','svp'],export_pitch=True)
        request=json.loads((job/'request.json').read_text(encoding='utf-8'))
        assert request['bpm']==chosen
        tempo=effective_bpm(request)
        (job/'audio').mkdir()
        for voice in ('lead','backing','instrumental'):
            import shutil
            shutil.copy2(source,job/'audio'/(voice+'.wav'))
        signature=midi_signature(job,request,'lead')
        assert signature==midi_signature(job,dict(request,bpm=tempo),'lead')
        assert signature!=midi_signature(job,dict(request,bpm=tempo+1),'lead')
        def native_fixture(cfg):
            from inference.pipeline import auto_lyric_hybrid
            from inference.API.ustx_api import save_ustx
            from inference.API.vsqx_api import save_vsqx
            from inference.API.rmvpe_api import RmvpeResult
            from pitch_accuracy import save_pitch
            assert cfg.tempo==tempo
            voice=Path(cfg.output_filename).stem
            base=60 if voice=='lead' else 64
            notes=[SimpleNamespace(onset=.5,offset=1.4,pitch=base,lyric=labels[0]),
                   SimpleNamespace(onset=1.5,offset=2.4,pitch=base+2,lyric=labels[1])]
            # Reproduce the upstream int(tempo) call, including fractional BPM.
            auto_lyric_hybrid._save_midi(notes,cfg.output_dir/(voice+'.mid'),int(cfg.tempo))
            for suffix in ('csv','txt'):
                (cfg.output_dir/(voice+'.'+suffix)).write_text('fixture',encoding='utf-8')
            pitch=np.zeros(300,dtype='float32')
            pitch[50:140]=base+.25*np.sin(np.arange(50,140)*.21)
            pitch[150:240]=base+2+.25*np.sin(np.arange(150,240)*.21)
            rmvpe=RmvpeResult(.01,pitch,pitch>0)
            save_pitch(cfg.output_dir/(voice+'.pitch.npz'),rmvpe)
            save_ustx(notes,cfg.output_dir/(voice+'.ustx'),tempo=cfg.tempo,rmvpe_result=rmvpe)
            save_vsqx(notes,cfg.output_dir/(voice+'.vsqx'),tempo=cfg.tempo,language='zh',rmvpe_result=rmvpe)
        with patch('application.pipeline.run_auto_lyric_job',side_effect=native_fixture):
            run(job,'lead');run(job,'backing')
        report=merge_voices([('Lead Vocal',job/'midi/lead/lead.mid'),
                             ('Backing Vocal',job/'midi/backing/backing.mid')],
                             job/'midi/voices.mid',bpm=chosen)
        assert report['tempo']==tempo
        for path in (job/'midi/lead/lead.mid',job/'midi/backing/backing.mid',job/'midi/voices.mid'):
            midi=mido.MidiFile(path,charset='utf8')
            assert [m.tempo for t in midi.tracks for m in t if m.type=='set_tempo']==[mido.bpm2tempo(tempo)]
            notes=read_midi(path)
            for note in notes:
                expected=(.5,1.4) if round(note['pitch']) in (60,64) else (1.5,2.4)
                assert abs(note['onset']-expected[0])<.002
                assert abs(note['offset']-expected[1])<.002
        cues=midi_cues(job/'midi/lead/lead.mid',reference_lines=list(labels))
        assert abs(cues[0].start-.5)<.002 and abs(cues[-1].end-2.4)<.002
        export_job(job,request)
        converters,_,SingingTrack,_=library()
        from libresvip.core.time_sync import TimeSynchronizer
        row={}
        for fmt in ('ustx','vsqx','svp'):
            project=converters[fmt].load(job/'exports'/('voices.'+fmt),
                {'import_pitch':True,'import_instrumental_track':True})
            assert abs(project.song_tempo_list[0].bpm-tempo)<.011,(fmt,tempo,project.song_tempo_list)
            clock=TimeSynchronizer(project.song_tempo_list)
            tracks=[t for t in project.track_list if isinstance(t,SingingTrack)]
            assert len(tracks)==2
            for track in tracks:
                assert len(track.note_list)==2
                assert [n.lyric for n in track.note_list]==list(labels),(fmt,'Lyrics changed')
                for note,(start,end) in zip(track.note_list,((.5,1.4),(1.5,2.4))):
                    assert abs(clock.get_actual_secs_from_ticks(note.start_pos)-start)<.004,(fmt,tempo,note)
                    assert abs(clock.get_actual_secs_from_ticks(note.end_pos)-end)<.004,(fmt,tempo,note)
                points=[p for p in track.edited_params.pitch.points.root if p.y!=-100]
                assert len(points)>50 and len({p.y for p in points})>8,(fmt,'Pitch curve lost')
                for seconds in (.6,1.,1.9,2.3):
                    point=min(points,key=lambda p:abs(clock.get_actual_secs_from_ticks(p.x-1920)-seconds))
                    assert abs(clock.get_actual_secs_from_ticks(point.x-1920)-seconds)<.035,(fmt,tempo,'Pitch timing')
            audio=[t for t in project.track_list if hasattr(t,'audio_file_path')]
            assert len(audio)==1 and audio[0].offset==0,(fmt,'Audio offset')
            row[fmt]=[clock.get_actual_secs_from_ticks(n.start_pos) for n in tracks[0].note_list]
        comparisons.append(row)
        if language=='ja':
            without_pitch=dict(request,export_pitch=False)
            export_job(job,without_pitch)
            for fmt in ('ustx','vsqx','svp'):
                loaded=converters[fmt].load(job/'exports'/('voices.'+fmt),{'import_pitch':False})
                assert [n.lyric for t in loaded.track_list if isinstance(t,SingingTrack)
                        for n in t.note_list]==list(labels)*2,(fmt,'Pitch-off changed lyrics')
        # Silent tracks use the same tempo, including their native empty projects.
        sf.write(job/'audio/backing.wav',np.zeros(44100,dtype='float32'),44100,subtype='FLOAT')
        with patch('application.pipeline.run_auto_lyric_job',side_effect=AssertionError('Silent input ran ASR')):
            run(job,'backing')
        empty=mido.MidiFile(job/'midi/backing/backing.mid')
        assert next(m.tempo for t in empty.tracks for m in t if m.type=='set_tempo')==mido.bpm2tempo(tempo)
        import yaml
        assert yaml.safe_load((job/'midi/backing/backing.ustx').read_text(encoding='utf-8'))['tempos'][0]['bpm']==tempo
    assert file_hash(source)==original_hash
    atomic_json(APP/'work/test-results/manual-bpm.json',{'bpms':[None,72.,137.5,200.,123.45],
        'roundtrip_seconds':comparisons,'audio_unchanged':True,'fractional_midi_call_tested':True})
print('PASS: manual BPM validation, real bridge, fractional MIDI, dual merge, three editor formats, pitch/audio timing and subtitles')
