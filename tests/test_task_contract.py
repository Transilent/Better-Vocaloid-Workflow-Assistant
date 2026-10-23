"""Task evidence, partial commits, invalidation, human handoff and cleanup boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import numpy as np
import soundfile as sf
import common
import pipeline
import storage
import task_contract as contract
import task_store
import workflow


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rejected(action, message):
    try: action()
    except (ValueError, OSError): return
    raise AssertionError(message)


with tempfile.TemporaryDirectory(dir=APP/'work', prefix='task-contract-') as temp:
    root = Path(temp)
    wave = (.1*np.sin(np.arange(44100)*2*np.pi*440/44100)).astype('float32')
    source = root/'source.wav'; sf.write(source, wave, 44100)
    stems = {}
    for name in ('lead', 'backing', 'instrumental'):
        p = root/(name+'.wav'); sf.write(p, wave/3, 44100); stems[name] = str(p)
    job = pipeline.create_job(local_music=source, parent=root/'jobs', voice_mode='import',
        imported_stems=stems, remember=False,
        workflow_graph=workflow.builtins()['内置 · 仅分离音频']['graph'])
    request_hash = common.file_hash(job/'request.json')
    pipeline.run(job)
    rows = contract.inspect(job)
    assert rows and all(row['state'] == 'verified' for row in rows), [(r['role'],r['state']) for r in rows]
    assert contract.handoff(job) == 'audio_ready'
    assert load(job/'status.json')['planned_steps'] == ['metadata','download','audio','separation']
    index_hash = {r['id']: r['sha256'] for r in rows}
    # A cache hit must not rehash all audio for indexing or rewrite request options.
    with patch.object(contract, 'file_hash', side_effect=AssertionError('Known files rehashed during migration')):
        contract.reconcile(job)
    assert common.file_hash(job/'request.json') == request_hash

    lead = job/'audio/lead.wav'; original = lead.read_bytes()
    sf.write(lead, wave/5, 44100)  # Valid PCM, same length, different content.
    assert next(r for r in contract.inspect(job) if r['path'] == str(lead))['state'] == 'modified'
    contract.reconcile(job)
    assert {r['id']:r['sha256'] for r in contract.inspect(job)} == index_hash
    lead.write_bytes(original)
    # Successful voice commits survive a later voice failure and can be migrated.
    voice = job/'midi/lead'; voice.mkdir(parents=True)
    import mido
    midi = mido.MidiFile(); track = mido.MidiTrack(); midi.tracks.append(track)
    track.extend([mido.Message('note_on', note=60), mido.Message('note_off', note=60, time=480)])
    midi.save(voice/'lead.mid')
    common.atomic_json(voice/'report.json', {'midi_sha256':common.file_hash(voice/'lead.mid'),
        'output_sha256':{'mid':common.file_hash(voice/'lead.mid')}, 'input_signature':'voice-revision-1'})
    contract.begin_stage(job,'midi'); contract.index_voice(job,'lead',replace=True)
    contract.fail_stage(job,'midi','failed','backing inference failed')
    assert next(r for r in contract.inspect(job) if r['role']=='midi')['state']=='verified'
    # Export each format immediately. Failure to produce another format cannot erase it.
    project = job/'exports/voices.svp'; project.parent.mkdir()
    common.atomic_json(project, {'tracks':[{'mainGroup':{'notes':[{'onset':0,'duration':100,'pitch':60,'lyrics':'a'}]},'mainRef':{}}]})
    from project_exports import commit_receipt
    contract.begin_stage(job,'exports')
    commit_receipt(job, project, 'export-revision-1', {})
    contract.fail_stage(job,'exports','failed','next format failed')
    assert next(r for r in contract.inspect(job) if r['path']==str(project))['state']=='verified'
    with patch.object(contract,'file_hash',side_effect=AssertionError('Known export rehashed during receipt migration')):
        contract.reconcile(job)
    project.write_text(project.read_text(encoding='utf-8')+' ',encoding='utf-8')
    assert next(r for r in contract.inspect(job) if r['path']==str(project))['state']=='modified'
    # Editing the generated project never rewrites it during a file check.
    modified_hash = common.file_hash(project); contract.reconcile(job)
    assert common.file_hash(project)==modified_hash
    contract.bind(job,'project',project)
    mixdown = job/'temp/user-mix.wav'; mixdown.parent.mkdir(exist_ok=True); sf.write(mixdown,wave,44100)
    contract.bind(job,'mixdown',mixdown)
    assert contract.selected(job,'mixdown')==mixdown
    assert contract.handoff(job)=='mixdown_ready'
    with patch.object(task_store,'job_directories',return_value=[job]):
        task_store.rename(job,'User task')
    assert load(job/'task.json')['bindings']['mixdown'] and load(job/'task.json')['name']=='User task'
    # Rebuilding an index retains selections and their original hashes.
    contract.rebuild(job)
    assert contract.selected(job,'mixdown')==mixdown
    project.write_text(project.read_text(encoding='utf-8')+' ',encoding='utf-8')
    rejected(lambda:contract.selected(job,'mixdown'),'A changed human score silently reused its old mix')
    assert contract.handoff(job,verify=True)=='needs_review'
    # Explicit re-registration accepts a new manual revision; old dependents stay stale.
    contract.bind(job,'project',project)
    rejected(lambda:contract.selected(job,'mixdown'),'Old mix accepted after score revision')
    contract.bind(job,'mixdown',mixdown)
    assert contract.selected(job,'mixdown')==mixdown

    # Referenced files in an otherwise disposable temp directory must survive cleanup.
    with patch.object(storage,'job_directories',return_value=[job]):
        assert job/'temp' not in [r['path'] for r in storage.candidates()]
        protected = next(r for r in storage.candidates(include_protected=True) if r['path']==job/'temp')
        assert protected['protected']
        rejected(lambda:storage.clean([job/'temp']),'Cleanup removed a live Mixdown')
    other = root/'other'; other.mkdir(); common.atomic_json(other/'request.json',{})
    contract.bind(other,'mixdown',mixdown)
    with patch.object(task_store,'job_directories',return_value=[job,other]):
        rejected(lambda:task_store.delete(job),'Deleted a task referenced by another task')
    contract.unbind(other,'mixdown')
    contract.unbind(job,'mixdown')
    with patch.object(storage,'job_directories',return_value=[job,other]):
        assert job/'temp' in [r['path'] for r in storage.candidates()]
        assert storage.clean([job/'temp'])>0

    # Metadata/failed-step contracts are exercised through the real pipeline wrapper.
    def failing(*_): raise RuntimeError('reproducible stage failure')
    with patch.object(pipeline,'STEPS',[(key,title,failing if key=='separation' else fn) for key,title,fn in pipeline.STEPS]):
        (job/'audio/lead.wav').unlink()
        try: pipeline.run(job)
        except RuntimeError as error: assert str(error)=='reproducible stage failure'
        else: raise AssertionError('Injected failure ignored')
    state = load(job/'status.json')
    assert state['state']=='failed' and state['steps']['separation']['state']=='failed'
    assert 'run_started' not in state
    cancelled_job = pipeline.create_job(local_music=source, parent=root/'jobs', remember=False)
    def stopped(*_): raise InterruptedError('user stopped')
    with patch.object(pipeline,'STEPS',[(key,title,stopped if key=='metadata' else fn) for key,title,fn in pipeline.STEPS]), \
            patch.object(contract,'fail_stage',side_effect=OSError('index temporarily unavailable')):
        try: pipeline.run(cancelled_job,until='metadata')
        except InterruptedError as error: assert str(error)=='user stopped'
        else: raise AssertionError('Cancellation swallowed')
    cancelled_state=load(cancelled_job/'status.json')
    assert cancelled_state['state']=='cancelled' and cancelled_state['steps']['metadata']['state']=='cancelled'
    assert cancelled_state['artifact_error']=='index temporarily unavailable'
    # New source bytes invalidate descendants even before a new output is written.
    contract.begin_stage(job,'audio')
    assert any(r['state']=='stale' for r in contract.inspect(job) if r['role']=='midi')

    # Untrusted paths, unsupported schemas and cycles fail closed.
    clean = root/'bounds'; clean.mkdir(); common.atomic_json(clean/'request.json',{})
    p=clean/'kept.txt';p.write_text('kept',encoding='utf-8')
    contract.register(clean,[{'path':p,'role':'report'}],'metadata')
    ledger=load(clean/'artifacts.json'); original_index=json.dumps(ledger)
    key=next(iter(ledger['entries']))
    ledger['entries'][key]['location']['path']='../source.wav'
    common.atomic_json(clean/'artifacts.json',ledger)
    rejected(lambda:contract.inspect(clean),'Traversal accepted')
    rejected(lambda:contract.protected_references([clean]),'Cleanup guessed through a broken manifest')
    common.atomic_json(clean/'artifacts.json',json.loads(original_index))
    ledger=json.loads(original_index);ledger['schema']=999;common.atomic_json(clean/'artifacts.json',ledger)
    rejected(lambda:contract.read(clean),'Unknown schema accepted')
    assert load(clean/'artifacts.json')['schema']==999
    common.atomic_json(clean/'artifacts.json',json.loads(original_index))
    ledger=load(clean/'artifacts.json');ledger['entries'][key]['dependencies']=[{'id':key,'revision':ledger['entries'][key]['revision']}]
    common.atomic_json(clean/'artifacts.json',ledger)
    assert contract.inspect(clean)[0]['state']=='stale'
    backup=contract.rebuild(clean);assert backup.is_file() and p.read_text(encoding='utf-8')=='kept'

    # Two worker processes commit separate records without losing one another's rows.
    concurrent=root/'concurrent';concurrent.mkdir()
    for name in ('a','b'):
        (concurrent/(name+'.txt')).write_text(name,encoding='utf-8')
    code="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import task_contract as c;j=Path(sys.argv[2]);c.register(j,[{'path':j/(sys.argv[3]+'.txt'),'role':'report'}],'metadata')"
    processes=[subprocess.Popen([sys.executable,'-B','-c',code,str(APP),str(concurrent),name],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for name in ('a','b')]
    for process in processes:
        out,err=process.communicate(timeout=30);assert process.returncode==0,err.decode(errors='replace')
    assert len(contract.read(concurrent)['entries'])==2
    # An empty lock file can already be locked by a process starting first.
    # The second writer must wait for ownership before initializing byte zero.
    import msvcrt,time
    cold=root/'cold-lock';cold.mkdir();(cold/'a.txt').write_text('a',encoding='utf-8')
    ready_code=code.replace('c.register(j,',"print('ready',flush=True);c.register(j,")
    with (cold/'contract.lock').open('a+b') as owner:
        owner.seek(0);msvcrt.locking(owner.fileno(),msvcrt.LK_NBLCK,1)
        proc=subprocess.Popen([sys.executable,'-B','-c',ready_code,str(APP),str(cold),'a'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            assert proc.stdout.readline().strip()==b'ready'
            time.sleep(.1)
            early=proc.poll()
        finally:
            owner.seek(0);msvcrt.locking(owner.fileno(),msvcrt.LK_UNLCK,1)
        out,err=proc.communicate(timeout=30)
        assert early is None,'Writer did not wait for the existing byte lock'
        assert proc.returncode==0,err.decode(errors='replace')
    assert len(contract.read(cold)['entries'])==1
    # A lost index plus a deleted/corrupted old project must not stop the scan.
    lost=root/'lost-index';(lost/'exports').mkdir(parents=True)
    common.atomic_json(lost/'request.json',{'voice_mode':'single'})
    common.atomic_json(lost/'status.json',{'state':'done','steps':{'exports':{'state':'done'}},'planned_steps':['midi','exports']})
    old_project=lost/'exports/voices.svp'
    common.atomic_json(old_project,{'tracks':[]})
    original_bytes=old_project.read_bytes()
    common.atomic_json(lost/'exports/receipts.json',{'exports/voices.svp':{'file':'exports/voices.svp',
        'sha256':common.file_hash(old_project),'bytes':old_project.stat().st_size,'input_signature':'old-export'}})
    old_project.unlink();contract.reconcile(lost)
    assert next(row for row in contract.inspect(lost) if row['role']=='editor_project')['state']=='missing'
    assert contract.handoff(lost)=='needs_review'
    (lost/'artifacts.json').unlink();old_project.write_bytes(b'corrupted JSON project')
    contract.reconcile(lost)
    assert next(row for row in contract.inspect(lost) if row['role']=='editor_project')['state']=='modified'
    # The sound referenced inside an edited project is itself a live dependency.
    attached=root/'attached';(attached/'temp').mkdir(parents=True)
    common.atomic_json(attached/'request.json',{})
    accompaniment=attached/'temp/accompaniment.wav';sf.write(accompaniment,wave,44100)
    edited=attached/'edited.svp'
    common.atomic_json(edited,{'tracks':[{'mainGroup':{'notes':[{'pitch':60}]},'mainRef':{}},
        {'mainRef':{'isInstrumental':True,'audio':{'filename':'temp/accompaniment.wav'}}}]})
    contract.bind(attached,'project',edited)
    assert contract.selected(attached,'project')==edited
    with patch.object(storage,'job_directories',return_value=[attached]):
        assert attached/'temp' not in [row['path'] for row in storage.candidates()]
    sf.write(accompaniment,wave/2,44100)
    contract.rebuild(attached)
    rejected(lambda:contract.selected(attached,'project'),'Rebuild accepted a changed project audio attachment')
    contract.bind(attached,'project',edited)
    assert contract.selected(attached,'project')==edited
    contract.unbind(attached,'project')
    with patch.object(storage,'job_directories',return_value=[attached]):
        assert attached/'temp' in [row['path'] for row in storage.candidates()]
    # A calibration session can use an external render folder inside a cache.
    calibration=root/'trial';calibration.mkdir()
    session=calibration/'session.json';render_folder=job/'temp/calibration-renders';render_folder.mkdir(parents=True)
    common.atomic_json(session,{'voice':'backing','reference':str(job/'audio/backing.wav'),
        'reference_sha256':common.file_hash(job/'audio/backing.wav'),'project':str(project),
        'project_sha256':common.file_hash(project),'render_folder':str(render_folder)})
    contract.register_session(session)
    contract.rebuild(job)
    assert any(path==render_folder for path,_,_ in contract.protected_references([job]))
    with patch.object(storage,'job_directories',return_value=[job]):
        assert job/'temp' not in [r['path'] for r in storage.candidates()]
print('PASS: producer receipts, partial commits, manual revision tracking, state transitions, cleanup references, bounds and concurrent commits')
