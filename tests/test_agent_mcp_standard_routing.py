"""Contracts for the 12-tool catalogue and continuation routing."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

from agent_control import TOOLS
from agent_errors import REMEDIATION
from agent_mcp_standard_routing import (
    INTERFACE_FILES,
    STANDARD_TOOLS,
    _ACTION_GROUPS,
    _ACTION_TO_TOOL,
    _FLAT_ACTION_ROUTES,
    interface_identity,
    standardize,
)


FLAT_HISTORICAL = frozenset((
    'vwa_operations_wait', 'vwa_operations_cancel', 'vwa_lyric_timeline_search',
))


class StandardRoutingTest(unittest.TestCase):
    def test_repair_operation_result_routes_only_its_typed_next(self):
        step = {'tool': 'vwa_preparation_start',
                'arguments': {'job_id': 'job_1', 'until': 'midi', 'idempotency_key': 'key'}}
        value = {'id': 'op_1', 'state': 'succeeded',
                 'request': {'action': 'separation_repair'},
                 'result': {'handoff': 'ready_for_midi', 'next': step,
                            'receipt': {'kind': 'separation_repair', 'handoff': 'ready_for_midi',
                                        'next': step}, 'lyrics': {'next': step}}}
        routed = standardize(value)
        self.assertEqual(routed['result']['next']['tool'], 'vwa_run')
        self.assertEqual(routed['result']['next']['arguments']['request'],
                         {'action': 'preparation_start', **step['arguments']})
        self.assertEqual(routed['result']['receipt']['next']['tool'], 'vwa_run')
        self.assertEqual(routed['result']['lyrics']['next'], step)
        unrelated = copy.deepcopy(value)
        unrelated['request']['action'] = 'other_operation'
        self.assertEqual(standardize(unrelated)['result']['next'], step)

    def test_catalog_and_action_groups_are_complete(self):
        self.assertEqual(TOOLS, STANDARD_TOOLS)
        self.assertEqual(len(STANDARD_TOOLS), 12)
        self.assertEqual(len(set(STANDARD_TOOLS)), 12)
        self.assertEqual(set(_ACTION_GROUPS), set(STANDARD_TOOLS) - {'vwa_wait', 'vwa_cancel', 'vwa_search'})
        self.assertEqual(len(_ACTION_TO_TOOL), 60)
        self.assertEqual(_ACTION_TO_TOOL['pitch_offset_review'], 'vwa_evidence')
        self.assertEqual(set(_ACTION_TO_TOOL.values()), set(_ACTION_GROUPS))
        self.assertEqual(set(_FLAT_ACTION_ROUTES.values()), {'vwa_wait', 'vwa_cancel', 'vwa_search'})

    def test_every_current_action_routes_to_its_grouped_tool(self):
        for action, target in _ACTION_TO_TOOL.items():
            with self.subTest(action=action):
                name = 'vwa_' + action
                arguments = {'workflow_id': 'flow_1', 'artifact_id': 'art_1'}
                source = {'next': {'tool': name, 'arguments': copy.deepcopy(arguments),
                                   'reason': 'test'}}
                step = standardize(source)['next']
                self.assertEqual(step['tool'], target)
                self.assertIn(step['tool'], STANDARD_TOOLS)
                request = step['arguments']['request']
                self.assertEqual(request['action'], action)
                for key, value in arguments.items():
                    self.assertEqual(request[key], value)
                self.assertEqual(source['next']['tool'], name)

        arguments_by_action = {
            'operations_wait': {'operation_id': 'op_1', 'timeout_ms': 25},
            'operations_cancel': {'operation_id': 'op_1'},
            'lyric_timeline_search': {'query': 'song', 'limit': 5, 'offset': 0},
        }
        for action, target in _FLAT_ACTION_ROUTES.items():
            with self.subTest(action=action):
                arguments = arguments_by_action[action]
                step = standardize({'next': {
                    'tool': 'vwa_' + action,
                    'arguments': copy.deepcopy(arguments),
                }})['next']
                self.assertEqual(step, {'tool': target, 'arguments': arguments})

    def test_conditional_write_and_flat_wait_routes(self):
        cases = [
            ('vwa_artifacts_inspect', {'artifact_id': 'art_1', 'verify': False}, 'vwa_status'),
            ('vwa_artifacts_inspect', {'artifact_id': 'art_1', 'verify': True}, 'vwa_run'),
            ('vwa_reference_audio_read', {'artifact_id': 'art_1', 'make_excerpt': False}, 'vwa_evidence'),
            ('vwa_reference_audio_read', {'artifact_id': 'art_1', 'start': 0, 'end': 1,
                                           'make_excerpt': True}, 'vwa_run'),
        ]
        for name, arguments, expected in cases:
            with self.subTest(action=name, arguments=arguments):
                step = standardize({'next': {'tool': name,
                                             'arguments': copy.deepcopy(arguments)}})['next']
                self.assertEqual(step['tool'], expected)
                self.assertEqual(step['arguments']['request']['action'],
                                 name.removeprefix('vwa_'))
                for key, value in arguments.items():
                    self.assertEqual(step['arguments']['request'][key], value)

        required = standardize({'next': {
            'tool': 'vwa_reference_audio_read',
            'required_arguments': {'artifact_id': 'art_1', 'start': 0, 'end': 1,
                                   'make_excerpt': True},
        }})['next']
        self.assertEqual(required['tool'], 'vwa_run')
        self.assertEqual(required['required_arguments']['request']['action'],
                         'reference_audio_read')

        wait = standardize({'next': {'tool': 'vwa_operations_wait',
                                     'arguments': {'operation_id': 'op_1',
                                                   'timeout_ms': 1000}}})['next']
        self.assertEqual(wait, {'tool': 'vwa_wait',
                                'arguments': {'operation_id': 'op_1',
                                              'timeout_ms': 1000}})

    def test_every_documented_continuation_envelope_is_translated(self):
        source = {
            'next': {
                'tool': 'vwa_workflow_advance',
                'arguments': {'workflow_id': 'flow_1', 'expected_revision': 3},
                'alternatives': [{
                    'tool': 'vwa_workflow_rebind_score',
                    'required_arguments': {'workflow_id': 'flow_1',
                                           'expected_revision': 3,
                                           'score_artifact_id': 'art_2',
                                           'reason': 'corrected'},
                }],
                'pronunciation_reads': [{
                    'tool': 'vwa_pronunciation_read',
                    'arguments': {'artifact_id': 'art_svp'},
                }],
                'after_wait': {
                    'tool': 'vwa_operations_wait',
                    'arguments': {'operation_id': 'op_1', 'timeout_ms': 50},
                },
                'then': {
                    'tool': 'vwa_workflow_style_review',
                    'arguments': {'workflow_id': 'flow_1'},
                },
            },
            'operation': {'details_tool': {
                'tool': 'vwa_operations_get', 'arguments': {'operation_id': 'op_1'},
            }},
            'available_references': [{'import_tool': {
                'tool': 'vwa_reference_import',
                'arguments': {'path': 'C:/managed/voice.wav', 'kind': 'audio'},
            }}],
            'reference_evidence': {'audio_excerpt_next': {
                'tool': 'vwa_reference_audio_read',
                'arguments': {'artifact_id': 'art_audio', 'start': 0, 'end': 3,
                              'make_excerpt': True},
            }},
            'view': {'full_entry': {
                'tool': 'vwa_workflow_get',
                'arguments': {'workflow_id': 'flow_1', 'include_details': True},
            }},
        }
        before = copy.deepcopy(source)
        result = standardize(source)
        self.assertEqual(source, before)

        def action(step, field='arguments'):
            return step[field]['request']['action']

        self.assertEqual(result['next']['tool'], 'vwa_run')
        self.assertEqual(action(result['next']), 'workflow_advance')
        alternative = result['next']['alternatives'][0]
        self.assertEqual(alternative['tool'], 'vwa_manage')
        self.assertEqual(action(alternative, 'required_arguments'),
                         'workflow_rebind_score')
        read = result['next']['pronunciation_reads'][0]
        self.assertEqual(read['tool'], 'vwa_evidence')
        self.assertEqual(action(read), 'pronunciation_read')
        self.assertEqual(result['next']['after_wait']['tool'], 'vwa_wait')
        self.assertEqual(result['next']['then']['tool'], 'vwa_evidence')
        self.assertEqual(action(result['next']['then']), 'workflow_style_review')
        self.assertEqual(result['operation']['details_tool']['tool'], 'vwa_status')
        self.assertEqual(action(result['operation']['details_tool']), 'operations_get')
        imported = result['available_references'][0]['import_tool']
        self.assertEqual(imported['tool'], 'vwa_input')
        self.assertEqual(action(imported), 'reference_import')
        excerpt = result['reference_evidence']['audio_excerpt_next']
        self.assertEqual(excerpt['tool'], 'vwa_run')
        self.assertEqual(action(excerpt), 'reference_audio_read')
        full = result['view']['full_entry']
        self.assertEqual(full['tool'], 'vwa_status')
        self.assertEqual(action(full), 'workflow_get')

    def test_user_content_and_nested_arguments_are_opaque(self):
        instruction_like = {
            'next': {'tool': 'vwa_workflow_advance', 'arguments': {'workflow_id': 'fake'}},
            'details_tool': {'tool': 'vwa_operations_get', 'arguments': {}},
            'remediation': ['vwa_score_review', {'tool': 'vwa_operations_wait'}],
            'server': 'VWA Agent MCP', 'tools': list(TOOLS),
        }
        protected_keys = (
            'notes', 'lyrics', 'request', 'plan', 'metadata', 'raw', 'raw_evidence',
            'details', 'evidence', 'payload', 'brief', 'rationale',
            'after_wait', 'then', 'steps',
        )
        source = {key: copy.deepcopy(instruction_like) for key in protected_keys}
        source['next'] = {'tool': 'vwa_workflow_review',
                          'arguments': {'workflow_id': 'flow_1'}}
        before = copy.deepcopy(source)
        result = standardize(source)
        self.assertEqual(source, before)
        for key in protected_keys:
            self.assertEqual(result[key], before[key])
        self.assertEqual(result['next']['tool'], 'vwa_evidence')

        step = standardize({'next': {
            'tool': 'vwa_workflow_advance',
            'arguments': {
                'workflow_id': 'flow_1', 'expected_revision': 2,
                'action': 'must-not-survive-in-the-wrong-position',
                'export_options': {'next': instruction_like['next'],
                                   'remediation': instruction_like['remediation']},
            },
        }})['next']
        request = step['arguments']['request']
        self.assertEqual(request['action'], 'workflow_advance')
        self.assertEqual(request['export_options']['next'], instruction_like['next'])
        self.assertEqual(request['export_options']['remediation'],
                         instruction_like['remediation'])

    def test_error_remediation_routes_and_preserves_diagnostics(self):
        details = {'next': {'tool': 'vwa_workflow_advance', 'arguments': {}},
                   'note': '用户内容 vwa_run 不是指令'}
        source = {
            'code': 'workflow_conflict',
            'message': '调用 vwa_run 前先读取 revision；vwa_operations_get 是正文。',
            'retryable': True,
            'details': details,
            'remediation': [
                'vwa_workflow_rebind_score',
                '检查现场后重试。',
                {'tool': 'vwa_operations_wait',
                 'arguments': {'operation_id': 'op_1', 'timeout_ms': 20}},
            ],
        }
        result = standardize(source)
        self.assertEqual(result['message'], source['message'])
        self.assertEqual(result['details'], details)
        self.assertEqual(result['remediation'][0]['tool'], 'vwa_manage')
        self.assertEqual(result['remediation'][0]['arguments']['request']['action'],
                         'workflow_rebind_score')
        self.assertEqual(result['remediation'][1], '检查现场后重试。')
        self.assertEqual(result['remediation'][2], {
            'tool': 'vwa_wait', 'arguments': {'operation_id': 'op_1', 'timeout_ms': 20},
        })
        nested = standardize({'error': source})['error']
        self.assertEqual(nested['remediation'], result['remediation'])
        self.assertEqual(nested['details'], details)

        for code, names in REMEDIATION.items():
            for name in names:
                with self.subTest(code=code, tool=name):
                    routed = standardize({'remediation': [name]})['remediation'][0]
                    self.assertEqual(routed['tool'], _ACTION_TO_TOOL[name.removeprefix('vwa_')])
                    if name not in FLAT_HISTORICAL:
                        self.assertEqual(routed['arguments']['request']['action'],
                                         name.removeprefix('vwa_'))

    def test_error_suggestion_tools_route_without_rewriting_user_content(self):
        source = {
            'code': 'rawstem_missing',
            'message': 'vwa_operations_get 只是错误说明文本。',
            'remediation': ['vwa_preparation_plan'],
            'details': {
                'role': 'lead',
                'action_suggestions': [
                    {'action': 'rerun_separation',
                     'instruction': '先检查 vwa_preparation_start 的输入。'},
                    {'action': 'new_single_job', 'tool': 'vwa_preparation_plan',
                     'arguments': {'source': 'song.mp3'},
                     'instruction': '保留这段 vwa_lyrics_read 文本。'},
                ],
                'lyrics': [
                    {'text': 'vwa_workflow_review', 'tool': 'vwa_lyrics_read'},
                ],
            },
            'suggestions': [
                {'code': 'regenerate_plan', 'tool': 'vwa_preparation_repair_plan',
                 'arguments': {'source_job_id': 'job_1'},
                 'message': '调用 vwa_operations_wait 的说明不变。'},
                {'code': 'inspect', 'tool': 'vwa_operations_get',
                 'arguments': {'operation_id': 'op_1'}},
                {'code': 'free_text', 'message': 'vwa_workflow_advance 不是工具字段。'},
            ],
        }
        before = copy.deepcopy(source)
        result = standardize(source)
        self.assertEqual(source, before)

        first, second = result['details']['action_suggestions']
        self.assertEqual(first, before['details']['action_suggestions'][0])
        self.assertEqual(second['tool'], 'vwa_start')
        self.assertEqual(second['arguments']['request'], {
            'action': 'preparation_plan', 'source': 'song.mp3',
        })
        self.assertEqual(second['instruction'],
                         before['details']['action_suggestions'][1]['instruction'])
        self.assertEqual(result['details']['lyrics'], before['details']['lyrics'])

        regenerate, inspect, free_text = result['suggestions']
        self.assertEqual(regenerate['tool'], 'vwa_recover')
        self.assertEqual(regenerate['arguments']['request'], {
            'action': 'preparation_repair_plan', 'source_job_id': 'job_1',
        })
        self.assertEqual(regenerate['message'], before['suggestions'][0]['message'])
        self.assertEqual(inspect['tool'], 'vwa_status')
        self.assertEqual(inspect['arguments']['request'], {
            'action': 'operations_get', 'operation_id': 'op_1',
        })
        self.assertEqual(free_text, before['suggestions'][2])
        self.assertEqual(result['message'], before['message'])
        self.assertEqual(standardize(result), result)

        nested = standardize({'error': source})['error']
        self.assertEqual(nested['details']['action_suggestions'][1]['tool'], 'vwa_start')
        self.assertEqual(nested['suggestions'][0]['tool'], 'vwa_recover')
        self.assertEqual(nested['details']['lyrics'], before['details']['lyrics'])

    def test_capabilities_report_only_current_tools_and_are_idempotent(self):
        source = {
            'schema': 1,
            'server': 'VWA Agent MCP',
            'tools': ['vwa_system_capabilities', 'vwa_workflow_create'],
            'workflow_entry': 'vwa_workflow_create',
            'interface_profile': 'legacy',
            'legacy_tool_count': 61,
            'code_revision': 'business-revision',
            'interface_revision': 'interface-revision',
            'source_commit': 'source-commit',
        }
        result = standardize(source)
        self.assertEqual(result['tools'], list(STANDARD_TOOLS))
        self.assertNotIn('legacy_tool_count', result)
        self.assertEqual(result['workflow_entry'], 'vwa_start')
        self.assertEqual(result['interface_profile'], 'standard')
        self.assertEqual(result['code_revision'], 'business-revision')
        self.assertEqual(result['interface_revision'], 'interface-revision')
        self.assertEqual(result['source_commit'], 'source-commit')
        self.assertEqual(source['tools'], ['vwa_system_capabilities', 'vwa_workflow_create'])
        self.assertEqual(standardize(result), result)

    def test_interface_identity_tracks_interface_files_only(self):
        hashes = {name: 'first:' + name for name in INTERFACE_FILES}

        def fake_hash(path):
            return hashes[Path(path).name]

        root = Path('managed-app')
        with patch('agent_control.file_hash', side_effect=fake_hash), patch(
                'agent_control.digest', side_effect=repr):
            first = interface_identity(root)
            hashes['agent_mcp.py'] = 'second:agent_mcp.py'
            second = interface_identity(root)
            self.assertNotEqual(first, second)
            hashes['business_only.py'] = 'business change'
            self.assertEqual(interface_identity(root), second)

    def test_decision_and_evidence_shortcuts_reference_new_tools(self):
        result = standardize({'decision_tool': 'vwa_workflow_apply_review',
                              'full_evidence_tools': ['vwa_score_read',
                                                      'vwa_lyrics_review',
                                                      'future_tool']})
        self.assertEqual(result['decision_tool'], 'vwa_decide')
        self.assertEqual(result['decision_action'], 'workflow_apply_review')
        self.assertEqual(result['full_evidence_tools'], ['vwa_evidence', 'future_tool'])
        self.assertEqual(len(result['full_evidence_actions']), 2)
        self.assertTrue(all(row['tool'] == 'vwa_evidence'
                            for row in result['full_evidence_actions']))

    def test_reviewed_summary_reads_full_receipt_before_advancing(self):
        source = {
            'id': 'flow_1',
            'stage': 'reviewed',
            'revision': 4,
            'review_receipt': {
                'artifact_id': 'art_edited',
                'source_sha256': 'a' * 64,
                'caller_reviewed': True,
                'details_available': True,
            },
            'next': {
                'tool': 'vwa_workflow_advance',
                'arguments': {'workflow_id': 'flow_1', 'expected_revision': 4},
                'reason': 'reviewed',
            },
        }
        result = standardize(source)
        self.assertEqual(result['next'], {
            'tool': 'vwa_status',
            'arguments': {'request': {
                'action': 'workflow_get',
                'workflow_id': 'flow_1',
                'include_details': True,
            }},
            'reason': 'read_full_review_receipt_before_advance',
        })
        self.assertEqual(standardize(result), result)

    def test_reviewed_full_receipt_routes_modified_and_clean_reviews(self):
        base = {
            'id': 'flow_1',
            'stage': 'reviewed',
            'revision': 4,
            'next': {
                'tool': 'vwa_workflow_advance',
                'arguments': {'workflow_id': 'flow_1', 'expected_revision': 4},
            },
        }
        modified = copy.deepcopy(base)
        modified['review_receipt'] = {
            'artifact_id': 'art_edited',
            'original_artifact_id': 'art_original',
            'source_sha256': 'a' * 64,
            'receipts': [{'action': 'notes'}],
            'caller_reviewed': True,
        }
        routed = standardize(modified)['next']
        self.assertEqual(routed['tool'], 'vwa_evidence')
        self.assertEqual(routed['arguments']['request'], {
            'action': 'workflow_review',
            'workflow_id': 'flow_1',
            'limit': 100,
            'offset': 0,
            'detail': 'notes',
        })
        self.assertEqual(routed['reason'], 'review_changes_require_reread')

        clean = copy.deepcopy(base)
        clean['review_receipt'] = {
            'artifact_id': 'art_same',
            'original_artifact_id': 'art_same',
            'source_sha256': 'b' * 64,
            'receipts': [],
            'caller_reviewed': True,
        }
        kept = standardize(clean)['next']
        self.assertEqual(kept['tool'], 'vwa_run')
        self.assertEqual(kept['arguments']['request'], {
            'action': 'workflow_advance',
            'workflow_id': 'flow_1',
            'expected_revision': 4,
        })

        segment = copy.deepcopy(base)
        segment['review_receipt'] = {
            'artifact_id': 'art_same',
            'original_artifact_id': 'art_same',
            'source_sha256': 'c' * 64,
            'receipts': [],
            'segment_completion': {'edited': True},
            'caller_reviewed': True,
        }
        self.assertEqual(standardize(segment)['next']['tool'], 'vwa_evidence')


if __name__ == '__main__':
    unittest.main()
