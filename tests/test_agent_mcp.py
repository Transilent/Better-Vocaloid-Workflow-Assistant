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

STANDARD_TOOLS = (
    'vwa_start', 'vwa_status', 'vwa_evidence', 'vwa_decide',
    'vwa_run', 'vwa_wait', 'vwa_cancel', 'vwa_input',
    'vwa_search', 'vwa_recover', 'vwa_delivery', 'vwa_manage',
)


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
    # Exercise the reviewed sources even without running the generated-copy
    # preparation step; the disposable app never replaces the user install.
    for path in (APP / 'src').glob('*.py'):
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
    np.savez(exchange/'reference-unvoiced.npz',schema=np.int64(1),time_step=np.float64(.01),
        midi_pitch=np.zeros(110,dtype=np.float32), voiced_mask=np.zeros(110,dtype=np.bool_))
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
    from agent_control import io_path
    config = json.loads(configuration.read_text(encoding='utf-8'))
    params = StdioServerParameters(command=sys.executable,
        args=['-B', '-u', str(Path(config['application_root']) / 'agent_mcp.py'),
              '--config', str(configuration)],
        env={'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    checks = []
    calls = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    async def call(client, name, arguments=None, *, error=False):
        result = await client.call_tool(name, arguments or {})
        calls.append({'tool': name, 'is_error': bool(result.is_error)})
        if error:
            check(result.is_error, 'rejected_' + name)
            return result
        check(not result.is_error, 'success_' + name)
        check(isinstance(result.structured_content, dict), 'structured_' + name)
        return result.structured_content['data']

    async def action(client, name, request_action, *, error=False, **fields):
        return await call(client, name,
                          {'request': {'action': request_action, **fields}},
                          error=error)

    async def poll(client, identifier, request_action='operations_get', *,
                   allow_interrupted=False):
        deadline = asyncio.get_running_loop().time() + 65
        while asyncio.get_running_loop().time() < deadline:
            value = await action(client, 'vwa_status', request_action,
                                 operation_id=identifier)
            if value['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                check(value['state'] == 'succeeded' or
                      (allow_interrupted and value['state'] == 'interrupted'),
                      'operation_terminal_' + value['request']['action'] +
                      str(value.get('error', '')))
                return value
            await asyncio.sleep(.1)
        raise AssertionError('operation polling timeout')

    def action_schema(tools_by_name, tool_name, request_action):
        schema = tools_by_name[tool_name].input_schema
        pointer = schema['properties']['request']['discriminator']['mapping'][request_action]
        return schema['$defs'][pointer.rsplit('/', 1)[-1]]

    def resolve_schema(schema, value):
        if '$ref' in value:
            target = schema
            for part in value['$ref'].removeprefix('#/').split('/'):
                target = target[part]
            return resolve_schema(schema, target)
        if 'anyOf' in value:
            choices = [row for row in value['anyOf'] if row.get('type') != 'null']
            if len(choices) == 1:
                return resolve_schema(schema, choices[0])
        return value

    originals = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in configuration.parent.rglob('*') if path.is_file()}
    async with Client(params, read_timeout_seconds=70) as client:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        check(set(tools) == set(STANDARD_TOOLS), 'all_standard_tools_discovered')
        check(len(tools) == 12, 'standard_tool_count')
        check(all(tool.input_schema and tool.output_schema for tool in listed.tools),
              'input_and_output_schemas')
        check(tools['vwa_start'].annotations.open_world_hint is True and
              tools['vwa_run'].annotations.open_world_hint is True,
              'preparation_and_workflow_network_is_declared')
        check(tools['vwa_status'].annotations.read_only_hint is False and
              tools['vwa_evidence'].annotations.read_only_hint is False,
              'conditional_status_and_evidence_writes_are_declared')
        check(tools['vwa_recover'].annotations.read_only_hint is False and
              tools['vwa_recover'].annotations.destructive_hint is False and
              tools['vwa_recover'].annotations.idempotent_hint is True,
              'preparation_recovery_is_explicit_and_non_destructive')
        repair_plan = action_schema(tools, 'vwa_recover', 'preparation_repair_plan')
        repair_start = action_schema(tools, 'vwa_recover', 'preparation_repair_start')
        check(repair_plan['properties']['source_job_id']['maxLength'] == 180 and
              repair_start['properties']['plan_id']['maxLength'] == 180 and
              repair_start['properties']['idempotency_key']['maxLength'] == 128,
              'preparation_repair_parameters_bounded')
        check('不跑模型' in (tools['vwa_recover'].description or '') and
              '不覆盖源任务' in (tools['vwa_recover'].description or ''),
              'preparation_repair_safety_documented')
        preparation = action_schema(tools, 'vwa_start', 'preparation_plan')
        options = resolve_schema(tools['vwa_start'].input_schema,
                                 preparation['properties']['options'])
        preparation_fields = options['properties']
        for name in ('separation_device', 'midi_device'):
            device = resolve_schema(tools['vwa_start'].input_schema,
                                    preparation_fields[name])
            check(device['enum'] == ['cpu', 'dml'],
                  'preparation_%s_execution_contract' % name)
        bpm = resolve_schema(tools['vwa_start'].input_schema,
                             preparation_fields['bpm'])
        threads = resolve_schema(tools['vwa_start'].input_schema,
                                 preparation_fields['cpu_threads'])
        check(bpm['minimum'] == 20.0 and bpm['maximum'] == 400.0,
              'preparation_bpm_schema_bounds')
        check(threads['minimum'] == 0 and threads['maximum'] == 64,
              'preparation_cpu_threads_schema_bounds')
        for tool_name, request_action in (
                ('vwa_start', 'workflow_create'),
                ('vwa_manage', 'workflow_clone')):
            mode = action_schema(tools, tool_name, request_action)['properties']['review_mode']
            check(mode.get('default') == 'full' and
                  set(mode.get('enum', [])) == {'full', 'segments', 'legacy'},
                  request_action + '_review_modes')
        check(all(term in (tools['vwa_start'].description or '') for term in (
            'export_options', 'F0/伴奏', '音轨映射')),
            'export_pitch_preservation_description')
        check('full整曲审核' in (client.instructions or '') and
              '不可自动批准' in (client.instructions or '') and
              '分页' in (client.instructions or ''),
              'full_review_service_instructions')

        caps = await action(client, 'vwa_status', 'system_capabilities')
        check(caps['tools'] == list(STANDARD_TOOLS) and
              caps['interface_profile'] == 'standard' and
              caps['workflow_entry'] == 'vwa_start' and
              'legacy_tool_count' not in caps and
              caps['semantic_provider'] == 'caller_supplied_plan' and
              caps['candidate_only'], 'current_12_tool_capabilities')
        health = await action(client, 'vwa_status', 'system_health')
        check(health['packages']['mido'] is not None and
              not health['model_execution'], 'real_worker_environment')
        jobs = await action(client, 'vwa_status', 'jobs_list', limit=1)
        job_id = jobs['jobs'][0]['id']
        job = await action(client, 'vwa_status', 'jobs_get', job_id=job_id)
        check(job['status']['state'] == 'ready', 'readonly_job_snapshot')
        artifacts = await action(client, 'vwa_status', 'artifacts_list', job_id=job_id)
        check(not artifacts['index_present'] and not artifacts['artifacts'],
              'missing_index_not_rebuilt')
        await action(client, 'vwa_input', 'assets_import',
                     path=str(configuration), error=True)
        await action(client, 'vwa_status', 'jobs_list', limit=101, error=True)
        midi = await action(client, 'vwa_input', 'assets_import',
                            path=str(configuration.parent / 'exchange/review.mid'))
        score = await action(client, 'vwa_evidence', 'score_read', artifact_id=midi['id'])
        review = await action(client, 'vwa_evidence', 'score_review', artifact_id=midi['id'])
        check(len(score['notes']) == 2 and isinstance(review, dict), 'real_midi_notes')
        unvoiced = await action(client, 'vwa_input', 'reference_import',
            path=str(configuration.parent / 'exchange/reference-unvoiced.npz'), kind='pitch')
        offsets = await action(client, 'vwa_evidence', 'pitch_offset_review',
            score_artifact_id=midi['id'], pitch_artifact_id=unvoiced['id'],
            track_index=1, category='unevaluated', limit=1)
        check(offsets['pagination']['total'] == 2 and offsets['next_request'],
              'pitch_offset_page_has_followup')
        body = {key: value for key, value in offsets.items() if key != 'review_sha256'}
        expected_review_sha = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        check(offsets['review_sha256'] == expected_review_sha,
              'pitch_offset_digest_covers_followup')
        next_offsets = await call(client, offsets['next_request']['tool'],
                                  offsets['next_request']['arguments'])
        check(next_offsets['revision'] == offsets['revision'] and
              next_offsets['items'][0]['note_id'] != offsets['items'][0]['note_id'] and
              next_offsets['next_request'] is None, 'pitch_offset_followup_is_distinct')
        await action(client, 'vwa_evidence', 'pitch_offset_review',
            score_artifact_id=midi['id'], pitch_artifact_id=unvoiced['id'],
            track_index=1, category='unevaluated', limit=1, offset=1,
            expected_revision='stale-revision', error=True)
        await action(client, 'vwa_evidence', 'pitch_offset_review',
            score_artifact_id=midi['id'], pitch_artifact_id=unvoiced['id'],
            track_index=1, category='unevaluated', limit=1, offset=1, error=True)
        note = score['notes'][0]
        structure = await action(client, 'vwa_decide', 'structure_patch_plan',
            artifact_id=midi['id'], expected_sha256=score['source_sha256'], edits=[
                {'operation': 'delete', 'note_id': score['notes'][1]['note_id'],
                 'lyric_policy': 'preserve'},
                {'operation': 'insert', 'track_index': 1, 'channel': 0, 'pitch': 65,
                 'onset_tick': 960, 'duration_tick': 480, 'velocity': 80, 'lyric': '海'}])
        structure_op = await action(client, 'vwa_decide', 'structure_patch_apply',
                                    plan_id=structure['id'], idempotency_key='structure-1')
        structured = await poll(client, structure_op['id'])
        structured_score = await action(client, 'vwa_evidence', 'score_read',
            artifact_id=structured['result']['artifacts'][0]['id'])
        check([item['pitch'] for item in structured_score['notes']] == [60, 65] and
              structured_score['notes'][1]['lyric'] == '海',
              'structure_candidate_verified')
        page = await action(client, 'vwa_status', 'artifacts_list', limit=1)
        check(page['total'] >= 2 and page['next_request'] is not None,
              'artifact_page_has_followup')
        followup = page['next_request']
        next_page = await call(client, followup['tool'], followup['arguments'])
        check(next_page['listing_revision'] == page['listing_revision'] and
              next_page['artifacts'][0]['id'] != page['artifacts'][0]['id'],
              'artifact_followup_reads_next_distinct_entry')
        await action(client, 'vwa_decide', 'structure_patch_plan',
            artifact_id=midi['id'], expected_sha256=score['source_sha256'],
            edits=[{'operation': 'delete', 'note_id': note['note_id']}], error=True)
        score_arguments = {
            'artifact_id': midi['id'],
            'expected_sha256': score['source_sha256'],
            'edits': [{'note_id': note['note_id'], 'pitch': note['pitch'] + 1}],
        }
        await action(client, 'vwa_decide', 'score_patch_plan',
            **{**score_arguments, 'expected_sha256': '0' * 64}, error=True)
        await action(client, 'vwa_decide', 'score_patch_plan',
            **{**score_arguments,
               'edits': [{'note_id': note['note_id'], 'pitch': True}]}, error=True)
        plan = await action(client, 'vwa_decide', 'score_patch_plan', **score_arguments)
        saved = await action(client, 'vwa_status', 'plans_get', plan_id=plan['id'])
        check(saved == {key: value for key, value in plan.items() if key != 'readback'},
              'plan_review_roundtrip')
        operation = await action(client, 'vwa_decide', 'score_patch_apply',
                                 plan_id=plan['id'], idempotency_key='review-1')
        result = await poll(client, operation['id'])
        candidate = result['result']['artifacts'][0]
        changed = await action(client, 'vwa_evidence', 'score_read',
                               artifact_id=candidate['id'])
        check(changed['notes'][0]['pitch'] == note['pitch'] + 1,
              'real_candidate_pitch_changed')
        retry = await action(client, 'vwa_decide', 'score_patch_apply',
                             plan_id=plan['id'], idempotency_key='review-1')
        check(retry['id'] == operation['id'], 'same_retry_same_operation')
        cancelled = await call(client, 'vwa_cancel',
                               {'operation_id': operation['id']})
        check(cancelled['state'] == 'succeeded',
              'late_cancel_preserves_committed_result')
        verify = await action(client, 'vwa_run', 'artifacts_inspect',
                              artifact_id=candidate['id'], verify=True)
        verified = await poll(client, verify['id'])
        check(verified['result']['verified'], 'real_artifact_hash_verification')

        svp = await action(client, 'vwa_input', 'assets_import',
                           path=str(configuration.parent / 'exchange/中文.svp'))
        context = await action(client, 'vwa_evidence', 'context_read',
                               artifact_id=svp['id'], track_indices=[0])
        check('山' in json.dumps(context, ensure_ascii=False), 'chinese_context_evidence')
        proposal = {'track_indices': [0],
            'brief': '思念故乡，字头略带戏腔，首句保持克制。', 'regions': [{
                'start': .1, 'end': .3, 'preset': 'sad', 'strength': .5,
                'style_pitch': True, 'style_technique': 'chinese_opera',
                'technique_strength': .4, 'technique_pitch': True,
                'rationale': '调用方结合用户要求为首音选择克制的悲伤和少量戏腔。',
                'evidence_refs': [],
                'detail': {'onset': .3, 'sustain': .8, 'connection': .9, 'release': .4}}]}
        style = await action(client, 'vwa_decide', 'style_plan',
                             artifact_id=svp['id'], proposal=proposal)
        await action(client, 'vwa_decide', 'style_plan', artifact_id=svp['id'],
            proposal={**proposal, 'regions': [
                {**proposal['regions'][0], 'start': 2.0, 'end': 2.1}]}, error=True)
        draft = await action(client, 'vwa_status', 'drafts_get', artifact_id=svp['id'])
        expected_key = hashlib.sha256(
            (configuration.parent / 'exchange/中文.svp').read_bytes()).hexdigest()
        check(not draft['exists'] and draft['revision'] is None and
              draft['input_sha256'] == expected_key and
              draft['artifact_sha256'] != expected_key,
              'draft_uses_original_key_after_media_snapshot')
        published = await action(client, 'vwa_decide', 'drafts_publish',
                                 plan_id=style['id'], expected_revision=None)
        check(published['data']['style_regions'][0]['detail']['onset'] == .3,
              'shared_draft_preserves_detail')
        from_draft = await action(client, 'vwa_decide', 'drafts_plan',
            artifact_id=svp['id'], expected_revision=published['revision'],
            brief='按GUI共享草稿生成，保留细分强度。')
        check(from_draft['shared_draft']['revision'] == published['revision'],
              'style_plan_binds_draft_revision')
        changed_proposal = {**proposal, 'regions': [
            {**proposal['regions'][0], 'strength': .4}]}
        changed_style = await action(client, 'vwa_decide', 'style_plan',
                                     artifact_id=svp['id'], proposal=changed_proposal)
        newer = await action(client, 'vwa_decide', 'drafts_publish',
                             plan_id=changed_style['id'],
                             expected_revision=published['revision'])
        check(newer['revision'] != published['revision'],
              'shared_revision_changes_with_content')
        await action(client, 'vwa_decide', 'drafts_publish', plan_id=style['id'],
                     expected_revision=published['revision'], error=True)
        await action(client, 'vwa_run', 'tuning_start', plan_id=from_draft['id'],
                     idempotency_key='stale-draft', error=True)
        style = await action(client, 'vwa_decide', 'drafts_plan',
            artifact_id=svp['id'], expected_revision=newer['revision'],
            brief='按已更新共享草稿生成独立候选。')
        style_op = await action(client, 'vwa_run', 'tuning_start',
                                plan_id=style['id'], idempotency_key='style-1')
        # Close this real stdio client while the owned business worker may still run.
    async with Client(params, read_timeout_seconds=70) as client:
        style_result = await poll(client, style_op['id'], 'tuning_result',
                                  allow_interrupted=True)
        disconnect_outcome = style_result['state']
        if style_result['state'] == 'interrupted':
            check(style_result['error']['code'] == 'worker_interrupted',
                  'host_shutdown_interruption_is_explicit')
            style_op = await action(client, 'vwa_run', 'tuning_start',
                                    plan_id=style['id'],
                                    idempotency_key='style-resume-2')
            style_result = await poll(client, style_op['id'], 'tuning_result')
        check(style_result['result']['handoff'] == 'awaiting_external_synthesis',
              'external_handoff_is_explicit')
        check(len(style_result['result']['artifacts']) == 1,
              'only_candidate_registered_not_session_input')
        target = Path(style_result['result']['artifacts'][0]['path'])
        value = json.loads(io_path(target).read_text(
            encoding='utf-8-sig').rstrip('\0'))
        source_value = json.loads((configuration.parent / 'exchange/中文.svp')
                                  .read_text(encoding='utf-8').rstrip('\0'))
        check(value['tracks'][0]['mainGroup']['notes'] ==
              source_value['tracks'][0]['mainGroup']['notes'], 'style_keeps_score')
        check(value['tracks'][0]['mainRef'] == source_value['tracks'][0]['mainRef'],
              'style_keeps_voice')
        check(value['tracks'][0]['mainGroup']['parameters'] !=
              source_value['tracks'][0]['mainGroup']['parameters'],
              'real_style_curves_generated')
        check(all(Path(path).is_file() and
                  hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha
                  for path, sha in originals.items()), 'all_original_inputs_unchanged')
        check(not (configuration.parent / 'jobs/fixture-job/artifacts.json').exists(),
              'job_index_not_created')
        resources = await client.list_resources()
        check(any(str(resource.uri) == 'vwa://capabilities'
                  for resource in resources.resources), 'capability_resource_discovered')
    # Older MCP protocol negotiation must expose the same 12-tool catalogue.
    async with Client(params, mode='legacy', read_timeout_seconds=70) as client:
        negotiated_tools = await client.list_tools()
        check({tool.name for tool in negotiated_tools.tools} == set(STANDARD_TOOLS),
              'older_protocol_version_discovery')
        negotiated_caps = await action(client, 'vwa_status', 'system_capabilities')
        check(negotiated_caps['tools'] == list(STANDARD_TOOLS) and
              negotiated_caps['interface_profile'] == 'standard',
              'older_protocol_version_structured_call')
    # The raw stream includes an invalid jobs request to exercise structured errors.
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'vwa-raw-test', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
            'name': 'vwa_status', 'arguments': {'request': {
                'action': 'jobs_list', 'limit': 101}}}},
    ]
    frames = []
    with tempfile.TemporaryFile() as error_log:
        raw = subprocess.Popen([params.command, *params.args], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=error_log,
            cwd=config['application_root'], env=dict(os.environ, PYTHONUTF8='1'))
        try:
            with ThreadPoolExecutor(max_workers=1) as reader:
                try:
                    raw.stdin.write((json.dumps(messages[0]) + '\n').encode())
                    raw.stdin.flush()
                    frames.append(json.loads(
                        reader.submit(raw.stdout.readline).result(timeout=10)))
                    for message in messages[1:]:
                        raw.stdin.write((json.dumps(message) + '\n').encode())
                    raw.stdin.flush()
                    frames.append(json.loads(
                        reader.submit(raw.stdout.readline).result(timeout=10)))
                finally:
                    raw.stdin.close()
                    raw.stdin = None
                    if any(frame.get('id') == 2 for frame in frames):
                        tail, _ = raw.communicate(timeout=10)
                    else:
                        raw.kill()
                        tail, _ = raw.communicate(timeout=10)
            frames.extend(json.loads(line) for line in tail.splitlines()
                          if line.strip())
        finally:
            if raw.poll() is None:
                raw.kill()
                raw.wait(timeout=10)
            if raw.stdout:
                raw.stdout.close()
        error_log.seek(0)
        stderr = error_log.read()
    check(raw.returncode == 0 and frames and
          all(frame.get('jsonrpc') == '2.0' for frame in frames),
          'stdout_contains_only_json_rpc')
    check(any(frame.get('id') == 2 and frame.get('result', {}).get('isError')
              for frame in frames), 'raw_business_error_is_structured')
    check(b'WARNING' in stderr, 'business_warning_goes_to_stderr')
    covered_tools = sorted({row['tool'] for row in calls})
    check(set(covered_tools) <= set(STANDARD_TOOLS), 'only_standard_tools_called')
    return {'checks': checks, 'calls': calls, 'tools': list(STANDARD_TOOLS),
            'covered_tools': covered_tools, 'midi_operation': operation['id'],
            'style_operation': style_op['id'],
            'disconnect_outcome': disconnect_outcome,
            'older_protocol_client_tested': True}


