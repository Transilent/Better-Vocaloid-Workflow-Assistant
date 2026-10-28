"""Black-box SDK stdio contracts for the standard and legacy MCP profiles.

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

LEGACY_TOOLS = (
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

# Required action coverage from the reviewed 61-to-12 map. The conditional
# routing may expose an old action through both status/evidence and run, so the
# union is exact while a tool's action set only has to include its mapped base.
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
        'lyric_timeline_review', 'lyrics_review', 'reference_pitch_review',
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
    checks.append('standard_actions_58_mapped')
    checks.append('standard_request_objects_strict')
    return actions_by_tool


def _validate_call_reference(value, discovered, actions_by_tool, label):
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
        _require(tool in discovered, label + ':unknown_tool:' + tool)
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


def validate_references(value, discovered, actions_by_tool, checks=None, label='value'):
    """Inspect only documented continuation/error fields, never user evidence."""
    if isinstance(value, dict):
        for key, item in value.items():
            where = label + '.' + str(key)
            if key in CALL_REFERENCE_KEYS:
                _validate_call_reference(item, discovered, actions_by_tool, where)
            elif key in CALL_REFERENCE_LISTS and isinstance(item, list):
                for index, row in enumerate(item):
                    if key == 'full_evidence_tools' and isinstance(row, str):
                        _require(row in discovered, where + ':unknown_tool:' + row)
                    else:
                        _validate_call_reference(row, discovered, actions_by_tool,
                                                 where + '[' + str(index) + ']')
            elif key == 'decision_action':
                _require(isinstance(item, str) and item in actions_by_tool.get('vwa_decide', ()),
                         where + ':decision_action_not_discovered')
            validate_references(item, discovered, actions_by_tool, checks, where)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_references(item, discovered, actions_by_tool, checks,
                                label + '[' + str(index) + ']')


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


async def call_success(client, name, arguments, discovered, actions_by_tool, checks, calls):
    result = await client.call_tool(name, arguments)
    calls.append({'tool': name, 'is_error': bool(result.is_error)})
    _require(not result.is_error, name + ':unexpected_error:' + _result_text(result)[-2000:])
    structured = result.structured_content
    _require(isinstance(structured, dict), name + ':structured_content')
    _require(structured.get('schema_version') == 1, name + ':schema_version')
    data = structured.get('data')
    _require(isinstance(data, dict), name + ':data_object')
    validate_references(data, discovered, actions_by_tool, checks, name + '.data')
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


def stdio_parameters(configuration, profile=None):
    from mcp.client.stdio import StdioServerParameters
    config = json.loads(configuration.read_text(encoding='utf-8'))
    arguments = [
        '-B', '-u', str(Path(config['application_root']) / 'agent_mcp.py'),
        '--config', str(configuration),
    ]
    if profile is not None:
        arguments.extend(('--profile', profile))
    env = {
        key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP')
        if key in os.environ
    }
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return StdioServerParameters(command=sys.executable, args=arguments, env=env)


async def standard_capabilities(client, discovered, actions_by_tool, checks, calls):
    capabilities = await call_success(
        client, 'vwa_status', {'request': {'action': 'system_capabilities'}},
        discovered, actions_by_tool, checks, calls,
    )
    _require(capabilities.get('schema') == 1, 'standard_capabilities:schema')
    _require(capabilities.get('interface_profile') == 'standard',
             'standard_capabilities:profile')
    _require(capabilities.get('legacy_tool_count') == 61,
             'standard_capabilities:legacy_count')
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

    async with Client(stdio_parameters(configuration, 'standard'),
                      read_timeout_seconds=30) as explicit:
        explicit_tools = await explicit.list_tools()
        explicit_names = tuple(tool.name for tool in explicit_tools.tools)
        _require(set(explicit_names) == set(STANDARD_TOOLS), 'explicit_standard_profile_exact')
        _require(len(explicit_names) == 12, 'explicit_standard_profile_count')

    async with Client(stdio_parameters(configuration, 'legacy'),
                      read_timeout_seconds=60) as legacy:
        legacy_listed = await legacy.list_tools()
        legacy_tools, legacy_names = validate_catalogue(legacy_listed, LEGACY_TOOLS, checks)
        legacy_set = frozenset(legacy_names)
        _require(len(legacy_set) == 61, 'legacy_profile_exact_count')
        _require(legacy_set.isdisjoint(STANDARD_TOOLS), 'profiles_do_not_mix')
        assets_schema = legacy_tools['vwa_assets_import'].input_schema
        _require(set((assets_schema.get('properties') or {})) == {'path'},
                 'legacy_assets_flat_interface')
        create_schema = legacy_tools['vwa_workflow_create'].input_schema
        create_properties = create_schema.get('properties') or {}
        _require('request' not in create_properties and 'score_artifact_id' in create_properties,
                 'legacy_workflow_create_flat_interface')
        mode = create_properties.get('review_mode') or {}
        _require(mode.get('default') == 'full', 'legacy_workflow_create_full_default')

        legacy_caps = await call_success(
            legacy, 'vwa_system_capabilities', {}, legacy_set, {}, checks, calls,
        )
        _require(legacy_caps.get('schema') == 1, 'legacy_capabilities:schema')
        _require(set(legacy_caps.get('tools') or ()) == legacy_set,
                 'legacy_capabilities:tools_match_discovery')
        _require(legacy_caps.get('workflow_entry') == 'vwa_workflow_create',
                 'legacy_capabilities:workflow_entry')
        _require(legacy_caps.get('interface_profile') == 'legacy',
                 'legacy_capabilities:profile')
        _require(re.fullmatch(r'[a-f0-9]{64}', legacy_caps.get('interface_revision') or '') is not None,
                 'legacy_capabilities:interface_revision')
        _require(legacy_caps.get('source_commit') == SOURCE_COMMIT,
                 'legacy_capabilities:source_commit')
        legacy_artifact = await call_success(
            legacy, 'vwa_assets_import', {'path': str(original)},
            legacy_set, {}, checks, calls,
        )
        _require(legacy_artifact.get('sha256') == original_sha,
                 'legacy_assets_import:real_call')

    return {
        'checks': checks,
        'calls': calls,
        'standard_tools': list(STANDARD_TOOLS),
        'legacy_tool_count': len(LEGACY_TOOLS),
        'standard_action_count': len(ALL_MAPPED_ACTIONS),
        'source_commit': SOURCE_COMMIT,
        'workflow_id': flow['id'],
        'review_operation_id': operation_id,
        'wait_state': wait_data['state'],
        'modified_artifact_id': advanced['score_artifact_id'],
        'original_input_unchanged': True,
        'model_inference': False,
        'synthesis': False,
        'network_requested': False,
        'user_tasks_modified': False,
    }


class StandardMCPIntegration(unittest.TestCase):
    def test_catalogues_are_exact(self):
        self.assertEqual(len(STANDARD_TOOLS), 12)
        self.assertEqual(len(set(STANDARD_TOOLS)), 12)
        self.assertEqual(len(LEGACY_TOOLS), 61)
        self.assertEqual(len(set(LEGACY_TOOLS)), 61)
        self.assertEqual(set(STANDARD_TOOLS).isdisjoint(LEGACY_TOOLS), True)
        self.assertEqual(len(ALL_MAPPED_ACTIONS), 58)

    @unittest.skipUnless(SDK_PYTHON, 'Dedicated optional SDK not configured')
    def test_real_sdk_stdio_profiles_and_workflow(self):
        self.assertTrue(Path(SDK_PYTHON).is_file(), SDK_PYTHON)
        parent = APP / 'work/mcp-standard-integration'
        parent.mkdir(parents=True, exist_ok=True)
        with temporary_workspace(parent) as folder:
            root = Path(folder)
            configuration = fixtures(root)
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
            self.assertGreaterEqual(len(receipt['checks']), 15)
            self.assertGreaterEqual(len(receipt['calls']), 20)
            self.assertEqual(receipt['standard_tools'], list(STANDARD_TOOLS))
            self.assertEqual(receipt['legacy_tool_count'], 61)
            self.assertEqual(receipt['standard_action_count'], 58)
            self.assertEqual(receipt['wait_state'], 'succeeded')
            self.assertTrue(receipt['original_input_unchanged'])
            self.assertFalse(receipt['model_inference'])
            self.assertFalse(receipt['synthesis'])
            self.assertFalse(receipt['network_requested'])
            self.assertFalse(receipt['user_tasks_modified'])
            (reports / 'mcp-standard-integration.json').write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--client':
        print(json.dumps(asyncio.run(integration_contract(Path(sys.argv[2]))),
                         ensure_ascii=False))
    else:
        unittest.main()
