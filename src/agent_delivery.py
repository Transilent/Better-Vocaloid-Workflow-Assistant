"""Build a verified, immutable delivery bundle for one completed workflow.

The delivery layer only packages evidence that has already passed the workflow
and export supervision checks.  It never runs exporters, models, synthesis, or
external commands.  Every source byte is verified before staging, the bundle is
published as one exclusive directory rename, and an identical retry reuses the
existing manifest instead of overwriting it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid

from agent_control import ControlError, io_path, safe_path


_SCHEMA = 1
_KIND = 'vwa.delivery-manifest'
_MANIFEST = 'manifest.json'
_README = 'README.txt'
_SHA_SUMS = 'SHA256SUMS.txt'
_SHA = re.compile(r'[a-f0-9]{64}')
_PROJECT_SUFFIXES = frozenset({'.svp', '.ustx', '.vsqx'})
_AUDIO_SUFFIXES = frozenset({'.wav', '.flac', '.mp3', '.m4a', '.aac', '.ogg', '.opus'})
_MAX_NAME = 120
_MAX_FILE_BYTES = 512 * 1024 * 1024
_PACKAGE_META = frozenset({_MANIFEST, _README, _SHA_SUMS})
_RESERVED = _PACKAGE_META | frozenset({'tuning-receipt.json'})
_RESERVED_CASEFOLD = frozenset(name.casefold() for name in _RESERVED)
_PACKAGE_META_CASEFOLD = frozenset(name.casefold() for name in _PACKAGE_META)


class DeliveryError(ControlError):
    """Stable delivery error with actionable, JSON-safe context."""

    def __init__(self, code, message, *, details=None, retryable=False,
                 remediation=None, component='delivery'):
        super().__init__(code, message)
        self.details = copy.deepcopy(details or {})
        self.retryable = bool(retryable)
        self.remediation = remediation or '检查工作流与文件身份后重试。'
        self.component = component


def _digest_text(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False,
    ).encode('utf-8')).hexdigest()


def _digest_file(path):
    checker = hashlib.sha256()
    with io_path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            checker.update(block)
    return checker.hexdigest()


def _iso(value):
    if value is None:
        return None
    try:
        return __import__('datetime').datetime.fromtimestamp(
            float(value), __import__('datetime').timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _safe_name(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeliveryError('invalid_delivery_name', '交付名称必须是文本。',
                            remediation='使用 1–120 个文件名安全的字符。')
    value = value.strip()
    if not value or len(value) > _MAX_NAME or value in ('.', '..'):
        raise DeliveryError('invalid_delivery_name', '交付名称长度需为 1–120 个字符。',
                            remediation='使用简短名称，不含路径分隔符。')
    if any(char in value for char in '<>:"/\\|?*') or any(ord(char) < 32 for char in value):
        raise DeliveryError('invalid_delivery_name', '交付名称包含文件名不允许的字符。',
                            remediation='移除路径分隔符、控制字符和 Windows 保留字符。')
    if value.endswith((' ', '.')):
        raise DeliveryError('invalid_delivery_name', '交付名称不能以空格或句点结尾。',
                            remediation='缩短名称并移除末位空格或句点。')
    stem = value.split('.', 1)[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4',
                'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2',
                'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}:
        raise DeliveryError('invalid_delivery_name', '交付名称是 Windows 保留名。',
                            remediation='在名称前添加项目名称。')
    return value


def _is_reserved(name):
    return isinstance(name, str) and name.casefold() in _RESERVED_CASEFOLD


def _sha(value, label):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise DeliveryError('invalid_delivery_evidence', label + '缺少有效 SHA-256。',
                            details={'field': label}, retryable=False,
                            remediation='重新读取工作流并完成对应操作。')
    return value


def _read_json(path, label, maximum=16 * 1024 * 1024):
    path = safe_path(path)
    if not io_path(path).is_file():
        raise DeliveryError('missing_delivery_evidence', label + '不存在。',
                            details={'path': str(path)}, retryable=True,
                            remediation='恢复对应回执或重新生成工作流证据。')
    if io_path(path).stat().st_size > maximum:
        raise DeliveryError('delivery_evidence_too_large', label + '超出读取上限。',
                            details={'path': str(path), 'limit': maximum},
                            remediation='改用较小的原始证据或从工作流重新生成。')
    try:
        value = json.loads(io_path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeliveryError('invalid_delivery_evidence', label + '不是有效 JSON。',
                            details={'path': str(path), 'error': str(error)[:500]},
                            remediation='核对回执文件并重新生成。') from error
    if not isinstance(value, dict):
        raise DeliveryError('invalid_delivery_evidence', label + '根节点必须是对象。',
                            details={'path': str(path)},
                            remediation='核对回执格式。')
    return value


def _accepted(workflow):
    summary = workflow.get('export_summary')
    review = workflow.get('export_review')
    if not isinstance(summary, dict) or not isinstance(review, dict):
        raise DeliveryError('delivery_export_review_required',
                            '工作流缺少导出监督报告或接受决策。',
                            retryable=False,
                            remediation='先完成导出核对并显式 accept。')
    if summary.get('can_accept') is not True or summary.get('blockers'):
        raise DeliveryError('delivery_export_blockers', '导出监督仍有阻断项。',
                            details={'blockers': copy.deepcopy(summary.get('blockers', []))},
                            retryable=False,
                            remediation='先修正导出的音符、歌词或时间差异。')
    expected = _sha(summary.get('report_sha256'), '导出监督报告')
    if review.get('decision') != 'accept' or review.get('report_sha256') != expected:
        raise DeliveryError('delivery_export_review_required',
                            '导出监督尚未对当前报告版本显式接受。',
                            details={'expected_report_sha256': expected,
                                     'actual_report_sha256': review.get('report_sha256'),
                                     'decision': review.get('decision')},
                            retryable=False,
                            remediation='重新读取工作流并对当前导出报告执行 accept。')
    required_value = summary.get('required_loss_codes', [])
    accepted_value = review.get('accepted_loss_codes', [])
    if (not isinstance(required_value, list) or not isinstance(accepted_value, list)
            or any(not isinstance(code, str) or not code for code in required_value + accepted_value)
            or len(set(required_value)) != len(required_value)
            or len(set(accepted_value)) != len(accepted_value)):
        raise DeliveryError('invalid_delivery_evidence',
                            '导出损失认可清单格式无效。', retryable=False,
                            remediation='重新读取当前导出监督报告与接受决策。')
    required = set(required_value)
    accepted_codes = set(accepted_value)
    if required - accepted_codes:
        raise DeliveryError('delivery_export_losses_unacknowledged',
                            '导出转换损失尚未全部认可。',
                            details={'missing_codes': sorted(required - accepted_codes)},
                            retryable=False,
                            remediation='逐项核对并认可当前报告列出的转换损失。')
    return copy.deepcopy(summary), copy.deepcopy(review)


def _artifact(control, identifier, label):
    if not isinstance(identifier, str) or not identifier:
        raise DeliveryError('missing_delivery_artifact', label + '缺少 artifact ID。',
                            retryable=False,
                            remediation='完成对应工作流阶段后重新生成交付。')
    try:
        record = control.artifact(identifier)
    except Exception as error:
        raise DeliveryError('delivery_artifact_invalid', label + '无法验证。',
                            details={'artifact_id': identifier,
                                     'error': getattr(error, 'code', type(error).__name__)},
                            retryable=True,
                            remediation='重新读取 artifact；若文件已改变请重新导出或调音。') from error
    path = safe_path(record.get('path'), [control.state, *control.assets, *control.jobs])
    _sha(record.get('sha256'), label + '的 artifact ')
    if not io_path(path).is_file():
        raise DeliveryError('delivery_artifact_invalid', label + '文件不存在。',
                            details={'artifact_id': identifier, 'path': str(path)},
                            retryable=True, remediation='重新生成对应 artifact。')
    if io_path(path).stat().st_size != record.get('bytes'):
        raise DeliveryError('delivery_artifact_invalid', label + '文件大小已改变。',
                            details={'artifact_id': identifier, 'path': str(path)},
                            retryable=True, remediation='重新读取或重新生成 artifact。')
    actual = _digest_file(path)
    if actual != record['sha256']:
        raise DeliveryError('delivery_artifact_invalid', label + '文件摘要已改变。',
                            details={'artifact_id': identifier, 'path': str(path),
                                     'expected_sha256': record['sha256'],
                                     'actual_sha256': actual},
                            retryable=True, remediation='从当前工作流重新导出或调音。')
    return {**copy.deepcopy(record), 'path': str(path), 'verified_sha256': actual}


def _operation(control, identifier):
    try:
        value = control.operation_get(identifier)
    except Exception as error:
        raise DeliveryError('delivery_operation_invalid', '交付所依赖的操作记录无法读取。',
                            details={'operation_id': identifier,
                                     'error': getattr(error, 'code', type(error).__name__)},
                            retryable=True, remediation='重新读取操作记录并确认已成功。') from error
    if value.get('id') != identifier or value.get('state') != 'succeeded':
        raise DeliveryError('delivery_operation_invalid', '交付所依赖的操作尚未成功。',
                            details={'operation_id': identifier, 'state': value.get('state')},
                            retryable=True, remediation='完成或重试对应操作后再交付。')
    if not isinstance(value.get('result'), dict):
        raise DeliveryError('delivery_operation_invalid', '成功操作缺少结构化结果。',
                            details={'operation_id': identifier},
                            retryable=True, remediation='重新读取操作结果。')
    return copy.deepcopy(value)


def _verify_export_candidates(control, summary, artifacts=None, selected=None):
    verified = {}
    rows = summary.get('candidates')
    if not isinstance(rows, list) or not rows:
        raise DeliveryError('invalid_delivery_evidence', '导出监督报告没有候选清单。',
                            remediation='重新完成导出监督。')
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DeliveryError('invalid_delivery_evidence', '导出候选条目无效。',
                                details={'index': index})
        record = _artifact(control, row.get('artifact_id'), '导出监督候选 ' + str(index + 1))
        if row.get('sha256') != record['sha256']:
            raise DeliveryError('delivery_artifact_invalid', '导出监督候选摘要不一致。',
                                details={'artifact_id': record['id'],
                                         'expected_sha256': row.get('sha256'),
                                         'actual_sha256': record['sha256']},
                                retryable=True,
                                remediation='重新读取当前导出报告。')
        verified[record['id']] = record
    for record in (artifacts or []):
        if record['id'] not in verified:
            raise DeliveryError('delivery_artifact_invalid', '最终工程不在当前导出监督候选中。',
                                details={'artifact_id': record['id']},
                                retryable=False,
                                remediation='重新核对 workflow 的导出候选。')
    if selected is not None:
        source = verified.get(selected['id'])
        if source is None or source['sha256'] != selected['sha256']:
            raise DeliveryError('delivery_artifact_invalid', '最终工程与导出监督报告不一致。',
                                details={'artifact_id': selected['id']},
                                retryable=True, remediation='重新完成导出核对。')
    return verified


def _style_result(control, workflow, selected, exported):
    from agent_project_state import current_style
    binding = current_style(workflow)
    if binding is None:
        return {'applied': False, 'status': 'not_applied',
                'style_status': 'not_applied', 'partial': False,
                'style_partial': False, 'reason': 'workflow_not_styled',
                'partial_reason_counts': {}, 'partial_remediation': [],
                'partial_advice': [], 'media': _media_summary({}),
                'applied_controls': 0, 'skipped_controls': 0,
                'protected_note_count': 0, 'regions': None, 'receipt': None,
                'report_sha256': None, 'session_id': None}, []
    if (binding.get('artifact_id') != selected['id'] or
            ('sha256' in binding and binding['sha256'] != selected['sha256'])):
        raise DeliveryError('delivery_style_evidence_missing', '风格结果身份与当前最终工程不一致。')
    operation_id = binding.get('operation_id')
    if not isinstance(operation_id, str) or not operation_id:
        raise DeliveryError('delivery_style_evidence_missing',
                            '已完成风格调音的工作流缺少操作 ID。',
                            remediation='重新读取完整工作流记录。')
    operation = _operation(control, operation_id)
    result = operation['result']
    rows = result.get('artifacts')
    if not isinstance(rows, list) or not rows:
        raise DeliveryError('delivery_style_evidence_missing', '样式操作没有最终工程。')
    operation_artifacts = [
        _artifact(control, row.get('id') if isinstance(row, dict) else None,
                  '样式操作产物 ' + str(index + 1))
        for index, row in enumerate(rows)]
    if not any(row['id'] == selected['id'] and row['sha256'] == selected['sha256']
               for row in operation_artifacts):
        raise DeliveryError('delivery_style_evidence_missing', '样式操作产物与最终工程不一致。',
                            details={'artifact_id': selected['id']},
                            retryable=True, remediation='重新读取当前工作流。')
    rendered = result.get('receipt')
    if not isinstance(rendered, dict):
        raise DeliveryError('delivery_style_evidence_missing', '样式操作缺少渲染回执。')
    if Path(rendered.get('candidate_path', '')).resolve() != Path(selected['path']).resolve():
        raise DeliveryError('delivery_style_evidence_missing',
                            '样式渲染候选路径与最终工程不一致。')
    if rendered.get('candidate_sha256') != selected['sha256']:
        raise DeliveryError('delivery_style_evidence_missing',
                            '样式渲染候选摘要与最终工程不一致。')
    source_sha = _sha(rendered.get('source_sha256'), '样式源工程')
    sources = [row for row in exported.values()
               if row['sha256'] == source_sha and Path(row['path']).suffix.lower() == '.svp']
    if len(sources) != 1:
        raise DeliveryError('delivery_style_evidence_missing',
                            '样式源工程未唯一绑定到已接受的 SVP 导出。',
                            details={'source_sha256': source_sha, 'matches': len(sources)},
                            retryable=True, remediation='重新生成并核对风格方案。')
    receipt_value = rendered.get('receipt')
    if not isinstance(receipt_value, str):
        raise DeliveryError('delivery_style_evidence_missing', '调音回执路径缺失。')
    receipt_path = safe_path(receipt_value, [control.state, *control.assets, *control.jobs])
    receipt_sha = _sha(rendered.get('receipt_sha256'), '调音回执')
    if not io_path(receipt_path).is_file() or _digest_file(receipt_path) != receipt_sha:
        raise DeliveryError('delivery_style_evidence_missing', '调音回执文件摘要不一致。',
                            details={'path': str(receipt_path), 'expected_sha256': receipt_sha},
                            retryable=True, remediation='恢复当前调音回执。')
    receipt = _read_json(receipt_path, '调音回执')
    if receipt.get('output_sha256') != selected['sha256'] or receipt.get('source_sha256') != source_sha:
        raise DeliveryError('delivery_style_evidence_missing',
                            '调音回执绑定了不同工程版本。',
                            details={'path': str(receipt_path)}, retryable=True,
                            remediation='重新执行风格调音。')
    report = rendered.get('report')
    if not isinstance(report, dict):
        tuning = rendered.get('tuning_result')
        report = tuning.get('report') if isinstance(tuning, dict) else None
    if not isinstance(report, dict) or not isinstance(report.get('status'), str):
        raise DeliveryError('delivery_style_evidence_missing', '调音结果缺少风格报告。')
    style_report = report.get('style') if isinstance(report.get('style'), dict) else {}
    regions = style_report.get('regions') or report.get('style_regions')
    reason_counts = copy.deepcopy(rendered.get('partial_reason_counts')
                                     or report.get('partial_reason_counts', {}))
    if isinstance(style_report.get('partial_reason_counts'), dict):
        reason_counts.update(copy.deepcopy(style_report['partial_reason_counts']))
    style_status = style_report.get('status') or report['status']
    return ({'applied': True, 'status': report['status'],
             'style_status': style_status,
             'partial': report['status'] == 'partial',
             'style_partial': style_status == 'partial',
             'reason': report.get('reason') or style_report.get('reason'),
             'partial_reason_counts': reason_counts,
             'partial_remediation': copy.deepcopy(rendered.get('partial_remediation', [])),
             'partial_advice': copy.deepcopy(rendered.get('partial_advice', [])),
             'media': _media_summary(rendered.get('media_relocation')
                                     if isinstance(rendered.get('media_relocation'), dict) else {}),
             'applied_controls': style_report.get('applied_controls', report.get('applied_controls', 0)),
             'skipped_controls': style_report.get('skipped_controls', report.get('skipped_controls', 0)),
             'protected_note_count': report.get('protected_note_count', style_report.get('protected_note_count', 0)),
             'regions': len(regions) if isinstance(regions, list) else None,
             'report_sha256': report.get('report_sha256'), 'session_id': rendered.get('session_id'),
             'receipt': {'path': str(receipt_path), 'sha256': receipt_sha,
                         'bytes': io_path(receipt_path).stat().st_size}},
            operation_artifacts)


def _score_summary(control, workflow, summary):
    record = _artifact(control, workflow.get('score_artifact_id'), '源 MIDI')
    try:
        score = control.score_read(record['id'])
    except Exception as error:
        raise DeliveryError('delivery_score_audit_failed', '音符、歌词和秒数摘要读取失败。',
                            details={'artifact_id': record['id'],
                                     'error': getattr(error, 'code', type(error).__name__)},
                            retryable=True,
                            remediation='重新读取当前 MIDI 工程后再交付。') from error
    if _digest_file(record['path']) != record['sha256']:
        raise DeliveryError('delivery_artifact_invalid', '生成摘要期间源 MIDI 已改变。',
                            details={'artifact_id': record['id']}, retryable=True,
                            remediation='重新核对工作流。')
    notes = score.get('notes', []) if isinstance(score, dict) else []
    if not isinstance(notes, list):
        raise DeliveryError('delivery_score_audit_failed', '音符读取结果格式无效。')
    lyrics = 0
    starts = []
    ends = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        if note.get('lyric') or note.get('text'):
            lyrics += 1
        start = note.get('start_seconds', note.get('onset_seconds'))
        end = note.get('end_seconds')
        if type(start) in (int, float) and math.isfinite(float(start)):
            starts.append(float(start))
        if type(end) in (int, float) and math.isfinite(float(end)):
            ends.append(float(end))
    timing = score.get('timing', {}) if isinstance(score.get('timing'), dict) else {}
    tempo = timing.get('tempo_events')
    timesig = timing.get('time_signature_events')
    return {'artifact_id': record['id'], 'sha256': record['sha256'],
            'note_count': len(notes), 'lyric_count': lyrics,
            'lyric_coverage': (lyrics / len(notes)) if notes else None,
            'start_seconds': min(starts) if starts else None,
            'end_seconds': max(ends) if ends else None,
            'tempo_count': len(tempo) if isinstance(tempo, list) else None,
            'time_signature_count': len(timesig) if isinstance(timesig, list) else None,
            'export_verified': copy.deepcopy(summary.get('verified', {})),
            'semantic_model_used': False, 'inference_performed': False}


def _export_summary(summary):
    audit = summary.get('independent_audit')
    compact = {'can_accept': summary.get('can_accept'),
               'blockers': len(summary.get('blockers', [])),
               'warnings': len(summary.get('warnings', [])),
               'information': len(summary.get('information', [])),
               'required_loss_codes': copy.deepcopy(summary.get('required_loss_codes', [])),
               'report_sha256': summary.get('report_sha256'),
               'verified': copy.deepcopy(summary.get('verified', {})),
               'continuous_pitch': copy.deepcopy(summary.get('continuous_pitch', {'status':'unknown'}))}
    if isinstance(audit, dict):
        compact['independent'] = {
            'kind': audit.get('kind'), 'status': audit.get('status'),
            'source_sha256': audit.get('source_sha256'),
            'blockers': len(audit.get('blockers', [])),
            'warnings': len(audit.get('warnings', [])),
            'audit_sha256': audit.get('audit_sha256'),
            'acoustic_or_phonetic_verified': audit.get('acoustic_or_phonetic_verified', False)}
    return compact

def _destination(control, workflow, destination, name):
    flow_id = workflow.get('id')
    revision = workflow.get('revision')
    if not isinstance(flow_id, str) or not re.fullmatch(r'flow_[a-f0-9]{32}', flow_id):
        raise DeliveryError('delivery_workflow_invalid', '工作流 ID 格式无效。')
    if type(revision) is not int or revision < 0:
        raise DeliveryError('delivery_workflow_invalid', '工作流 revision 无效。')
    wanted = _safe_name(name)
    if destination is None:
        parent = safe_path(control.state / 'outputs' / 'deliveries' / flow_id,
                           [control.state / 'outputs'])
        target = parent / (str(revision) if wanted is None
                           else str(revision) + '-' + wanted)
        scope = 'managed_default'
    else:
        roots = getattr(control, 'config', {}).get('allowed_delivery_roots') or []
        if not isinstance(roots, list) or any(not isinstance(root, str) or not root for root in roots):
            raise DeliveryError('invalid_delivery_roots',
                                'config.allowed_delivery_roots 必须是绝对路径列表。',
                                remediation='仅配置明确允许的外部交付目录。')
        valid_roots = [safe_path(root) for root in roots]
        requested = safe_path(destination)
        if not any(requested == root or requested.is_relative_to(root) for root in valid_roots):
            raise DeliveryError('outside_delivery_roots',
                                '外部交付目录不在 config.allowed_delivery_roots 内。',
                                details={'destination': str(requested),
                                         'allowed_roots': [str(root) for root in valid_roots]},
                                remediation='选择配置中明确允许的交付目录。')
        if wanted is None:
            parent = requested.parent
            target = requested
        else:
            if io_path(requested / _MANIFEST).is_file():
                raise DeliveryError('delivery_conflict',
                                    '不能在已有交付包内部创建另一个交付包。',
                                    details={'destination': str(requested)},
                                    remediation='保留已有包，并选择其父目录或新目录。')
            parent = requested
            target = requested / wanted
        scope = 'external_allowed_root'
    return parent, target, scope
    return parent, target, scope


def _verify_existing(folder, signature, selected):
    manifest_path = safe_path(folder / _MANIFEST, [folder])
    if not io_path(manifest_path).is_file():
        raise DeliveryError('delivery_conflict', '同名目录缺少交付清单，拒绝覆盖。',
                            details={'target': str(folder)}, retryable=False,
                            remediation='保留现场并使用新名称或新目标。')
    try:
        value = _read_json(manifest_path, '已有交付清单')
    except DeliveryError as error:
        raise DeliveryError('delivery_conflict', '同名目录的交付清单无效，拒绝覆盖。',
                            details={'target': str(folder), 'reason': error.code},
                            retryable=False,
                            remediation='保留现场并使用新名称或新目标。') from error
    if value.get('kind') != _KIND or value.get('schema') != _SCHEMA:
        raise DeliveryError('delivery_conflict', '同名目录不是当前交付格式。',
                            details={'target': str(folder)}, retryable=False,
                            remediation='保留现场并使用新名称。')
    if value.get('request_sha256') != signature:
        raise DeliveryError('delivery_conflict', '同名交付包属于不同请求，拒绝覆盖。',
                            details={'target': str(folder)}, retryable=False,
                            remediation='保留现场并使用新名称或新目标。')
    stored = _sha(value.get('manifest_sha256'), '交付清单')
    if _digest_text({key: item for key, item in value.items()
                     if key != 'manifest_sha256'}) != stored:
        raise DeliveryError('delivery_conflict', '已有交付清单摘要无效，拒绝复用。',
                            details={'target': str(folder)}, retryable=True,
                            remediation='保留现场并重新生成到新目录。')
    rows = value.get('files')
    if not isinstance(rows, list) or not rows:
        raise DeliveryError('delivery_conflict', '已有交付清单缺少文件。',
                            details={'target': str(folder)}, retryable=True,
                            remediation='保留现场并重新生成到新目录。')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise DeliveryError('delivery_conflict', '已有交付清单文件条目无效。')
        filename = _safe_name(row.get('name'))
        if (not filename or filename in seen
                or filename.casefold() in _PACKAGE_META_CASEFOLD):
            raise DeliveryError('delivery_conflict', '已有交付清单文件名称冲突。')
        seen.add(filename.casefold())
        expected = _sha(row.get('sha256'), '已有交付文件 ' + filename)
        path = safe_path(folder / filename, [folder])
        if (not io_path(path).is_file() or io_path(path).stat().st_size != row.get('bytes')
                or _digest_file(path) != expected):
            raise DeliveryError('delivery_conflict', '已有交付文件缺失或摘要不一致。',
                                details={'target': str(folder), 'file': filename},
                                retryable=True,
                                remediation='保留现场并重新生成到新目录。')
    expected_names = seen | _PACKAGE_META_CASEFOLD
    actual_names = set()
    for item in folder.iterdir():
        item = safe_path(item, [folder])
        if not io_path(item).is_file():
            raise DeliveryError('delivery_conflict', '已有交付包包含非普通文件。',
                                details={'target': str(folder), 'name': item.name},
                                retryable=False,
                                remediation='保留现场并使用新名称或新目标。')
        actual_names.add(item.name.casefold())
    if actual_names != expected_names:
        raise DeliveryError('delivery_conflict', '已有交付包包含缺失或未知文件。',
                            details={'target': str(folder),
                                     'extra': sorted(actual_names - expected_names),
                                     'missing': sorted(expected_names - actual_names)},
                            retryable=False,
                            remediation='保留现场并使用新名称或新目标。')
    project = next((row for row in rows if row.get('role') == 'final_project'), None)
    if not project or project.get('source_sha256') != selected['sha256']:
        raise DeliveryError('delivery_conflict', '已有交付包绑定了不同最终工程。',
                            details={'target': str(folder)}, retryable=False,
                            remediation='保留现场并使用新名称。')
    expected_sums = ''.join(row['sha256'] + '  ' + row['name'] + '\n'
                                for row in rows).encode('utf-8')
    sums_path = safe_path(folder / _SHA_SUMS, [folder])
    if not io_path(sums_path).is_file() or io_path(sums_path).read_bytes() != expected_sums:
        raise DeliveryError('delivery_conflict', '已有交付包摘要清单不一致。',
                            details={'target': str(folder)}, retryable=True,
                            remediation='保留现场并重新生成到新目录。')
    readme_path = safe_path(folder / _README, [folder])
    if (not io_path(readme_path).is_file()
            or io_path(readme_path).read_bytes() != _readme(value).encode('utf-8')):
        raise DeliveryError('delivery_conflict', '已有交付包 README 不一致。',
                            details={'target': str(folder)}, retryable=True,
                            remediation='保留现场并重新生成到新目录。')
    return value


def _write_exclusive(path, data):
    path = safe_path(path)
    with io_path(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_verified(source, target, sha):
    if _digest_file(source) != sha:
        raise DeliveryError('delivery_source_changed', '复制前源文件已改变。',
                            details={'source': str(source), 'sha256': sha},
                            retryable=True, remediation='重新读取工作流并重试交付。')
    io_path(target.parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(io_path(source), io_path(target))
    if _digest_file(target) != sha:
        raise DeliveryError('delivery_copy_mismatch', '复制后的交付文件摘要不一致。',
                            details={'source': str(source), 'target': str(target)},
                            retryable=True, remediation='删除本次未完成目录后重试。')


def _media_call(selected, copied_project, control):
    try:
        from agent_media import relocate_svp_media
    except ImportError as error:
        raise DeliveryError('delivery_media_unavailable',
                            '伴奏迁入模块不可用；拒绝生成缺少伴奏证据的包。',
                            retryable=True,
                            remediation='由负责 agent_media 的代理完成后再重试。') from error
    try:
        evidence = relocate_svp_media(
            selected['path'], copied_project,
            allowed_roots=[control.state, *control.assets, *control.jobs, copied_project.parent])
    except Exception as error:
        if isinstance(error, DeliveryError):
            raise
        raise DeliveryError('delivery_media_failed', '伴奏安全迁入失败。',
                            details={'error': getattr(error, 'code', type(error).__name__),
                                     'message': str(error)[:1000]},
                            retryable=True,
                            remediation='核对 SVP 支持的本地伴奏与允许根后再试。') from error
    if evidence is not None and not isinstance(evidence, dict):
        raise DeliveryError('delivery_media_failed', '伴奏迁入证据格式无效。')
    return evidence or {}


def _media_summary(evidence):
    moved = evidence.get('moved') if isinstance(evidence.get('moved'), list) else []
    items = evidence.get('items') if isinstance(evidence.get('items'), list) else []
    return {
        'included': bool(evidence.get('included', evidence.get('instrumental_included', bool(moved)))),
        'count': evidence.get('count', len(items)),
        'reason': evidence.get('reason'),
        'moved': [{key: row.get(key) for key in
                   ('source_track_index', 'candidate_track_index', 'relative_path',
                    'sha256', 'duration_seconds', 'copied', 'reused')
                  if key in row} for row in moved if isinstance(row, dict)],
        'items': [{key: row.get(key) for key in
                   ('track_index', 'track_name', 'relative_path', 'sha256', 'bytes',
                    'duration_seconds', 'sample_rate', 'channels', 'format', 'verified')
                  if key in row} for row in items if isinstance(row, dict)]}


def _readme(manifest):
    style = manifest.get('style') or manifest.get('audit', {}).get('style', {})
    lines = ['# VWA 交付包', '',
             '工作流：`' + manifest['workflow']['id'] + '`',
             '修订：`' + str(manifest['workflow']['revision']) + '`',
             '导出监督：已接受（`' + manifest['export']['report_sha256'] + '`）', '',
             '## 文件', '']
    for row in manifest['files']:
        lines.append('- `' + row['name'] + '` — ' + row['role'] + ' — SHA-256 `'
                     + row['sha256'] + '`')
    lines.extend(['', '## 审计摘要', '',
                  '音符：' + str(manifest['audit']['score']['note_count']),
                  '歌词：' + str(manifest['audit']['score']['lyric_count']),
                  '风格状态：' + str(style.get('status')),
                  '原唱连续音高：' + str(manifest['audit']['export'].get('continuous_pitch', {}).get('status', 'unknown')),
                  '局部完成：' + ('是' if style.get('partial') else '否'), '',
                  '## 未运行项', ''])
    lines.extend('- ' + item for item in manifest['not_run'])
    lines.extend(['', '本包只汇总已验证的本地候选与回执。它不证明声库合成、听感、'
                  '发音正确性、网络平台上传或模型推理已经完成。', ''])
    return '\n'.join(lines)


def build_delivery(control, workflow_value, destination=None, name=None):
    """Return an auditable manifest for a ready, explicitly accepted workflow."""
    if not isinstance(workflow_value, dict):
        raise DeliveryError('delivery_workflow_invalid', '工作流必须是完整对象。')
    if workflow_value.get('stage') != 'ready':
        raise DeliveryError('delivery_workflow_not_ready', '仅完整工作流可交付。',
                            details={'stage': workflow_value.get('stage')},
                            retryable=False,
                            remediation='先完成导出核对与风格阶段。')
    summary, review = _accepted(workflow_value)
    identifier = workflow_value.get('id')
    revision = workflow_value.get('revision')
    final_rows = workflow_value.get('final_artifacts')
    if not isinstance(final_rows, list) or not final_rows:
        final_rows = workflow_value.get('project_artifacts')
    if not isinstance(final_rows, list) or not final_rows:
        raise DeliveryError('delivery_project_invalid', '工作流没有最终工程候选。')
    final_artifacts = [
        _artifact(control, row.get('id') if isinstance(row, dict) else None,
                  '最终工程候选 ' + str(index + 1))
        for index, row in enumerate(final_rows)]
    selected = _artifact(control, workflow_value.get('project_artifact_id'), '最终工程')
    if not any(row['id'] == selected['id'] and row['sha256'] == selected['sha256']
               for row in final_artifacts):
        raise DeliveryError('delivery_project_invalid', '最终工程不在当前最终候选清单中。',
                            details={'artifact_id': selected['id']},
                            remediation='重新读取完整工作流。')
    suffix = Path(selected['path']).suffix.lower()
    if suffix not in _PROJECT_SUFFIXES:
        raise DeliveryError('delivery_project_invalid', '最终工程格式不受支持。',
                            details={'artifact_id': selected['id'], 'suffix': suffix},
                            remediation='使用 SVP、USTX 或 VSQX 最终工程。')
    project_rows = workflow_value.get('project_artifacts')
    project_artifacts = [
        _artifact(control, row.get('id') if isinstance(row, dict) else None,
                  '导出工程候选 ' + str(index + 1))
        for index, row in enumerate(project_rows if isinstance(project_rows, list) else [])]
    exported = _verify_export_candidates(control, summary, project_artifacts)
    style, style_artifacts = _style_result(control, workflow_value, selected, exported)
    if not style['applied']:
        _verify_export_candidates(control, summary, final_artifacts, selected)
    else:
        selected_operation = next((row for row in style_artifacts
                                   if row['id'] == selected['id']), None)
        if selected_operation is None:
            raise DeliveryError('delivery_style_evidence_missing',
                                '最终工程没有通过样式操作重新验证。')
    score = _score_summary(control, workflow_value, summary)
    parent, target, scope = _destination(control, workflow_value, destination, name)
    signature_input = {'workflow': identifier, 'revision': revision,
                              'report_sha256': summary['report_sha256'],
                              'project_artifact_id': selected['id'],
                              'project_sha256': selected['sha256'],
                              'target': str(target)}
    source_evidence = workflow_value.get('source_evidence')
    if source_evidence:
        from agent_preparation import normalize_source_url
        provenance = source_evidence.get('source_provenance', {}) if isinstance(source_evidence, dict) else {}
        audio_sha = source_evidence.get('local_audio_sha256') if isinstance(source_evidence, dict) else None
        if (not isinstance(source_evidence, dict)
                or set(source_evidence) - {'source_provenance', 'local_audio_sha256', 'preparation_request_sha256', 'preparation_plan_sha256'}
                or not isinstance(provenance, dict) or set(provenance) != {'kind', 'url'} or provenance.get('kind') != 'caller_supplied_unverified'
                or normalize_source_url(provenance.get('url')) != provenance.get('url')
                or not isinstance(audio_sha, str) or len(audio_sha) != 64
                or any(c not in '0123456789abcdef' for c in audio_sha)
                or any(not isinstance(source_evidence[k], str) or len(source_evidence[k]) != 64
                       or any(c not in '0123456789abcdef' for c in source_evidence[k])
                       for k in ('preparation_request_sha256', 'preparation_plan_sha256') if k in source_evidence)):
            raise DeliveryError('delivery_source_invalid', '来源声明或本地音频摘要无效。')
        signature_input['source_evidence'] = source_evidence
    signature = _digest_text(signature_input)
    if io_path(target).exists():
        if not io_path(target).is_dir():
            raise DeliveryError('delivery_conflict', '同名交付目标已存在且不是目录。',
                                details={'target': str(target)}, retryable=False,
                                remediation='保留现场并使用新名称或新目标。')
        existing = _verify_existing(target, signature, selected)
        return _public_manifest(existing, target / _MANIFEST, reused=True)
    if io_path(parent).exists() and not io_path(parent).is_dir():
        raise DeliveryError('invalid_delivery_destination', '交付目标父路径不是目录。',
                            details={'path': str(parent)},
                            remediation='选择目录作为交付目标。')
    io_path(parent).mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
    if io_path(temporary).exists():
        raise DeliveryError('delivery_conflict', '临时交付目录已存在，拒绝覆盖。',
                            details={'target': str(temporary)}, retryable=True,
                            remediation='清理本次临时目录后重试。')
    files = []
    try:
        io_path(temporary).mkdir(parents=False, exist_ok=False)
        project_name = _safe_name(name) or Path(selected['path']).name
        if name is not None and Path(project_name).suffix.lower()!=suffix:
            project_name+=suffix
        if (not project_name or _is_reserved(project_name)
                or Path(project_name).suffix.lower() != suffix):
            project_name = 'vocal-project' + suffix
        project_target = temporary / project_name
        _copy_verified(selected['path'], project_target, selected['sha256'])
        files.append({'name': project_name, 'role': 'final_project',
                      'artifact_id': selected['id'], 'source_sha256': selected['sha256'],
                      'sha256': selected['sha256'], 'bytes': selected['bytes']})
        media_evidence = {'status': 'not_applicable'}
        if suffix == '.svp':
            before = set(os.listdir(io_path(temporary)))
            media_evidence = _media_call(selected, project_target, control)
            after = [temporary/name for name in os.listdir(io_path(temporary)) if name not in before]
            for media_path in sorted(after, key=lambda item: item.name):
                media_path = safe_path(media_path, [temporary])
                if not io_path(media_path).is_file():
                    raise DeliveryError('delivery_media_failed', '伴奏迁入产生了非普通文件。',
                                        details={'name': media_path.name}, retryable=True)
                media_name = _safe_name(media_path.name)
                if (not media_name or _is_reserved(media_name)
                        or any(row['name'] == media_name for row in files)):
                    raise DeliveryError('delivery_media_failed', '伴奏迁入文件名称冲突。',
                                        details={'name': media_path.name})
                files.append({'name': media_name, 'role': 'instrumental',
                              'sha256': _digest_file(media_path),
                              'bytes': io_path(media_path).stat().st_size})
        packaged_project_sha = _digest_file(project_target)
        files[0].update(sha256=packaged_project_sha,
                        bytes=io_path(project_target).stat().st_size)
        receipt = style.get('receipt')
        if receipt is not None:
            receipt_target = temporary / 'tuning-receipt.json'
            _copy_verified(receipt['path'], receipt_target, receipt['sha256'])
            files.append({'name': receipt_target.name, 'role': 'tuning_receipt',
                          'sha256': receipt['sha256'], 'bytes': receipt['bytes']})
        style_public = {key: copy.deepcopy(style.get(key)) for key in
                        ('applied', 'status', 'style_status', 'partial', 'style_partial', 'reason',
                         'partial_reason_counts', 'partial_remediation', 'partial_advice',
                         'media', 'applied_controls', 'skipped_controls',
                         'protected_note_count', 'regions', 'session_id', 'report_sha256')}
        manifest = {
            'schema': _SCHEMA, 'kind': _KIND, 'complete': True,
            'request_sha256': signature,
            'workflow': {'id': identifier, 'revision': revision,
                         'stage': workflow_value.get('stage'),
                         'code_revision': workflow_value.get('code_revision')},
            'export': {'report_sha256': summary.get('report_sha256'),
                       'receipt_sha256': summary.get('receipt_sha256'),
                       'reviewed_at': _iso(review.get('reviewed_at')),
                       'accepted_loss_codes': copy.deepcopy(review.get('accepted_loss_codes', [])),
                       'verified': copy.deepcopy(summary.get('verified', {})),
                       'candidates': [{key: row.get(key) for key in
                                       ('artifact_id', 'format', 'sha256')}
                                      for row in summary.get('candidates', [])
                                      if isinstance(row, dict)]},
            'audit': {'score': score, 'style': style_public,
                      'export': _export_summary(summary)},
            'media': {'evidence': _media_summary(media_evidence)},
            'files': files, 'destination_scope': scope,
            'not_run': ['声库合成', '声学试听与发音正确性', '模型推理或自动语义判断',
                        '平台上传或发布'],
            'limitations': ['SHA 与本地回读只证明文件身份及已有审计证据。',
                            '本清单不包含直接执行命令。'],
            'created_at': _iso(workflow_value.get('updated')),
            'source': 'workflow_value'}
        if source_evidence:
            manifest['source_evidence'] = copy.deepcopy(source_evidence)
            manifest['limitations'].append('来源链接由调用方提供，未验证其与本地音频同源。')
        manifest['manifest_sha256'] = _digest_text(manifest)
        sums = ''.join(row['sha256'] + '  ' + row['name'] + '\n'
                       for row in files).encode('utf-8')
        _write_exclusive(temporary / _README, _readme(manifest).encode('utf-8'))
        _write_exclusive(temporary / _SHA_SUMS, sums)
        _write_exclusive(temporary / _MANIFEST, json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, indent=2,
            allow_nan=False).encode('utf-8') + b'\n')
        if io_path(target).exists():
            if not io_path(target).is_dir():
                raise DeliveryError('delivery_conflict', '交付发布目标被非目录占用。',
                                    details={'target': str(target)}, retryable=True)
            existing = _verify_existing(target, signature, selected)
            shutil.rmtree(io_path(temporary))
            return _public_manifest(existing, target / _MANIFEST, reused=True)
        _verify_existing(temporary, signature, selected)
        try:
            os.rename(io_path(temporary), io_path(target))
        except OSError:
            if not io_path(target).is_dir():
                raise
            existing = _verify_existing(target, signature, selected)
            shutil.rmtree(io_path(temporary))
            return _public_manifest(existing, target / _MANIFEST, reused=True)
        return _public_manifest(manifest, target / _MANIFEST, reused=False)
    except Exception:
        if io_path(temporary).exists():
            shutil.rmtree(io_path(temporary), ignore_errors=True)
        raise


def _public_manifest(manifest, manifest_path, *, reused):
    files = copy.deepcopy(manifest.get('files', []))
    for row in files:
        row['path']=str(manifest_path.parent/row['name'])
        row['markdown_link']='['+row['name']+']('+Path(row['path']).as_posix()+')'
    return {'manifest_path': str(manifest_path),
            'manifest_file_sha256':_digest_file(manifest_path),
            'manifest_sha256': manifest.get('manifest_sha256'),
            'sha256': manifest.get('manifest_sha256'), 'files': files,
            'reused': bool(reused), 'complete': manifest.get('complete') is True,
            'summary': {'workflow': copy.deepcopy(manifest.get('workflow')),
                        'export_report_sha256': manifest.get('export', {}).get('report_sha256'),
                        'files': len(files),
                        'style': copy.deepcopy(manifest.get('audit', {}).get('style')),
                        'not_run': copy.deepcopy(manifest.get('not_run', []))},
            'limitations': copy.deepcopy(manifest.get('limitations', []))}


__all__ = ['DeliveryError', 'build_delivery']
