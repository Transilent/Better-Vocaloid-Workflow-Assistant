"""Contracts for the typed standard-profile review/decision tool."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


APP = Path(__file__).resolve().parents[1]
SOURCE = APP / 'src' / 'agent_mcp_standard_decide.py'
SDK_PYTHON = os.environ.get('VWA_MCP_SDK_PYTHON')
EXPECTED_ACTIONS = {
    'score_patch_plan', 'score_patch_apply', 'style_plan',
    'lyrics_patch_plan', 'lyrics_patch_apply',
    'structure_patch_plan', 'structure_patch_apply',
    'pronunciation_patch_plan', 'pronunciation_patch_apply',
    'drafts_publish', 'drafts_plan', 'workflow_style_decide',
    'workflow_apply_review', 'workflow_pronunciation_apply',
}
EXPECTED_FIELDS = {
    'score_patch_plan': {
        'action', 'artifact_id', 'expected_sha256', 'edits', 'move_attached_lyrics',
    },
    'score_patch_apply': {'action', 'plan_id', 'idempotency_key'},
    'style_plan': {'action', 'artifact_id', 'proposal', 'detail'},
    'lyrics_patch_plan': {'action', 'artifact_id', 'expected_sha256', 'edits'},
    'lyrics_patch_apply': {'action', 'plan_id', 'idempotency_key'},
    'structure_patch_plan': {'action', 'artifact_id', 'expected_sha256', 'edits'},
    'structure_patch_apply': {'action', 'plan_id', 'idempotency_key'},
    'pronunciation_patch_plan': {'action', 'artifact_id', 'expected_sha256', 'edits'},
    'pronunciation_patch_apply': {'action', 'plan_id', 'idempotency_key'},
    'drafts_publish': {'action', 'plan_id', 'expected_revision'},
    'drafts_plan': {'action', 'artifact_id', 'expected_revision', 'brief'},
    'workflow_style_decide': {
        'action', 'workflow_id', 'expected_revision', 'segment_id', 'rationale', 'proposal',
    },
    'workflow_apply_review': {
        'action', 'workflow_id', 'expected_revision', 'rationale', 'note_edits',
        'lyric_edits', 'structure_edits', 'move_attached_lyrics', 'segment_id',
        'run_until_gate', 'wait_ms',
    },
    'workflow_pronunciation_apply': {
        'action', 'workflow_id', 'expected_revision', 'rationale', 'changes',
    },
}
ACTION_CLASSES = {
    'ScorePatchPlan': 'score_patch_plan',
    'ScorePatchApply': 'score_patch_apply',
    'StylePlan': 'style_plan',
    'LyricsPatchPlan': 'lyrics_patch_plan',
    'LyricsPatchApply': 'lyrics_patch_apply',
    'StructurePatchPlan': 'structure_patch_plan',
    'StructurePatchApply': 'structure_patch_apply',
    'PronunciationPatchPlan': 'pronunciation_patch_plan',
    'PronunciationPatchApply': 'pronunciation_patch_apply',
    'DraftsPublish': 'drafts_publish',
    'DraftsPlan': 'drafts_plan',
    'WorkflowStyleDecide': 'workflow_style_decide',
    'WorkflowApplyReview': 'workflow_apply_review',
    'WorkflowPronunciationApply': 'workflow_pronunciation_apply',
}


def declared_actions(tree):
    found = {}
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
                found[node.name] = actions[0]
    return found


class StandardDecideStaticContract(unittest.TestCase):
    def test_register_signature_and_typed_action_catalogue(self):
        text = SOURCE.read_text(encoding='utf-8')
        tree = ast.parse(text)
        register = next(node for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == 'register')
        self.assertEqual([item.arg for item in register.args.args],
                         ['mcp', 'control', 'workflow', 'invoke'])
        declared = declared_actions(tree)
        self.assertEqual(declared, ACTION_CLASSES)
        self.assertEqual(set(declared.values()), EXPECTED_ACTIONS)
        self.assertEqual(text.count("Field(discriminator='action')"), 1)
        self.assertEqual(text.count('@mcp.tool'), 1)
        self.assertNotIn('payload', text)
        self.assertNotIn('dict[str, Any]', text)
        for forbidden in ('subprocess', 'os.system', 'start_operation',
                          'call_worker', 'getattr(', 'eval(', 'exec('):
            self.assertNotIn(forbidden, text)
        self.assertIn("control.make_plan", text)
        self.assertIn("control.start_plan", text)
        self.assertIn("control.drafts_publish", text)
        self.assertIn("control.drafts_plan", text)
        self.assertIn("workflow.apply_review", text)
        self.assertIn("workflow.pronunciation_apply", text)
        self.assertIn("style_decide", text)
        self.assertNotIn('workflow.advance', text)
        self.assertNotIn("'tuning_start'", text)
        self.assertNotIn("'exports_start'", text)

    def test_register_is_the_only_public_function(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        public = [node.name for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and not node.name.startswith('_')]
        self.assertEqual(public, ['register'])


def invocation_contract(source_directory):
    import asyncio
    import inspect
    sys.path.insert(0, str(source_directory))

    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    import agent_segment_workflow as segment_workflow
    import agent_mcp_standard_decide as decide
    import agent_mcp_standard_flow as standard_flow
    from agent_mcp_models import Result
    from agent_control import ControlError
    from agent_errors import error_payload
    from agent_mcp_standard_routing import standardize
    from agent_views import workflow_view
    import copy

    checks = []
    calls = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    def tool_error_payload(error):
        candidates = [str(error), str(error.__cause__ or '')]
        for candidate in candidates:
            start = candidate.find('{')
            if start >= 0:
                try:
                    return json.JSONDecoder().raw_decode(candidate[start:])[0]
                except json.JSONDecodeError:
                    pass
        raise AssertionError('tool error did not contain structured JSON: ' + repr(error))


    class Backend:
        def __init__(self, owner):
            self.owner = owner

        def record(self, name, args, kwargs, value):
            calls.append({
                'owner': self.owner,
                'callback': name,
                'args': list(args),
                'kwargs': kwargs,
                'value': value,
            })
            return value

        def make_plan(self, kind, artifact_id, **kwargs):
            plan = {
                'id': 'plan_' + kind,
                'kind': kind,
                'artifact_id': artifact_id,
                'plan': {'args': kwargs},
            }
            if kind == 'style':
                plan['plan']['aligned_ranges'] = [
                    {'track_index': 0, 'note_ids': ['n1', 'n2']},
                ]
            return self.record('make_plan', (kind, artifact_id), kwargs, plan)

        def start_plan(self, plan_id, kind, idempotency_key):
            value = {
                'id': 'op_' + kind,
                'state': 'queued',
                'action': kind + '_apply',
                'request': {'plan_id': plan_id},
            }
            return self.record(
                'start_plan', (plan_id, kind, idempotency_key), {}, value)

        def drafts_publish(self, plan_id, expected_revision):
            value = {
                'input_sha256': 'd' * 64,
                'revision': expected_revision or 'e' * 64,
                'writer': 'mcp:' + plan_id,
            }
            return self.record(
                'drafts_publish', (plan_id, expected_revision), {}, value)

        def drafts_plan(self, artifact_id, expected_revision, brief):
            value = {
                'id': 'plan_draft',
                'artifact_id': artifact_id,
                'shared_draft': {
                    'input_sha256': 'd' * 64,
                    'revision': expected_revision,
                },
                'plan': {'brief': brief},
            }
            return self.record(
                'drafts_plan', (artifact_id, expected_revision, brief), {}, value)

    control = Backend('control')
    workflow = Backend('workflow')

    def apply_review(self, workflow_id, expected_revision, rationale,
                     note_edits, lyric_edits, structure_edits,
                     move_attached_lyrics, segment_id):
        value = {
            'id': workflow_id,
            'revision': expected_revision + 1,
            'stage': 'reviewing',
            'operation_id': 'op_review',
            'next': {'marker': 'workflow-owned-next'},
        }
        return self.record(
            'apply_review',
            (workflow_id, expected_revision, rationale, note_edits, lyric_edits,
             structure_edits, move_attached_lyrics, segment_id),
            {},
            value,
        )

    def pronunciation_apply(self, workflow_id, expected_revision, changes, rationale):
        value = {
            'id': workflow_id,
            'revision': expected_revision + 1,
            'stage': 'refining',
            'operation_id': 'op_pronunciation',
        }
        return self.record(
            'pronunciation_apply',
            (workflow_id, expected_revision, changes, rationale),
            {},
            value,
        )

    workflow.apply_review = apply_review.__get__(workflow, Backend)
    workflow.pronunciation_apply = pronunciation_apply.__get__(workflow, Backend)

    def style_decide(workflow_object, workflow_id, expected_revision, segment_id,
                     rationale, proposal):
        value = {
            'id': workflow_id,
            'revision': expected_revision + 1,
            'stage': 'project_ready',
        }
        return workflow.record(
            'style_decide',
            (workflow_id, expected_revision, segment_id, rationale, proposal),
            {},
            value,
        )

    segment_workflow.style_decide = style_decide

    async def invoke(function, *args, **kwargs):
        before = len(calls)
        try:
            value = function(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
            if getattr(function, '__name__', '') not in (
                    'reject_invalid_wait', 'raise_driver_error'):
                check(len(calls) == before + 1, 'backend_called_once')
            check(isinstance(value, dict), 'backend_returns_structured_data')
            return Result(data=value)
        except (ControlError, ValueError, OSError, TypeError, KeyError) as error:
            raise ToolError(json.dumps(error_payload(error), ensure_ascii=False)) from error

    async def call(server, request):
        result = await server.call_tool('vwa_decide', {'request': request})
        check(result.is_error is False, 'tool_result_not_error')
        return result.structured_content['data']

    async def rejected(server, request, name):
        before = len(calls)
        try:
            await server.call_tool('vwa_decide', {'request': request})
        except ToolError as error:
            check(len(calls) == before, name + ':no_backend_call')
            return error
        raise AssertionError(name + ':accepted')

    async def contract():
        server = MCPServer('standard-decide-contract', log_level='ERROR')
        decide.register(server, control, workflow, invoke)
        tools = await server.list_tools()
        check(len(tools) == 1 and tools[0].name == 'vwa_decide', 'exactly_one_tool')
        tool = tools[0]
        annotations = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        check(annotations == {
            'title': 'Submit explicit review decisions and plans',
            'readOnlyHint': False,
            'destructiveHint': False,
            'idempotentHint': False,
            'openWorldHint': False,
        }, 'write_annotations_are_conservative')

        schema = tool.input_schema
        check(set(schema.get('properties', {})) == {'request'}, 'request_only')
        request_schema = schema['properties']['request']
        check(request_schema['discriminator']['propertyName'] == 'action',
              'action_discriminator')
        mapping = request_schema['discriminator']['mapping']
        check(set(mapping) == EXPECTED_ACTIONS, 'all_14_actions_mapped')
        definitions = {}
        for action, reference in mapping.items():
            name = reference.rsplit('/', 1)[-1]
            definition = schema['$defs'][name]
            definitions[action] = definition
            check(definition['additionalProperties'] is False, action + ':extra_forbid')
            check(definition['properties']['action']['const'] == action,
                  action + ':const_action')
            check('action' in definition['required'], action + ':action_required')
            check(set(definition['properties']) == EXPECTED_FIELDS[action],
                  action + ':original_fields_preserved')
        check(definitions['score_patch_plan']['properties']['edits']['maxItems'] == 4096,
              'score_edit_limit')
        check(definitions['lyrics_patch_plan']['properties']['edits']['maxItems'] == 4096,
              'lyric_edit_limit')
        check(definitions['pronunciation_patch_plan']['properties']['edits']['maxItems'] == 512,
              'pronunciation_edit_limit')
        review_properties = definitions['workflow_apply_review']['properties']
        check(review_properties['run_until_gate']['default'] is False,
              'workflow_apply_review:run_until_gate_default_off')
        check(review_properties['wait_ms']['default'] == 0,
              'workflow_apply_review:wait_default_zero')
        check(review_properties['wait_ms']['maximum'] == 60000,
              'workflow_apply_review:wait_bounded')
        check('minItems' not in definitions['structure_patch_plan']['properties']['edits'],
              'structure_empty_list_left_to_backend')
        check('maxItems' not in definitions['workflow_pronunciation_apply']
              ['properties']['changes'], 'workflow_change_limit_left_to_backend')
        check(definitions['workflow_apply_review']['properties']['expected_revision']['type']
              == 'integer', 'workflow_revision_is_integer')

        sha = 'a' * 64
        draft_revision = 'b' * 64
        workflow_id = 'flow_0123456789abcdef0123456789abcdef'
        proposal = {
            'track_indices': [0],
            'brief': '调用者明确要求克制悲伤并轻带戏腔。',
            'regions': [{
                'start': 0.1,
                'end': 0.3,
                'preset': 'sad',
                'strength': 0.5,
                'style_pitch': True,
                'style_technique': 'chinese_opera',
                'technique_strength': 0.3,
                'technique_pitch': True,
                'rationale': '按已读整音符范围明确选择。',
                'evidence_refs': ['n1'],
                'detail': {'onset': 0.8, 'sustain': 0.7,
                           'connection': 0.9, 'release': 0.4},
            }],
        }
        score_edit = {'note_id': 'n1', 'pitch': 61}
        lyric_edit = {'note_id': 'n1', 'lyric': '山', 'operation': 'replace'}
        structure_edits = [
            {'operation': 'delete', 'note_id': 'n2', 'lyric_policy': 'preserve'},
            {'operation': 'insert', 'track_index': 0, 'channel': 0, 'pitch': 65,
             'onset_tick': 960, 'duration_tick': 480, 'velocity': 80, 'lyric': '河'},
        ]
        pronunciation_edits = [{'note_ref': 'n1', 'phonemes': 'sh an'}]
        pronunciation_targets = [{
            'artifact_id': 'art_project',
            'expected_sha256': sha,
            'edits': pronunciation_edits,
        }]

        cases = [
            ({
                'action': 'score_patch_plan', 'artifact_id': 'art_score',
                'expected_sha256': sha, 'edits': [score_edit],
                'move_attached_lyrics': True,
            }, 'control', 'make_plan', ('score', 'art_score'), {
                'expected_sha256': sha, 'edits': [score_edit],
                'move_attached_lyrics': True,
            }),
            ({
                'action': 'score_patch_apply', 'plan_id': 'plan_score',
                'idempotency_key': 'score-once',
            }, 'control', 'start_plan', ('plan_score', 'score', 'score-once'), {}),
            ({
                'action': 'style_plan', 'artifact_id': 'art_svp', 'proposal': proposal,
            }, 'control', 'make_plan', ('style', 'art_svp'), {
                'proposal': {**proposal, 'regions': [{
                    **proposal['regions'][0],
                    'detail': {**proposal['regions'][0]['detail']},
                }]},
            }),
            ({
                'action': 'lyrics_patch_plan', 'artifact_id': 'art_score',
                'expected_sha256': sha, 'edits': [lyric_edit],
            }, 'control', 'make_plan', ('lyrics', 'art_score'), {
                'expected_sha256': sha, 'edits': [lyric_edit],
            }),
            ({
                'action': 'lyrics_patch_apply', 'plan_id': 'plan_lyrics',
                'idempotency_key': 'lyrics-once',
            }, 'control', 'start_plan', ('plan_lyrics', 'lyrics', 'lyrics-once'), {}),
            ({
                'action': 'structure_patch_plan', 'artifact_id': 'art_score',
                'expected_sha256': sha, 'edits': structure_edits,
            }, 'control', 'make_plan', ('structure', 'art_score'), {
                'expected_sha256': sha, 'edits': structure_edits,
            }),
            ({
                'action': 'structure_patch_apply', 'plan_id': 'plan_structure',
                'idempotency_key': 'structure-once',
            }, 'control', 'start_plan',
             ('plan_structure', 'structure', 'structure-once'), {}),
            ({
                'action': 'pronunciation_patch_plan', 'artifact_id': 'art_project',
                'expected_sha256': sha, 'edits': pronunciation_edits,
            }, 'control', 'make_plan', ('pronunciation', 'art_project'), {
                'expected_sha256': sha, 'edits': pronunciation_edits,
            }),
            ({
                'action': 'pronunciation_patch_apply', 'plan_id': 'plan_pronunciation',
                'idempotency_key': 'pronunciation-once',
            }, 'control', 'start_plan',
             ('plan_pronunciation', 'pronunciation', 'pronunciation-once'), {}),
            ({
                'action': 'drafts_publish', 'plan_id': 'plan_style',
                'expected_revision': draft_revision,
            }, 'control', 'drafts_publish', ('plan_style', draft_revision), {}),
            ({
                'action': 'drafts_plan', 'artifact_id': 'art_svp',
                'expected_revision': draft_revision, 'brief': '按已读共享草稿生成。',
            }, 'control', 'drafts_plan',
             ('art_svp', draft_revision, '按已读共享草稿生成。'), {}),
            ({
                'action': 'workflow_style_decide', 'workflow_id': workflow_id,
                'expected_revision': 7, 'segment_id': 'segment_1',
                'rationale': '本段明确保留原表现。', 'proposal': None,
            }, 'workflow', 'style_decide',
             (workflow_id, 7, 'segment_1', '本段明确保留原表现。', None), {}),
            ({
                'action': 'workflow_apply_review', 'workflow_id': workflow_id,
                'expected_revision': 8,
                'rationale': '已分页核对并明确提交修改。',
                'note_edits': [score_edit], 'lyric_edits': [lyric_edit],
                'structure_edits': structure_edits,
                'move_attached_lyrics': True, 'segment_id': 'segment_1',
            }, 'workflow', 'apply_review',
             (workflow_id, 8, '已分页核对并明确提交修改。', [score_edit],
              [lyric_edit], structure_edits, True, 'segment_1'), {}),
            ({
                'action': 'workflow_pronunciation_apply', 'workflow_id': workflow_id,
                'expected_revision': 9, 'rationale': '调用者明确给出音素，待回读验证。',
                'changes': pronunciation_targets,
            }, 'workflow', 'pronunciation_apply',
             (workflow_id, 9, pronunciation_targets,
              '调用者明确给出音素，待回读验证。'), {}),
        ]

        for request, owner, callback, expected_args, expected_kwargs in cases:
            before = len(calls)
            data = await call(server, request)
            check(len(calls) == before + 1, request['action'] + ':one_backend_call')
            record = calls[-1]
            check(record['owner'] == owner, request['action'] + ':owner')
            check(record['callback'] == callback, request['action'] + ':callback')
            check(record['args'] == list(expected_args), request['action'] + ':arguments')
            check(record['kwargs'] == expected_kwargs, request['action'] + ':kwargs')
            check(data['readback']['required'] is True,
                  request['action'] + ':readback_required')
            check(isinstance(data['readback']['instruction'], str)
                  and data['readback']['instruction'],
                  request['action'] + ':readback_instruction')
            if request['action'].endswith('_patch_plan') or request['action'] == 'style_plan':
                check(data['readback']['steps'][0]['tool'] == 'vwa_status',
                      request['action'] + ':plan_readback_tool')
                check(data['readback']['steps'][0]['arguments']['request']['action']
                      == 'plans_get', request['action'] + ':plan_readback_action')
            if request['action'].endswith('_patch_apply'):
                check(data['readback']['steps'][0]['arguments']['request']['action']
                      == 'operations_get', request['action'] + ':operation_readback')
            if request['action'].startswith('workflow_'):
                check(any(step['arguments']['request']['action'] == 'workflow_get'
                          for step in data['readback'].get('steps', [])),
                      request['action'] + ':workflow_readback')
            if request['action'] == 'workflow_apply_review':
                check(data['readback']['required'] is True,
                      'workflow_edit:candidate_readback_required')
                check(data['readback']['purpose'] == 'verify_review_candidate',
                      'workflow_edit:purpose')
                check(data['readback']['operation_wait_required'] is True,
                      'workflow_edit:wait_operation_required')
                check(data['readback']['candidate_ready'] is False,
                      'workflow_edit:candidate_not_ready')
                check(data['readback']['operation_state'] is None,
                      'workflow_edit:operation_state_unknown')
                check(data['next']['tool'] == 'vwa_wait',
                      'workflow_edit:next_wait')
                check([row['arguments']['request']['action']
                       for row in data['readback']['steps']]
                      == ['operations_get', 'workflow_get'],
                      'workflow_edit:candidate_and_workflow_steps')
                check('绝不等于候选已获认可' in data['readback']['instruction'],
                      'workflow_edit:candidate_not_approved')
                check('必须再次提交明确审核' in data['readback']['instruction'],
                      'workflow_edit:explicit_rereview_required')
                check(data['readback']['approval_required'] is True,
                      'workflow_edit:approval_required_flag')
            if request['action'] == 'drafts_plan':
                self_steps = data['readback']['steps']
                check([row['arguments']['request']['action'] for row in self_steps]
                      == ['plans_get', 'drafts_get'], 'drafts_plan_both_readbacks')
            if request['action'] == 'style_plan':
                check(data['view'] == {'detail': 'summary', 'note_ids_omitted': True},
                      'style_summary_view')
                check(data['plan']['aligned_ranges'][0]['note_ids_count'] == 2,
                      'style_summary_omits_note_ids')
                check('note_ids' not in data['plan']['aligned_ranges'][0],
                      'style_summary_has_no_ids')
                check(data['details_tool']['arguments']['request']['action'] == 'plans_get',
                      'style_summary_details_route')

        full_style = await call(server, {
            'action': 'style_plan', 'artifact_id': 'art_svp',
            'proposal': proposal, 'detail': 'full',
        })
        check(full_style['plan']['aligned_ranges'][0]['note_ids'] == ['n1', 'n2'],
              'style_full_preserves_note_ids')
        check(full_style['readback']['required'] is True, 'style_full_readback')

        keep_request = {
            'action': 'workflow_apply_review', 'workflow_id': workflow_id,
            'expected_revision': 10,
            'rationale': '已读取所需证据并明确保留原音符与歌词。',
            'note_edits': [], 'lyric_edits': [], 'structure_edits': [],
        }
        before = len(calls)
        kept = await call(server, keep_request)
        check(len(calls) == before + 1, 'workflow_keep:one_backend_call')
        check(calls[-1]['callback'] == 'apply_review', 'workflow_keep:callback')
        check(calls[-1]['args'] == [
            workflow_id, 10, '已读取所需证据并明确保留原音符与歌词。',
            [], [], [], False, None,
        ], 'workflow_keep:original_arguments')
        check(kept['readback']['required'] is False, 'workflow_keep:no_candidate_readback')
        check(kept['readback']['purpose'] == 'await_review_operation',
              'workflow_keep:purpose')
        check(kept['readback']['operation_wait_required'] is True,
              'workflow_keep:wait_operation_required')
        check([row['arguments']['request']['action']
               for row in kept['readback']['steps']]
              == ['operations_get', 'workflow_get'], 'workflow_keep:wait_steps')
        check('不会自动批准未读证据' in kept['readback']['instruction'],
              'workflow_keep:no_automatic_approval')
        check(kept.get('next') == {'marker': 'workflow-owned-next'},
              'workflow_keep:next_preserved')
        check('approval_required' not in kept['readback'],
              'workflow_keep:no_candidate_approval_flag')

        original_drive = standard_flow._drive_workflow
        driver_calls = []
        driver_mode = {'name': 'edited'}

        async def fake_drive(workflow_object, control_object, invoke_function, current,
                             *, action, run_until_gate, wait_ms, decisions,
                             initial_export_options=None):
            driver_calls.append({
                'workflow_object': workflow_object,
                'control_object': control_object,
                'invoke_function': invoke_function,
                'current': current,
                'action': action,
                'run_until_gate': run_until_gate,
                'wait_ms': wait_ms,
                'decisions': decisions,
                'initial_export_options': initial_export_options,
            })
            if driver_mode['name'] == 'succeeded_unconsumed':
                return {
                    **current,
                    'stage': 'reviewing',
                    'operation': {'id': 'op_review', 'state': 'succeeded'},
                }
            if driver_mode['name'] == 'edited':
                return {
                    **current,
                    'stage': 'reviewed',
                    'revision': current['revision'] + 1,
                    'score_artifact_id': 'art_candidate',
                    'operation': {'id': 'op_review', 'state': 'succeeded'},
                    'review_receipt': {
                        'artifact_id': 'art_candidate',
                        'original_artifact_id': 'art_source',
                        'source_sha256': sha,
                        'caller_reviewed': True,
                        'receipts': [{'candidate_path': 'candidate.mid'}],
                    },
                    'next': {
                        'tool': 'vwa_evidence',
                        'arguments': {'request': {
                            'action': 'workflow_review', 'workflow_id': workflow_id,
                        }},
                    },
                    'readback': {'required': False, 'purpose': 'stale'},
                }
            return {
                **current,
                'stage': 'project_ready',
                'revision': current['revision'] + 2,
                'export_summary': {'report_sha256': 'f' * 64},
                'next': {'tool': 'vwa_run', 'arguments': {'request': {
                    'action': 'workflow_advance', 'workflow_id': workflow_id,
                }}},
            }

        standard_flow._drive_workflow = fake_drive
        try:
            auto_edit = await call(server, {
                'action': 'workflow_apply_review',
                'workflow_id': workflow_id,
                'expected_revision': 11,
                'rationale': '明确提交修改，等待候选回读。',
                'note_edits': [score_edit],
                'run_until_gate': True,
                'wait_ms': 60000,
            })
            check(len(driver_calls) == 1, 'workflow_auto_edit:driver_called_once')
            drive = driver_calls[0]
            check(drive['workflow_object'] is workflow, 'workflow_auto_edit:workflow_reused')
            check(drive['control_object'] is control, 'workflow_auto_edit:control_reused')
            check(drive['invoke_function'] is invoke, 'workflow_auto_edit:invoke_reused')
            check(drive['action'] == 'workflow_apply_review',
                  'workflow_auto_edit:driver_action')
            check(drive['run_until_gate'] is True, 'workflow_auto_edit:gate_enabled')
            check(drive['wait_ms'] == 60000, 'workflow_auto_edit:wait_forwarded')
            check(drive['decisions'] == {
                'style_proposal': None,
                'finish': False,
                'export_review': None,
                'style_plan_id': None,
            }, 'workflow_auto_edit:no_implicit_downstream_decision')
            check(drive['current']['stage'] == 'reviewing',
                  'workflow_auto_edit:starts_from_review_operation')
            check('advance' not in [item['callback'] for item in calls],
                  'workflow_auto_edit:decide_never_calls_advance_directly')
            check(auto_edit['stage'] == 'reviewed',
                  'workflow_auto_edit:driver_result_preserved')
            check(auto_edit['readback']['required'] is True,
                  'workflow_auto_edit:readback_not_relaxed')
            check(auto_edit['readback']['approval_required'] is True,
                  'workflow_auto_edit:candidate_not_approved')
            check(auto_edit['readback']['operation_wait_required'] is False,
                  'workflow_auto_edit:completed_operation_not_rewait')
            check(auto_edit['readback']['candidate_ready'] is True,
                  'workflow_auto_edit:candidate_ready_for_reread')
            check(auto_edit['readback']['operation_state'] == 'succeeded',
                  'workflow_auto_edit:operation_state_succeeded')
            check(auto_edit['readback']['purpose'] == 'verify_review_candidate',
                  'workflow_auto_edit:readback_purpose')
            check('绝不等于候选已获认可' in auto_edit['readback']['instruction'],
                  'workflow_auto_edit:explicitly_not_accepted')
            check('必须再次提交明确审核' in auto_edit['readback']['instruction'],
                  'workflow_auto_edit:rereview_required')
            check('vwa_evidence workflow_review' in
                  auto_edit['readback']['after_terminal'],
                  'workflow_auto_edit:evidence_before_reapproval')
            check(auto_edit['next']['tool'] == 'vwa_evidence',
                  'workflow_auto_edit:next_evidence_preserved')

            driver_mode['name'] = 'succeeded_unconsumed'
            auto_unconsumed = await call(server, {
                'action': 'workflow_apply_review',
                'workflow_id': workflow_id,
                'expected_revision': 12,
                'rationale': '修改操作已成功，但工作流结果尚待消费。',
                'note_edits': [score_edit],
                'run_until_gate': True,
                'wait_ms': 60000,
            })
            check(auto_unconsumed['readback']['operation_wait_required'] is False,
                  'workflow_auto_unconsumed:no_repeat_wait')
            check(auto_unconsumed['readback']['candidate_ready'] is False,
                  'workflow_auto_unconsumed:candidate_not_bound')
            check(auto_unconsumed['readback']['operation_state'] == 'succeeded',
                  'workflow_auto_unconsumed:operation_succeeded')
            check(auto_unconsumed['next']['tool'] == 'vwa_run',
                  'workflow_auto_unconsumed:next_advance')
            check(auto_unconsumed['next']['arguments']['request']['run_until_gate'] is True,
                  'workflow_auto_unconsumed:advance_has_budget')

            driver_mode['name'] = 'clean'
            auto_keep = await call(server, {
                'action': 'workflow_apply_review',
                'workflow_id': workflow_id,
                'expected_revision': 12,
                'rationale': '已读候选并明确保留原音符与歌词。',
                'note_edits': [],
                'lyric_edits': [],
                'structure_edits': [],
                'run_until_gate': True,
                'wait_ms': 60000,
            })
            check(len(driver_calls) == 3, 'workflow_auto_keep:driver_called_once')
            check(driver_calls[2]['wait_ms'] == 60000,
                  'workflow_auto_keep:wait_forwarded')
            check(auto_keep['stage'] == 'project_ready',
                  'workflow_auto_keep:deterministic_progress')
            check('readback' not in auto_keep,
                  'workflow_auto_keep:no_edit_readback')
            check(auto_keep['next']['tool'] == 'vwa_run',
                  'workflow_auto_keep:next_run_preserved')

            async def failing_drive(*args, **kwargs):
                raise ControlError('workflow_conflict', '工作流版本已改变。')

            standard_flow._drive_workflow = failing_drive
            before_driver_error = len(calls)
            try:
                await server.call_tool('vwa_decide', {'request': {
                    'action': 'workflow_apply_review',
                    'workflow_id': workflow_id,
                    'expected_revision': 13,
                    'rationale': '驱动冲突必须返回结构化错误。',
                    'note_edits': [score_edit],
                    'run_until_gate': True,
                    'wait_ms': 10,
                }})
            except ToolError as error:
                payload = tool_error_payload(error)
                check(payload.get('code') == 'workflow_conflict',
                      'workflow_driver_error:structured_code')
                check('工作流版本已改变' in payload.get('message', ''),
                      'workflow_driver_error:structured_message')
            else:
                raise AssertionError('workflow_driver_error:unexpected_success')
            check(len(calls) == before_driver_error + 1,
                  'workflow_driver_error:decision_submitted_before_driver')
        finally:
            standard_flow._drive_workflow = original_drive
        # Compose the real flow driver with this adapter.  The edited path must
        # consume the review operation and stop at the new candidate; the clean
        # path may reach the bounded export wait, but never the report gate.
        real_calls = []
        real_waits = []

        class RealReviewControl:
            def operation_wait(self, operation_id, timeout_ms, *args, **kwargs):
                real_waits.append(timeout_ms)
                succeeded = operation_id == 'op_review'
                value = {
                    'id': operation_id,
                    'state': 'succeeded' if succeeded else 'queued',
                    'timed_out': not succeeded,
                }
                real_calls.append(('operation_wait', operation_id, value))
                return value

        class RealReviewWorkflow:
            def __init__(self, edited):
                self.edited = edited
                self.stage = 'review'
                self.revision = 20
                self.operation = None
                self.receipt = None
                self.advance_count = 0

            def state(self):
                value = {
                    'id': 'flow_real_driver',
                    'revision': self.revision,
                    'stage': self.stage,
                    'request': {'brief': '', 'options': {}, 'formats': ['svp']},
                    'score_artifact_id': 'art_source',
                    'history': [],
                }
                if self.operation is not None:
                    value['operation'] = copy.deepcopy(self.operation)
                    value['operation_id'] = self.operation['id']
                if self.receipt is not None:
                    value['review_receipt'] = copy.deepcopy(self.receipt)
                return value

            def apply_review(self, workflow_id, revision, rationale, note_edits,
                             lyric_edits, structure_edits, move_attached_lyrics,
                             segment_id):
                self.revision += 1
                self.stage = 'reviewing'
                self.operation = {'id': 'op_review', 'state': 'queued'}
                real_calls.append(('apply_review', workflow_id, note_edits))
                return self.state()

            def get(self, *args, **kwargs):
                real_calls.append(('get', args[0], self.stage))
                return self.state()

            def advance(self, workflow_id, revision, *args, **kwargs):
                self.advance_count += 1
                if self.stage == 'reviewing':
                    self.stage = 'reviewed'
                    self.revision += 1
                    self.operation = {'id': 'op_review', 'state': 'succeeded'}
                    self.receipt = {
                        'artifact_id': 'art_candidate' if self.edited else 'art_source',
                        'original_artifact_id': 'art_source',
                        'source_sha256': sha,
                        'caller_reviewed': True,
                        'receipts': ([{'candidate_path': 'candidate.mid'}]
                                     if self.edited else []),
                    }
                elif self.stage == 'reviewed':
                    if self.edited:
                        raise AssertionError('edited review reached export')
                    self.stage = 'exporting'
                    self.revision += 1
                    self.operation = {'id': 'op_export', 'state': 'queued'}
                else:
                    raise AssertionError('unexpected real driver stage')
                real_calls.append(('advance', workflow_id, self.stage))
                return self.state()

        async def real_invoke(function, *args, **kwargs):
            value = function(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
            if getattr(function, '__name__', '') in ('apply_review', 'advance'):
                value = workflow_view(value)
            return Result(data=standardize(value))

        async def real_service(edited, request):
            real_workflow = RealReviewWorkflow(edited)
            real_control = RealReviewControl()
            service = MCPServer('standard-decide-real-driver', log_level='ERROR')
            decide.register(service, real_control, real_workflow, real_invoke)
            return real_workflow, await call(service, request)

        edited_flow, actual_edited = await real_service(True, {
            'action': 'workflow_apply_review',
            'workflow_id': 'flow_real_driver',
            'expected_revision': 20,
            'rationale': '明确修改音符，候选必须再次审核。',
            'note_edits': [score_edit],
            'run_until_gate': True,
            'wait_ms': 60000,
        })
        check(edited_flow.advance_count == 1,
              'real_driver_edit:review_consumed_once')
        check(actual_edited['stage'] == 'reviewed',
              'real_driver_edit:stops_reviewed')
        check(actual_edited['readback']['required'] is True,
              'real_driver_edit:readback_required')
        check(actual_edited['readback']['approval_required'] is True,
              'real_driver_edit:approval_required')
        check(actual_edited['readback']['operation_wait_required'] is False,
              'real_driver_edit:completed_operation_not_rewait')
        check(actual_edited['readback']['candidate_ready'] is True,
              'real_driver_edit:candidate_ready_for_reread')
        check(actual_edited['readback']['operation_state'] == 'succeeded',
              'real_driver_edit:operation_state_succeeded')
        check(actual_edited['next']['tool'] == 'vwa_evidence',
              'real_driver_edit:next_evidence')
        check(not any(call_[0] == 'advance' and call_[2] == 'exporting'
                      for call_ in real_calls),
              'real_driver_edit:no_export_advance')
        check(any(call_[0] == 'operation_wait' for call_ in real_calls),
              'real_driver_edit:waited_operation')
        check(real_waits and all(0 <= value <= 60000 for value in real_waits),
              'real_driver_edit:bounded_waits')

        pending_flow, actual_pending = await real_service(True, {
            'action': 'workflow_apply_review',
            'workflow_id': 'flow_real_driver',
            'expected_revision': 20,
            'rationale': '修改已提交，但本轮不等待操作完成。',
            'note_edits': [score_edit],
            'run_until_gate': True,
            'wait_ms': 0,
        })
        check(pending_flow.advance_count == 0,
              'real_driver_pending:no_premature_advance')
        check(actual_pending['stage'] == 'reviewing',
              'real_driver_pending:stays_reviewing')
        check(actual_pending['flow_control']['stopped_reason'] == 'wait_timeout',
              'real_driver_pending:zero_budget_stops')
        check(actual_pending['readback']['required'] is True,
              'real_driver_pending:readback_flagged_before_completion')
        check(actual_pending['readback']['approval_required'] is True,
              'real_driver_pending:approval_blocked_before_completion')
        check(actual_pending['readback']['operation_wait_required'] is True,
              'real_driver_pending:wait_required')
        check(actual_pending['readback']['operation_state'] == 'queued',
              'real_driver_pending:operation_state_queued')
        check(actual_pending['readback']['candidate_ready'] is False,
              'real_driver_pending:candidate_not_ready')
        check(actual_pending['flow_control']['readback_required'] is True,
              'real_driver_pending:flow_control_readback')
        check(actual_pending['flow_control']['approval_required'] is True,
              'real_driver_pending:flow_control_approval')
        check(actual_pending['next']['tool'] == 'vwa_wait',
              'real_driver_pending:next_wait')
        clean_flow, actual_clean = await real_service(False, {
            'action': 'workflow_apply_review',
            'workflow_id': 'flow_real_driver',
            'expected_revision': 20,
            'rationale': '已读候选并明确保留原音符与歌词。',
            'note_edits': [],
            'lyric_edits': [],
            'structure_edits': [],
            'run_until_gate': True,
            'wait_ms': 60000,
        })
        check(clean_flow.advance_count == 2,
              'real_driver_keep:clean_review_auto_continued')
        check(actual_clean['stage'] == 'exporting',
              'real_driver_keep:entered_export')
        check(actual_clean['flow_control']['stopped_reason'] == 'wait_timeout',
              'real_driver_keep:bounded_at_wait')
        check(actual_clean['next']['tool'] == 'vwa_wait',
              'real_driver_keep:next_wait')
        check('readback' not in actual_clean,
              'real_driver_keep:no_edit_readback')
        check(actual_clean['stage'] != 'project_ready',
              'real_driver_keep:did_not_fake_report_completion')
        rejected_cases = [
            ({'action': 'unknown_action'}, 'unknown_action'),
            ({'artifact_id': 'art_score'}, 'missing_action'),
            ({'action': 'score_patch_apply', 'plan_id': 'plan_score',
              'idempotency_key': 'once', 'extra': True}, 'extra_field'),
            ({'action': 'workflow_apply_review', 'workflow_id': workflow_id,
              'expected_revision': '8', 'rationale': 'strict'}, 'string_revision'),
            ({'action': 'workflow_apply_review', 'workflow_id': workflow_id,
              'expected_revision': 8, 'rationale': 'strict', 'wait_ms': 60001},
             'wait_over_limit'),
            ({'action': 'workflow_apply_review', 'workflow_id': workflow_id,
              'expected_revision': 8, 'rationale': 'strict', 'run_until_gate': 1},
             'non_boolean_gate'),
            ({'action': 'score_patch_plan', 'artifact_id': 'art_score',
              'expected_sha256': sha,
              'edits': [{'note_id': 'n1', 'pitch': True}]}, 'bool_pitch'),
            ({'action': 'score_patch_plan', 'artifact_id': 'art_score',
              'expected_sha256': sha, 'edits': []}, 'empty_score_edits'),
            ({'action': 'pronunciation_patch_plan', 'artifact_id': 'art_project',
              'expected_sha256': sha, 'edits': []}, 'empty_pronunciation_edits'),
        ]
        for request, name in rejected_cases:
            await rejected(server, request, name)
        wait_error = await rejected(server, {
            'action': 'workflow_apply_review', 'workflow_id': workflow_id,
            'expected_revision': 8, 'rationale': 'strict', 'wait_ms': 1,
        }, 'wait_requires_explicit_gate')
        wait_payload = tool_error_payload(wait_error)
        check(wait_payload.get('code') == 'invalid_wait',
              'wait_requires_explicit_gate:structured_code')
        check('run_until_gate=true' in wait_payload.get('message', ''),
              'wait_requires_explicit_gate:structured_message')

        return {
            'checks': checks,
            'actions': sorted(mapping),
            'call_count': len(calls),
            'callbacks': [item['callback'] for item in calls],
            'driver_call_count': len(driver_calls),
            'model_inference': False,
            'network_requested': False,
            'synthesis': False,
            'workflow_advance_called': any(item['callback'] == 'advance' for item in calls),
        }

    return asyncio.run(contract())


class StandardDecideSDKContract(unittest.TestCase):
    @unittest.skipUnless(SDK_PYTHON, 'Dedicated optional SDK not configured')
    def test_register_and_dispatch_via_independent_sdk_subprocess(self):
        with tempfile.TemporaryDirectory(prefix='mcp-standard-decide-', dir=APP / 'work') as temporary:
            result = subprocess.run(
                [SDK_PYTHON, '-B', str(Path(__file__).resolve()),
                 '--contract', str(SOURCE.parent)],
                cwd=APP,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=90,
                env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                         TEMP=str(temporary), TMP=str(temporary)),
            )
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-12000:])
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['actions'], sorted(EXPECTED_ACTIONS))
        self.assertEqual(receipt['call_count'], 20)
        self.assertEqual(receipt['driver_call_count'], 3)
        self.assertNotIn('advance', receipt['callbacks'])
        self.assertFalse(receipt['workflow_advance_called'])
        self.assertFalse(receipt['model_inference'])
        self.assertFalse(receipt['network_requested'])
        self.assertFalse(receipt['synthesis'])
        self.assertGreater(len(receipt['checks']), 100)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--contract':
        print(json.dumps(invocation_contract(Path(sys.argv[2])), ensure_ascii=False))
    else:
        unittest.main()
