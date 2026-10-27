"""Optional SDK subprocess integration, selected through the original test runner.

Set VWA_MCP_SDK_PYTHON to the dedicated SDK environment. The normal application
Python remains the business worker; SDK packages never enter that environment.
"""
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]


@contextmanager
def temporary_workspace(parent):
    root = Path(tempfile.mkdtemp(dir=parent)).resolve()
    try:
        yield root
    finally:
        # Test-owned absolute directory only; native IO also removes long leaves.
        if root.parent != parent.resolve() or root.is_symlink() or root.is_junction():
            raise RuntimeError('Refusing cleanup outside the owned MCP test directory')
        sys.path.insert(0, str(APP))
        from agent_control import io_path
        shutil.rmtree(io_path(root))


def fixtures(directory):
    app = directory / 'app'
    app.mkdir()
    for path in APP.glob('*.py'):
        shutil.copy2(path, app / path.name)
    shutil.copy2(APP / 'config.example.json', app / 'config.json')
    for name in ('exchange', 'jobs'):
        (directory / name).mkdir()
    exchange = directory / 'exchange'
    # Two tracks, explicit tempo change, program and controller events.
    conductor = b'\x00\xff\x51\x03\x07\xa1\x20\x83\x60\xff\x51\x03\x09\x27\xc0\x00\xff\x2f\x00'
    vocal = (b'\x00\xc0\x01\x00\xb0\x07\x64\x00\x90\x3c\x50'
             b'\x83\x60\x80\x3c\x20\x00\x90\x40\x48\x83\x60\x80\x40\x18\x00\xff\x2f\x00')
    raw = b'MThd' + struct.pack('>IHHH', 6, 1, 2, 480)
    for track in (conductor, vocal):
        raw += b'MTrk' + struct.pack('>I', len(track)) + track
    (exchange / 'review.mid').write_bytes(raw)
    import numpy as np
    import wave
    np.savez(exchange/'reference.npz',schema=np.int64(1),time_step=np.float64(.01),
        midi_pitch=np.array([72.0]*50+[64.0]*60,dtype=np.float32),
        voiced_mask=np.ones(110,dtype=np.bool_))
    with wave.open(str(exchange/'reference.wav'),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
        stream.writeframes(struct.pack('<h',1000)*16000)
    q = 705600000
    project = {'version': 137, 'uuid': 'agent-fixture', 'library': [],
        'renderConfig': {'keep': True}, 'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}, {'position': 2*q, 'bpm': 100}]},
        'tracks': [{'name': '中文演唱', 'mainRef': {'blickOffset': 0, 'pitchOffset': 0,
            'isInstrumental': False, 'database': {'name': 'fixture-bank'}, 'voice': {}},
            'mainGroup': {'uuid': 'agent-notes', 'notes': [
                {'onset': 0, 'duration': q, 'pitch': 60, 'lyrics': '山', 'attributes': {}},
                {'onset': q, 'duration': q, 'pitch': 64, 'lyrics': '河', 'attributes': {}}],
                'parameters': {}}, 'groups': []},
            {'name':'Reference','mainRef':{'isInstrumental':True,'blickOffset':0,
                'audio':{'filename':'reference.wav'}},'groups':[]}]}
    (exchange / '中文.svp').write_bytes(json.dumps(project, ensure_ascii=False).encode() + b'\0')
    job = directory / 'jobs' / 'fixture-job'
    job.mkdir()
    (job / 'request.json').write_text('{"language":"zh","title":"fixture"}', encoding='utf-8')
    (job / 'status.json').write_text('{"state":"ready","steps":{}}', encoding='utf-8')
    config = directory / 'config.json'
    config.write_text(json.dumps({'schema': 1, 'application_root': str(app),
        'worker_python': os.environ.get('VWA_MCP_WORKER_PYTHON', sys.executable),
        'state_root': str(directory / 'state'), 'allowed_asset_roots': [str(exchange)],
        'allowed_job_roots': [str(directory / 'jobs')],
        'shared_draft_root':str(app/'cache/style-region-drafts')}, ensure_ascii=False), encoding='utf-8')
    return config


async def client_contract(configuration):
    import asyncio
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    sys.path.insert(0, str(APP))
    from agent_control import TOOLS, io_path
    config = json.loads(configuration.read_text(encoding='utf-8'))
    params = StdioServerParameters(command=sys.executable,
        args=['-B', '-u', str(Path(config['application_root']) / 'agent_mcp.py'), '--config', str(configuration)],
        env={'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    checks = []
    calls = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    async def call(client, name, arguments=None, *, error=False):
        result = await client.call_tool(name, arguments or {})
        calls.append({'tool': name, 'is_error': result.is_error})
        if error:
            check(result.is_error, 'rejected_' + name)
            return result
        check(not result.is_error, 'success_' + name)
        check(isinstance(result.structured_content, dict), 'structured_' + name)
        return result.structured_content['data']

    async def poll(client, identifier, tool='vwa_operations_get', *, allow_interrupted=False):
        deadline = asyncio.get_running_loop().time() + 65
        while asyncio.get_running_loop().time() < deadline:
            value = await call(client, tool, {'operation_id': identifier})
            if value['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                check(value['state'] == 'succeeded' or (allow_interrupted and value['state'] == 'interrupted'),
                      'operation_terminal_' + value['request']['action'] + str(value.get('error', '')))
                return value
            await asyncio.sleep(.1)
        raise AssertionError('operation polling timeout')

    originals = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in configuration.parent.rglob('*') if p.is_file()}
    async with Client(params, read_timeout_seconds=70) as client:
        listed = await client.list_tools()
        check({t.name for t in listed.tools} == set(TOOLS), 'all_tools_discovered')
        check(all(t.input_schema and t.output_schema for t in listed.tools), 'input_and_output_schemas')
        caps = await call(client, 'vwa_system_capabilities')
        check(caps['semantic_provider'] == 'caller_supplied_plan' and caps['candidate_only'], 'honest_capabilities')
        health = await call(client, 'vwa_system_health')
        check(health['packages']['mido'] is not None and not health['model_execution'], 'real_worker_environment')
        jobs = await call(client, 'vwa_jobs_list', {'limit': 1})
        job_id = jobs['jobs'][0]['id']
        job = await call(client, 'vwa_jobs_get', {'job_id': job_id})
        check(job['status']['state'] == 'ready', 'readonly_job_snapshot')
        artifacts = await call(client, 'vwa_artifacts_list', {'job_id': job_id})
        check(not artifacts['index_present'] and not artifacts['artifacts'], 'missing_index_not_rebuilt')
        await call(client, 'vwa_assets_import', {'path': str(configuration)}, error=True)
        await call(client, 'vwa_jobs_list', {'limit': 101}, error=True)
        midi = await call(client, 'vwa_assets_import', {'path': str(configuration.parent / 'exchange/review.mid')})
        score = await call(client, 'vwa_score_read', {'artifact_id': midi['id']})
        review = await call(client, 'vwa_score_review', {'artifact_id': midi['id']})
        check(len(score['notes']) == 2 and isinstance(review, dict), 'real_midi_notes')
        note = score['notes'][0]
        structure = await call(client,'vwa_structure_patch_plan',{'artifact_id':midi['id'],
            'expected_sha256':score['source_sha256'],'edits':[
                {'operation':'delete','note_id':score['notes'][1]['note_id'],'lyric_policy':'preserve'},
                {'operation':'insert','track_index':1,'channel':0,'pitch':65,'onset_tick':960,
                 'duration_tick':480,'velocity':80,'lyric':'海'}]})
        structure_op = await call(client,'vwa_structure_patch_apply',
            {'plan_id':structure['id'],'idempotency_key':'structure-1'})
        structured = await poll(client,structure_op['id'])
        structured_score = await call(client,'vwa_score_read',
            {'artifact_id':structured['result']['artifacts'][0]['id']})
        check([n['pitch'] for n in structured_score['notes']]==[60,65] and
              structured_score['notes'][1]['lyric']=='海','structure_candidate_verified')
        await call(client,'vwa_structure_patch_plan',{'artifact_id':midi['id'],
            'expected_sha256':score['source_sha256'],'edits':[
                {'operation':'delete','note_id':note['note_id']}]},error=True)
        arguments = {'artifact_id': midi['id'], 'expected_sha256': score['source_sha256'],
                     'edits': [{'note_id': note['note_id'], 'pitch': note['pitch'] + 1}]}
        await call(client, 'vwa_score_patch_plan', {**arguments, 'expected_sha256': '0'*64}, error=True)
        await call(client, 'vwa_score_patch_plan', {**arguments,
            'edits': [{'note_id': note['note_id'], 'pitch': True}]}, error=True)
        plan = await call(client, 'vwa_score_patch_plan', arguments)
        saved = await call(client, 'vwa_plans_get', {'plan_id': plan['id']})
        check(saved == plan, 'plan_review_roundtrip')
        operation = await call(client, 'vwa_score_patch_apply', {'plan_id': plan['id'], 'idempotency_key': 'review-1'})
        result = await poll(client, operation['id'])
        candidate = result['result']['artifacts'][0]
        changed = await call(client, 'vwa_score_read', {'artifact_id': candidate['id']})
        check(changed['notes'][0]['pitch'] == note['pitch'] + 1, 'real_candidate_pitch_changed')
        retry = await call(client, 'vwa_score_patch_apply', {'plan_id': plan['id'], 'idempotency_key': 'review-1'})
        check(retry['id'] == operation['id'], 'same_retry_same_operation')
        cancelled = await call(client, 'vwa_operations_cancel', {'operation_id': operation['id']})
        check(cancelled['state'] == 'succeeded', 'late_cancel_preserves_committed_result')
        verify = await call(client, 'vwa_artifacts_inspect', {'artifact_id': candidate['id'], 'verify': True})
        verified = await poll(client, verify['id'])
        check(verified['result']['verified'], 'real_artifact_hash_verification')
        svp = await call(client, 'vwa_assets_import', {'path': str(configuration.parent / 'exchange/中文.svp')})
        context = await call(client, 'vwa_context_read', {'artifact_id': svp['id'], 'track_indices': [0]})
        check('山' in json.dumps(context, ensure_ascii=False), 'chinese_context_evidence')
        proposal = {'track_indices': [0], 'brief': '思念故乡，字头略带戏腔，首句保持克制。', 'regions': [{
            'start': .1, 'end': .3, 'preset': 'sad', 'strength': .5, 'style_pitch': True,
            'style_technique': 'chinese_opera', 'technique_strength': .4, 'technique_pitch': True,
            'rationale': '调用方结合用户要求为首音选择克制的悲伤和少量戏腔。', 'evidence_refs': [],
            'detail':{'onset':.3,'sustain':.8,'connection':.9,'release':.4}}]}
        style = await call(client, 'vwa_style_plan', {'artifact_id': svp['id'], 'proposal': proposal})
        await call(client, 'vwa_style_plan', {'artifact_id': svp['id'],
            'proposal': {**proposal, 'regions': [{**proposal['regions'][0], 'start': 2.0, 'end': 2.1}]}}, error=True)
        draft=await call(client,'vwa_drafts_get',{'artifact_id':svp['id']})
        expected_key=hashlib.sha256((configuration.parent/'exchange/中文.svp').read_bytes()).hexdigest()
        check(not draft['exists'] and draft['revision'] is None and draft['input_sha256']==expected_key
              and draft['artifact_sha256']!=expected_key,'draft_uses_original_key_after_media_snapshot')
        published=await call(client,'vwa_drafts_publish',{'plan_id':style['id'],'expected_revision':None})
        check(published['data']['style_regions'][0]['detail']['onset']==.3,'shared_draft_preserves_detail')
        from_draft=await call(client,'vwa_drafts_plan',{'artifact_id':svp['id'],
            'expected_revision':published['revision'],'brief':'按GUI共享草稿生成，保留细分强度。'})
        check(from_draft['shared_draft']['revision']==published['revision'],'style_plan_binds_draft_revision')
        changed_proposal={**proposal,'regions':[{**proposal['regions'][0],'strength':.4}]}
        changed_style=await call(client,'vwa_style_plan',{'artifact_id':svp['id'],'proposal':changed_proposal})
        newer=await call(client,'vwa_drafts_publish',{'plan_id':changed_style['id'],
            'expected_revision':published['revision']})
        check(newer['revision']!=published['revision'],'shared_revision_changes_with_content')
        await call(client,'vwa_drafts_publish',{'plan_id':style['id'],'expected_revision':published['revision']},error=True)
        await call(client,'vwa_tuning_start',{'plan_id':from_draft['id'],'idempotency_key':'stale-draft'},error=True)
        style=await call(client,'vwa_drafts_plan',{'artifact_id':svp['id'],
            'expected_revision':newer['revision'],'brief':'按已更新共享草稿生成独立候选。'})
        style_op = await call(client, 'vwa_tuning_start', {'plan_id': style['id'], 'idempotency_key': 'style-1'})
        # Close this real stdio client while the owned business worker may still run.
    async with Client(params, read_timeout_seconds=70) as client:
        style_result = await poll(client, style_op['id'], 'vwa_tuning_result', allow_interrupted=True)
        disconnect_outcome = style_result['state']
        if style_result['state'] == 'interrupted':
            check(style_result['error']['code'] == 'worker_interrupted', 'host_shutdown_interruption_is_explicit')
            style_op = await call(client, 'vwa_tuning_start', {'plan_id': style['id'], 'idempotency_key': 'style-resume-2'})
            style_result = await poll(client, style_op['id'], 'vwa_tuning_result')
        check(style_result['result']['handoff'] == 'awaiting_external_synthesis', 'external_handoff_is_explicit')
        check(len(style_result['result']['artifacts']) == 1, 'only_candidate_registered_not_session_input')
        target = Path(style_result['result']['artifacts'][0]['path'])
        value = json.loads(io_path(target).read_text(encoding='utf-8-sig').rstrip('\0'))
        source = json.loads((configuration.parent / 'exchange/中文.svp').read_text(encoding='utf-8').rstrip('\0'))
        check(value['tracks'][0]['mainGroup']['notes'] == source['tracks'][0]['mainGroup']['notes'], 'style_keeps_score')
        check(value['tracks'][0]['mainRef'] == source['tracks'][0]['mainRef'], 'style_keeps_voice')
        check(value['tracks'][0]['mainGroup']['parameters'] != source['tracks'][0]['mainGroup']['parameters'], 'real_style_curves_generated')
        check(all(Path(path).is_file() and hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha
                  for path, sha in originals.items()), 'all_original_inputs_unchanged')
        check(not (configuration.parent / 'jobs/fixture-job/artifacts.json').exists(), 'job_index_not_created')
        resources = await client.list_resources()
        check(any(str(r.uri) == 'vwa://capabilities' for r in resources.resources), 'capability_resource_discovered')
    async with Client(params, mode='legacy', read_timeout_seconds=70) as client:
        legacy_tools = await client.list_tools()
        check({t.name for t in legacy_tools.tools} == set(TOOLS), 'legacy_initialize_discovery')
        legacy_caps = await call(client, 'vwa_system_capabilities')
        check(legacy_caps['tools'] == list(TOOLS), 'legacy_structured_call')
    # Capture the raw server stream as well: permissive SDK parsers must not
    # conceal a business print on protocol stdout. Invalid jobs emit a warning.
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'vwa-raw-test', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'vwa_jobs_list', 'arguments': {'limit': 101}}},
    ]
    frames = []
    with tempfile.TemporaryFile() as error_log:
        raw = subprocess.Popen([params.command, *params.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=error_log, cwd=config['application_root'], env=dict(os.environ, PYTHONUTF8='1'))
        try:
            with ThreadPoolExecutor(max_workers=1) as reader:
                try:
                    raw.stdin.write((json.dumps(messages[0])+'\n').encode()); raw.stdin.flush()
                    frames.append(json.loads(reader.submit(raw.stdout.readline).result(timeout=10)))
                    for message in messages[1:]:
                        raw.stdin.write((json.dumps(message)+'\n').encode())
                    raw.stdin.flush()
                    frames.append(json.loads(reader.submit(raw.stdout.readline).result(timeout=10)))
                finally:
                    raw.stdin.close(); raw.stdin = None
                    if any(f.get('id') == 2 for f in frames):
                        tail, _ = raw.communicate(timeout=10)
                    else:
                        raw.kill(); tail, _ = raw.communicate(timeout=10)
            frames.extend(json.loads(line) for line in tail.splitlines() if line.strip())
        finally:
            if raw.poll() is None:
                raw.kill(); raw.wait(timeout=10)
            if raw.stdout:
                raw.stdout.close()
        error_log.seek(0); stderr = error_log.read()
    check(raw.returncode == 0 and frames and all(f.get('jsonrpc') == '2.0' for f in frames), 'stdout_contains_only_json_rpc')
    check(any(f.get('id') == 2 and f.get('result', {}).get('isError') for f in frames), 'raw_business_error_is_structured')
    check(b'WARNING' in stderr, 'business_warning_goes_to_stderr')
    return {'checks': checks, 'calls': calls, 'tools': list(TOOLS),
            'midi_operation': operation['id'], 'style_operation': style_op['id'],
            'disconnect_outcome': disconnect_outcome, 'legacy_client_tested': True}


class MCPContract(unittest.TestCase):
    def test_optional_entry_is_not_imported_by_application(self):
        self.assertNotIn('import agent_mcp', (APP / 'app.py').read_text(encoding='utf-8'))

    @unittest.skipUnless(os.environ.get('VWA_MCP_SDK_PYTHON'), 'Dedicated optional SDK not configured')
    def test_real_stdio_workflow(self):
        parent = APP / 'work/mcp-protocol'
        parent.mkdir(parents=True, exist_ok=True)
        with temporary_workspace(parent) as folder:
            root = Path(folder)
            config = fixtures(root)
            result = subprocess.run([os.environ['VWA_MCP_SDK_PYTHON'], '-B', str(Path(__file__).resolve()),
                '--client', str(config)], cwd=APP, capture_output=True,
                env=dict(os.environ, VWA_MCP_WORKER_PYTHON=sys.executable,
                         TEMP=str(root), TMP=str(root), PYTHONUTF8='1'), timeout=140)
            reports = APP / 'work/test-results'
            reports.mkdir(parents=True, exist_ok=True)
            (reports / 'mcp-stdio-client.log').write_bytes(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode('utf-8', errors='replace')[-6000:])
            receipt = json.loads(result.stdout)
            self.assertGreater(len(receipt['checks']), 45)
            (reports / 'mcp-stdio-client.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')

    @unittest.skipUnless(os.environ.get('VWA_MCP_SDK_PYTHON') and os.environ.get('VWA_MCP_EXPORT_RUNTIME'),
                         'Optional SDK and real export runtime not configured')
    def test_real_supervised_lyrics_to_project_workflow(self):
        parent = APP / 'work/mcp-supervised'
        parent.mkdir(parents=True, exist_ok=True)
        with temporary_workspace(parent) as root:
            config = fixtures(root)
            value = json.loads(config.read_text(encoding='utf-8'))
            value['export_runtime'] = os.environ['VWA_MCP_EXPORT_RUNTIME']
            config.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([os.environ['VWA_MCP_SDK_PYTHON'], '-B', str(Path(__file__).resolve()),
                '--workflow-client', str(config)], cwd=APP, capture_output=True,
                env=dict(os.environ, VWA_MCP_WORKER_PYTHON=sys.executable,
                         TEMP=str(root), TMP=str(root), PYTHONUTF8='1'), timeout=140)
            reports = APP / 'work/test-results'
            reports.mkdir(parents=True, exist_ok=True)
            (reports / 'mcp-supervised-client.log').write_bytes(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode('utf-8', errors='replace')[-8000:])
            receipt = json.loads(result.stdout)
            self.assertTrue(receipt['project_ready'])
            (reports / 'mcp-supervised-client.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')


async def workflow_contract(configuration):
    import asyncio
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    sys.path.insert(0, str(APP))
    from agent_control import io_path
    config = json.loads(configuration.read_text(encoding='utf-8'))
    params = StdioServerParameters(command=sys.executable,
        args=['-B','-u',str(Path(config['application_root'])/'agent_mcp.py'),'--config',str(configuration)],
        env={'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'})
    checks = []; calls = []
    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)
    async def call(client, name, arguments, error=False):
        response = await client.call_tool(name, arguments)
        calls.append({'tool':name, 'error':response.is_error})
        if error:
            check(response.is_error, 'rejected_'+name)
            return
        if response.is_error:
            raise AssertionError(name+': '+str(response.content))
        return response.structured_content['data']
    async def wait(client, flow):
        deadline = asyncio.get_running_loop().time()+90
        while asyncio.get_running_loop().time()<deadline:
            value = await call(client,'vwa_workflow_get',{'workflow_id':flow['id']})
            operation = value.get('operation')
            if operation and operation['state'] in ('succeeded','failed','cancelled','interrupted'):
                check(operation['state']=='succeeded', 'operation_'+operation['request']['action']+str(operation.get('error','')))
                return value
            await asyncio.sleep(.1)
        raise AssertionError('supervised operation timeout')
    def version(flow, **kwargs):
        return {'workflow_id':flow['id'],'expected_revision':flow['revision'],**kwargs}
    original = configuration.parent/'exchange/review.mid'
    original_sha = hashlib.sha256(original.read_bytes()).hexdigest()
    async with Client(params,read_timeout_seconds=80) as client:
        # The real link plan is validated without downloading or running models.
        link_plan = await call(client,'vwa_preparation_plan',{'source':'https://www.bilibili.com/video/BV1xx411c7mD/',
            'options':{'language':'zh','voice_mode':'single','lyrics':'山河'}})
        check(link_plan['kind']=='preparation','real_link_preparation_plan')
        await call(client,'vwa_preparation_plan',{'source':'https://example.com/a-song'},error=True)
        artifact = await call(client,'vwa_assets_import',{'path':str(original)})
        flow = await call(client,'vwa_workflow_create',{'score_artifact_id':artifact['id'],
            'options':{'language':'zh'},'brief':'首句克制思乡，少量戏腔。','reference_lyrics':'山河',
            'idempotency_key':'supervised-demo','review_mode':'legacy'})
        pitch = await call(client,'vwa_reference_import',
            {'path':str(configuration.parent/'exchange/reference.npz'),'kind':'pitch'})
        audio = await call(client,'vwa_reference_import',
            {'path':str(configuration.parent/'exchange/reference.wav'),'kind':'audio'})
        flow = await call(client,'vwa_workflow_references_set',version(flow,
            pitch_artifact_id=pitch['id'],audio_artifact_id=audio['id'],track_indices=[1],
            time_origin=0.0,audio_time_origin=0.0,
            rationale='自制夹具明确以MIDI秒0对齐；参考仅为条件性证据。'))
        check(flow['stage']=='review' and flow['next']['tool']=='vwa_workflow_review','review_gate_and_next_tool')
        unchanged = await call(client,'vwa_workflow_advance',version(flow))
        check(unchanged['stage']=='review' and unchanged['revision']==flow['revision'],'cannot_skip_review')
        bundle = await call(client,'vwa_workflow_review',{'workflow_id':flow['id'],'limit':1})
        check(bundle['reference_evidence']['pitch']['summary']['notes_selected']==2,
              'existing_pitch_evidence_attached')
        check(not bundle['reference_evidence']['audio']['frame_analysis_performed'],
              'audio_header_default_no_inference')
        excerpt_next=bundle['reference_evidence']['audio_excerpt_next']
        excerpt=await call(client,excerpt_next['tool'],excerpt_next['arguments'])
        deadline=asyncio.get_running_loop().time()+30
        while asyncio.get_running_loop().time()<deadline:
            excerpt_result=await call(client,'vwa_operations_get',{'operation_id':excerpt['id']})
            if excerpt_result['state'] in ('succeeded','failed','interrupted','cancelled'):
                break
            await asyncio.sleep(.1)
        check(excerpt_result['state']=='succeeded' and
              not excerpt_result['result']['receipt']['frame_preservation']['resampled'],
              'pcm_excerpt_generated_without_resampling')
        check(bundle['total_notes']==2 and bundle['next_offset']==1,'review_note_pagination')
        score = await call(client,'vwa_score_read',{'artifact_id':artifact['id']})
        await call(client,'vwa_workflow_apply_review',version(flow,rationale=''),error=True)
        flow = await call(client,'vwa_workflow_apply_review',version(flow,
            rationale='两音歌词缺失；明确补入山河，首音按谱升高半音，第二音保持。',
            lyric_edits=[{'note_id':note['note_id'],'lyric':lyric,'operation':'insert'}
                         for note,lyric in zip(score['notes'],('山','河'))],
            note_edits=[{'note_id':score['notes'][0]['note_id'],'pitch':61}]))
        await call(client,'vwa_workflow_advance',{'workflow_id':flow['id'],'expected_revision':0},error=True)
        flow = await wait(client,flow)
        flow = await call(client,'vwa_workflow_advance',version(flow))
        check(flow['stage']=='reviewed','review_decision_consumed')
        corrected = await call(client,'vwa_score_read',{'artifact_id':flow['score_artifact_id']})
        check(corrected['notes'][0]['pitch']==61 and [n['lyric'] for n in corrected['notes']]==['山','河'],
              'lyrics_and_pitch_corrected_together')
        check(hashlib.sha256(original.read_bytes()).hexdigest()==original_sha,'original_midi_unchanged')
        flow = await call(client,'vwa_workflow_advance',version(flow))
        flow = await wait(client,flow)
        flow = await call(client,'vwa_workflow_advance',version(flow))
        check(flow['stage']=='project_ready','verified_svp_exported')
        summary=flow['export_summary']
        check(summary['independent_audit']['status']=='verified' and summary['can_accept'],
              'independent_export_seconds_verified')
        check(summary['required_loss_codes'] and summary['review_required'],'losses_visible_by_default')
        unchanged=await call(client,'vwa_workflow_advance',version(flow,finish=True))
        check(unchanged['stage']=='project_ready' and unchanged['revision']==flow['revision'],
              'cannot_finish_without_export_review')
        original_report=summary['report_sha256']
        original_project=flow['project_artifacts'][0]
        pronunciation=await call(client,'vwa_pronunciation_read',{'artifact_id':original_project['id'],'limit':1})
        check(pronunciation['source_sha256']==original_project['sha256'] and len(pronunciation['notes'])==1,
              'native_pronunciation_read_paginates')
        flow=await call(client,'vwa_workflow_pronunciation_apply',version(flow,
            rationale='自制夹具明确指定山字音素，仅验证写入、原字与音符保留，不声称声学正确。',
            changes=[{'artifact_id':original_project['id'],'expected_sha256':original_project['sha256'],
                'edits':[{'note_ref':pronunciation['notes'][0]['note_ref'],'phonemes':'sh an'}]}]))
        check(flow['stage']=='refining','pronunciation_uses_owned_persistent_operation')
        flow=await wait(client,flow)
        flow=await call(client,'vwa_workflow_advance',version(flow))
        summary=flow['export_summary']
        check(summary['report_sha256']!=original_report and flow['export_review'] is None,
              'pronunciation_invalidates_previous_export_report')
        check(summary['independent_audit']['status']=='verified' and
              'explicit_phonemes_unverified' in summary['required_loss_codes'],
              'pronunciation_reaudits_notes_lyrics_seconds_and_retains_unknown_acoustics')
        edited_pronunciation=await call(client,'vwa_pronunciation_read',{'artifact_id':flow['project_artifact_id']})
        check('sh an' in json.dumps(edited_pronunciation,ensure_ascii=False),'explicit_phonemes_written')
        old_file=Path(original_project['path'])
        check(hashlib.sha256(io_path(old_file).read_bytes()).hexdigest()==original_project['sha256'],
              'previous_export_candidate_preserved')
        review={'report_sha256':summary['report_sha256'],'decision':'accept',
            'rationale':'自制谱的音符、歌词和实际秒数回读一致；明确认可列明格式损失。',
            'accepted_loss_codes':summary['required_loss_codes']}
        await call(client,'vwa_workflow_advance',version(flow,
            export_review={**review,'report_sha256':'0'*64}),error=True)
        context = await call(client,'vwa_context_read',{'artifact_id':flow['project_artifact_id']})
        first = next(note for note in context['notes'] if note.get('singing'))
        proposal = {'track_indices':[first['track_index']],'brief':'首句克制思乡，少量戏腔。','regions':[{
            'start':first['start_seconds']+.01,'end':first['end_seconds']-.01,
            'preset':'sad','strength':.5,'style_pitch':True,'style_technique':'chinese_opera',
            'technique_strength':.3,'technique_pitch':True,'rationale':'按创作背景保留山字的完整时值并轻收起音。',
            'evidence_refs':[first['note_id']]}]}
        style=await call(client,'vwa_style_plan',{'artifact_id':flow['project_artifact_id'],'proposal':proposal})
        published=await call(client,'vwa_drafts_publish',{'plan_id':style['id'],'expected_revision':None})
        bound=await call(client,'vwa_drafts_plan',{'artifact_id':flow['project_artifact_id'],
            'expected_revision':published['revision'],'brief':'按已共享到GUI的片段草稿生成。'})
        flow = await call(client,'vwa_workflow_advance',version(flow,style_plan_id=bound['id'],export_review=review))
        flow = await wait(client,flow)
        flow = await call(client,'vwa_workflow_advance',version(flow))
        check(flow['completed'] and flow['stage']=='ready','editable_styled_project_ready')
        final = flow['final_artifacts'][0]
        data = json.loads(io_path(final['path']).read_text(encoding='utf-8-sig').rstrip('\0'))
        notes = [note for track in data['tracks'] for note in track.get('mainGroup',{}).get('notes',[])]
        check([note['lyrics'] for note in notes]==['山','河'] and notes[0]['pitch']==61,'exported_project_keeps_corrected_score_and_lyrics')
        check(notes[0].get('phonemes')=='sh an','style_generation_preserves_explicit_phonemes')
        check(flow['next']['external_handoff']=='voice_synthesis_if_requested','project_completion_is_not_voice_synthesis')
    return {'project_ready':True,'checks':checks,'calls':calls,'workflow_id':flow['id'],
            'final_artifacts':flow['final_artifacts'],'model_inference':False,'synthesis':False}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--client':
        import asyncio
        print(json.dumps(asyncio.run(client_contract(Path(sys.argv[2]))), ensure_ascii=False))
    elif len(sys.argv) == 3 and sys.argv[1] == '--workflow-client':
        import asyncio
        print(json.dumps(asyncio.run(workflow_contract(Path(sys.argv[2]))), ensure_ascii=False))
    else:
        unittest.main()
