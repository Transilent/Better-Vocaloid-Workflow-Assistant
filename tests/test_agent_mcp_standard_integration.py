"""Black-box SDK stdio contracts for the current 12-tool MCP service.

The outer unittest uses the application Python only to build the same isolated
fixture used by 'test_agent_mcp'. The dedicated SDK subprocess then connects
to the real stdio server, discovers tools, validates their public schemas, and
runs a short local MIDI workflow. It never starts model inference, synthesis,
a download, a user job, or an external request.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
SDK_PYTHON = os.environ.get('VWA_MCP_SDK_PYTHON')
SOURCE_COMMIT = '0123456789abcdef0123456789abcdef01234567'

# Reuse the established temporary-app and fixture contract without importing
# product internals into the SDK-side black-box assertions.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agent_mcp import fixtures, temporary_workspace  # noqa: E402


STANDARD_TOOLS = (
    'vwa_start', 'vwa_status', 'vwa_evidence', 'vwa_decide',
    'vwa_run', 'vwa_wait', 'vwa_cancel', 'vwa_input',
    'vwa_search', 'vwa_recover', 'vwa_delivery', 'vwa_manage',
)

HISTORICAL_TOOL_NAMES = (
    'vwa_system_capabilities', 'vwa_system_health', 'vwa_jobs_list', 'vwa_jobs_get',
    'vwa_artifacts_list', 'vwa_artifacts_inspect', 'vwa_operations_get',
    'vwa_operations_cancel', 'vwa_assets_import', 'vwa_score_read', 'vwa_score_review',
    'vwa_score_patch_plan', 'vwa_score_patch_apply', 'vwa_context_read',
    'vwa_style_plan', 'vwa_plans_get', 'vwa_tuning_start', 'vwa_tuning_result',
    'vwa_preparation_plan', 'vwa_preparation_create', 'vwa_preparation_start',
    'vwa_lyrics_read', 'vwa_lyrics_review', 'vwa_lyrics_patch_plan', 'vwa_lyrics_patch_apply',
    'vwa_exports_plan', 'vwa_exports_start', 'vwa_workflow_create', 'vwa_workflow_get',
    'vwa_workflow_review', 'vwa_workflow_apply_review', 'vwa_workflow_advance',
    'vwa_structure_patch_plan', 'vwa_structure_patch_apply', 'vwa_reference_import',
    'vwa_reference_pitch_review', 'vwa_reference_audio_read', 'vwa_workflow_references_set',
    'vwa_pronunciation_read', 'vwa_pronunciation_patch_plan', 'vwa_pronunciation_patch_apply',
    'vwa_workflow_pronunciation_apply', 'vwa_drafts_get', 'vwa_drafts_publish', 'vwa_drafts_plan',
    'vwa_operations_wait', 'vwa_preparation_reuse_plan', 'vwa_workflows_list',
    'vwa_workflow_rebind_score', 'vwa_workflow_close', 'vwa_workflow_archive',
    'vwa_workflow_clone', 'vwa_workflow_delivery', 'vwa_workflow_style_review',
    'vwa_workflow_style_decide', 'vwa_lyric_timeline_search', 'vwa_lyric_timeline_fetch',
    'vwa_lyric_timeline_import', 'vwa_lyric_timeline_review',
    'vwa_preparation_repair_plan', 'vwa_preparation_repair_start',
)

FLAT_TOOLS = frozenset(('vwa_wait', 'vwa_cancel', 'vwa_search'))

# Required action coverage derived from the reviewed 61-tool contract. These are
# current request actions, not a separately registered compatibility catalogue.
GROUPED_REQUIRED_ACTIONS = {
    'vwa_status': frozenset((
        'system_capabilities', 'system_health', 'jobs_list', 'jobs_get',
        'artifacts_list', 'artifacts_inspect', 'operations_get', 'plans_get',
        'tuning_result', 'drafts_get', 'workflow_get', 'workflows_list',
    )),
    'vwa_input': frozenset((
        'assets_import', 'lyric_timeline_fetch', 'lyric_timeline_import',
        'reference_import', 'workflow_references_set',
    )),
    'vwa_evidence': frozenset((
        'score_read', 'score_review', 'context_read', 'lyrics_read',
        'lyric_timeline_read', 'lyric_timeline_review', 'lyrics_review',
        'reference_pitch_review', 'pitch_offset_review',
        'reference_audio_read', 'pronunciation_read', 'workflow_review',
        'workflow_style_review',
    )),
    'vwa_decide': frozenset((
        'score_patch_plan', 'score_patch_apply', 'style_plan', 'drafts_publish',
        'drafts_plan', 'workflow_style_decide', 'workflow_apply_review',
        'workflow_pronunciation_apply', 'lyrics_patch_plan', 'lyrics_patch_apply',
        'structure_patch_plan', 'structure_patch_apply',
        'pronunciation_patch_plan', 'pronunciation_patch_apply',
    )),
    'vwa_run': frozenset((
        'tuning_start', 'preparation_start', 'exports_plan', 'exports_start',
        'workflow_advance',
    )),
    'vwa_start': frozenset(('preparation_plan', 'preparation_create', 'workflow_create')),
    'vwa_recover': frozenset((
        'preparation_reuse_plan', 'preparation_repair_plan', 'preparation_repair_start',
    )),
    'vwa_delivery': frozenset(('workflow_delivery',)),
    'vwa_manage': frozenset((
        'workflow_rebind_score', 'workflow_close', 'workflow_archive', 'workflow_clone',
    )),
}

ALL_MAPPED_ACTIONS = frozenset().union(*GROUPED_REQUIRED_ACTIONS.values())
FLAT_ACTION_NAMES = frozenset(('operations_wait', 'operations_cancel', 'lyric_timeline_search'))
FLAT_REQUIRED_PROPERTIES = {
    'vwa_wait': frozenset(('operation_id', 'timeout_ms')),
    'vwa_cancel': frozenset(('operation_id',)),
    'vwa_search': frozenset(('query', 'limit', 'offset')),
}

CALL_REFERENCE_KEYS = frozenset((
    'next', 'details_tool', 'import_tool', 'audio_excerpt_next', 'decision_tool',
))
CALL_REFERENCE_LISTS = frozenset((
    'alternatives', 'remediation', 'suggestions', 'full_evidence_actions',
    'pronunciation_reads',
))


def _require(condition, label):
    if not condition:
        raise AssertionError(label)


def _schema_mapping(tool):
    schema = tool.input_schema
    _require(isinstance(schema, dict), tool.name + ':input_schema')
    properties = schema.get('properties') or {}
    _require(set(properties) == {'request'}, tool.name + ':request_only')
    request = properties['request']
    discriminator = request.get('discriminator') or {}
    _require(discriminator.get('propertyName') == 'action', tool.name + ':action_discriminator')
    mapping = discriminator.get('mapping') or {}
    _require(mapping, tool.name + ':action_mapping')
    _require(schema.get('required') == ['request'], tool.name + ':request_required')
    return schema, mapping


def _resolve_pointer(schema, pointer):
    target = schema
    for part in pointer.removeprefix('#/').split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        target = target[part]
    return target


def validate_catalogue(listed, expected, checks):
    tools = {tool.name: tool for tool in listed.tools}
    names = tuple(tool.name for tool in listed.tools)
    _require(len(names) == len(set(names)), 'catalogue_unique_names')
    _require(len(expected) == len(set(expected)), 'expected_unique_names')
    _require(set(names) == set(expected), 'catalogue_exact_names')
    _require(len(names) == len(expected), 'catalogue_exact_count')
    _require(all(tool.input_schema and tool.output_schema for tool in listed.tools),
             'catalogue_input_output_schemas')
    for tool in listed.tools:
        output = tool.output_schema
        _require(output.get('additionalProperties') is False, tool.name + ':output_strict')
        _require('data' in (output.get('required') or []), tool.name + ':output_data_required')
        version = (output.get('properties') or {}).get('schema_version') or {}
        _require(version.get('const') == 1, tool.name + ':output_schema_version')
    checks.append('catalogue_' + str(len(expected)))
    return tools, names


def validate_standard_schema(tools, checks):
    _require(set(tools) == set(STANDARD_TOOLS), 'standard_tools_exact')
    actions_by_tool = {}
    for name in STANDARD_TOOLS:
        _require(tools[name].input_schema.get('additionalProperties') is False,
                 name + ':top_level_extra_fields_forbid')
        if name in FLAT_TOOLS:
            properties = set((tools[name].input_schema.get('properties') or {}))
            _require('request' not in properties and 'action' not in properties,
                     name + ':flat_contract')
            _require(FLAT_REQUIRED_PROPERTIES[name] <= properties,
                     name + ':flat_required_properties')
            continue
        schema, mapping = _schema_mapping(tools[name])
        actions = frozenset(mapping)
        actions_by_tool[name] = actions
        _require(GROUPED_REQUIRED_ACTIONS[name] <= actions,
                 name + ':mapped_actions_missing:' + ','.join(sorted(GROUPED_REQUIRED_ACTIONS[name] - actions)))
        _require(actions <= ALL_MAPPED_ACTIONS, name + ':unmapped_actions')
        _require(not (actions & FLAT_ACTION_NAMES), name + ':flat_action_leaked')
        for action, pointer in mapping.items():
            target = _resolve_pointer(schema, pointer)
            _require(target.get('additionalProperties') is False,
                     name + '.' + action + ':extra_fields_forbid')
            action_schema = (target.get('properties') or {}).get('action') or {}
            _require(action_schema.get('const') == action,
                     name + '.' + action + ':action_const')
            _require('action' in (target.get('required') or []),
                     name + '.' + action + ':action_required')
        if name in ('vwa_decide', 'vwa_input', 'vwa_run', 'vwa_delivery', 'vwa_manage'):
            for action, pointer in mapping.items():
                target = _resolve_pointer(schema, pointer)
                revision = (target.get('properties') or {}).get('expected_revision') or {}
                if action.startswith('workflow_'):
                    _require('expected_revision' in (target.get('required') or []),
                             name + '.' + action + ':workflow_revision_required')
                    _require(revision.get('type') == 'integer',
                             name + '.' + action + ':strict_workflow_revision_type')
                elif action == 'drafts_plan':
                    _require(revision.get('type') == 'string',
                             name + '.' + action + ':strict_draft_revision_type')
        if 'workflow_apply_review' in mapping:
            target = _resolve_pointer(schema, mapping['workflow_apply_review'])
            rationale = (target.get('properties') or {}).get('rationale') or {}
            _require('rationale' in (target.get('required') or []), 'apply_review:rationale_required')
            _require(rationale.get('type') == 'string', 'apply_review:rationale_string')
    union = frozenset().union(*actions_by_tool.values())
    _require(union == ALL_MAPPED_ACTIONS, 'standard_action_union_mismatch')
    checks.append('standard_actions_60_mapped')
    checks.append('standard_request_objects_strict')
    return actions_by_tool


def _validate_call_reference(value, discovered, actions_by_tool, label, issues=None):
    if value is None:
        return
    if isinstance(value, str):
        if value.startswith('vwa_'):
            _require(value in discovered, label + ':unknown_tool:' + value)
        return
    _require(isinstance(value, dict), label + ':tool_reference_object')
    tool = value.get('tool')
    if tool is not None:
        _require(isinstance(tool, str), label + ':tool_type')
        if tool not in discovered:
            message = label + ':unknown_tool:' + tool
            if issues is None:
                _require(False, message)
            issues.append(message)
            return
        arguments = value.get('arguments')
        if arguments is None:
            arguments = value.get('required_arguments')
        arguments = arguments or {}
        _require(isinstance(arguments, dict), label + ':arguments_object')
        if tool in actions_by_tool:
            request = arguments.get('request')
            _require(isinstance(request, dict), label + ':grouped_request_missing')
            action = request.get('action')
            _require(isinstance(action, str), label + ':grouped_action_missing')
            _require(action in actions_by_tool[tool], label + ':action_not_discovered:' + action)
        elif tool in FLAT_TOOLS:
            _require('request' not in arguments, label + ':flat_arguments_nested')
            _require('action' not in arguments, label + ':flat_action_leaked')


def validate_references(value, discovered, actions_by_tool, checks=None, label='value',
                        issues=None):
    """Inspect only documented continuation/error fields, never user evidence."""
    if isinstance(value, dict):
        for key, item in value.items():
            where = label + '.' + str(key)
            if key in CALL_REFERENCE_KEYS:
                _validate_call_reference(item, discovered, actions_by_tool, where, issues)
            elif key in CALL_REFERENCE_LISTS and isinstance(item, list):
                for index, row in enumerate(item):
                    if key == 'full_evidence_tools' and isinstance(row, str):
                        _require(row in discovered, where + ':unknown_tool:' + row)
                    else:
                        _validate_call_reference(row, discovered, actions_by_tool,
                                                 where + '[' + str(index) + ']', issues)
            elif key == 'decision_action':
                _require(isinstance(item, str) and item in actions_by_tool.get('vwa_decide', ()),
                         where + ':decision_action_not_discovered')
            validate_references(item, discovered, actions_by_tool, checks, where, issues)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_references(item, discovered, actions_by_tool, checks,
                                label + '[' + str(index) + ']', issues)


def _result_text(result):
    parts = []
    structured = getattr(result, 'structured_content', None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    for item in getattr(result, 'content', None) or ():
        text = getattr(item, 'text', None)
        if text:
            parts.append(text)
        else:
            parts.append(str(item))
    return '\n'.join(parts)


def _structured_from_text(text):
    stripped = (text or '').strip()
    values = []
    if stripped:
        try:
            values.append(json.loads(stripped))
        except (TypeError, ValueError):
            start, end = stripped.find('{'), stripped.rfind('}')
            if 0 <= start < end:
                try:
                    values.append(json.loads(stripped[start:end + 1]))
                except ValueError:
                    pass
    return values


async def call_success(client, name, arguments, discovered, actions_by_tool, checks, calls,
                       reference_issues=None):
    result = await client.call_tool(name, arguments)
    calls.append({'tool': name, 'is_error': bool(result.is_error)})
    _require(not result.is_error, name + ':unexpected_error:' + _result_text(result)[-2000:])
    structured = result.structured_content
    _require(isinstance(structured, dict), name + ':structured_content')
    _require(structured.get('schema_version') == 1, name + ':schema_version')
    data = structured.get('data')
    _require(isinstance(data, dict), name + ':data_object')
    validate_references(data, discovered, actions_by_tool, checks, name + '.data',
                        reference_issues)
    checks.append('success_' + name)
    return data


async def call_rejected(client, name, arguments, discovered, actions_by_tool, calls, label,
                        fragments=()):
    error_text = ''
    result = None
    try:
        result = await client.call_tool(name, arguments)
    except Exception as error:  # SDK versions expose schema errors differently.
        error_text = str(error)
    if result is not None:
        calls.append({'tool': name, 'is_error': bool(result.is_error)})
        error_text = _result_text(result)
        _require(result.is_error, label + ':accepted')
    else:
        calls.append({'tool': name, 'is_error': True})
    folded = error_text.casefold()
    if fragments:
        _require(any(fragment.casefold() in folded for fragment in fragments),
                 label + ':missing_error:' + '|'.join(fragments))
    for referenced in set(re.findall(r'\bvwa_[a-z_]+\b', error_text)):
        _require(referenced in discovered, label + ':unknown_error_tool:' + referenced)
    for payload in _structured_from_text(error_text):
        validate_references(payload, discovered, actions_by_tool, label=label + '.error')
    return error_text


def stdio_parameters(configuration):
    from mcp.client.stdio import StdioServerParameters
    config = json.loads(configuration.read_text(encoding='utf-8'))
    arguments = [
        '-B', '-u', str(Path(config['application_root']) / 'agent_mcp.py'),
        '--config', str(configuration),
    ]
    env = {
        key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP')
        if key in os.environ
    }
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return StdioServerParameters(command=sys.executable, args=arguments, env=env)



def overlay_fixture_with_current_sources(directory):
    # Reviewed src is overlaid only into the disposable black-box app copy.
    import shutil

    app = directory / 'app'
    for source in sorted((APP / 'src').glob('*.py')):
        shutil.copy2(source, app / source.name)


def write_routing_operation_fixture(directory):
    # A terminal operation record lets the real stdio adapter exercise both
    # typed continuation locations without inventing a worker execution.
    operations = directory / 'state/operations'
    operations.mkdir(parents=True, exist_ok=True)
    operation_id = 'op_' + 'a' * 32
    job_id = 'job_' + 'b' * 32
    continuation = {
        'tool': 'vwa_preparation_start',
        'arguments': {
            'job_id': job_id,
            'until': 'midi',
            'idempotency_key': 'routing-fixture-midi',
        },
    }
    record = {
        'schema': 1,
        'id': operation_id,
        'state': 'succeeded',
        'request': {
            'action': 'separation_repair',
            'arguments': {'plan_id': 'plan_' + 'c' * 32},
            'code_revision': 'd' * 64,
        },
        'created': 1.0,
        'updated': 2.0,
        'result': {
            'receipt': {
                'kind': 'separation_repair',
                'handoff': 'ready_for_midi',
                'next': continuation,
                'user_payload': {
                    'next': {
                        'tool': 'vwa_preparation_start',
                        'arguments': {'note': 'receipt user data'},
                    },
                },
            },
            'job_id': job_id,
            'model_execution': False,
            'handoff': 'ready_for_midi',
            'next': continuation,
            'user_payload': {
                'next': {
                    'tool': 'vwa_preparation_start',
                    'arguments': {'note': 'result user data'},
                },
            },
        },
    }
    (operations / (operation_id + '.json')).write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    return operation_id


def write_recovery_fixtures(directory):
    """Write tiny, isolated preparation snapshots for offline recovery calls."""
    import mido
    import numpy as np
    import soundfile as sf

    rebound = mido.MidiFile(str(directory / 'exchange/review.mid'))
    for track in rebound.tracks:
        for message in track:
            if message.type == 'note_on' and message.velocity:
                message.velocity = 47
    rebound.save(str(directory / 'exchange/review-rebound.mid'))

    jobs = directory / 'jobs'
    source_job = jobs / 'recovery-source'
    (source_job / 'audio').mkdir(parents=True)
    (source_job / 'input').mkdir()
    source_audio = source_job / 'input/source.wav'
    source_frames = np.zeros((4096, 2), dtype=np.float32)
    sf.write(str(source_audio), source_frames, 44100, format='WAV', subtype='FLOAT')
    sf.write(str(source_job / 'audio/source.wav'), source_frames, 44100,
             format='WAV', subtype='FLOAT')
    # The partition repair requires readable, complementary float stems. Silent
    # zero samples are sufficient and cannot invoke a model or user media.
    for role in ('vocals', 'lead', 'backing', 'instrumental'):
        sf.write(str(source_job / 'audio' / (role + '.wav')), source_frames, 44100,
                 format='WAV', subtype='FLOAT')
    source_sha = hashlib.sha256(source_audio.read_bytes()).hexdigest()
    (source_job / 'import-report.json').write_text(
        json.dumps({'schema': 1, 'sha256': source_sha}, sort_keys=True),
        encoding='utf-8')
    (source_job / 'audio-info.json').write_text(
        json.dumps({'schema': 1, 'format': 'WAV', 'subtype': 'FLOAT',
                    'sample_rate': 44100, 'channels': 2,
                    'frames': len(source_frames)}, sort_keys=True),
        encoding='utf-8')
    (source_job / 'source-info.json').write_text(
        json.dumps({'kind': 'local', 'title': 'synthetic offline recovery fixture'},
                   ensure_ascii=False, sort_keys=True), encoding='utf-8')
    (source_job / 'upstream-info.json').write_text(
        json.dumps({'fixture': True}, sort_keys=True), encoding='utf-8')
    (source_job / '发布简介草稿.txt').write_text(
        'synthetic fixture only\n', encoding='utf-8')
    request = {
        'schema': 1,
        'source_kind': 'local',
        'source_audio': str(source_audio),
        'input_file': 'input/source.wav',
        'source_title': 'synthetic offline recovery fixture',
        'language': 'zh',
        'lyrics': '山河',
        'recognize_lyrics': False,
        'voice_mode': 'dual',
        'midi_backend': 'v1',
        'alignment_engine': 'hfa',
        'midi_steps': 16,
        'separation_device': 'cpu',
        'midi_device': 'cpu',
        'cpu_threads': 1,
        'tools': {},
    }
    status = {
        'schema': 1,
        'state': 'partial',
        # Opaque synthetic user data must survive standard routing unchanged.
        'user_payload': {
            'next': {
                'tool': 'vwa_preparation_start',
                'arguments': {'note': 'user data, not a continuation'},
            },
        },
        'steps': {
            'metadata': {'state': 'done'},
            'download': {'state': 'done'},
            'audio': {'state': 'done'},
            'separation': {'state': 'failed', 'error': 'synthetic separation failure'},
            'midi': {'state': 'pending'},
        },
    }
    (source_job / 'request.json').write_text(
        json.dumps(request, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    (source_job / 'status.json').write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True), encoding='utf-8')

    # These jobs stop in preflight and let the real stdio error boundary prove
    # that busy/completed sources are refused without scanning any media.
    for name, state in (('recovery-running', 'running'),
                        ('recovery-complete', 'done')):
        job = jobs / name
        job.mkdir()
        (job / 'request.json').write_text(
            json.dumps({**request, 'source_audio': None}, ensure_ascii=False,
                       sort_keys=True), encoding='utf-8')
        (job / 'status.json').write_text(
            json.dumps({'schema': 1, 'state': state, 'steps': {
                'separation': {'state': state},
            }}, ensure_ascii=False, sort_keys=True), encoding='utf-8')


async def standard_capabilities(client, discovered, actions_by_tool, checks, calls):
    capabilities = await call_success(
        client, 'vwa_status', {'request': {'action': 'system_capabilities'}},
        discovered, actions_by_tool, checks, calls,
    )
    _require(capabilities.get('schema') == 1, 'standard_capabilities:schema')
    _require(capabilities.get('interface_profile') == 'standard',
             'standard_capabilities:profile')
    _require('legacy_tool_count' not in capabilities,
             'standard_capabilities:old_compatibility_not_exposed')
    _require(capabilities.get('tools') == list(STANDARD_TOOLS),
             'standard_capabilities:tools_match_discovery')
    _require(capabilities.get('workflow_entry') == 'vwa_start',
             'standard_capabilities:workflow_entry')
    _require(capabilities.get('source_commit') == SOURCE_COMMIT,
             'standard_capabilities:source_commit')
    _require(re.fullmatch(r'[a-f0-9]{64}', capabilities.get('code_revision') or '') is not None,
             'standard_capabilities:code_revision')
    _require(re.fullmatch(r'[a-f0-9]{64}', capabilities.get('interface_revision') or '') is not None,
             'standard_capabilities:interface_revision')
    _require(capabilities.get('default_review_mode') == 'full',
             'standard_capabilities:default_review')
    _require(capabilities.get('reference_inference') is False,
             'standard_capabilities:no_reference_inference')
    checks.append('standard_capabilities_match')
    return capabilities


async def integration_contract(configuration):
    from mcp import Client

    config = json.loads(configuration.read_text(encoding='utf-8'))
    original = configuration.parent / 'exchange/review.mid'
    original_sha = hashlib.sha256(original.read_bytes()).hexdigest()
    forbidden = configuration.parent / 'forbidden.mid'
    forbidden.write_bytes(original.read_bytes())
    checks = []
    calls = []
    reference_issues = []
    fixture_routing = Path(config['application_root']) / 'agent_mcp_standard_routing.py'
    source_routing = APP / 'src/agent_mcp_standard_routing.py'
    routing_sha256 = hashlib.sha256(fixture_routing.read_bytes()).hexdigest()
    _require(routing_sha256 == hashlib.sha256(source_routing.read_bytes()).hexdigest(),
             'isolated_fixture_uses_current_src_routing')
    checks.append('isolated_fixture_uses_current_src_routing')

    async with Client(stdio_parameters(configuration), read_timeout_seconds=90) as client:
        listed = await client.list_tools()
        tools, names = validate_catalogue(listed, STANDARD_TOOLS, checks)
        discovered = frozenset(names)
        _require(len(discovered) == 12, 'default_profile_is_not_standard')
        actions_by_tool = validate_standard_schema(tools, checks)
        capabilities = await standard_capabilities(
            client, discovered, actions_by_tool, checks, calls,
        )
        _require(set(capabilities['tools']) == set(names), 'standard_capability_discovery_mismatch')

        # Validate the envelope as well as the nested action. A blank search
        # query guarantees that a regression here cannot reach the network.
        for name, arguments in (
            ('vwa_status', {'request': {'action': 'system_capabilities'}, 'unexpected': True}),
            ('vwa_cancel', {'operation_id': 'nonexistent', 'unexpected': True}),
            ('vwa_search', {'query': '', 'unexpected': True}),
        ):
            await call_rejected(
                client, name, arguments, discovered, actions_by_tool, calls,
                name + ':top_level_extra_field', fragments=('extra', 'unexpected'),
            )

        for name in sorted(actions_by_tool):
            await call_rejected(
                client, name, {'request': {'action': 'unknown_action'}},
                discovered, actions_by_tool, calls,
                name + ':unknown_action',
                fragments=('unknown_action', 'literal', 'validation', 'invalid'),
            )

        await call_rejected(
            client, 'vwa_input',
            {'request': {
                'action': 'assets_import', 'path': str(original), 'unexpected': True,
            }},
            discovered, actions_by_tool, calls, 'assets_import:extra_field',
            fragments=('extra', 'unexpected', 'validation', 'invalid'),
        )
        await call_rejected(
            client, 'vwa_input',
            {'request': {'action': 'assets_import', 'path': str(forbidden)}},
            discovered, actions_by_tool, calls, 'assets_import:outside_roots',
            fragments=('outside_roots',),
        )

        artifact = await call_success(
            client, 'vwa_input',
            {'request': {'action': 'assets_import', 'path': str(original)}},
            discovered, actions_by_tool, checks, calls,
        )
        _require(artifact.get('id', '').startswith('art_'), 'assets_import:artifact_id')
        _require(artifact.get('sha256') == original_sha, 'assets_import:source_sha')

        pitch = await call_success(
            client, 'vwa_input',
            {'request': {
                'action': 'reference_import',
                'path': str(configuration.parent / 'exchange/reference.npz'),
                'kind': 'pitch',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        pitch_id = pitch.get('id') or pitch.get('artifact_id')
        _require(isinstance(pitch_id, str) and pitch_id.startswith('art_'),
                 'initial_export_options:pitch_id')
        initial_options = {
            'preserve_pitch': True, 'pitch_artifact_id': pitch_id,
            'include_instrumental': False,
        }
        create_request = {
            'action': 'workflow_create',
            'score_artifact_id': artifact['id'],
            'options': {'language': 'zh'},
            'brief': '独立验收夹具：只修改首音并回读，不做风格或合成。',
            'reference_lyrics': '山河',
            'idempotency_key': 'standard-integration-create-1',
            'review_mode': 'full',
            'export_options': initial_options,
        }
        flow = await call_success(
            client, 'vwa_start',
            {'request': create_request},
            discovered, actions_by_tool, checks, calls,
        )
        _require(flow.get('stage') == 'review', 'workflow_create:review_gate')
        _require(type(flow.get('revision')) is int and flow['revision'] >= 0,
                 'workflow_create:revision')
        _require(flow.get('score_artifact_id') == artifact['id'],
                 'workflow_create:score_binding')
        _require((flow.get('request') or {}).get('review_mode') == 'full',
                 'workflow_create:full_review')
        _require(flow.get('export_options') == initial_options,
                 'workflow_create:initial_f0_binding')
        _require((flow.get('next') or {}).get('tool') == 'vwa_evidence',
                 'workflow_create:next_evidence')

        retried = await call_success(
            client, 'vwa_start', {'request': create_request},
            discovered, actions_by_tool, checks, calls,
        )
        _require(retried.get('id') == flow['id'] and retried.get('revision') == flow['revision'],
                 'workflow_create:identical_retry')
        await call_rejected(
            client, 'vwa_start',
            {'request': {**create_request, 'export_options': {
                **initial_options, 'project_name': 'different intent',
            }}},
            discovered, actions_by_tool, calls, 'workflow_create:changed_initial_options',
            fragments=('idempotency_conflict',),
        )

        review = await call_success(
            client, 'vwa_evidence',
            {'request': {
                'action': 'workflow_review', 'workflow_id': flow['id'],
                'limit': 20, 'offset': 0, 'detail': 'notes',
                'include_note_ids': True, 'include_raw_evidence': False,
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(review.get('revision') == flow['revision'], 'workflow_review:revision')
        _require(review.get('total_notes') == 2, 'workflow_review:short_midi_notes')
        _require(review.get('next_offset') is None, 'workflow_review:one_page')
        _require(review.get('decision_tool') == 'vwa_decide', 'workflow_review:decision_tool')
        _require(review.get('decision_action') == 'workflow_apply_review',
                 'workflow_review:decision_action')
        notes = (((review.get('evidence') or {}).get('score') or {}).get('notes') or [])
        _require(len(notes) == 2, 'workflow_review:note_page')
        first = notes[0]
        _require(first.get('pitch') == 60, 'workflow_review:fixture_pitch')
        note_id = first.get('note_id')
        _require(isinstance(note_id, str) and note_id, 'workflow_review:stable_note_id')

        rationale = '已核对两枚音符；仅将首音升高半音并补入山字，第二音保持原样。'
        edits = {
            'workflow_id': flow['id'],
            'rationale': rationale,
            'note_edits': [{'note_id': note_id, 'pitch': 61}],
            'lyric_edits': [{'note_id': note_id, 'lyric': '山', 'operation': 'insert'}],
        }
        stale_revision = flow['revision'] - 1 if flow['revision'] > 0 else 1
        await call_rejected(
            client, 'vwa_decide',
            {'request': {
                'action': 'workflow_apply_review',
                **edits, 'expected_revision': stale_revision,
            }},
            discovered, actions_by_tool, calls, 'workflow_apply_review:stale_revision',
            fragments=('workflow_conflict',),
        )
        await call_rejected(
            client, 'vwa_decide',
            {'request': {
                'action': 'workflow_apply_review',
                **edits, 'expected_revision': flow['revision'], 'rationale': '   ',
            }},
            discovered, actions_by_tool, calls, 'workflow_apply_review:empty_rationale',
            fragments=('review_rationale_required',),
        )
        decided = await call_success(
            client, 'vwa_decide',
            {'request': {
                'action': 'workflow_apply_review',
                **edits, 'expected_revision': flow['revision'],
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(decided.get('stage') == 'reviewing', 'workflow_apply_review:reviewing')
        _require(decided.get('revision') == flow['revision'] + 1,
                 'workflow_apply_review:revision_increment')
        operation = decided.get('operation') or {}
        operation_id = operation.get('id')
        _require(isinstance(operation_id, str) and operation_id,
                 'workflow_apply_review:operation_id')
        _require(operation.get('state') in ('queued', 'running', 'succeeded'),
                 'workflow_apply_review:operation_state')

        wait_data = None
        for _ in range(3):
            wait_data = await call_success(
                client, 'vwa_wait',
                {'operation_id': operation_id, 'timeout_ms': 60000},
                discovered, actions_by_tool, checks, calls,
            )
            if wait_data.get('state') in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                break
        _require(wait_data.get('state') == 'succeeded',
                 'vwa_wait:review_operation:' + json.dumps(wait_data, ensure_ascii=False)[:2000])
        _require((wait_data.get('request') or {}).get('action') == 'workflow_review',
                 'vwa_wait:owned_review_action')
        _require(wait_data.get('result_available') is True, 'vwa_wait:result_available')
        await call_rejected(
            client, 'vwa_wait',
            {'operation_id': operation_id, 'timeout_ms': 1, 'unexpected': True},
            discovered, actions_by_tool, calls, 'vwa_wait:flat_extra_field',
            fragments=('extra', 'unexpected', 'validation', 'invalid'),
        )

        advanced = await call_success(
            client, 'vwa_run',
            {'request': {
                'action': 'workflow_advance', 'workflow_id': flow['id'],
                'expected_revision': decided['revision'],
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(advanced.get('stage') == 'reviewed', 'workflow_advance:reviewed')
        _require(type(advanced.get('revision')) is int
                 and advanced['revision'] > decided['revision'],
                 'workflow_advance:revision_increment')
        _require(advanced.get('score_artifact_id') != artifact['id'],
                 'workflow_advance:corrected_candidate_bound')
        _require((advanced.get('review_receipt') or {}).get('rationale') == rationale,
                 'workflow_advance:explicit_rationale_retained')
        _require((advanced.get('next') or {}).get('tool') == 'vwa_evidence',
                 'workflow_advance:next_readback')
        _require(((advanced.get('next') or {}).get('arguments') or {})
                 .get('request', {}).get('action') == 'workflow_review',
                 'workflow_advance:next_review_candidate')
        _require((advanced.get('readback') or {}).get('required') is True,
                 'workflow_advance:readback_required')

        status = await call_success(
            client, 'vwa_status',
            {'request': {'action': 'workflow_get', 'workflow_id': flow['id']}},
            discovered, actions_by_tool, checks, calls,
        )
        _require(status.get('revision') == advanced['revision'], 'review_status:revision')
        _require((status.get('next') or {}).get('tool') == 'vwa_evidence',
                 'review_status:edited_candidate_readback')

        # Neither a one-step advance nor automatic continuation may export an
        # edited score before the caller reads and explicitly confirms it.
        for automatic in (False, True):
            guarded = await call_success(
                client, 'vwa_run',
                {'request': {
                    'action': 'workflow_advance', 'workflow_id': flow['id'],
                    'expected_revision': advanced['revision'],
                    'run_until_gate': automatic,
                }},
                discovered, actions_by_tool, checks, calls,
            )
            _require(guarded.get('stage') == 'reviewed', 'readback_gate:stage')
            _require(guarded.get('revision') == advanced['revision'], 'readback_gate:revision')
            _require((guarded.get('readback') or {}).get('required') is True,
                     'readback_gate:required')
            _require((guarded.get('next') or {}).get('tool') == 'vwa_evidence',
                     'readback_gate:next')

        readback = await call_success(
            client, 'vwa_evidence',
            {'request': {
                'action': 'workflow_review', 'workflow_id': flow['id'],
                'limit': 20, 'offset': 0, 'detail': 'notes',
                'include_note_ids': True, 'include_raw_evidence': False,
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(readback.get('artifact_id') == advanced['score_artifact_id'],
                 'modified_readback:candidate_binding')
        changed_notes = (((readback.get('evidence') or {}).get('score') or {})
                         .get('notes') or [])
        _require(len(changed_notes) == 2, 'modified_readback:note_count')
        _require(changed_notes[0].get('pitch') == 61,
                 'modified_readback:pitch_changed')
        _require(changed_notes[0].get('lyric') == '山',
                 'modified_readback:lyric_changed')
        _require(changed_notes[1].get('pitch') == 64,
                 'modified_readback:other_note_preserved')
        _require(hashlib.sha256(original.read_bytes()).hexdigest() == original_sha,
                 'modified_readback:source_snapshot_unchanged')

        confirmed = await call_success(
            client, 'vwa_decide',
            {'request': {
                'action': 'workflow_apply_review', 'workflow_id': flow['id'],
                'expected_revision': advanced['revision'],
                'rationale': '已回读两枚音符和歌词，确认首音及新增歌词，保留其余内容。',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        confirmation_id = (confirmed.get('operation') or {}).get('id')
        _require(isinstance(confirmation_id, str) and confirmation_id,
                 'modified_confirmation:operation')
        for _ in range(3):
            confirmation_wait = await call_success(
                client, 'vwa_wait', {'operation_id': confirmation_id, 'timeout_ms': 60000},
                discovered, actions_by_tool, checks, calls,
            )
            if confirmation_wait.get('state') in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                break
        _require(confirmation_wait.get('state') == 'succeeded', 'modified_confirmation:success')
        clean = await call_success(
            client, 'vwa_run',
            {'request': {
                'action': 'workflow_advance', 'workflow_id': flow['id'],
                'expected_revision': confirmed['revision'],
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(clean.get('stage') == 'reviewed', 'modified_confirmation:reviewed')
        _require(clean.get('score_artifact_id') == advanced['score_artifact_id'],
                 'modified_confirmation:candidate_preserved')
        _require(not (clean.get('readback') or {}).get('required'),
                 'modified_confirmation:readback_satisfied')
        clean_status = await call_success(
            client, 'vwa_status',
            {'request': {'action': 'workflow_get', 'workflow_id': flow['id']}},
            discovered, actions_by_tool, checks, calls,
        )
        _require((clean_status.get('next') or {}).get('tool') == 'vwa_run',
                 'review_status:confirmed_candidate_can_advance')

        automatic_flow = await call_success(
            client, 'vwa_start',
            {'request': {
                'action': 'workflow_create', 'score_artifact_id': artifact['id'],
                'idempotency_key': 'standard-integration-automatic-edits-1',
                'review_mode': 'full',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        automatic_result = await call_success(
            client, 'vwa_decide',
            {'request': {
                'action': 'workflow_apply_review', 'workflow_id': automatic_flow['id'],
                'expected_revision': automatic_flow['revision'],
                'rationale': '已读取两枚原始音符，本次修改首音后要求复核候选。',
                'note_edits': [{'note_id': note_id, 'pitch': 61}],
                'run_until_gate': True, 'wait_ms': 60000,
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(automatic_result.get('stage') == 'reviewed', 'automatic_edits:reviewed')
        _require((automatic_result.get('readback') or {}).get('required') is True,
                 'automatic_edits:readback_required')
        _require((automatic_result.get('next') or {}).get('tool') == 'vwa_evidence',
                 'automatic_edits:next_readback')
        _require((automatic_result.get('flow_control') or {}).get('stopped_reason')
                 == 'review_readback_required', 'automatic_edits:no_export')

        # Recover: exercise the real worker against a tiny isolated preparation
        # snapshot. The source contains only generated silence and metadata.
        recovery_source = configuration.parent / 'jobs/recovery-source'
        watched_recovery = (
            'request.json', 'status.json', 'input/source.wav', 'import-report.json',
            'audio-info.json', 'source-info.json', 'upstream-info.json',
            '发布简介草稿.txt', 'audio/source.wav', 'audio/vocals.wav',
            'audio/lead.wav', 'audio/backing.wav', 'audio/instrumental.wav',
        )
        recovery_source_before = {
            name: hashlib.sha256((recovery_source / name).read_bytes()).hexdigest()
            for name in watched_recovery
        }
        listed_jobs = await call_success(
            client, 'vwa_status', {'request': {'action': 'jobs_list', 'limit': 100}},
            discovered, actions_by_tool, checks, calls,
        )
        jobs_by_name = {row.get('name'): row.get('id') for row in listed_jobs.get('jobs', [])}
        for name in ('recovery-source', 'recovery-running', 'recovery-complete'):
            _require(isinstance(jobs_by_name.get(name), str), 'recovery_fixture_job:' + name)
        source_job_id = jobs_by_name['recovery-source']
        running_job_id = jobs_by_name['recovery-running']
        complete_job_id = jobs_by_name['recovery-complete']

        # Read the raw job snapshot without treating a user-defined embedded next
        # as a protocol continuation, then prove routing left it unchanged.
        pseudo_result = await client.call_tool(
            'vwa_status',
            {'request': {'action': 'jobs_get', 'job_id': source_job_id}},
        )
        calls.append({'tool': 'vwa_status', 'is_error': bool(pseudo_result.is_error)})
        _require(not pseudo_result.is_error, 'pseudo_user_next:call_succeeded')
        pseudo_status = (((pseudo_result.structured_content or {}).get('data') or {})
                         .get('status') or {})
        pseudo_next = (pseudo_status.get('user_payload') or {}).get('next') or {}
        _require(pseudo_next == {
            'tool': 'vwa_preparation_start',
            'arguments': {'note': 'user data, not a continuation'},
        }, 'pseudo_user_next:preserved')
        checks.append('pseudo_user_next_preserved')

        routing_fixture_id = 'op_' + 'a' * 32
        routing_result = await client.call_tool(
            'vwa_status',
            {'request': {'action': 'operations_get',
                         'operation_id': routing_fixture_id}},
        )
        calls.append({'tool': 'vwa_status', 'is_error': bool(routing_result.is_error)})
        _require(not routing_result.is_error, 'two_next_routing:call_succeeded')
        routing_data = (routing_result.structured_content or {}).get('data') or {}
        routing_operation_result = routing_data.get('result') or {}
        routing_receipt = routing_operation_result.get('receipt') or {}
        for label, next_value in (
                ('result', routing_operation_result.get('next') or {}),
                ('receipt', routing_receipt.get('next') or {})):
            _require(next_value.get('tool') == 'vwa_run',
                     'two_next_routing:' + label + ':tool')
            _require(((next_value.get('arguments') or {}).get('request') or {})
                     .get('action') == 'preparation_start',
                     'two_next_routing:' + label + ':action')
        _require((routing_operation_result.get('user_payload') or {}).get('next') == {
            'tool': 'vwa_preparation_start',
            'arguments': {'note': 'result user data'},
        }, 'two_next_routing:result_user_data_preserved')
        _require((routing_receipt.get('user_payload') or {}).get('next') == {
            'tool': 'vwa_preparation_start',
            'arguments': {'note': 'receipt user data'},
        }, 'two_next_routing:receipt_user_data_preserved')
        checks.append('two_next_routing_both_standardized')

        recover_actions = {}
        safety_refusals = []
        await call_rejected(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_reuse_plan',
                         'source_job_id': 'job_' + '0' * 32}},
            discovered, actions_by_tool, calls, 'preparation_reuse_plan:unknown_job',
            fragments=('not_found',),
        )
        safety_refusals.append('preparation_reuse_plan:unknown_job')
        await call_rejected(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_plan',
                         'source_job_id': running_job_id}},
            discovered, actions_by_tool, calls, 'preparation_repair_plan:running_source',
            fragments=('separation_source_busy',),
        )
        safety_refusals.append('preparation_repair_plan:running_source')
        await call_rejected(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_plan',
                         'source_job_id': complete_job_id}},
            discovered, actions_by_tool, calls, 'preparation_repair_plan:complete_source',
            fragments=('separation_already_complete',),
        )
        safety_refusals.append('preparation_repair_plan:complete_source')

        reuse_record = await call_success(
            client, 'vwa_recover',
            {'request': {
                'action': 'preparation_reuse_plan', 'source_job_id': source_job_id,
                'reuse_stages': ['metadata', 'download', 'audio'],
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(reuse_record.get('id', '').startswith('plan_'),
                 'preparation_reuse_plan:record_id')
        reuse_plan = reuse_record.get('plan') or {}
        reuse_evidence = reuse_plan.get('reuse') or {}
        _require(reuse_record.get('kind') == 'preparation_reuse',
                 'preparation_reuse_plan:record_kind')
        _require(reuse_plan.get('kind') == 'preparation_reuse',
                 'preparation_reuse_plan:plan_kind')
        _require(reuse_plan.get('source_job') == str(recovery_source),
                 'preparation_reuse_plan:source_binding')
        _require(reuse_evidence.get('requested_stages')
                 == ['metadata', 'download', 'audio'],
                 'preparation_reuse_plan:requested_stages')
        _require(reuse_evidence.get('reuse_stages')
                 == ['metadata', 'download', 'audio'],
                 'preparation_reuse_plan:verified_reuse_stages:'
                 + json.dumps(reuse_evidence, ensure_ascii=False)[:4000])
        _require(reuse_evidence.get('run_stages') == ['separation', 'midi'],
                 'preparation_reuse_plan:run_stages')
        recover_actions['preparation_reuse_plan'] = True
        checks.append('recover_reuse_plan_real_worker')

        repair_record = await call_success(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_plan',
                         'source_job_id': source_job_id}},
            discovered, actions_by_tool, checks, calls,
        )
        repair_plan = repair_record.get('plan') or {}
        repair_plan_id = repair_record.get('id')
        _require(repair_record.get('kind') == 'separation_repair',
                 'preparation_repair_plan:record_kind')
        _require(repair_plan.get('kind') == 'separation_repair',
                 'preparation_repair_plan:plan_kind')
        _require(repair_plan.get('model_execution') is False,
                 'preparation_repair_plan:no_model')
        _require(set(repair_plan.get('audio') or {})
                 == {'vocals', 'lead', 'backing'},
                 'preparation_repair_plan:stem_roles')
        _require(set(repair_plan.get('supporting_audio') or {}) == {'instrumental'},
                 'preparation_repair_plan:supporting_role')
        _require((repair_plan.get('source_failure') or {}).get('state') == 'failed',
                 'preparation_repair_plan:source_failure')
        _require(((repair_plan.get('reuse_plan') or {}).get('reuse') or {})
                 .get('reuse_stages') == ['metadata', 'download', 'audio'],
                 'preparation_repair_plan:reuse_prefix')
        recover_actions['preparation_repair_plan'] = True
        checks.append('recover_repair_plan_real_worker')

        await call_rejected(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_start',
                         'plan_id': 'plan_' + '0' * 32,
                         'idempotency_key': 'repair-missing-plan'}},
            discovered, actions_by_tool, calls, 'preparation_repair_start:unknown_plan',
            fragments=('not_found',),
        )
        safety_refusals.append('preparation_repair_start:unknown_plan')
        await call_rejected(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_start',
                         'plan_id': repair_plan_id, 'idempotency_key': ''}},
            discovered, actions_by_tool, calls, 'preparation_repair_start:empty_key',
            fragments=('string_too_short', 'min_length', 'validation', 'invalid'),
        )
        safety_refusals.append('preparation_repair_start:empty_key')

        repair_started = await call_success(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_start',
                         'plan_id': repair_plan_id,
                         'idempotency_key': 'standard-repair-start-1'}},
            discovered, actions_by_tool, checks, calls,
        )
        repair_operation_id = repair_started.get('id')
        _require(isinstance(repair_operation_id, str) and repair_operation_id.startswith('op_'),
                 'preparation_repair_start:operation_id')
        _require((repair_started.get('request') or {}).get('action') == 'separation_repair',
                 'preparation_repair_start:owned_action')
        repair_wait = None
        for _ in range(3):
            repair_wait = await call_success(
                client, 'vwa_wait',
                {'operation_id': repair_operation_id, 'timeout_ms': 60000},
                discovered, actions_by_tool, checks, calls,
            )
            if repair_wait.get('state') in (
                    'succeeded', 'failed', 'cancelled', 'interrupted'):
                break
        _require(repair_wait.get('state') == 'succeeded',
                 'preparation_repair_start:success:' +
                 json.dumps(repair_wait, ensure_ascii=False)[:2000])
        repair_operation = await call_success(
            client, 'vwa_status',
            {'request': {'action': 'operations_get', 'operation_id': repair_operation_id}},
            discovered, actions_by_tool, checks, calls,
            reference_issues=reference_issues,
        )
        repair_result = repair_operation.get('result') or {}
        repair_receipt = repair_result.get('receipt') or {}
        _require(repair_result.get('model_execution') is False,
                 'preparation_repair_start:no_model_execution')
        _require(repair_result.get('handoff') == 'ready_for_midi',
                 'preparation_repair_start:midi_handoff')
        _require(repair_receipt.get('kind') == 'separation_repair',
                 'preparation_repair_start:receipt_kind')
        _require(repair_receipt.get('replay') is False,
                 'preparation_repair_start:first_execution')
        _require(isinstance(repair_result.get('job_id'), str),
                 'preparation_repair_start:new_job')
        repair_next_tool = (repair_result.get('next') or {}).get('tool')
        _require(repair_next_tool == 'vwa_run',
                 'preparation_repair_start:next_tool:' + str(repair_next_tool))
        repair_receipt_next_emitted = 'next' in repair_receipt
        replay = await call_success(
            client, 'vwa_recover',
            {'request': {'action': 'preparation_repair_start',
                         'plan_id': repair_plan_id,
                         'idempotency_key': 'standard-repair-start-1'}},
            discovered, actions_by_tool, checks, calls,
            reference_issues=reference_issues,
        )
        _require(replay.get('id') == repair_operation_id,
                 'preparation_repair_start:idempotent_replay')
        _require({
            name: hashlib.sha256((recovery_source / name).read_bytes()).hexdigest()
            for name in watched_recovery
        } == recovery_source_before, 'preparation_repair_start:source_unchanged')
        recover_actions['preparation_repair_start'] = True
        checks.append('recover_repair_start_offline_success')

        # Manage: clone, rebind, close, clone closed evidence, then archive.
        rebound = await call_success(
            client, 'vwa_input',
            {'request': {
                'action': 'assets_import',
                'path': str(configuration.parent / 'exchange/review-rebound.mid'),
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(rebound.get('id', '').startswith('art_') and rebound['id'] != artifact['id'],
                 'manage_rebind:distinct_midi_artifact')
        manage_source = await call_success(
            client, 'vwa_start',
            {'request': {
                'action': 'workflow_create',
                'score_artifact_id': artifact['id'],
                'brief': 'synthetic lifecycle fixture; no style, export, or synthesis',
                'idempotency_key': 'manage-source-1',
                'review_mode': 'full',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        manage_actions = {}
        revision_cas = []

        clone_request = {
            'action': 'workflow_clone', 'workflow_id': manage_source['id'],
            'expected_revision': manage_source['revision'],
            'reason': 'synthetic full-review lifecycle clone',
            'idempotency_key': 'manage-clone-1', 'review_mode': 'full',
        }
        clone_one = await call_success(
            client, 'vwa_manage', {'request': clone_request},
            discovered, actions_by_tool, checks, calls,
        )
        _require(clone_one.get('id') != manage_source['id'], 'workflow_clone:new_id')
        _require(clone_one.get('lifecycle', 'active') == 'active',
                 'workflow_clone:active')
        _require(clone_one.get('stage') == 'review', 'workflow_clone:review_stage')
        _require((clone_one.get('request') or {}).get('review_mode') == 'full',
                 'workflow_clone:full_review')
        _require(clone_one.get('score_artifact_id') == artifact['id'],
                 'workflow_clone:score_bound')
        _require((clone_one.get('cloned_from') or {}).get('workflow_id')
                 == manage_source['id'], 'workflow_clone:parent_binding')
        _require((clone_one.get('cloned_from') or {}).get('revision')
                 == manage_source['revision'], 'workflow_clone:parent_revision')
        clone_replay = await call_success(
            client, 'vwa_manage', {'request': clone_request},
            discovered, actions_by_tool, checks, calls,
        )
        _require(clone_replay.get('id') == clone_one['id']
                 and clone_replay.get('revision') == clone_one['revision'],
                 'workflow_clone:idempotent_replay')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {**clone_request,
                         'expected_revision': manage_source['revision'] + 1}},
            discovered, actions_by_tool, calls, 'workflow_clone:stale_revision',
            fragments=('workflow_conflict',),
        )
        revision_cas.append('workflow_clone:stale_revision')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {**clone_request, 'reason': '   '}},
            discovered, actions_by_tool, calls, 'workflow_clone:empty_reason',
            fragments=('clone_reason_required',),
        )
        safety_refusals.append('workflow_clone:empty_reason')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {**clone_request, 'score_artifact_id': rebound['id']}},
            discovered, actions_by_tool, calls, 'workflow_clone:key_conflict',
            fragments=('idempotency_conflict',),
        )
        safety_refusals.append('workflow_clone:key_conflict')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {**clone_request, 'idempotency_key': 'manage-source-1'}},
            discovered, actions_by_tool, calls, 'workflow_clone:reuse_parent_key',
            fragments=('invalid_idempotency_key',),
        )
        safety_refusals.append('workflow_clone:reuse_parent_key')
        manage_actions['workflow_clone'] = True
        checks.append('manage_clone_real_worker')

        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_rebind_score', 'workflow_id': clone_one['id'],
                'expected_revision': clone_one['revision'] + 1,
                'score_artifact_id': rebound['id'], 'reason': 'stale rebind',
            }},
            discovered, actions_by_tool, calls, 'workflow_rebind_score:stale_revision',
            fragments=('workflow_conflict',),
        )
        revision_cas.append('workflow_rebind_score:stale_revision')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_rebind_score', 'workflow_id': clone_one['id'],
                'expected_revision': clone_one['revision'],
                'score_artifact_id': rebound['id'], 'reason': '   ',
            }},
            discovered, actions_by_tool, calls, 'workflow_rebind_score:empty_reason',
            fragments=('rebind_reason_required',),
        )
        safety_refusals.append('workflow_rebind_score:empty_reason')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_rebind_score', 'workflow_id': clone_one['id'],
                'expected_revision': clone_one['revision'],
                'score_artifact_id': pitch_id, 'reason': 'wrong artifact kind',
            }},
            discovered, actions_by_tool, calls, 'workflow_rebind_score:non_midi',
            fragments=('unsupported_source',),
        )
        safety_refusals.append('workflow_rebind_score:non_midi')
        rebound_value = await call_success(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_rebind_score', 'workflow_id': clone_one['id'],
                'expected_revision': clone_one['revision'],
                'score_artifact_id': rebound['id'],
                'reason': 'synthetic corrected MIDI must be reviewed again',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(rebound_value.get('stage') == 'review', 'workflow_rebind_score:review')
        _require(rebound_value.get('revision') == clone_one['revision'] + 1,
                 'workflow_rebind_score:revision_increment')
        _require(rebound_value.get('score_artifact_id') == rebound['id'],
                 'workflow_rebind_score:new_binding')
        _require(rebound_value.get('rebind_reason')
                 == 'synthetic corrected MIDI must be reviewed again',
                 'workflow_rebind_score:reason_retained')
        _require((rebound_value.get('recovery_history') or [])[-1].get('revision')
                 == clone_one['revision'],
                 'workflow_rebind_score:old_revision_retained')
        manage_actions['workflow_rebind_score'] = True
        checks.append('manage_rebind_real_worker')

        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_close', 'workflow_id': clone_one['id'],
                'expected_revision': rebound_value['revision'] + 1,
                'reason': 'stale close',
            }},
            discovered, actions_by_tool, calls, 'workflow_close:stale_revision',
            fragments=('workflow_conflict',),
        )
        revision_cas.append('workflow_close:stale_revision')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_close', 'workflow_id': clone_one['id'],
                'expected_revision': rebound_value['revision'], 'reason': '   ',
            }},
            discovered, actions_by_tool, calls, 'workflow_close:empty_reason',
            fragments=('close_reason_required',),
        )
        safety_refusals.append('workflow_close:empty_reason')
        closed = await call_success(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_close', 'workflow_id': clone_one['id'],
                'expected_revision': rebound_value['revision'],
                'reason': 'synthetic lifecycle close; evidence remains readable',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(closed.get('lifecycle') == 'closed', 'workflow_close:lifecycle')
        _require(closed.get('revision') == rebound_value['revision'] + 1,
                 'workflow_close:revision_increment')
        _require((closed.get('history') or [])[-1].get('action') == 'closed',
                 'workflow_close:history_retained')
        await call_rejected(
            client, 'vwa_run',
            {'request': {
                'action': 'workflow_advance', 'workflow_id': closed['id'],
                'expected_revision': closed['revision'],
            }},
            discovered, actions_by_tool, calls, 'workflow_close:advance_refused',
            fragments=('workflow_closed',),
        )
        safety_refusals.append('workflow_close:advance_refused')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_rebind_score', 'workflow_id': closed['id'],
                'expected_revision': closed['revision'],
                'score_artifact_id': rebound['id'], 'reason': 'closed rebind',
            }},
            discovered, actions_by_tool, calls, 'workflow_close:rebind_refused',
            fragments=('workflow_closed',),
        )
        safety_refusals.append('workflow_close:rebind_refused')
        closed_status = await call_success(
            client, 'vwa_status',
            {'request': {'action': 'workflow_get', 'workflow_id': closed['id']}},
            discovered, actions_by_tool, checks, calls,
        )
        _require((closed_status.get('next') or {}).get('tool') == 'vwa_manage',
                 'workflow_close:next_manage')
        _require(((closed_status.get('next') or {}).get('arguments') or {})
                 .get('request', {}).get('action') == 'workflow_clone',
                 'workflow_close:next_clone_action')
        manage_actions['workflow_close'] = True
        checks.append('manage_close_real_worker')

        clone_two = await call_success(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_clone', 'workflow_id': closed['id'],
                'expected_revision': closed['revision'],
                'reason': 'clone retained closed evidence',
                'idempotency_key': 'manage-clone-2', 'review_mode': 'full',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(clone_two.get('lifecycle', 'active') == 'active',
                 'workflow_clone_closed:active')
        _require((clone_two.get('cloned_from') or {}).get('workflow_id') == closed['id'],
                 'workflow_clone_closed:parent')
        _require((clone_two.get('cloned_from') or {}).get('revision') == closed['revision'],
                 'workflow_clone_closed:revision_binding')
        _require(clone_two.get('score_artifact_id') == rebound['id'],
                 'workflow_clone_closed:latest_score')

        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_archive', 'workflow_id': clone_two['id'],
                'expected_revision': clone_two['revision'] + 1,
                'reason': 'stale archive',
            }},
            discovered, actions_by_tool, calls, 'workflow_archive:stale_revision',
            fragments=('workflow_conflict',),
        )
        revision_cas.append('workflow_archive:stale_revision')
        await call_rejected(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_archive', 'workflow_id': clone_two['id'],
                'expected_revision': clone_two['revision'], 'reason': '   ',
            }},
            discovered, actions_by_tool, calls, 'workflow_archive:empty_reason',
            fragments=('close_reason_required',),
        )
        safety_refusals.append('workflow_archive:empty_reason')
        archived = await call_success(
            client, 'vwa_manage',
            {'request': {
                'action': 'workflow_archive', 'workflow_id': clone_two['id'],
                'expected_revision': clone_two['revision'],
                'reason': 'synthetic lifecycle archive; evidence remains readable',
            }},
            discovered, actions_by_tool, checks, calls,
        )
        _require(archived.get('lifecycle') == 'archived', 'workflow_archive:lifecycle')
        _require(archived.get('revision') == clone_two['revision'] + 1,
                 'workflow_archive:revision_increment')
        _require((archived.get('history') or [])[-1].get('action') == 'archived',
                 'workflow_archive:history_retained')
        archived_status = await call_success(
            client, 'vwa_status',
            {'request': {'action': 'workflow_get', 'workflow_id': archived['id']}},
            discovered, actions_by_tool, checks, calls,
        )
        _require((archived_status.get('next') or {}).get('tool') == 'vwa_manage',
                 'workflow_archive:next_manage')
        _require(((archived_status.get('next') or {}).get('arguments') or {})
                 .get('request', {}).get('action') == 'workflow_clone',
                 'workflow_archive:next_clone_action')
        listed_workflows = await call_success(
            client, 'vwa_status',
            {'request': {'action': 'workflows_list', 'limit': 100, 'offset': 0}},
            discovered, actions_by_tool, checks, calls,
        )
        lifecycle_by_id = {
            row.get('id'): row.get('lifecycle')
            for row in listed_workflows.get('workflows', [])
        }
        _require(lifecycle_by_id.get(closed['id']) == 'closed',
                 'workflow_close:listed')
        _require(lifecycle_by_id.get(archived['id']) == 'archived',
                 'workflow_archive:listed')
        manage_actions['workflow_archive'] = True
        checks.append('manage_archive_real_worker')

    # The former compatibility switch is gone from the public entry point.
    removed_profile = subprocess.run(
        [sys.executable, '-B', str(Path(config['application_root']) / 'agent_mcp.py'),
         '--config', str(configuration), '--profile', 'legacy'],
        cwd=config['application_root'], capture_output=True, timeout=20,
        env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    removed_output = (removed_profile.stdout + removed_profile.stderr).decode(
        'utf-8', errors='replace')
    _require(removed_profile.returncode != 0 and
             'unrecognized arguments' in removed_output,
             'removed_profile_cli_rejected')
    checks.append('removed_profile_cli_rejected')

    return {
        'checks': checks,
        'calls': calls,
        'standard_tools': list(STANDARD_TOOLS),
        'historical_tool_count': len(HISTORICAL_TOOL_NAMES),
        'standard_action_count': len(ALL_MAPPED_ACTIONS),
        'source_commit': SOURCE_COMMIT,
        'workflow_id': flow['id'],
        'review_operation_id': operation_id,
        'wait_state': wait_data['state'],
        'modified_artifact_id': advanced['score_artifact_id'],
        'recover_actions': recover_actions,
        'manage_actions': manage_actions,
        'safety_refusals': safety_refusals,
        'revision_cas': revision_cas,
        'reference_issues': reference_issues,
        'repair_next_tool': repair_next_tool,
        'repair_operation_evidence': repair_operation,
        'routing_sha256': routing_sha256,
        'pseudo_user_next_preserved': True,
        'two_next_routing_verified': True,
        'repair_receipt_next_emitted': repair_receipt_next_emitted,
        'original_input_unchanged': True,
        'model_inference': False,
        'synthesis': False,
        'network_requested': False,
        'user_tasks_modified': False,
    }


class StandardMCPIntegration(unittest.TestCase):
    def test_current_and_historical_catalogues_are_complete(self):
        self.assertEqual(len(STANDARD_TOOLS), 12)
        self.assertEqual(len(set(STANDARD_TOOLS)), 12)
        self.assertEqual(len(HISTORICAL_TOOL_NAMES), 61)
        self.assertEqual(len(set(HISTORICAL_TOOL_NAMES)), 61)
        self.assertTrue(set(STANDARD_TOOLS).isdisjoint(HISTORICAL_TOOL_NAMES))
        self.assertEqual(len(ALL_MAPPED_ACTIONS), 60)
        historical_actions = {
            name.removeprefix('vwa_') for name in HISTORICAL_TOOL_NAMES
        }
        self.assertEqual(
            (historical_actions - FLAT_ACTION_NAMES) | {'lyric_timeline_read', 'pitch_offset_review'},
            ALL_MAPPED_ACTIONS,
        )

    @unittest.skipUnless(SDK_PYTHON, 'Dedicated optional SDK not configured')
    def test_real_sdk_stdio_workflow(self):
        self.assertTrue(Path(SDK_PYTHON).is_file(), SDK_PYTHON)
        parent = APP / 'work/mcp-standard-integration'
        parent.mkdir(parents=True, exist_ok=True)
        with temporary_workspace(parent) as folder:
            root = Path(folder)
            configuration = fixtures(root)
            overlay_fixture_with_current_sources(root)
            write_recovery_fixtures(root)
            write_routing_operation_fixture(root)
            config = json.loads(configuration.read_text(encoding='utf-8'))
            config['source_commit'] = SOURCE_COMMIT
            configuration.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(
                [SDK_PYTHON, '-B', str(Path(__file__).resolve()),
                 '--client', str(configuration)],
                cwd=APP,
                capture_output=True,
                env=dict(
                    os.environ,
                    VWA_MCP_WORKER_PYTHON=sys.executable,
                    TEMP=str(root),
                    TMP=str(root),
                    PYTHONUTF8='1',
                    PYTHONDONTWRITEBYTECODE='1',
                ),
                timeout=180,
            )
            reports = APP / 'work/test-results'
            reports.mkdir(parents=True, exist_ok=True)
            (reports / 'mcp-standard-integration.log').write_bytes(
                result.stdout + result.stderr
            )
            self.assertEqual(
                result.returncode, 0,
                (result.stdout + result.stderr).decode('utf-8', errors='replace')[-10000:],
            )
            receipt = json.loads(result.stdout.decode('utf-8'))
            (reports / 'mcp-standard-integration.json').write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            self.assertGreaterEqual(len(receipt['checks']), 32)
            self.assertGreaterEqual(len(receipt['calls']), 60)
            self.assertEqual(receipt['standard_tools'], list(STANDARD_TOOLS))
            self.assertEqual(receipt['historical_tool_count'], 61)
            self.assertEqual(receipt['standard_action_count'], 60)
            self.assertEqual(receipt['wait_state'], 'succeeded')
            self.assertEqual(
                set(receipt['recover_actions']),
                set(GROUPED_REQUIRED_ACTIONS['vwa_recover']),
            )
            self.assertTrue(all(receipt['recover_actions'].values()))
            self.assertEqual(
                set(receipt['manage_actions']),
                set(GROUPED_REQUIRED_ACTIONS['vwa_manage']),
            )
            self.assertTrue(all(receipt['manage_actions'].values()))
            self.assertIn('preparation_repair_plan:running_source',
                          receipt['safety_refusals'])
            self.assertIn('preparation_repair_plan:complete_source',
                          receipt['safety_refusals'])
            self.assertIn('workflow_rebind_score:non_midi', receipt['safety_refusals'])
            self.assertIn('workflow_close:advance_refused', receipt['safety_refusals'])
            self.assertEqual(set(receipt['revision_cas']), {
                'workflow_clone:stale_revision',
                'workflow_rebind_score:stale_revision',
                'workflow_close:stale_revision',
                'workflow_archive:stale_revision',
            })
            self.assertEqual(receipt['reference_issues'], [])
            self.assertTrue(receipt['pseudo_user_next_preserved'])
            self.assertTrue(receipt['two_next_routing_verified'])
            self.assertTrue(receipt['original_input_unchanged'])
            self.assertFalse(receipt['model_inference'])
            self.assertFalse(receipt['synthesis'])
            self.assertFalse(receipt['network_requested'])
            self.assertFalse(receipt['user_tasks_modified'])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--client':
        print(json.dumps(asyncio.run(integration_contract(Path(sys.argv[2]))),
                         ensure_ascii=False))
    else:
        unittest.main()
