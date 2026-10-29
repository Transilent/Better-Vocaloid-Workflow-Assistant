"""Contracts for standard-profile status/evidence/wait/cancel/search tools."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


APP = Path(__file__).resolve().parents[1]
SOURCE = APP / 'src' / 'agent_mcp_standard_read.py'
SDK_PYTHON = os.environ.get('VWA_MCP_SDK_PYTHON')
EXPECTED_ACTIONS = {
    'vwa_status': {
        'system_capabilities', 'system_health', 'jobs_list', 'jobs_get',
        'artifacts_list', 'artifacts_inspect', 'operations_get', 'plans_get',
        'tuning_result', 'drafts_get', 'workflow_get', 'workflows_list',
    },
    'vwa_evidence': {
        'score_read', 'score_review', 'context_read', 'lyrics_read',
        'lyric_timeline_read', 'lyric_timeline_review', 'lyrics_review',
        'reference_pitch_review',
        'reference_audio_read', 'pronunciation_read', 'workflow_review',
        'workflow_style_review',
    },
}
ACTION_CLASSES = {
    'SystemCapabilities': 'vwa_status',
    'SystemHealth': 'vwa_status',
    'JobsList': 'vwa_status',
    'JobsGet': 'vwa_status',
    'ArtifactsList': 'vwa_status',
    'ArtifactsInspect': 'vwa_status',
    'OperationsGet': 'vwa_status',
    'PlansGet': 'vwa_status',
    'TuningResult': 'vwa_status',
    'DraftsGet': 'vwa_status',
    'WorkflowGet': 'vwa_status',
    'WorkflowsList': 'vwa_status',
    'ScoreRead': 'vwa_evidence',
    'ScoreReview': 'vwa_evidence',
    'ContextRead': 'vwa_evidence',
    'LyricsRead': 'vwa_evidence',
    'LyricTimelineRead': 'vwa_evidence',
    'LyricTimelineReview': 'vwa_evidence',
    'LyricsReview': 'vwa_evidence',
    'ReferencePitchReview': 'vwa_evidence',
    'ReferenceAudioRead': 'vwa_evidence',
    'PronunciationRead': 'vwa_evidence',
    'WorkflowReview': 'vwa_evidence',
    'WorkflowStyleReview': 'vwa_evidence',
}


def declared_actions(tree):
    found = {name: set() for name in EXPECTED_ACTIONS}
    classes = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            if not isinstance(statement.target, ast.Name) or statement.target.id != 'action':
                continue
            annotation = statement.annotation
            if not (isinstance(annotation, ast.Subscript)
                    and isinstance(annotation.value, ast.Name)
                    and annotation.value.id == 'Literal'):
                continue
            members = (annotation.slice.elts if isinstance(annotation.slice, ast.Tuple)
                       else (annotation.slice,))
            actions = [item.value for item in members
                       if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if len(actions) == 1:
                classes[node.name] = actions[0]
    for class_name, action in classes.items():
        if class_name in ACTION_CLASSES:
            found[ACTION_CLASSES[class_name]].add(action)
    return found


class StandardReadStaticContract(unittest.TestCase):
    def test_register_signature_and_typed_action_catalogue(self):
        text = SOURCE.read_text(encoding='utf-8')
        tree = ast.parse(text)
        register = next(node for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == 'register')
        self.assertEqual([item.arg for item in register.args.args],
                         ['mcp', 'control', 'workflow', 'invoke'])
        self.assertEqual(declared_actions(tree), EXPECTED_ACTIONS)
        self.assertEqual(text.count("Field(discriminator='action')"), 2)
        self.assertNotIn('payload', text)
        self.assertNotIn('dict[str, Any]', text)
        self.assertEqual(text.count('call_worker'), 1)
        self.assertIn("control.call_worker, 'health', {}", text)
        for forbidden in ('subprocess', 'os.system', 'start_operation',
                          'getattr(', 'eval(', 'exec('):
            self.assertNotIn(forbidden, text)
        self.assertNotIn('workflow.advance', text)
        self.assertNotIn('start_plan', text)
        self.assertNotIn('preparation_start', text)

    def test_register_is_the_only_public_registration_entry(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        public_functions = [node.name for node in tree.body
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not node.name.startswith('_')]
        self.assertEqual(public_functions, ['register'])


def invocation_contract(source_directory):
    import asyncio
    import inspect
    sys.path.insert(0, str(source_directory))

    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from agent_control import ControlError
    from agent_errors import error_payload
    from agent_mcp_models import Result
    import agent_segment_workflow as segment_workflow
    import agent_mcp_standard_read as read_tools

    checks = []
    calls = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    class Control:
        def capabilities(self):
            return {'server': 'VWA Agent MCP', 'tools': ['legacy']}

        def call_worker(self, *args, **kwargs):
            return {'worker': True}

        def jobs_list(self, *args, **kwargs):
            return {'jobs': []}

        def job_get(self, *args, **kwargs):
            return {'job': True}

        def artifacts_list(self, *args, **kwargs):
            return {'artifacts': []}

        def inspect_artifact(self, *args, **kwargs):
            return {'artifact': True}

        def operation_get(self, operation_id):
            action = 'score_render' if operation_id == 'op_wrong' else 'style_render'
            return {'id': operation_id, 'request': {'action': action}}

        def operation_wait(self, *args, **kwargs):
            return {'operation': True}

        def operation_cancel(self, *args, **kwargs):
            return {'cancel_requested': True}

        def plan_get(self, *args, **kwargs):
            return {'plan': True}

        def drafts_get(self, *args, **kwargs):
            return {'draft': True}

        def score_read(self, *args, **kwargs):
            return {'score': True}

        def context_read(self, *args, **kwargs):
            return {'context': True}

        def lyrics_read(self, *args, **kwargs):
            return {'lyrics': True}

        def lyric_timeline_read(self, *args, **kwargs):
            return {'timeline_read': True}

        def lyric_timeline_review(self, *args, **kwargs):
            return {'timeline': True}

        def reference_pitch_review(self, *args, **kwargs):
            return {'pitch': True}

        def reference_audio_read(self, *args, **kwargs):
            return {'audio': True}

        def pronunciation_read(self, *args, **kwargs):
            return {'pronunciation': True}

        def lyric_timeline_search(self, *args, **kwargs):
            return {'results': []}

    class Workflow:
        def get(self, *args, **kwargs):
            return {'workflow': True}

        def list(self, *args, **kwargs):
            return {'workflows': []}

        def review(self, *args, **kwargs):
            return {'review': True}

    control = Control()
    workflow = Workflow()

    async def invoke(function, *args, **kwargs):
        name = getattr(function, '__name__', type(function).__name__)
        try:
            value = function(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
        except (ControlError, ValueError, OSError, TypeError, KeyError) as error:
            raise ToolError(json.dumps(error_payload(error), ensure_ascii=False)) from error
        record = {'callback': name, 'args': list(args), 'kwargs': kwargs}
        calls.append(record)
        display_args = [repr(item) if isinstance(item, Workflow) else item
                        for item in args]
        return Result(data={'callback': name, 'args': display_args,
                            'kwargs': kwargs, 'result': value})

    async def call_request(server, tool, request):
        result = await server.call_tool(tool, {'request': request})
        check(result.is_error is False, tool + ':' + request['action'] + ':not_error')
        return result.structured_content['data']

    async def call_flat(server, tool, arguments):
        result = await server.call_tool(tool, arguments)
        check(result.is_error is False, tool + ':not_error')
        return result.structured_content['data']

    async def rejected(server, tool, arguments, name, code=None):
        before = len(calls)
        try:
            await server.call_tool(tool, arguments)
        except ToolError as error:
            raised = error
        except Exception as error:
            raise AssertionError(name + ':wrong_exception_type:' + type(error).__name__) from error
        else:
            raise AssertionError(name + ':accepted')
        check(len(calls) == before, name + ':no_backend_call')
        text = str(raised)
        if code is not None:
            try:
                start = text.find('{')
                if start < 0:
                    raise ValueError('JSON object not found')
                receipt = json.loads(text[start:])
            except (ValueError, json.JSONDecodeError) as error:
                raise AssertionError(name + ':structured_receipt:' + text) from error
            check(receipt.get('code') == code, name + ':error_code')
            return receipt
        return None

    async def contract():
        server = MCPServer('standard-read-contract', log_level='ERROR')
        read_tools.register(server, control, workflow, invoke)
        tools = {tool.name: tool for tool in await server.list_tools()}
        check(set(tools) == {
            'vwa_status', 'vwa_evidence', 'vwa_wait', 'vwa_cancel', 'vwa_search',
        }, 'exactly_five_tools')
        check(set(EXPECTED_ACTIONS) == {'vwa_status', 'vwa_evidence'},
              'two_grouped_tools')

        annotations = {
            name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
            for name, tool in tools.items()
        }
        check(annotations['vwa_status']['readOnlyHint'] is False,
              'status_tracks_records_registration_and_interruption')
        check(annotations['vwa_evidence']['readOnlyHint'] is False,
              'evidence_worker_records_are_not_pure_read')
        check(annotations['vwa_wait']['readOnlyHint'] is False,
              'wait_may_reconcile_interrupted')
        check(annotations['vwa_cancel']['readOnlyHint'] is False,
              'cancel_is_cooperative_write')
        check(annotations['vwa_cancel']['idempotentHint'] is True,
              'cancel_request_is_repeat_safe')
        check(annotations['vwa_search']['openWorldHint'] is True,
              'search_declares_network')
        check(annotations['vwa_search']['readOnlyHint'] is False,
              'search_is_not_pure_read_because_network_and_worker_record')
        check(all(value['destructiveHint'] is False for value in annotations.values()),
              'no_destructive_hint')

        schema_actions = {}
        for name in EXPECTED_ACTIONS:
            schema = tools[name].input_schema
            check(set(schema.get('properties', {})) == {'request'},
                  name + ':request_only')
            request = schema['properties']['request']
            check(request['discriminator']['propertyName'] == 'action',
                  name + ':discriminator')
            mapping = request['discriminator']['mapping']
            schema_actions[name] = set(mapping)
            for action, reference in mapping.items():
                target = schema['$defs'][reference.rsplit('/', 1)[-1]]
                check(target['additionalProperties'] is False,
                      action + ':extra_forbid')
                check(target['properties']['action']['const'] == action,
                      action + ':const_action')
                check('action' in target['required'], action + ':action_required')
        check(schema_actions == EXPECTED_ACTIONS, 'all_legacy_actions_mapped')
        evidence_schema = tools['vwa_evidence'].input_schema
        evidence_mapping = evidence_schema['properties']['request']['discriminator']['mapping']
        timeline_read_schema = evidence_schema['$defs'][
            evidence_mapping['lyric_timeline_read'].rsplit('/', 1)[-1]
        ]
        check('歌词时间轴' in timeline_read_schema['properties']['artifact_id']['description'],
              'timeline_read_names_managed_artifact')
        check(timeline_read_schema['properties']['limit']['maximum'] == 200,
              'timeline_read_is_bounded')
        check(timeline_read_schema['properties']['include_words']['default'] is False,
              'timeline_read_words_opt_in')
        check('不会绑定工作流' in tools['vwa_evidence'].description,
              'timeline_read_has_no_implicit_binding')
        check('不是必须步骤' in tools['vwa_evidence'].description,
              'lyrics_reference_is_optional')

        wait_schema = tools['vwa_wait'].input_schema
        check({'operation_id', 'job_id', 'timeout_ms', 'after_cursor',
               'return_on', 'detail', 'wait_ms'} <= set(wait_schema['properties']),
              'wait_flat_fields')
        check(set(tools['vwa_cancel'].input_schema['properties']) == {'operation_id'},
              'cancel_flat_property')
        check(set(tools['vwa_search'].input_schema['properties']) == {
            'query', 'limit', 'offset',
        }, 'search_flat_properties')

        artifact = 'art_0123456789abcdef0123456789abcdef'
        job_id = 'job_0123456789abcdef0123456789abcdef'
        operation_id = 'op_0123456789abcdef0123456789abcdef'
        workflow_id = 'flow_0123456789abcdef0123456789abcdef'

        status_cases = [
            ({'action': 'system_capabilities'}, 'capabilities', ()),
            ({'action': 'system_health'}, 'call_worker', ('health', {})),
            ({'action': 'jobs_list', 'limit': 5, 'offset': 2},
             'jobs_list', (5, 2)),
            ({'action': 'jobs_get', 'job_id': job_id, 'wait_ms': 1500,
              'after_cursor': 'cursor-a'}, 'job_get', (job_id, 1500, 'cursor-a')),
            ({'action': 'artifacts_list'}, 'artifacts_list', (None,)),
            ({'action': 'artifacts_list', 'job_id': job_id},
             'artifacts_list', (job_id,)),
            ({'action': 'artifacts_inspect', 'artifact_id': artifact,
              'verify': False, 'wait_ms': 0},
             'inspect_artifact', (artifact, False, 0)),
            ({'action': 'operations_get', 'operation_id': operation_id},
             'operation_get', (operation_id,)),
            ({'action': 'plans_get', 'plan_id': 'plan_' + '1' * 32},
             'plan_get', ('plan_' + '1' * 32,)),
            ({'action': 'tuning_result', 'operation_id': operation_id},
             'tuning_result', ()),
            ({'action': 'drafts_get', 'artifact_id': artifact},
             'drafts_get', (artifact,)),
            ({'action': 'workflow_get', 'workflow_id': workflow_id,
              'include_details': True, 'detail': 'full',
              'include_note_ids': True, 'include_raw_evidence': True},
             'get', (workflow_id, True, 'full', True, True)),
            ({'action': 'workflows_list', 'limit': 7, 'offset': 3,
              'status': 'review', 'query': 'fixture'}, 'list',
             (7, 3, 'review', 'fixture')),
        ]
        for request, callback, expected_args in status_cases:
            before = len(calls)
            result = await call_request(server, 'vwa_status', request)
            check(len(calls) == before + 1, request['action'] + ':one_backend_call')
            check(result['callback'] == callback, request['action'] + ':callback')
            check(result['args'] == list(expected_args), request['action'] + ':arguments')
            check(result['kwargs'] == {}, request['action'] + ':positional_only')

        operation = await call_request(server, 'vwa_status',
            {'action': 'operations_get', 'operation_id': operation_id})
        check(operation['result']['request']['action'] == 'style_render',
              'operations_get_preserves_live_record')
        await rejected(server, 'vwa_status',
            {'request': {'action': 'tuning_result', 'operation_id': 'op_wrong'}},
            'tuning_result_wrong_operation', 'wrong_operation')

        receipt = await rejected(server, 'vwa_status',
            {'request': {'action': 'artifacts_inspect', 'artifact_id': artifact,
                         'verify': True, 'wait_ms': 1000}},
            'verify_artifact_redirected', 'write_action_required')
        suggestion = receipt['details']['suggested_tool']
        check(suggestion['tool'] == 'vwa_run', 'inspect_suggests_run')
        check(suggestion['arguments']['request']['action'] == 'artifacts_inspect',
              'inspect_preserves_action')
        check(suggestion['arguments']['request']['verify'] is True,
              'inspect_preserves_verify')

        evidence_cases = [
            ({'action': 'score_read', 'artifact_id': artifact},
             'score_read', (artifact,)),
            ({'action': 'score_review', 'artifact_id': artifact},
             'score_read', (artifact, True)),
            ({'action': 'context_read', 'artifact_id': artifact,
              'track_indices': [0, 2], 'limit': 15, 'offset': 5,
              'detail': 'full', 'include_note_ids': True},
             'context_read', (artifact, [0, 2], 15, 5, 'full', True)),
            ({'action': 'lyrics_read', 'artifact_id': artifact,
              'language': 'ja'}, 'lyrics_read', (artifact,)),
            ({'action': 'lyric_timeline_read', 'artifact_id': 'line-art',
              'limit': 8, 'offset': 3, 'include_words': True},
             'lyric_timeline_read', ('line-art', 8, 3, True)),
            ({'action': 'lyric_timeline_review',
              'score_artifact_id': 'score-art', 'timeline_artifact_id': 'line-art',
              'start': 0.5, 'end': 3.5, 'time_origin': 1.25,
              'track_indices': [0], 'limit': 9, 'offset': 2},
             'lyric_timeline_review',
             ('score-art', 'line-art', 1.25, 0.5, 3.5, [0], 9, 2)),
            ({'action': 'lyrics_review', 'artifact_id': artifact,
              'reference_text': '参考歌词', 'language': 'ja'},
             'lyrics_read', (artifact, '参考歌词', 'ja', True)),
            ({'action': 'reference_pitch_review',
              'score_artifact_id': 'score-art', 'pitch_artifact_id': 'pitch-art',
              'track_indices': [1], 'time_origin': 0.25, 'limit': 50,
              'offset': 10},
             'reference_pitch_review',
             ('score-art', 'pitch-art', [1], 0.25, 50, 10)),
            ({'action': 'reference_audio_read', 'artifact_id': 'audio-art',
              'start': 0.5, 'end': 2.5, 'make_excerpt': False},
             'reference_audio_read', ('audio-art', 0.5, 2.5, False)),
            ({'action': 'pronunciation_read', 'artifact_id': artifact,
              'limit': 25, 'offset': 5}, 'pronunciation_read',
             (artifact, 25, 5)),
            ({'action': 'workflow_review', 'workflow_id': workflow_id,
              'limit': 8, 'offset': 4, 'detail': 'full',
              'include_note_ids': True, 'include_raw_evidence': True,
              'segment_id': 'segment-1'}, 'review',
             (workflow_id, 8, 4, 'full', True, True, 'segment-1')),
        ]
        for request, callback, expected_args in evidence_cases:
            before = len(calls)
            result = await call_request(server, 'vwa_evidence', request)
            check(len(calls) == before + 1, request['action'] + ':one_backend_call')
            check(result['callback'] == callback, request['action'] + ':callback')
            check(result['args'] == list(expected_args), request['action'] + ':arguments')
            if request['action'] == 'lyrics_read':
                check(result['kwargs'] == {'language': 'ja'},
                      'lyrics_read_keyword_language')
            else:
                check(result['kwargs'] == {}, request['action'] + ':positional_only')

        original_style_review = segment_workflow.style_review
        try:
            def fake_style_review(workflow_object, identifier, segment_id=None):
                return {'workflow_object': workflow_object is workflow,
                        'identifier': identifier, 'segment_id': segment_id}

            segment_workflow.style_review = fake_style_review
            result = await call_request(server, 'vwa_evidence', {
                'action': 'workflow_style_review', 'workflow_id': workflow_id,
                'segment_id': 'segment-1',
            })
            check(result['callback'] == 'fake_style_review',
                  'workflow_style_review_uses_original_free_function')
            check(calls[-1]['args'][0] is workflow, 'workflow_instance_reused')
            check(calls[-1]['args'][1:] == [workflow_id, 'segment-1'],
                  'workflow_style_review_arguments')
            check(result['result']['workflow_object'] is True,
                  'workflow_style_review_received_instance')
        finally:
            segment_workflow.style_review = original_style_review

        receipt = await rejected(server, 'vwa_evidence',
            {'request': {'action': 'reference_audio_read',
                         'artifact_id': 'audio-art', 'start': 0.5,
                         'end': 2.5, 'make_excerpt': True}},
            'audio_excerpt_redirected', 'write_action_required')
        suggestion = receipt['details']['suggested_tool']
        check(suggestion['tool'] == 'vwa_run', 'audio_suggests_run')
        check(suggestion['arguments']['request']['action'] == 'reference_audio_read',
              'audio_preserves_action')
        check(suggestion['arguments']['request']['make_excerpt'] is True,
              'audio_preserves_excerpt_flag')

        result = await call_flat(server, 'vwa_wait', {
            'operation_id': operation_id, 'timeout_ms': 60000,
            'after_cursor': 'cursor-a', 'return_on': 'change', 'detail': 'full',
        })
        check(result['callback'] == 'operation_wait', 'wait_operation_callback')
        check(result['args'] == [operation_id, 60000, 'cursor-a', 'change', 'full'],
              'wait_operation_arguments')

        result = await call_flat(server, 'vwa_wait', {
            'job_id': job_id, 'timeout_ms': 1234, 'after_cursor': 'cursor-b',
        })
        check(result['callback'] == 'job_get', 'wait_job_callback')
        check(result['args'] == [job_id, 1234, 'cursor-b'],
              'wait_job_arguments')

        result = await call_flat(server, 'vwa_wait', {
            'job_id': job_id, 'timeout_ms': 30000, 'wait_ms': 900,
        })
        check(result['args'] == [job_id, 900, None],
              'wait_job_old_wait_ms_preserved')

        for arguments, name in (
            ({}, 'wait_needs_identity'),
            ({'operation_id': 'op', 'job_id': job_id}, 'wait_rejects_two_identities'),
            ({'operation_id': 'op', 'timeout_ms': 60001}, 'wait_rejects_over_60000'),
            ({'operation_id': 'op', 'timeout_ms': 1, 'wait_ms': 1},
             'wait_rejects_operation_wait_ms'),
            ({'job_id': job_id, 'return_on': 'change'},
             'wait_rejects_job_return_on'),
            ({'job_id': job_id, 'detail': 'full'},
             'wait_rejects_job_detail'),
        ):
            await rejected(server, 'vwa_wait', arguments, name, 'invalid_wait')

        result = await call_flat(server, 'vwa_cancel', {'operation_id': operation_id})
        check(result['callback'] == 'operation_cancel', 'cancel_callback')
        check(result['args'] == [operation_id], 'cancel_arguments')

        result = await call_flat(server, 'vwa_search', {
            'query': '戏游九州', 'limit': 7, 'offset': 3,
        })
        check(result['callback'] == 'lyric_timeline_search', 'search_callback')
        check(result['args'] == ['戏游九州', 7, 3], 'search_arguments')

        for tool, arguments, name in (
            ('vwa_status', {'request': {'action': 'operations_get',
                'operation_id': 'op', 'extra': True}}, 'status_extra_rejected'),
            ('vwa_evidence', {'request': {'action': 'score_read',
                'artifact_id': artifact, 'extra': True}}, 'evidence_extra_rejected'),
            ('vwa_wait', {'operation_id': 'op', 'timeout_ms': True},
             'wait_bool_timeout_rejected'),
            ('vwa_wait', {'operation_id': 'op', 'timeout_ms': '1000'},
             'wait_string_timeout_rejected'),

            ('vwa_search', {'query': 'x', 'limit': '10'},
             'search_string_limit_rejected'),
            ('vwa_search', {}, 'search_query_required'),
        ):
            await rejected(server, tool, arguments, name)

        return {
            'checks': checks,
            'actions': {name: sorted(values)
                        for name, values in sorted(schema_actions.items())},
            'call_count': len(calls),
            'callbacks': [item['callback'] for item in calls],
            'model_inference': False,
            'network_requested': False,
            'synthesis': False,
        }

    return asyncio.run(contract())


class StandardReadSDKContract(unittest.TestCase):
    @unittest.skipUnless(SDK_PYTHON, 'Dedicated optional SDK not configured')
    def test_register_and_dispatch_via_independent_sdk_subprocess(self):
        result = subprocess.run(
            [SDK_PYTHON, '-B', str(Path(__file__).resolve()),
             '--contract', str(SOURCE.parent)],
            cwd=APP,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=60,
            env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'),
        )
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-12000:])
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['actions'], {
            name: sorted(values) for name, values in sorted(EXPECTED_ACTIONS.items())
        })
        self.assertEqual(receipt['call_count'], 31)
        self.assertNotIn('advance', receipt['callbacks'])
        self.assertFalse(receipt['model_inference'])
        self.assertFalse(receipt['network_requested'])
        self.assertFalse(receipt['synthesis'])
        self.assertGreater(len(receipt['checks']), 100)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--contract':
        print(json.dumps(invocation_contract(Path(sys.argv[2])), ensure_ascii=False))
    else:
        unittest.main()