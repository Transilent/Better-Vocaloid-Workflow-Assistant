"""Contracts for the standard-profile start/run/delivery tools.

The static checks run in the normal test process.  End-to-end model and tool
registration checks run in the optional dedicated MCP SDK subprocess so the
business runtime does not acquire SDK dependencies.
"""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


APP = Path(__file__).resolve().parents[1]
SOURCE = APP / 'src' / 'agent_mcp_standard_flow.py'
SDK_PYTHON = os.environ.get('VWA_MCP_SDK_PYTHON')
EXPECTED_ACTIONS = {
    'vwa_start': {'workflow_create', 'preparation_plan', 'preparation_create'},
    'vwa_run': {
        'workflow_advance', 'preparation_start', 'tuning_start',
        'exports_plan', 'exports_start', 'artifacts_inspect',
        'reference_audio_read',
    },
    'vwa_delivery': {'workflow_delivery'},
}


def declared_actions(tree):
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
            values = [item.value for item in members
                      if isinstance(item, ast.Constant) and isinstance(item.value, str)]
            if len(values) == 1:
                classes[node.name] = values[0]
    tool_for_class = {
        'WorkflowCreate': 'vwa_start',
        'PreparationPlan': 'vwa_start',
        'PreparationCreate': 'vwa_start',
        'WorkflowAdvance': 'vwa_run',
        'PreparationStart': 'vwa_run',
        'TuningStart': 'vwa_run',
        'ExportsPlan': 'vwa_run',
        'ExportsStart': 'vwa_run',
        'ArtifactsInspect': 'vwa_run',
        'ReferenceAudioRead': 'vwa_run',
        'WorkflowDelivery': 'vwa_delivery',
    }
    found = {name: set() for name in EXPECTED_ACTIONS}
    for class_name, action in classes.items():
        if class_name in tool_for_class:
            found[tool_for_class[class_name]].add(action)
    return found


