"""Hash-bound, visible export reports and explicit Agent review decisions.

This module only validates evidence already produced by local exporters/audits.
Acknowledging a conversion loss is an Agent decision, not acoustic validation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA = 1
_SHA = re.compile(r'[a-f0-9]{64}')
_INFORMATIONAL = {'vsqx_lyrics_restored_after_dump'}
_ZERO_COUNT_INACTIVE = {'midi_controllers_not_automation'}


class SupervisionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _sha(value, label):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise SupervisionError('invalid_export_report', label + ' 缺少有效 SHA-256。')
    return value


def _path(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise SupervisionError('invalid_export_report', '候选路径必须为绝对路径。')
    return os.path.normcase(str(Path(value).resolve()))


def summarize_export(result, source_sha256, operation_id):
    """Validate the actual receipt, then retain losses in the default flow view."""
    _sha(source_sha256, '源文件')
    if not isinstance(result, dict) or not isinstance(result.get('receipt'), dict):
        raise SupervisionError('invalid_export_report', '导出操作缺少业务回执。')
    outer = result['receipt']
    receipt = outer.get('receipt', outer)
    if not isinstance(receipt, dict) or receipt.get('kind') != 'vwa-export-receipt' or receipt.get('schema_version') != 1:
        raise SupervisionError('invalid_export_report', '导出回执版本或类型无效。')
    receipt_sha = _sha(receipt.get('receipt_sha256'), '回执')
    try:
        calculated = _digest({key:value for key,value in receipt.items() if key != 'receipt_sha256'})
    except (TypeError, ValueError) as error:
        raise SupervisionError('invalid_export_report', '回执包含无效或非有限 JSON 数据。') from error
    if calculated != receipt_sha or receipt.get('source_sha256') != source_sha256:
        raise SupervisionError('export_report_mismatch', '导出回执摘要或源文件修订不匹配。')
    _sha(receipt.get('plan_sha256'), '导出方案')
    rows = receipt.get('candidates')
    artifacts = result.get('artifacts')
    verified = receipt.get('verified')
    if not isinstance(rows, list) or not rows or not isinstance(artifacts, list) or not artifacts or not isinstance(verified, dict):
        raise SupervisionError('invalid_export_report', '导出候选或核验记录不完整。')
    if type(verified.get('notes')) is not int or type(verified.get('lyrics')) is not int:
        raise SupervisionError('invalid_export_report', '音符/歌词核验计数无效。')
    index = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise SupervisionError('invalid_export_report', '产物登记格式无效。')
        key = (_path(artifact.get('path')), _sha(artifact.get('sha256'), '已登记产物'))
        if key in index:
            raise SupervisionError('invalid_export_report', '同一产物重复登记。')
        index[key] = artifact
    warnings = []
    info = []
    blockers = []
    losses = receipt.get('conversion_losses')
    if not isinstance(losses, list):
        raise SupervisionError('invalid_export_report', '回执没有明确的格式转换损失列表。')
    for loss in losses:
        if not isinstance(loss, dict) or not isinstance(loss.get('code'), str) or not loss['code']:
            raise SupervisionError('invalid_export_report', '格式转换损失条目无效。')
        row = copy.deepcopy(loss)
        if (row.get('count') == 0 and row['code'] in _ZERO_COUNT_INACTIVE) or row['code'] in _INFORMATIONAL:
            info.append(row)
        else:
            warnings.append(row)
    candidate_reports = []
    used = set()
    pronunciation={}
    native_rows=outer.get('pronunciation_evidence',[])
    if not isinstance(native_rows,list) or len(native_rows)>3:
        raise SupervisionError('invalid_export_report','读音证据清单无效。')
    for evidence in native_rows:
        if (not isinstance(evidence,dict) or not isinstance(evidence.get('report'),dict)
                or evidence.get('format') not in ('svp','vsqx')
                or not isinstance(evidence.get('source_sha256'),str)
                or evidence['report'].get('source_sha256')!=evidence.get('source_sha256')):
            raise SupervisionError('export_report_mismatch','读音证据与候选摘要不一致。')
        key=(evidence.get('format'),evidence.get('source_sha256'))
        if key in pronunciation:
            raise SupervisionError('export_report_mismatch','读音证据重复。')
        pronunciation[key]=copy.deepcopy(evidence['report'])
    for row in rows:
        if not isinstance(row, dict) or row.get('format') not in ('svp','ustx','vsqx'):
            raise SupervisionError('invalid_export_report', '候选工程格式无效。')
        key = (_path(row.get('candidate_path')), _sha(row.get('candidate_sha256'), '候选工程'))
        artifact = index.get(key)
        if artifact is None or key in used:
            raise SupervisionError('export_report_mismatch', '回执候选与已登记产物不一致。')
        used.add(key)
        codes = row.get('loss_codes')
        if not isinstance(codes, list) or any(not isinstance(code,str) or not code for code in codes):
            raise SupervisionError('invalid_export_report', '候选工程损失代码无效。')
        flags = ('tempo_verified','timing_verified','time_signature_verified')
        if any(type(row.get(flag)) is not bool for flag in flags):
            raise SupervisionError('invalid_export_report', '候选工程的核验状态必须明确。')
        if row.get('verified_notes') != verified['notes'] or row.get('verified_lyrics') != verified['lyrics']:
            blockers.append({'code':'export_content_count_mismatch','format':row['format'],
                             'message':'候选音符或歌词计数与源回执不一致。'})
        if not row['timing_verified']:
            blockers.append({'code':'export_tick_roundtrip_failed','format':row['format'],
                             'message':'导出后的音符 tick 时序未核验通过。'})
        for code in codes:
            warnings.append({'code':code,'format':row['format'],
                'message':{'tempo_table_normalized':'导出速度表结构发生归一化；需查看实际秒数比较。',
                           'time_signature_normalized':'导出拍号结构发生归一化。'}.get(code,code)})
        candidate_reports.append({'artifact_id':artifact['id'],'format':row['format'],
            'sha256':row['candidate_sha256'],'verified_notes':row['verified_notes'],
            'verified_lyrics':row['verified_lyrics'],'tick_roundtrip_verified':row['timing_verified'],
            'tempo_table_equal':row['tempo_verified'],'time_signature_equal':row['time_signature_verified'],
            'expected_tempo':copy.deepcopy(row.get('expected_tempo')),
            'output_tempo':copy.deepcopy(row.get('output_tempo')),'loss_codes':list(codes)})
        candidate_reports[-1]['pronunciation']=pronunciation.pop((row['format'],row['candidate_sha256']),
            {'status':'unknown','reason':'native_phonemes_not_read','acoustic_verification':False})
        if candidate_reports[-1]['pronunciation'].get('evidence_available') is False:
            warnings.append({'code':'native_phonemes_unavailable','format':row['format'],
                'message':'此工程的原生读音结构未展开；保留unknown，不能视为读音通过。'})
    if used != set(index):
        raise SupervisionError('export_report_mismatch', '存在未纳入导出回执的产物。')
    if pronunciation:
        raise SupervisionError('export_report_mismatch','读音证据包含当前清单之外的工程。')
    audit = outer.get('independent_audit')
    if isinstance(audit, dict):
        if audit.get('source_sha256') != source_sha256:
            raise SupervisionError('export_report_mismatch', '独立核对绑定了不同 MIDI 版本。')
        audit_sha=_sha(audit.get('audit_sha256'),'独立核对')
        if _digest({k:v for k,v in audit.items() if k!='audit_sha256'})!=audit_sha:
            raise SupervisionError('export_report_mismatch','独立核对摘要不匹配。')
        audited=audit.get('candidates')
        if not isinstance(audited,list) or len(audited)!=len(rows):
            raise SupervisionError('export_report_mismatch','独立核对候选清单不完整。')
        declared={(row['format'],_path(row['candidate_path']),row['candidate_sha256']) for row in rows}
        observed={(row.get('format'),_path(row.get('candidate_path')),
                   row.get('declared_candidate_sha256',row.get('candidate_sha256'))) for row in audited}
        if declared!=observed:
            raise SupervisionError('export_report_mismatch','独立核对绑定了不同候选工程。')
        blockers.extend(copy.deepcopy(audit.get('blockers', [])))
        warnings.extend(copy.deepcopy(audit.get('warnings', [])))
        if audit.get('status') not in ('verified','differences'):
            warnings.append({'code':'independent_audit_unknown','message':'独立回读包含未知项，需检查来源与限制。'})
    else:
        audit = {'status':'unknown','inference_performed':False}
        warnings.append({'code':'independent_audit_unavailable','message':'尚无独立秒数核对，不能把 tick 核验当作实际时间核验。'})
    for row in warnings + blockers:
        if not isinstance(row,dict) or not isinstance(row.get('code'),str) or not row['code']:
            raise SupervisionError('invalid_export_report', '独立核对提示无效。')
    report = {'schema':SCHEMA,'kind':'vwa.export-supervision','operation_id':operation_id,
        'source_sha256':source_sha256,'plan_sha256':receipt['plan_sha256'],
        'receipt_sha256':receipt_sha,'candidates':candidate_reports,'warnings':warnings,
        'information':info,'blockers':blockers,'independent_audit':copy.deepcopy(audit),
        'required_loss_codes':sorted({row['code'] for row in warnings}),
        'review_required':True,'can_accept':not blockers,'inference_performed':False,
        'instructions':'确认实际音符、歌词和秒数差异；格式损失须逐项认可。未知读音与听感不算通过。'}
    try:report['report_sha256'] = _digest(report)
    except (TypeError,ValueError) as error:
        raise SupervisionError('invalid_export_report','监督报告含无效或非有限JSON数据。') from error
    return report


def validate_export_review(report, decision):
    """Validate one explicit Agent decision against an immutable report version."""
    if not isinstance(report,dict):
        raise SupervisionError('missing_export_report', '缺少导出后核对报告。')
    unsigned = {key:value for key,value in report.items() if key != 'report_sha256'}
    if _digest(unsigned) != report.get('report_sha256'):
        raise SupervisionError('export_report_mismatch', '导出监督报告已改变。')
    if not isinstance(decision,dict) or set(decision) - {'report_sha256','decision','rationale','accepted_loss_codes'}:
        raise SupervisionError('invalid_export_review', '导出核对决策字段无效。')
    if decision.get('report_sha256') != report['report_sha256']:
        raise SupervisionError('export_review_conflict', '导出报告版本已改变，请重新读取。')
    action = decision.get('decision')
    rationale = decision.get('rationale')
    codes = decision.get('accepted_loss_codes',[])
    if action not in ('accept','needs_revision') or not isinstance(rationale,str) or not rationale.strip() or len(rationale)>8000:
        raise SupervisionError('invalid_export_review', '需要明确的导出核对结论与理由。')
    if not isinstance(codes,list) or any(not isinstance(code,str) for code in codes) or len(codes)!=len(set(codes)):
        raise SupervisionError('invalid_export_review', '已认可损失代码必须是不重复的文本列表。')
    required = set(report['required_loss_codes'])
    if set(codes)-required:
        raise SupervisionError('invalid_export_review', '包含当前报告没有要求认可的损失代码。')
    if action == 'accept':
        if not report['can_accept']:
            raise SupervisionError('export_blockers', '导出存在音符、歌词或实际时间的阻断差异，需先修正。')
        if required-set(codes):
            raise SupervisionError('export_losses_unacknowledged', '尚未认可转换损失：'+', '.join(sorted(required-set(codes))))
    return {'report_sha256':report['report_sha256'],'decision':action,'rationale':rationale.strip(),
            'accepted_loss_codes':sorted(codes),'source_sha256':report['source_sha256'],
            'candidates':[{k:row[k] for k in ('artifact_id','format','sha256')} for row in report['candidates']],
            'caller_reviewed':True,'acoustic_validation':False}