class MCPContract(unittest.TestCase):
    def test_optional_entry_is_not_imported_by_application(self):
        self.assertNotIn('import agent_mcp', (APP / 'app.py').read_text(encoding='utf-8'))

    @unittest.skipUnless(os.environ.get('VWA_MCP_SDK_PYTHON'), 'Dedicated optional SDK not configured')
    def test_preparation_schema_matches_backend_boundaries(self):
        script = r'''
import json
import sys
sys.path.insert(0, sys.argv[1])
from pydantic import ValidationError
from agent_mcp_models import PreparationOptions
import agent_preparation as preparation

schema = PreparationOptions.model_json_schema()
fields = schema['properties']

def non_null(name):
    if 'anyOf' in fields[name]:
        return next(row for row in fields[name]['anyOf'] if row.get('type') != 'null')
    return fields[name]

assert non_null('bpm')['minimum'] == preparation.PREPARATION_BPM_RANGE[0]
assert non_null('bpm')['maximum'] == preparation.PREPARATION_BPM_RANGE[1]
assert non_null('cpu_threads')['minimum'] == preparation.PREPARATION_CPU_THREADS_RANGE[0]
assert non_null('cpu_threads')['maximum'] == preparation.PREPARATION_CPU_THREADS_RANGE[1]
for name in ('separation_device', 'midi_device'):
    assert tuple(non_null(name)['enum']) == preparation.PREPARATION_DEVICE_VALUES
    assert set(non_null(name)['enum']).isdisjoint(
        set(preparation.ROFORMER_DEVICE_VALUES) - {'cpu'})

for options in (
    {'bpm': 20, 'cpu_threads': 0, 'separation_device': 'cpu', 'midi_device': 'dml'},
    {'bpm': 400, 'cpu_threads': 64, 'separation_device': 'dml', 'midi_device': 'cpu'},
):
    PreparationOptions(**options)
for options in (
    {'bpm': 19.999}, {'bpm': 400.001}, {'cpu_threads': -1}, {'cpu_threads': 65},
    {'separation_device': 'auto'}, {'separation_device': 'cuda'},
    {'midi_device': 'auto'}, {'midi_device': 'cuda'},
):
    try:
        PreparationOptions(**options)
    except ValidationError:
        pass
    else:
        raise AssertionError('schema accepted execution-unsupported options: %r' % options)
print(json.dumps({'schema': True, 'boundaries': True}))
'''
        result = subprocess.run(
            [os.environ['VWA_MCP_SDK_PYTHON'], '-B', '-c', script, str(APP / 'src')],
            cwd=APP, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'schema': True, 'boundaries': True})

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
        args=['-B', '-u', str(Path(config['application_root']) / 'agent_mcp.py'),
              '--config', str(configuration)],
        env={'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
    checks = []
    calls = []
    trace = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    async def call(client, name, arguments=None, *, error=False):
        response = await client.call_tool(name, arguments or {})
        calls.append({'tool': name, 'error': bool(response.is_error)})
        if error:
            check(response.is_error, 'rejected_' + name)
            return
        if response.is_error:
            raise AssertionError(name + ': ' + str(response.content))
        check(isinstance(response.structured_content, dict), 'structured_' + name)
        return response.structured_content['data']

    async def action(client, name, request_action, *, error=False, **fields):
        return await call(client, name,
                          {'request': {'action': request_action, **fields}},
                          error=error)

    def workflow_id(flow):
        return flow.get('id') or flow['workflow_id']

    async def wait(client, flow, *, label=None):
        deadline = asyncio.get_running_loop().time() + 90
        identifier = flow.get('operation_id')
        if not isinstance(identifier, str) or not identifier:
            operation = flow.get('operation')
            identifier = operation.get('id') if isinstance(operation, dict) else None
        if not isinstance(identifier, str) or not identifier:
            raise AssertionError('wait_requires_operation_id: ' +
                                 json.dumps(flow, ensure_ascii=False)[:6000])
        observed = {
            'label': label,
            'input_stage': flow.get('stage'),
            'input_revision': flow.get('revision'),
            'operation_id': identifier,
            'operation_action': (flow.get('operation') or {}).get('request', {}).get('action'),
            'operation_state': (flow.get('operation') or {}).get('state'),
        }
        trace.append({'kind': 'wait_request', **observed})
        last = None
        while asyncio.get_running_loop().time() < deadline:
            last = await call(client, 'vwa_wait', {
                'operation_id': identifier,
                'timeout_ms': 60000,
                'detail': 'full',
            })
            if last['state'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                check(last['state'] == 'succeeded',
                      'operation_' + last['request']['action'] +
                      str(last.get('error', '')))
                current = await action(client, 'vwa_status', 'workflow_get',
                                       workflow_id=workflow_id(flow), detail='full')
                trace.append({
                    'kind': 'wait_result', **observed,
                    'wait_result': last,
                    'workflow_after_wait': current,
                })
                return current
            if not last.get('timed_out'):
                await asyncio.sleep(.1)
        raise AssertionError('supervised operation timeout: ' +
                             json.dumps(last, ensure_ascii=False)[:2000])

    async def wait_current(client, flow, expected_action, *, label):
        current = await refresh(client, flow)
        operation = current.get('operation') or {}
        identifier = current.get('operation_id') or operation.get('id')
        action = (operation.get('request') or {}).get('action')
        check(isinstance(identifier, str) and bool(identifier),
              label + '_requires_operation_id')
        check(action == expected_action,
              label + '_expected_' + expected_action + ':got_' + str(action))
        trace.append({
            'kind': 'current_operation_before_wait',
            'label': label,
            'stage': current.get('stage'),
            'revision': current.get('revision'),
            'operation_id': identifier,
            'operation_action': action,
            'operation_state': operation.get('state'),
            'stage_error': current.get('stage_error'),
        })
        waited = await call(client, 'vwa_wait', {
            'operation_id': identifier,
            'timeout_ms': 60000,
            'detail': 'full',
        })
        check(waited.get('state') == 'succeeded' and not waited.get('error'),
              label + '_succeeded:' + str(waited.get('state')) + ':' +
              json.dumps(waited.get('error'), ensure_ascii=False))
        current = await refresh(client, current)
        trace.append({
            'kind': 'current_operation_after_wait',
            'label': label,
            'stage': current.get('stage'),
            'revision': current.get('revision'),
            'operation_id': current.get('operation_id'),
            'wait_result': waited,
            'workflow_after_wait': current,
        })
        return current

    def version(flow, **kwargs):
        return {'workflow_id': workflow_id(flow),
                'expected_revision': flow['revision'], **kwargs}

    async def refresh(client, flow):
        value = await action(client, 'vwa_status', 'workflow_get',
                             workflow_id=workflow_id(flow), detail='full')
        previous = flow.get('revision')
        check(type(value.get('revision')) is int and
              (previous is None or value['revision'] >= previous),
              'workflow_status_revision')
        return value

    original = configuration.parent / 'exchange/review.mid'
    original_sha = hashlib.sha256(original.read_bytes()).hexdigest()
    async with Client(params, read_timeout_seconds=80) as client:
        listed = await client.list_tools()
        check({tool.name for tool in listed.tools} == set(STANDARD_TOOLS),
              'workflow_standard_tool_discovery')
        # This creates a plan only; it does not download or run a model.
        link_plan = await action(client, 'vwa_start', 'preparation_plan',
            source='https://www.bilibili.com/video/BV1xx411c7mD/',
            options={'language': 'zh', 'voice_mode': 'single', 'lyrics': '山河'})
        check(link_plan['kind'] == 'preparation', 'real_link_preparation_plan')
        boundary_plan = await action(client, 'vwa_start', 'preparation_plan',
            source='https://www.bilibili.com/video/BV1xx411c7mD/',
            options={'bpm': 20, 'cpu_threads': 0,
                     'separation_device': 'cpu', 'midi_device': 'dml'})
        check(boundary_plan['plan']['options']['bpm'] == 20.0 and
              boundary_plan['plan']['options']['cpu_threads'] == 0 and
              boundary_plan['plan']['options']['separation_device'] == 'cpu' and
              boundary_plan['plan']['options']['midi_device'] == 'dml',
              'preparation_minimum_boundaries_execute')
        boundary_plan = await action(client, 'vwa_start', 'preparation_plan',
            source='https://www.bilibili.com/video/BV1xx411c7mD/',
            options={'bpm': 400, 'cpu_threads': 64,
                     'separation_device': 'dml', 'midi_device': 'cpu'})
        check(boundary_plan['plan']['options']['bpm'] == 400.0 and
              boundary_plan['plan']['options']['cpu_threads'] == 64,
              'preparation_maximum_boundaries_execute')
        for invalid_options in (
                {'bpm': 19.999}, {'bpm': 400.001}, {'cpu_threads': -1},
                {'cpu_threads': 65}, {'separation_device': 'auto'},
                {'separation_device': 'cuda'}, {'midi_device': 'auto'},
                {'midi_device': 'cuda'}):
            await action(client, 'vwa_start', 'preparation_plan',
                source='https://www.bilibili.com/video/BV1xx411c7mD/',
                options=invalid_options, error=True)
        await action(client, 'vwa_start', 'preparation_plan',
                     source='https://example.com/a-song', error=True)

        artifact = await action(client, 'vwa_input', 'assets_import',
                                path=str(original))
        flow = await action(client, 'vwa_start', 'workflow_create',
            score_artifact_id=artifact['id'], options={'language': 'zh'},
            brief='首句克制思乡，少量戏腔。', reference_lyrics='山河',
            idempotency_key='supervised-demo', review_mode='legacy')
        pitch = await action(client, 'vwa_input', 'reference_import',
            path=str(configuration.parent / 'exchange/reference.npz'),
            kind='pitch')
        offset_review = await action(client, 'vwa_evidence', 'pitch_offset_review',
            score_artifact_id=artifact['id'], pitch_artifact_id=pitch['id'],
            track_index=1, category='warning', limit=1)
        check(offset_review['summary']['warning_count'] >= 1 and
              offset_review['items'][0]['note_id'] and
              offset_review['pitch_sha256'] == pitch['sha256'] and
              offset_review['truth_determined'] is False,
              'pitch_offset_review_real_stdio')
        audio = await action(client, 'vwa_input', 'reference_import',
            path=str(configuration.parent / 'exchange/reference.wav'),
            kind='audio')
        flow = await action(client, 'vwa_input', 'workflow_references_set',
            **version(flow, pitch_artifact_id=pitch['id'],
                      audio_artifact_id=audio['id'], track_indices=[1],
                      time_origin=0.0, audio_time_origin=0.0,
                      rationale='自制夹具明确以MIDI秒0对齐；参考仅为条件性证据。'))
        check(flow['stage'] == 'review' and
              flow['next']['tool'] == 'vwa_evidence',
              'review_gate_and_next_tool')
        unchanged = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        check(unchanged['stage'] == 'review' and
              unchanged['revision'] == flow['revision'], 'cannot_skip_review')
        bundle = await action(client, 'vwa_evidence', 'workflow_review',
                              workflow_id=workflow_id(flow), limit=1)
        check(bundle['reference_evidence']['pitch']['summary']['notes_selected'] == 2,
              'existing_pitch_evidence_attached')
        check(not bundle['reference_evidence']['audio']['frame_analysis_performed'],
              'audio_header_default_no_inference')
        excerpt_next = bundle['reference_evidence']['audio_excerpt_next']
        check(excerpt_next['tool'] == 'vwa_run' and
              excerpt_next['arguments']['request']['action'] == 'reference_audio_read',
              'audio_excerpt_routes_to_standard_run')
        excerpt = await call(client, excerpt_next['tool'], excerpt_next['arguments'])
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            excerpt_result = await action(client, 'vwa_status', 'operations_get',
                                           operation_id=excerpt['id'])
            if excerpt_result['state'] in (
                    'succeeded', 'failed', 'interrupted', 'cancelled'):
                break
            await asyncio.sleep(.1)
        check(excerpt_result['state'] == 'succeeded' and
              not excerpt_result['result']['receipt']['frame_preservation']['resampled'],
              'pcm_excerpt_generated_without_resampling')
        check(bundle['total_notes'] == 2 and bundle['next_offset'] == 1,
              'review_note_pagination')
        score = await action(client, 'vwa_evidence', 'score_read',
                             artifact_id=artifact['id'])
        await action(client, 'vwa_decide', 'workflow_apply_review',
                     **version(flow, rationale=''), error=True)
        flow = await action(client, 'vwa_decide', 'workflow_apply_review',
            **version(flow,
                rationale='两音歌词缺失；明确补入山河，首音按谱升高半音，第二音保持。',
                lyric_edits=[{'note_id': note['note_id'], 'lyric': lyric,
                              'operation': 'insert'}
                             for note, lyric in zip(score['notes'], ('山', '河'))],
                note_edits=[{'note_id': score['notes'][0]['note_id'], 'pitch': 61}]))
        await action(client, 'vwa_run', 'workflow_advance',
                     workflow_id=workflow_id(flow), expected_revision=0, error=True)
        flow = await wait(client, flow)
        guarded = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        check(guarded['stage'] == 'reviewed' and
              guarded['readback']['required'] is True and
              guarded['next']['tool'] == 'vwa_evidence',
              'edited_candidate_requires_readback')
        corrected = await action(client, 'vwa_evidence', 'score_read',
                                 artifact_id=guarded['score_artifact_id'])
        check(corrected['notes'][0]['pitch'] == 61 and
              [item['lyric'] for item in corrected['notes']] == ['山', '河'],
              'lyrics_and_pitch_corrected_together')
        check(hashlib.sha256(original.read_bytes()).hexdigest() == original_sha,
              'original_midi_unchanged')
        confirmed = await action(client, 'vwa_decide', 'workflow_apply_review',
            **version(guarded,
                rationale='已回读修正候选：首音升高半音、两音歌词为山河，保留其他内容。'))
        check(confirmed['stage'] == 'reviewing' and
              (confirmed.get('operation') or {}).get('state') in
              ('queued', 'running', 'succeeded'),
              'edited_candidate_confirmation_started')
        flow = await refresh(client, confirmed)
        if flow['stage'] in ('reviewing', 'exporting', 'styling', 'refining'):
            flow = await wait(client, flow)
            flow = await refresh(client, flow)
        check(flow['stage'] == 'reviewing',
              'review_confirmation_wait_preserves_reviewing:' + str(flow.get('stage')))
        first_advance = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        first_advance['diagnostics'] = {
            'label': 'consume_review_from_reviewing',
            'steps': (first_advance.get('flow_control') or {}).get('steps'),
            'operation': first_advance.get('operation'),
            'stage_error': first_advance.get('stage_error'),
        }
        check(first_advance.get('stage') == 'reviewed',
              'review_result_consumed_to_reviewed:' + str(first_advance.get('stage')))
        trace.append({
            'kind': 'consume_review_from_reviewing',
            'stage': first_advance.get('stage'),
            'revision': first_advance.get('revision'),
            'operation_id': first_advance.get('operation_id'),
            'operation': first_advance.get('operation'),
            'flow_control': first_advance.get('flow_control'),
        })
        flow = await refresh(client, first_advance)
        second_advance = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        second_advance['diagnostics'] = {
            'label': 'start_export_from_reviewed',
            'steps': (second_advance.get('flow_control') or {}).get('steps'),
            'operation': second_advance.get('operation'),
            'stage_error': second_advance.get('stage_error'),
        }
        operation = second_advance.get('operation') or {}
        check(second_advance.get('stage') == 'exporting' and
              (operation.get('request') or {}).get('action') == 'export_apply',
              'export_started_from_reviewed:' + str(second_advance.get('stage')) +
              ':action_' + str((operation.get('request') or {}).get('action')))
        trace.append({
            'kind': 'start_export_from_reviewed',
            'stage': second_advance.get('stage'),
            'revision': second_advance.get('revision'),
            'operation_id': second_advance.get('operation_id'),
            'operation': operation,
            'flow_control': second_advance.get('flow_control'),
        })
        flow = await refresh(client, second_advance)
        flow = await wait_current(client, flow, 'export_apply',
                                  label='wait_export_started_from_reviewed')
        check(flow.get('stage') == 'exporting',
              'export_wait_preserves_unconsumed_stage:' + str(flow.get('stage')))
        consumed = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        trace.append({
            'kind': 'consume_successful_export',
            'stage': consumed.get('stage'),
            'revision': consumed.get('revision'),
            'operation_id': consumed.get('operation_id'),
            'operation': consumed.get('operation'),
            'export_summary_present': isinstance(consumed.get('export_summary'), dict),
            'stage_error': consumed.get('stage_error'),
            'flow_control': consumed.get('flow_control'),
        })
        flow = await refresh(client, consumed)
        if flow['stage'] != 'project_ready':
            operation = flow.get('operation') or {}
            operation_id = flow.get('operation_id') or operation.get('id')
            operation_detail = None
            if operation_id:
                operation_detail = await action(client, 'vwa_status', 'operations_get',
                                                operation_id=operation_id)
            diagnostic = {
                'stage': flow.get('stage'),
                'revision': flow.get('revision'),
                'operation_id': operation_id,
                'operation': operation,
                'operation_detail': operation_detail,
                'stage_error': flow.get('stage_error'),
                'last_error': flow.get('last_error'),
                'export_summary': flow.get('export_summary'),
                'export_review': flow.get('export_review'),
                'attempt': flow.get('attempt'),
                'export_attempt_history': flow.get('export_attempt_history'),
                'export_preflight': flow.get('export_preflight'),
                'next': flow.get('next'),
                'flow_control': flow.get('flow_control'),
                'diagnostics': flow.get('diagnostics'),
                'trace': trace,
                'calls': calls,
                'checks': checks,
                'workflow': flow,
            }
            path = APP / 'work/test-results/mcp-supervised-failure.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2),
                            encoding='utf-8')
            raise AssertionError(
                'verified_svp_exported:' + str(flow.get('stage')) +
                ':diagnostic=' + str(path))
        summary = flow['export_summary']
        check(summary['independent_audit']['status'] == 'verified' and
              summary['can_accept'], 'independent_export_seconds_verified')
        check(summary['required_loss_codes'] and summary['review_required'],
              'losses_visible_by_default')
        unchanged = await action(client, 'vwa_run', 'workflow_advance',
                                 **version(flow, finish=True))
        check(unchanged['stage'] == 'project_ready' and
              unchanged['revision'] == flow['revision'],
              'cannot_finish_without_export_review')
        original_report = summary['report_sha256']
        original_project = flow['project_artifacts'][0]
        pronunciation = await action(client, 'vwa_evidence', 'pronunciation_read',
                                     artifact_id=original_project['id'], limit=1)
        check(pronunciation['source_sha256'] == original_project['sha256'] and
              len(pronunciation['notes']) == 1,
              'native_pronunciation_read_paginates')
        flow = await action(client, 'vwa_decide', 'workflow_pronunciation_apply',
            **version(flow,
                rationale='自制夹具明确指定山字音素，仅验证写入、原字与音符保留，不声称声学正确。',
                changes=[{'artifact_id': original_project['id'],
                          'expected_sha256': original_project['sha256'],
                          'edits': [{'note_ref': pronunciation['notes'][0]['note_ref'],
                                     'phonemes': 'sh an'}]}]))
        check(flow['stage'] == 'refining',
              'pronunciation_uses_owned_persistent_operation')
        flow = await refresh(client, flow)
        flow = await wait(client, flow)
        flow = await refresh(client, flow)
        flow = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        flow = await wait(client, flow)
        flow = await refresh(client, flow)
        summary = flow['export_summary']
        check(summary['report_sha256'] != original_report and
              flow['export_review'] is None,
              'pronunciation_invalidates_previous_export_report')
        check(summary['independent_audit']['status'] == 'verified' and
              'explicit_phonemes_unverified' in summary['required_loss_codes'],
              'pronunciation_reaudits_notes_lyrics_seconds_and_retains_unknown_acoustics')
        edited_pronunciation = await action(client, 'vwa_evidence',
                                             'pronunciation_read',
                                             artifact_id=flow['project_artifact_id'])
        check('sh an' in json.dumps(edited_pronunciation, ensure_ascii=False),
              'explicit_phonemes_written')
        old_file = Path(original_project['path'])
        check(hashlib.sha256(io_path(old_file).read_bytes()).hexdigest() ==
              original_project['sha256'], 'previous_export_candidate_preserved')
        review = {'report_sha256': summary['report_sha256'], 'decision': 'accept',
            'rationale': '自制谱的音符、歌词和实际秒数回读一致；明确认可列明格式损失。',
            'accepted_loss_codes': summary['required_loss_codes']}
        await action(client, 'vwa_run', 'workflow_advance',
            **version(flow, export_review={**review, 'report_sha256': '0' * 64}),
            error=True)
        context = await action(client, 'vwa_evidence', 'context_read',
                               artifact_id=flow['project_artifact_id'])
        first = next(note for note in context['notes'] if note.get('singing'))
        proposal = {'track_indices': [first['track_index']],
            'brief': '首句克制思乡，少量戏腔。', 'regions': [{
                'start': first['start_seconds'] + .01,
                'end': first['end_seconds'] - .01,
                'preset': 'sad', 'strength': .5, 'style_pitch': True,
                'style_technique': 'chinese_opera', 'technique_strength': .3,
                'technique_pitch': True,
                'rationale': '按创作背景保留山字的完整时值并轻收起音。',
                'evidence_refs': [first['note_id']]}]}
        style = await action(client, 'vwa_decide', 'style_plan',
                             artifact_id=flow['project_artifact_id'],
                             proposal=proposal)
        published = await action(client, 'vwa_decide', 'drafts_publish',
                                 plan_id=style['id'], expected_revision=None)
        bound = await action(client, 'vwa_decide', 'drafts_plan',
            artifact_id=flow['project_artifact_id'],
            expected_revision=published['revision'],
            brief='按已共享到GUI的片段草稿生成。')
        flow = await action(client, 'vwa_run', 'workflow_advance',
            **version(flow, style_plan_id=bound['id'], export_review=review))
        flow = await refresh(client, flow)
        flow = await wait(client, flow)
        flow = await refresh(client, flow)
        flow = await action(client, 'vwa_run', 'workflow_advance', **version(flow))
        flow = await refresh(client, flow)
        check(flow['completed'] and flow['stage'] == 'ready',
              'editable_styled_project_ready')
        final = flow['final_artifacts'][0]
        data = json.loads(io_path(final['path']).read_text(
            encoding='utf-8-sig').rstrip('\0'))
        notes = [note for track in data['tracks']
                 for note in track.get('mainGroup', {}).get('notes', [])]
        check([note['lyrics'] for note in notes] == ['山', '河'] and
              notes[0]['pitch'] == 61,
              'exported_project_keeps_corrected_score_and_lyrics')
        check(notes[0].get('phonemes') == 'sh an',
              'style_generation_preserves_explicit_phonemes')
        check(flow['next']['external_handoff'] == 'voice_synthesis_if_requested',
              'project_completion_is_not_voice_synthesis')
    covered_tools = sorted({row['tool'] for row in calls})
    check(set(covered_tools) <= set(STANDARD_TOOLS),
          'workflow_only_standard_tools_called')
    return {'project_ready': True, 'checks': checks, 'calls': calls,
            'covered_tools': covered_tools, 'workflow_id': workflow_id(flow),
            'final_artifacts': flow['final_artifacts'],
            'model_inference': False, 'synthesis': False,
            'network_requested': False}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--client':
        import asyncio
        print(json.dumps(asyncio.run(client_contract(Path(sys.argv[2]))), ensure_ascii=False))
    elif len(sys.argv) == 3 and sys.argv[1] == '--workflow-client':
        import asyncio
        print(json.dumps(asyncio.run(workflow_contract(Path(sys.argv[2]))), ensure_ascii=False))
    else:
        unittest.main()
