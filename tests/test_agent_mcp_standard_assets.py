"""Contracts for the standard-profile asset/recovery/lifecycle tools."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


APP = Path(__file__).resolve().parents[1]
SOURCE = APP / 'src' / 'agent_mcp_standard_assets.py'
SDK_PYTHON = os.environ.get('VWA_MCP_SDK_PYTHON')
EXPECTED_ACTIONS = {
    'vwa_input': {
        'assets_import', 'lyric_timeline_fetch', 'lyric_timeline_import',
        'reference_import', 'workflow_references_set',
    },
    'vwa_recover': {
        'preparation_reuse_plan', 'preparation_repair_plan',
        'preparation_repair_start',
    },
    'vwa_manage': {
        'workflow_rebind_score', 'workflow_close', 'workflow_archive',
        'workflow_clone',
    },
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
    tool_for_class = {
        'AssetsImport': 'vwa_input',
        'LyricTimelineFetch': 'vwa_input',
        'LyricTimelineImport': 'vwa_input',
        'ReferenceImport': 'vwa_input',
        'WorkflowReferencesSet': 'vwa_input',
        'PreparationReusePlan': 'vwa_recover',
        'PreparationRepairPlan': 'vwa_recover',
        'PreparationRepairStart': 'vwa_recover',
        'WorkflowRebindScore': 'vwa_manage',
        'WorkflowClose': 'vwa_manage',
        'WorkflowArchive': 'vwa_manage',
        'WorkflowClone': 'vwa_manage',
    }
    for class_name, action in classes.items():
        if class_name in tool_for_class:
            found[tool_for_class[class_name]].add(action)
    return found


class StandardAssetsStaticContract(unittest.TestCase):
    def test_register_signature_and_typed_action_catalogue(self):
        text = SOURCE.read_text(encoding='utf-8')
        tree = ast.parse(text)
        register = next(node for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == 'register')
        self.assertEqual([item.arg for item in register.args.args],
                         ['mcp', 'control', 'workflow', 'invoke'])
        self.assertEqual(declared_actions(tree), EXPECTED_ACTIONS)
        self.assertEqual(text.count("Field(discriminator='action')"), 3)
        self.assertNotIn('payload', text)
        self.assertNotIn('dict[str, Any]', text)
        for forbidden in ('subprocess', 'os.system', 'start_operation', 'call_worker'):
            self.assertNotIn(forbidden, text)
        self.assertIn('歌词先输入', text)
        self.assertIn('不会自动绑定', text)
        self.assertIn('歌词不是必填项', text)


def invocation_contract(source_directory):
    import asyncio
    import sys
    sys.path.insert(0, str(source_directory))

    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from agent_mcp_models import Result
    import agent_mcp_standard_assets as assets

    checks = []
    calls = []

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    class Backend:
        def __init__(self, owner):
            self.owner = owner

        def record(self, name, args, kwargs):
            calls.append({'owner': self.owner, 'callback': name,
                          'args': list(args), 'kwargs': kwargs})
            return {'ok': True}

        def import_asset(self, *args, **kwargs):
            return self.record('import_asset', args, kwargs)

        def lyric_timeline_fetch(self, *args, **kwargs):
            return self.record('lyric_timeline_fetch', args, kwargs)

        def lyric_timeline_import(self, *args, **kwargs):
            return self.record('lyric_timeline_import', args, kwargs)

        def import_reference(self, *args, **kwargs):
            return self.record('import_reference', args, kwargs)

        def preparation_reuse_plan(self, *args, **kwargs):
            return self.record('preparation_reuse_plan', args, kwargs)

        def preparation_repair_plan(self, *args, **kwargs):
            return self.record('preparation_repair_plan', args, kwargs)

        def preparation_repair_start(self, *args, **kwargs):
            return self.record('preparation_repair_start', args, kwargs)

        def rebind_score(self, *args, **kwargs):
            return self.record('rebind_score', args, kwargs)

        def close(self, *args, **kwargs):
            return self.record('close', args, kwargs)

        def clone(self, *args, **kwargs):
            return self.record('clone', args, kwargs)

        def references_set(self, *args, **kwargs):
            return self.record('references_set', args, kwargs)

    control = Backend('control')
    workflow = Backend('workflow')

    async def invoke(function, *args, **kwargs):
        value = function(*args, **kwargs)
        record = calls[-1]
        return Result(data={'callback': record['callback'], 'args': record['args'],
                            'kwargs': record['kwargs'], 'result': value})

    async def call(server, tool, request):
        result = await server.call_tool(tool, {'request': request})
        check(result.is_error is False, tool + ':not_error')
        return result.structured_content['data']

    async def rejected(server, tool, request, name):
        before = len(calls)
        try:
            await server.call_tool(tool, {'request': request})
        except ToolError:
            check(len(calls) == before, name + ':no_backend_call')
        else:
            raise AssertionError(name + ':accepted')

    async def contract():
        server = MCPServer('standard-assets-contract', log_level='ERROR')
        assets.register(server, control, workflow, invoke)
        tools = {tool.name: tool for tool in await server.list_tools()}
        check(set(tools) == set(EXPECTED_ACTIONS), 'exactly_three_tools')

        annotations = {
            name: tool.annotations.model_dump(by_alias=True, exclude_none=True)
            for name, tool in tools.items()
        }
        check(annotations['vwa_input'] == {
            'title': 'Import assets and bind references', 'readOnlyHint': False,
            'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': True,
        }, 'input_marks_network_write')
        check(annotations['vwa_recover'] == {
            'title': 'Plan preparation recovery', 'readOnlyHint': False,
            'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False,
        }, 'recovery_marks_plan_write')
        check(annotations['vwa_manage'] == {
            'title': 'Manage workflow lifecycle', 'readOnlyHint': False,
            'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False,
        }, 'manage_marks_revision_write')

        schema_actions = {}
        for name, tool in tools.items():
            schema = tool.input_schema
            check(set(schema.get('properties', {})) == {'request'}, name + ':request_only')
            request = schema['properties']['request']
            check(request['discriminator']['propertyName'] == 'action', name + ':discriminator')
            mapping = request['discriminator']['mapping']
            schema_actions[name] = set(mapping)
            for action, reference in mapping.items():
                target = schema['$defs'][reference.rsplit('/', 1)[-1]]
                check(target['additionalProperties'] is False, action + ':extra_forbid')
                check(target['properties']['action']['const'] == action, action + ':const_action')
                check('action' in target['required'], action + ':action_required')
        check(schema_actions == EXPECTED_ACTIONS, 'all_legacy_actions_mapped')

        input_schema = tools['vwa_input'].input_schema
        input_mapping = input_schema['properties']['request']['discriminator']['mapping']
        fetch_schema = input_schema['$defs'][
            input_mapping['lyric_timeline_fetch'].rsplit('/', 1)[-1]
        ]
        import_schema = input_schema['$defs'][
            input_mapping['lyric_timeline_import'].rsplit('/', 1)[-1]
        ]
        references_schema = input_schema['$defs'][
            input_mapping['workflow_references_set'].rsplit('/', 1)[-1]
        ]
        check('不会自动绑定' in fetch_schema['properties']['song_id_or_url']['description'],
              'fetch_is_reference_only')
        check('不会自动绑定' in import_schema['properties']['path']['description'],
              'import_is_reference_only')
        check('明确确认' in references_schema['properties']['lyrics_artifact_id']['description'],
              'timeline_binding_requires_explicit_confirmation')
        check(references_schema['properties']['lyrics_artifact_id']['default'] is None,
              'lyrics_binding_is_optional')
        check('MIDI 秒 = 参考秒' in references_schema['properties']['lyrics_time_origin']['description'],
              'timeline_offset_mapping_is_explicit')
        check('参考来源' in references_schema['properties']['rationale']['description'],
              'timeline_binding_requires_rationale')
        check('歌词先输入' in tools['vwa_input'].description,
              'input_describes_lyrics_first')
        check('歌词不是必填项' in tools['vwa_input'].description,
              'lyrics_are_not_forced')
        check('不会因发现外部歌词而自动重对齐' in tools['vwa_recover'].description,
              'reuse_reports_no_automatic_realign')

        path = 'C:/allowed/review.mid'
        wav = 'C:/allowed/reference.wav'
        lrc = 'C:/allowed/reference.lrc'
        npz = 'C:/allowed/reference.npz'
        workflow_id = 'flow_0123456789abcdef0123456789abcdef'
        cases = [
            ('vwa_input', {'action': 'assets_import', 'path': path},
             'control', 'import_asset', (path,)),
            ('vwa_input', {'action': 'lyric_timeline_fetch', 'song_id_or_url': '123456'},
             'control', 'lyric_timeline_fetch', ('123456',)),
            ('vwa_input', {'action': 'lyric_timeline_import', 'path': lrc},
             'control', 'lyric_timeline_import', (lrc,)),
            ('vwa_input', {'action': 'reference_import', 'path': npz, 'kind': 'pitch'},
             'control', 'import_reference', (npz, 'pitch')),
            ('vwa_input', {
                'action': 'workflow_references_set', 'workflow_id': workflow_id,
                'expected_revision': 7, 'rationale': '原唱与歌词版本已人工比对',
                'pitch_artifact_id': 'art_pitch', 'audio_artifact_id': 'art_audio',
                'track_indices': [0, 2], 'time_origin': 1.25,
                'audio_time_origin': 2.5, 'lyrics_artifact_id': 'art_lyrics',
                'lyrics_time_origin': 3.75, 'lyric_version_note': '词版B，无逐字时间',
            }, 'workflow', 'references_set', (
                workflow_id, 7, 'art_pitch', 'art_audio', [0, 2], 1.25, 2.5,
                '原唱与歌词版本已人工比对', 'art_lyrics', 3.75, '词版B，无逐字时间',
            )),
            ('vwa_recover', {
                'action': 'preparation_reuse_plan', 'source_job_id': 'job_source',
                'options': {'language': 'ja', 'bpm': 128.0, 'recognize_lyrics': False},
                'reuse_stages': ['metadata', 'audio'],
            }, 'control', 'preparation_reuse_plan', (
                'job_source', {'language': 'ja', 'bpm': 128.0, 'recognize_lyrics': False},
                ['metadata', 'audio'],
            )),
            ('vwa_recover', {
                'action': 'preparation_repair_plan', 'source_job_id': 'job_source',
            }, 'control', 'preparation_repair_plan', ('job_source',)),
            ('vwa_recover', {
                'action': 'preparation_repair_start', 'plan_id': 'plan_repair',
                'idempotency_key': 'repair-once-20261027',
            }, 'control', 'preparation_repair_start',
             ('plan_repair', 'repair-once-20261027')),
            ('vwa_manage', {
                'action': 'workflow_rebind_score', 'workflow_id': workflow_id,
                'expected_revision': 8, 'score_artifact_id': 'art_fixed',
                'reason': '修正后回读通过',
            }, 'workflow', 'rebind_score',
             (workflow_id, 8, 'art_fixed', '修正后回读通过')),
            ('vwa_manage', {
                'action': 'workflow_close', 'workflow_id': workflow_id,
                'expected_revision': 9, 'reason': '证据已保留',
            }, 'workflow', 'close', (workflow_id, 9, '证据已保留')),
            ('vwa_manage', {
                'action': 'workflow_archive', 'workflow_id': workflow_id,
                'expected_revision': 10, 'reason': '归档保留',
            }, 'workflow', 'close', (workflow_id, 10, '归档保留', True)),
            ('vwa_manage', {
                'action': 'workflow_clone', 'workflow_id': workflow_id,
                'expected_revision': 11, 'reason': '从保留MIDI继续',
            }, 'workflow', 'clone', (workflow_id, 11, None, '从保留MIDI继续', None, 'full')),
        ]
        for tool, request, owner, callback, expected_args in cases:
            before = len(calls)
            result = await call(server, tool, request)
            check(len(calls) == before + 1, tool + ':one_backend_call')
            check(result['callback'] == callback, request['action'] + ':callback')
            check(result['args'] == list(expected_args), request['action'] + ':arguments')
            check(result['kwargs'] == {}, request['action'] + ':positional_only')
            check(calls[-1]['owner'] == owner, request['action'] + ':owner')
        check(len(calls) == len(cases), 'no_hidden_backend_calls')

        await rejected(server, 'vwa_input', {
            'action': 'reference_import', 'path': wav, 'kind': 'pitch', 'extra': True,
        }, 'extra_field_rejected')
        await rejected(server, 'vwa_input', {
            'action': 'reference_import', 'path': wav, 'kind': 'auto',
        }, 'unknown_reference_kind_rejected')
        await rejected(server, 'vwa_input', {
            'action': 'unknown_action', 'path': wav,
        }, 'unknown_action_rejected')
        await rejected(server, 'vwa_manage', {
            'action': 'workflow_close', 'workflow_id': workflow_id,
            'expected_revision': '9', 'reason': 'strict integer',
        }, 'non_integer_revision_rejected')
        await rejected(server, 'vwa_recover', {
            'action': 'preparation_repair_plan', 'source_job_id': '',
        }, 'empty_source_job_rejected')

        return {
            'checks': checks,
            'actions': {name: sorted(values) for name, values in sorted(schema_actions.items())},
            'call_count': len(calls),
            'model_inference': False,
            'network_requested': False,
            'synthesis': False,
        }

    return asyncio.run(contract())


class StandardAssetsSDKContract(unittest.TestCase):
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
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-8000:])
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['call_count'], 12)
        self.assertEqual(receipt['actions'], {
            name: sorted(values) for name, values in sorted(EXPECTED_ACTIONS.items())
        })
        self.assertFalse(receipt['model_inference'])
        self.assertFalse(receipt['network_requested'])
        self.assertFalse(receipt['synthesis'])
        self.assertGreater(len(receipt['checks']), 70)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--contract':
        print(json.dumps(invocation_contract(Path(sys.argv[2])), ensure_ascii=False))
    else:
        unittest.main()