class StandardFlowStaticContract(unittest.TestCase):
    def test_register_signature_and_action_catalogue(self):
        text = SOURCE.read_text(encoding='utf-8')
        tree = ast.parse(text)
        register = next(node for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == 'register')
        self.assertEqual([item.arg for item in register.args.args],
                         ['mcp', 'control', 'workflow', 'invoke'])
        self.assertEqual(declared_actions(tree), EXPECTED_ACTIONS)
        self.assertEqual(text.count("Field(discriminator='action')"), 3)
        self.assertIn('MAX_WAIT_MS = 60_000', text)
        self.assertIn('review_readback_required', text)
        self.assertNotIn('payload:', text)
        for forbidden in ('subprocess', 'os.system', 'start_operation', 'call_worker', 'getattr('):
            self.assertNotIn(forbidden, text)
        for function in (node for node in ast.walk(tree)
                         if isinstance(node, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                if not isinstance(node, ast.Raise) or node.exc is None:
                    continue
                call = node.exc if isinstance(node.exc, ast.Call) else None
                self.assertFalse(
                    call is not None and isinstance(call.func, ast.Name)
                    and call.func.id == 'ControlError',
                    function.name + ':async_control_error_bypasses_invoke',
                )


def invocation_contract(source_directory):
    import asyncio
    import sys
    sys.path.insert(0, str(source_directory))

    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
    from agent_control import ControlError
    from agent_errors import error_payload
    from agent_mcp_models import Result
    from agent_mcp_standard_routing import standardize
    import agent_mcp_standard_flow as flow

    checks = []
    calls = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    def record(owner, callback, args, kwargs, result):
        calls.append({'owner': owner, 'callback': callback,
                      'args': list(args), 'kwargs': kwargs})
        return result

    class Control:
        def __init__(self, imported_id='art_midi'):
            self.imported_id = imported_id
            self.waits = []

        def import_asset(self, *args, **kwargs):
            return record('control', 'import_asset', args, kwargs,
                          {'id': self.imported_id, 'kind': 'input'})

        def preparation_plan(self, *args, **kwargs):
            return record('control', 'preparation_plan', args, kwargs,
                          {'id': 'plan_prep', 'kind': 'preparation'})

        def preparation_create(self, *args, **kwargs):
            return record('control', 'preparation_create', args, kwargs,
                          {'id': 'job_1'})

        def preparation_start(self, *args, **kwargs):
            return record('control', 'preparation_start', args, kwargs,
                          {'id': 'op_prepare', 'state': 'queued'})

        def start_plan(self, *args, **kwargs):
            return record('control', 'start_plan', args, kwargs,
                          {'id': 'op_' + str(args[1]), 'state': 'queued'})

        def make_plan(self, *args, **kwargs):
            return record('control', 'make_plan', args, kwargs,
                          {'id': 'plan_export', 'kind': 'export'})

        def inspect_artifact(self, *args, **kwargs):
            return record('control', 'inspect_artifact', args, kwargs,
                          {'id': 'op_verify', 'state': 'queued'})

        def reference_audio_read(self, *args, **kwargs):
            return record('control', 'reference_audio_read', args, kwargs,
                          {'id': 'op_excerpt', 'state': 'queued'})

        def operation_wait(self, operation_id, timeout_ms, *args, **kwargs):
            self.waits.append(timeout_ms)
            state = 'succeeded'
            return record('control', 'operation_wait', (operation_id, timeout_ms, *args),
                          kwargs, {'id': operation_id, 'state': state,
                                   'timed_out': False})

    class Workflow:
        def __init__(self):
            self.create_calls = []
            self.created_by_key = {}

        def create(self, *args, **kwargs):
            key = args[6]
            identity = (tuple(repr(value) for value in args[:6]),
                        repr(kwargs.get('initial_export_options') or {}))
            if key in self.created_by_key and self.created_by_key[key] != identity:
                from agent_control import ControlError
                raise ControlError('idempotency_conflict',
                                   '工作流重试键已用于不同请求。')
            self.created_by_key[key] = identity
            self.create_calls.append((args, kwargs))
            return record('workflow', 'create', args, kwargs, {
                'id': 'flow_1', 'revision': 0, 'stage': 'review',
                'request': {'brief': '', 'options': {}, 'formats': ['svp']},
                'score_artifact_id': args[4], 'history': [],
                'export_options': kwargs.get('initial_export_options') or {},
            })

        def advance(self, *args, **kwargs):
            return record('workflow', 'advance', args, kwargs, {
                'id': args[0], 'revision': args[1], 'stage': 'review',
                'request': {'brief': '', 'options': {}}, 'history': [],
            })

        def get(self, *args, **kwargs):
            return record('workflow', 'get', args, kwargs, {
                'id': args[0], 'revision': 1, 'stage': 'review',
                'request': {'brief': '', 'options': {}}, 'history': [],
                'review_receipt': {
                    'artifact_id': 'art_midi', 'source_sha256': 'a' * 64,
                    'original_artifact_id': 'art_midi', 'receipts': [],
                    'caller_reviewed': True,
                },
            })

        def delivery(self, *args, **kwargs):
            return record('workflow', 'delivery', args, kwargs,
                          {'manifest': 'manifest.json', 'complete': True})

    async def invoke(function, *args, **kwargs):
        try:
            value = function(*args, **kwargs)
            return Result(data=standardize(value))
        except (ControlError, ValueError, OSError, TypeError, KeyError) as error:
            raise ToolError(json.dumps(error_payload(error), ensure_ascii=False)) from error

    def make_server(control, workflow):
        server = MCPServer('standard-flow-contract', log_level='ERROR')
        flow.register(server, control, workflow, invoke)
        return server

    async def call(server, tool, request):
        result = await server.call_tool(tool, {'request': request})
        check(result.is_error is False, tool + ':not_error')
        return result.structured_content['data']

    async def rejected(server, tool, request, name):
        before = len(calls)
        try:
            result = await server.call_tool(tool, {'request': request})
        except ToolError:
            check(len(calls) == before, name + ':no_backend_call')
        else:
            check(result.is_error is True, name + ':error_result')
            check(len(calls) == before, name + ':no_backend_call')

    async def rejected_with_code(server, tool, request, code, name):
        try:
            result = await server.call_tool(tool, {'request': request})
        except UnexpectedToolError as error:
            raise AssertionError(name + ':unexpected_tool_error') from error
        except ToolError as error:
            check(code in str(error), name + ':stable_error_code')
            return error
        check(result.is_error is True, name + ':structured_error_result')
        rendered = json.dumps(result.model_dump(mode='json'), ensure_ascii=False)
        check(code in rendered, name + ':stable_error_code')
        return result

    def workflow_state(stage, revision, *, brief='', receipt=None, edited=False):
        value = {
            'id': 'flow_review', 'revision': revision, 'stage': stage,
            'request': {'brief': brief, 'options': {}, 'formats': ['svp']},
            'history': [],
        }
        if stage in ('preparing', 'reviewing', 'exporting', 'styling', 'refining'):
            value['operation'] = {
                'id': 'op_review' if stage == 'reviewing' else 'op_export',
                'state': 'running', 'progress': {'detail': 'working'},
            }
        if stage in ('reviewed', 'project_ready', 'ready'):
            value['review_receipt'] = receipt or {
                'artifact_id': 'art_changed' if edited else 'art_source',
                'source_sha256': 'b' * 64,
                'original_artifact_id': 'art_source',
                'receipts': ([{'kind': 'note'}] if edited else []),
                'caller_reviewed': True,
            }
        if stage == 'project_ready':
            value['export_summary'] = {
                'schema_version': 1, 'report_sha256': 'c' * 64,
                'can_accept': True, 'required_loss_codes': [],
                'candidates': [], 'blockers': [], 'warnings': [],
                'independent_audit': {'status': 'pass'},
            }
            value['export_review'] = None
        return value

    class ReviewFlow(Workflow):
        def __init__(self, edited=False, start_stage='reviewed'):
            super().__init__()
            self.edited = edited
            self.start_stage = start_stage
            self.advance_count = 0
            self.last_revision = 5

        def get(self, *args, **kwargs):
            if self.advance_count == 0:
                value = workflow_state(self.start_stage, self.last_revision,
                                       edited=self.edited)
            elif self.advance_count == 1:
                value = workflow_state('reviewing', self.last_revision)
            elif self.advance_count == 2:
                value = workflow_state('reviewed', self.last_revision + 1,
                                       edited=self.edited)
            elif self.advance_count == 3:
                value = workflow_state('exporting', self.last_revision + 2)
            else:
                value = workflow_state('project_ready', self.last_revision + 3)
            return record('workflow', 'get', args, kwargs, value)

        def advance(self, *args, **kwargs):
            self.advance_count += 1
            if self.advance_count == 1 and self.start_stage == 'reviewed':
                value = workflow_state('exporting', self.last_revision + 1)
            elif self.advance_count == 1:
                value = workflow_state('reviewing', self.last_revision)
            elif self.advance_count == 2:
                value = workflow_state('reviewed', self.last_revision + 1,
                                       edited=self.edited)
            elif self.advance_count == 3:
                value = workflow_state('exporting', self.last_revision + 2)
            else:
                value = workflow_state('project_ready', self.last_revision + 3)
            return record('workflow', 'advance', args, kwargs, value)

    class ConflictFlow(ReviewFlow):
        def advance(self, *args, **kwargs):
            raise ControlError('workflow_conflict', '工作流版本已改变，请重新读取当前状态。')

    class ReviewTransitionFlow(Workflow):
        """An advance that itself crosses reviewing -> reviewed."""

        def __init__(self, edited):
            super().__init__()
            self.edited = edited
            self.advance_count = 0

        def get(self, *args, **kwargs):
            if self.advance_count == 0:
                value = workflow_state('reviewing', 5)
                value['operation']['state'] = 'succeeded'
                value['next'] = {
                    'tool': 'vwa_workflow_advance',
                    'arguments': {'workflow_id': 'flow_review', 'expected_revision': 5},
                    'reason': 'consume_verified_operation_result',
                }
            else:
                value = workflow_state('reviewed', 6, edited=self.edited)
                value['next'] = {
                    'tool': 'vwa_workflow_advance',
                    'arguments': {'workflow_id': 'flow_review', 'expected_revision': 6},
                    'reason': 'reviewed',
                }
            return record('workflow', 'get', args, kwargs, value)

        def advance(self, *args, **kwargs):
            self.advance_count += 1
            value = workflow_state('reviewed', 6, edited=self.edited)
            value['next'] = {
                'tool': 'vwa_workflow_advance',
                'arguments': {'workflow_id': 'flow_review', 'expected_revision': 6},
                'reason': 'reviewed',
            }
            return record('workflow', 'advance', args, kwargs, value)

    class BrokenStartFlow(Workflow):
        def create(self, *args, **kwargs):
            return record('workflow', 'create', args, kwargs, {
                'revision': 0, 'stage': 'created', 'request': {'brief': ''},
            })

    class BrokenOperationFlow(Workflow):
        def get(self, *args, **kwargs):
            return record('workflow', 'get', args, kwargs,
                          workflow_state('created', 0))

        def advance(self, *args, **kwargs):
            value = workflow_state('exporting', 1)
            value['operation'].pop('id', None)
            return record('workflow', 'advance', args, kwargs, value)

    class DeliveryFlow(Workflow):
        """Exercise report acceptance and packaging as one tool call."""

        def __init__(self, *, brief='', accepted=False, conflict_on_advance=False,
                     delivery_failures=0):
            super().__init__()
            self.brief = brief
            self.revision = 7
            self.stage = 'ready' if accepted else 'project_ready'
            self.review = ({
                'report_sha256': 'c' * 64, 'decision': 'accept',
                'rationale': '已核对音符、歌词和损失', 'accepted_loss_codes': ['tick_rounding'],
            } if accepted else None)
            self.conflict_on_advance = conflict_on_advance
            self.delivery_failures = delivery_failures
            self.delivery_attempts = 0

        def state(self):
            value = workflow_state('project_ready', self.revision, brief=self.brief)
            value['stage'] = self.stage
            value['export_summary']['required_loss_codes'] = ['tick_rounding']
            value['export_review'] = copy.deepcopy(self.review)
            return value

        def get(self, *args, **kwargs):
            return record('workflow', 'get', args, kwargs, self.state())

        def advance(self, *args, **kwargs):
            if self.conflict_on_advance:
                raise ControlError('workflow_conflict', '工作流版本并发改变。')
            review = args[5]
            summary = self.state()['export_summary']
            if not isinstance(review, dict) or review.get('report_sha256') != summary['report_sha256']:
                raise ControlError('export_review_conflict', '导出报告版本不匹配。')
            missing = set(summary['required_loss_codes']) - set(review.get('accepted_loss_codes', []))
            if review.get('decision') == 'accept' and missing:
                raise ControlError('export_losses_unacknowledged', '导出损失尚未全部认可。')
            self.revision += 1
            self.review = {
                **copy.deepcopy(review),
                'reviewed_at': 100.0,
            }
            self.stage = 'review' if review.get('decision') == 'needs_revision' else (
                'project_ready' if self.brief else 'ready'
            )
            return record('workflow', 'advance', args, kwargs, self.state())

        def delivery(self, *args, **kwargs):
            self.delivery_attempts += 1
            review = self.review or {}
            summary = self.state()['export_summary']
            if (self.stage != 'ready' or review.get('decision') != 'accept'
                    or review.get('report_sha256') != summary.get('report_sha256')):
                raise ControlError('delivery_export_review_required', '当前报告尚未接受。')
            if self.delivery_attempts <= self.delivery_failures:
                raise ControlError('delivery_package_failed', '交付写入中断，可安全重试。')
            return record('workflow', 'delivery', args, kwargs,
                          {'manifest': 'manifest.json', 'complete': True})

    async def contract():
        control = Control()
        workflow = Workflow()
        server = make_server(control, workflow)
        tools = {tool.name: tool for tool in await server.list_tools()}
        check(set(tools) == set(EXPECTED_ACTIONS), 'exactly_three_tools')
        annotations = {
            name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
            for name, tool in tools.items()
        }
        check(annotations['vwa_start'] == {
            'title': 'Start or plan a VWA workflow', 'readOnlyHint': False,
            'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': True,
        }, 'start_annotation_conservative')
        check(annotations['vwa_run'] == {
            'title': 'Run deterministic workflow steps', 'readOnlyHint': False,
            'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': True,
        }, 'run_annotation_conservative')
        check(annotations['vwa_delivery'] == {
            'title': 'Package an accepted workflow', 'readOnlyHint': False,
            'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False,
        }, 'delivery_annotation_conservative')

        schema_actions = {}
        for name, tool in tools.items():
            schema = tool.input_schema
            check(set(schema.get('properties', {})) == {'request'}, name + ':request_only')
            request = schema['properties']['request']
            check(request['discriminator']['propertyName'] == 'action', name + ':discriminator')
            schema_actions[name] = set(request['discriminator']['mapping'])
        check(schema_actions == EXPECTED_ACTIONS, 'schema_actions_complete')
        run_defs = tools['vwa_run'].input_schema['$defs']
        wait_schema = run_defs['WorkflowAdvance']['properties']['wait_ms']
        check(wait_schema.get('minimum') == 0 and wait_schema.get('maximum') == 60000,
              'workflow_wait_bounded')
        check(run_defs['WorkflowAdvance']['properties']['run_until_gate'].get('default') is False,
              'run_until_gate_explicit')
        check('export_options' in run_defs['WorkflowAdvance']['properties'],
              'workflow_export_bindings_typed')
        check('export_review' in tools['vwa_delivery'].input_schema['$defs']['WorkflowDelivery']['properties'],
              'delivery_explicit_review_typed')
        check(run_defs['ArtifactsInspect']['properties']['verify'].get('default') is True,
              'inspect_write_default_true')
        check(run_defs['ReferenceAudioRead']['properties']['make_excerpt'].get('default') is True,
              'excerpt_write_default_true')

        # Ordinary typed dispatch retains original positional signatures.
        await call(server, 'vwa_start', {
            'action': 'preparation_plan', 'source': 'BV1xx411c7mD',
            'options': {'language': 'ja', 'bpm': 128.0},
        })
        await call(server, 'vwa_start', {
            'action': 'preparation_create', 'plan_id': 'plan_prep',
        })
        await call(server, 'vwa_run', {
            'action': 'preparation_start', 'job_id': 'job_1',
            'idempotency_key': 'prep-1', 'until': 'midi',
        })
        await call(server, 'vwa_run', {
            'action': 'tuning_start', 'plan_id': 'plan_style',
            'idempotency_key': 'style-1',
        })
        await call(server, 'vwa_run', {
            'action': 'exports_plan', 'artifact_id': 'art_svp',
            'formats': ['svp'], 'options': {'preserve_pitch': True},
        })
        await call(server, 'vwa_run', {
            'action': 'exports_start', 'plan_id': 'plan_export',
            'idempotency_key': 'export-1',
        })
        await call(server, 'vwa_run', {
            'action': 'artifacts_inspect', 'artifact_id': 'art_svp',
            'verify': True, 'wait_ms': 250,
        })
        await call(server, 'vwa_run', {
            'action': 'reference_audio_read', 'artifact_id': 'art_audio',
            'start': 1.0, 'end': 2.0, 'make_excerpt': True,
        })
        await call(server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_1',
            'expected_revision': 7, 'destination': 'C:/managed/delivery',
            'name': 'fixture-delivery',
        })
        callbacks = [row['callback'] for row in calls]
        check('preparation_plan' in callbacks and 'preparation_create' in callbacks,
              'start_actions_dispatched')
        check(callbacks.count('start_plan') == 2, 'two_plan_starts_dispatched')
        check('inspect_artifact' in callbacks and 'reference_audio_read' in callbacks,
              'conditional_write_actions_dispatched')
        export_plan_call = next(row for row in calls if row['callback'] == 'make_plan')
        check(export_plan_call['args'] == ['export', 'art_svp'], 'export_plan_args')
        check(export_plan_call['kwargs'] == {
            'formats': ['svp'], 'options': {'preserve_pitch': True},
        }, 'export_plan_options_preserved')

        # One local MIDI path produces one import and one create.
        before = len(calls)
        started = await call(server, 'vwa_start', {
            'action': 'workflow_create', 'midi_path': 'C:/managed/input.mid',
            'brief': '克制', 'formats': ['svp'], 'idempotency_key': 'midi-start-1',
            'review_mode': 'full',
        })
        check(started['stage'] == 'review', 'local_midi_enters_review')
        created = [row for row in calls[before:] if row['callback'] == 'create']
        imported = [row for row in calls[before:] if row['callback'] == 'import_asset']
        check(len(created) == 1 and len(imported) == 1, 'local_midi_single_import_create')
        check(created[0]['args'][4] == 'art_midi', 'local_midi_artifact_bound')
        check(created[0]['args'][6] == 'midi-start-1', 'local_midi_key_forwarded')
        await rejected(server, 'vwa_start', {
            'action': 'workflow_create', 'source': 'BV1different',
            'idempotency_key': 'midi-start-1',
        }, 'same_key_different_workflow_input_not_executed')

        before = len(calls)
        binding = {'pitch_source_job_id': 'job_pitch', 'include_instrumental': False}
        started_with_binding = await call(server, 'vwa_start', {
            'action': 'workflow_create', 'midi_path': 'C:/managed/input.mid',
            'export_options': binding,
            'idempotency_key': 'atomic-start-binding-required',
        })
        binding_create = [row for row in calls[before:] if row['callback'] == 'create']
        check(len(binding_create) == 1, 'start_binding_single_create')
        check(binding_create[0]['kwargs'].get('initial_export_options') == binding,
              'start_binding_forwarded_atomically')
        check(started_with_binding['export_options'] == binding,
              'start_binding_visible_in_result')
        await rejected(server, 'vwa_start', {
            'action': 'workflow_create', 'source': 'BV1xx411c7mD',
            'midi_path': 'C:/managed/input.mid', 'idempotency_key': 'bad-mix',
        }, 'mixed_local_and_source_rejected_before_import')
        await rejected(server, 'vwa_start', {
            'action': 'workflow_create', 'midi_path': 'C:/managed/input.svp',
            'idempotency_key': 'wrong-suffix',
        }, 'non_midi_path_rejected_before_import')

        # A directly requested advance from an edited reviewed revision cannot
        # start export.  The decision that generated the candidate is not a
        # readback acknowledgement.
        dirty = ReviewFlow(edited=True, start_stage='reviewed')
        dirty_server = make_server(Control(), dirty)
        guarded = await call(dirty_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5,
        })
        check(dirty.advance_count == 0, 'direct_advance_dirty_review_does_not_export')
        check(guarded['readback']['required'] is True, 'direct_advance_reports_readback')
        check(guarded['flow_control']['stopped_reason'] == 'review_readback_required',
              'direct_advance_stops_at_readback')
        check(guarded['next']['tool'] == 'vwa_evidence', 'direct_advance_next_evidence')
        check(guarded['next']['then']['arguments']['request']['action'] ==
              'workflow_apply_review', 'readback_then_is_copyable_request')
        check(guarded['view']['full_entry']['tool'] == 'vwa_status',
              'workflow_view_full_entry_is_standardized')

        # A direct advance can itself cross reviewing -> reviewed.  Its backend
        # next may still point at another advance, but an edited candidate must
        # override that next and the following call must stop before mutation.
        transition_dirty = ReviewTransitionFlow(edited=True)
        transition_server = make_server(Control(), transition_dirty)
        transition_result = await call(transition_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5,
        })
        check(transition_dirty.advance_count == 1,
              'review_transition_dirty_advance_once')
        check(transition_result['flow_control']['stopped_reason'] ==
              'review_readback_required',
              'review_transition_dirty_stops_for_readback')
        check(transition_result['readback']['required'] is True,
              'review_transition_dirty_readback_required')
        check(transition_result['next']['tool'] == 'vwa_evidence',
              'review_transition_dirty_next_overrides_backend')
        again = await call(transition_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 6,
        })
        check(transition_dirty.advance_count == 1,
              'review_transition_second_advance_guarded')
        check(again['next']['tool'] == 'vwa_evidence',
              'review_transition_second_advance_keeps_reread_next')

        transition_auto = ReviewTransitionFlow(edited=True)
        transition_auto_server = make_server(Control(), transition_auto)
        transition_auto_result = await call(transition_auto_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5, 'run_until_gate': True, 'wait_ms': 60000,
        })
        check(transition_auto.advance_count == 1,
              'review_transition_auto_does_not_export')
        check(transition_auto_result['next']['tool'] == 'vwa_evidence',
              'review_transition_auto_next_is_readback')

        transition_clean = ReviewTransitionFlow(edited=False)
        transition_clean_server = make_server(Control(), transition_clean)
        transition_clean_result = await call(transition_clean_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5,
        })
        check(transition_clean.advance_count == 1,
              'review_transition_clean_advance_once')
        check(transition_clean_result['next']['tool'] == 'vwa_run',
              'review_transition_clean_keeps_advance_next')

        clean = ReviewFlow(edited=False, start_stage='reviewed')
        clean_server = make_server(Control(), clean)
        clean_result = await call(clean_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5,
        })
        check(clean.advance_count == 1, 'direct_advance_clean_review_starts_export')
        check(clean_result['stage'] == 'exporting', 'clean_review_export_started')

        stale = ConflictFlow(edited=False, start_stage='reviewed')
        stale_server = make_server(Control(), stale)
        try:
            stale_result = await stale_server.call_tool('vwa_run', {'request': {
                'action': 'workflow_advance', 'workflow_id': 'flow_review',
                'expected_revision': 4,
            }})
        except ToolError:
            pass
        else:
            check(stale_result.is_error is True, 'stale_revision_error_result')
        check(stale.advance_count == 0, 'stale_revision_not_executed')

        # run_until_gate consumes a clean explicit review and automatically
        # starts export, then stops at its report.  An edited review is consumed
        # but not exported.
        auto_clean = ReviewFlow(edited=False, start_stage='reviewing')
        auto_control = Control()
        auto_server = make_server(auto_control, auto_clean)
        auto_result = await call(auto_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5, 'run_until_gate': True, 'wait_ms': 60000,
        })
        check(auto_clean.advance_count == 4, 'clean_review_auto_export_consumed')
        check(auto_result['stage'] == 'project_ready', 'auto_stops_at_report')
        check(auto_result['flow_control']['stopped_reason'] == 'export_review_required',
              'auto_report_boundary')
        check(auto_result['next']['tool'] == 'vwa_run', 'auto_report_next_run')
        check(len(auto_control.waits) == 2 and all(0 < value <= 60000 for value in auto_control.waits),
              'auto_waits_bounded')

        auto_edited = ReviewFlow(edited=True, start_stage='reviewing')
        edited_control = Control()
        edited_server = make_server(edited_control, auto_edited)
        edited_result = await call(edited_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5, 'run_until_gate': True, 'wait_ms': 60000,
        })
        check(auto_edited.advance_count == 2, 'edited_review_consumed_without_export')
        check(edited_result['stage'] == 'reviewed', 'edited_review_stops_reviewed')
        check(edited_result['readback']['required'] is True, 'edited_review_readback_flag')
        check(edited_result['next']['tool'] == 'vwa_evidence', 'edited_review_next_evidence')

        # Export review/style/F0/accompaniment fields are passed through without
        # normalization or loss.  The fake backend deliberately returns review
        # so no decision is consumed by this routing test.
        decision_control = Control()
        decision_workflow = ReviewFlow(edited=False, start_stage='review')
        decision_server = make_server(decision_control, decision_workflow)
        style = {
            'track_indices': [0], 'brief': '克制',
            'regions': [{'start': 0.0, 'end': 0.25, 'preset': 'gentle',
                         'strength': 0.4, 'rationale': '由调用方明确选择'}],
        }
        export_review = {
            'report_sha256': 'd' * 64, 'decision': 'accept',
            'rationale': '已核对报告', 'accepted_loss_codes': ['tick_rounding'],
        }
        bindings = {
            'preserve_pitch': True,
            'pitch_source_job_id': 'job_pitch',
            'instrumental_artifact_id': 'art_inst',
            'pitch_bindings': [{'pitch_artifact_id': 'art_pitch',
                                'vocal_track_index': 0,
                                'time_origin_seconds': 1.25}],
        }
        before = len(calls)
        await call(decision_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 5, 'style_proposal': style,
            'export_review': export_review, 'finish': False,
            'style_plan_id': None, 'export_options': bindings,
        })
        advance_call = next(row for row in calls[before:] if row['callback'] == 'advance')
        preserved_style = advance_call['args'][2]
        check(preserved_style['track_indices'] == [0] and
              preserved_style['brief'] == '克制' and
              preserved_style['regions'][0]['preset'] == 'gentle' and
              preserved_style['regions'][0]['strength'] == 0.4,
              'style_decision_preserved')
        check(advance_call['args'][5] == export_review, 'export_review_preserved')
        check(advance_call['args'][7] == bindings, 'f0_and_instrumental_bindings_preserved')

        delivery_flow = DeliveryFlow()
        delivery_server = make_server(Control(), delivery_flow)
        accepted = {
            'report_sha256': 'c' * 64, 'decision': 'accept',
            'rationale': '已核对音符、歌词和损失', 'accepted_loss_codes': ['tick_rounding'],
        }
        before = len(calls)
        delivered = await call(delivery_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': accepted,
            'destination': 'C:/managed/delivery', 'name': 'accepted-delivery',
        })
        callbacks = [row['callback'] for row in calls[before:]]
        check(callbacks == ['get', 'advance', 'delivery'],
              'delivery_accept_then_package_order')
        accepted_advance = next(row for row in calls[before:]
                                if row['callback'] == 'advance')
        accepted_delivery = next(row for row in calls[before:]
                                 if row['callback'] == 'delivery')
        check(accepted_advance['args'][5] == accepted,
              'delivery_report_sha_and_losses_preserved')
        check(accepted_delivery['args'][1] == 8,
              'delivery_uses_post_acceptance_revision')
        check(delivered['acceptance']['decision'] == 'accept' and
              delivered['acceptance']['report_sha256'] == 'c' * 64,
              'delivery_acceptance_receipt')
        check(delivered['complete'] is True, 'delivery_package_complete')

        rejection_flow = DeliveryFlow()
        rejection_server = make_server(Control(), rejection_flow)
        before = len(calls)
        rejected_export = await call(rejection_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': {
                'report_sha256': 'c' * 64, 'decision': 'needs_revision',
                'rationale': '歌词核对不合格', 'accepted_loss_codes': [],
            },
        })
        callbacks = [row['callback'] for row in calls[before:]]
        check(callbacks == ['get', 'advance'], 'delivery_revision_skips_package')
        check(rejected_export['delivery']['generated'] is False,
              'delivery_revision_not_packaged')
        rejected_advance = next(row for row in calls[before:]
                                if row['callback'] == 'advance')
        check(rejected_advance['args'][5]['decision'] == 'needs_revision',
              'delivery_revision_explicit')
        rejection_review = {
            'report_sha256': 'c' * 64, 'decision': 'needs_revision',
            'rationale': '歌词核对不合格', 'accepted_loss_codes': [],
        }
        before = len(calls)
        rejected_retry = await call(rejection_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 8, 'export_review': rejection_review,
        })
        callbacks = [row['callback'] for row in calls[before:]]
        check(callbacks == ['get'], 'delivery_rejection_retry_skips_duplicate_advance')
        check(rejected_retry['flow_control']['steps'] == [],
              'delivery_rejection_retry_reports_no_advance')
        check(rejected_retry['delivery']['generated'] is False,
              'delivery_rejection_retry_not_packaged')

        # An accepted report is idempotent only for the exact same decision.
        # Changed losses or rationale must be rejected instead of ignored.
        accepted_retry_flow = DeliveryFlow(accepted=True)
        accepted_retry_server = make_server(Control(), accepted_retry_flow)
        before = len(calls)
        accepted_retry = await call(accepted_retry_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': accepted,
            'destination': 'C:/managed/delivery', 'name': 'accepted-retry',
        })
        callbacks = [row['callback'] for row in calls[before:]]
        check(callbacks == ['get', 'delivery'],
              'accepted_exact_retry_skips_duplicate_acceptance')
        check(accepted_retry['complete'] is True,
              'accepted_exact_retry_packages')

        changed_loss_flow = DeliveryFlow(accepted=True)
        changed_loss_server = make_server(Control(), changed_loss_flow)
        before = len(calls)
        await rejected_with_code(changed_loss_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': {
                **accepted, 'accepted_loss_codes': ['tick_rounding', 'new_loss'],
            },
        }, 'export_review_conflict', 'accepted_changed_losses_conflict')
        check([row['callback'] for row in calls[before:]] == ['get'],
              'accepted_changed_losses_do_not_deliver')

        changed_reason_flow = DeliveryFlow(accepted=True)
        changed_reason_server = make_server(Control(), changed_reason_flow)
        before = len(calls)
        await rejected_with_code(changed_reason_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': {
                **accepted, 'rationale': '静默替换的新理由',
            },
        }, 'export_review_conflict', 'accepted_changed_rationale_conflict')
        check([row['callback'] for row in calls[before:]] == ['get'],
              'accepted_changed_rationale_do_not_deliver')

        stale_delivery_flow = DeliveryFlow()
        stale_delivery_server = make_server(Control(), stale_delivery_flow)
        before = len(calls)
        await rejected_with_code(stale_delivery_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 6, 'export_review': accepted,
        }, 'workflow_conflict', 'delivery_stale_revision_structured')
        check([row['callback'] for row in calls[before:]] == ['get'],
              'delivery_stale_revision_no_mutation')

        loss_flow = DeliveryFlow()
        loss_server = make_server(Control(), loss_flow)
        before = len(calls)
        await rejected_with_code(loss_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': {
                **accepted, 'accepted_loss_codes': [],
            },
        }, 'export_losses_unacknowledged', 'delivery_missing_loss_rejected')
        check(not any(row['callback'] == 'delivery' for row in calls[before:]),
              'delivery_missing_loss_not_packaged')

        sha_flow = DeliveryFlow()
        sha_server = make_server(Control(), sha_flow)
        before = len(calls)
        await rejected_with_code(sha_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': {
                **accepted, 'report_sha256': 'd' * 64,
            },
        }, 'export_review_conflict', 'delivery_different_sha_rejected')
        check(not any(row['callback'] == 'delivery' for row in calls[before:]),
              'delivery_different_sha_not_packaged')

        race_flow = DeliveryFlow(conflict_on_advance=True)
        race_server = make_server(Control(), race_flow)
        before = len(calls)
        await rejected_with_code(race_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': accepted,
        }, 'workflow_conflict', 'delivery_race_cas_rejected')
        check(not any(row['callback'] == 'delivery' for row in calls[before:]),
              'delivery_race_not_packaged')

        half_flow = DeliveryFlow(delivery_failures=1)
        half_server = make_server(Control(), half_flow)
        await rejected_with_code(half_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': accepted,
            'destination': 'C:/managed/delivery', 'name': 'half-retry',
        }, 'delivery_package_failed', 'delivery_half_success_structured')
        check(half_flow.stage == 'ready' and half_flow.revision == 8,
              'delivery_acceptance_survives_package_failure')
        before = len(calls)
        half_retry = await call(half_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 8, 'export_review': accepted,
            'destination': 'C:/managed/delivery', 'name': 'half-retry',
        })
        callbacks = [row['callback'] for row in calls[before:]]
        check(callbacks == ['get', 'delivery'],
              'delivery_half_success_retry_skips_duplicate_acceptance')
        check(half_retry['complete'] is True,
              'delivery_half_success_retry_completes')

        style_flow = DeliveryFlow(brief='需要先审核风格')
        style_server = make_server(Control(), style_flow)
        before = len(calls)
        style_result = await call(style_server, 'vwa_delivery', {
            'action': 'workflow_delivery', 'workflow_id': 'flow_review',
            'expected_revision': 7, 'export_review': accepted,
        })
        check(style_result['stage'] == 'project_ready',
              'delivery_style_decision_pause_stage')
        check(style_result['delivery']['generated'] is False,
              'delivery_style_decision_pause_no_package')
        check(style_result['flow_control']['stopped_reason'] ==
              'delivery_workflow_not_ready',
              'delivery_style_decision_pause_reason')
        check(not any(row['callback'] == 'delivery' for row in calls[before:]),
              'delivery_style_decision_pause_not_packaged')

        broken_start_server = make_server(Control(), BrokenStartFlow())
        await rejected_with_code(broken_start_server, 'vwa_start', {
            'action': 'workflow_create', 'midi_path': 'C:/managed/broken.mid',
            'run_until_gate': True,
        }, 'invalid_workflow_result', 'drive_invalid_workflow_result_structured')

        broken_operation_server = make_server(Control(), BrokenOperationFlow())
        await rejected_with_code(broken_operation_server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_review',
            'expected_revision': 0, 'run_until_gate': True,
        }, 'missing_operation_id', 'drive_missing_operation_structured')

        await rejected(server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_1',
            'expected_revision': 1, 'run_until_gate': True, 'wait_ms': 60001,
        }, 'wait_over_60_seconds_rejected')
        await rejected(server, 'vwa_run', {
            'action': 'workflow_advance', 'workflow_id': 'flow_1',
            'expected_revision': True,
        }, 'boolean_revision_rejected')
        await rejected(server, 'vwa_start', {
            'action': 'preparation_plan', 'source': 'BV1', 'payload': {},
        }, 'extra_field_rejected')

        return {
            'checks': checks,
            'actions': {name: sorted(values) for name, values in sorted(schema_actions.items())},
            'call_count': len(calls),
            'model_inference': False,
            'network_requested': False,
            'synthesis': False,
        }

    return asyncio.run(contract())


class StandardFlowSDKContract(unittest.TestCase):
    @unittest.skipUnless(SDK_PYTHON, 'Dedicated optional SDK not configured')
    def test_register_and_dispatch_via_independent_sdk_subprocess(self):
        result = subprocess.run(
            [SDK_PYTHON, '-B', str(Path(__file__).resolve()),
             '--contract', str(SOURCE.parent)],
            cwd=APP,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=90,
            env=dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1'),
        )
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-12000:])
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['actions'], {
            name: sorted(values) for name, values in sorted(EXPECTED_ACTIONS.items())
        })
        self.assertFalse(receipt['model_inference'])
        self.assertFalse(receipt['network_requested'])
        self.assertFalse(receipt['synthesis'])
        self.assertGreaterEqual(len(receipt['checks']), 65)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--contract':
        print(json.dumps(invocation_contract(Path(sys.argv[2])), ensure_ascii=False))
    else:
        unittest.main()
