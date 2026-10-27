"""Rebuild an immutable export bundle after explicit pronunciation edits.

Only the native phoneme worker may change a project. A new bundle retains the
MIDI/source plan identity, names every post-process receipt, and is independently
audited again before the coordinator asks for a new export review.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from agent_control import ControlError,atomic_json,file_hash,io_path,safe_path
from agent_exports import candidate_path_for,_publish_exclusive,_existing_file,_digest
from agent_export_audit import audit_export


def collect_pronunciation(candidates,export_runtime,cancel=lambda:None):
    """Keep unsupported native forms explicit without discarding music exports."""
    from agent_pronunciation import read_pronunciation
    evidence=[]
    for row in candidates:
        if row['format'] not in ('svp','vsqx'):continue
        cancel()
        if file_hash(row['candidate_path'])!=row['candidate_sha256']:
            raise ControlError('candidate_mismatch','读取音素前工程摘要已改变。')
        try:
            report=read_pronunciation(row['candidate_path'],limit=50,export_runtime=export_runtime)
            if report.get('source_sha256')!=row['candidate_sha256']:
                raise ControlError('candidate_mismatch','读音证据绑定了不同工程版本。')
            report={**report,'evidence_available':True}
        except (ValueError,OSError) as error:
            if isinstance(error,ControlError):raise
            report={'kind':'vwa-pronunciation-unavailable','format':row['format'],
                'source_sha256':row['candidate_sha256'],'status':'unknown','evidence_available':False,
                'reason':str(error)[:1000],'notes':[],'inference_performed':False,
                'g2p_performed':False,'acoustic_pronunciation_verified':False}
        if file_hash(row['candidate_path'])!=row['candidate_sha256']:
            raise ControlError('candidate_mismatch','读取音素期间工程摘要已改变。')
        evidence.append({'format':row['format'],'source_sha256':row['candidate_sha256'],'report':report})
    return evidence


def refine_export(source_path,previous,changes,output_root,*,export_runtime,cancel=lambda:None,
                  progress=lambda text:None,rationale=''):
    from agent_pronunciation import plan_pronunciation_patch,apply_pronunciation_patch
    root=safe_path(output_root)
    io_path(root).mkdir(parents=True,exist_ok=True)
    body=previous['receipt'].get('receipt',previous['receipt'])
    document=copy.deepcopy(body)
    if _digest({k:v for k,v in body.items() if k!='receipt_sha256'})!=body.get('receipt_sha256'):
        raise ControlError('export_report_mismatch','原工程回执摘要已失效。')
    by_path={str(Path(row['path']).resolve()):row for row in previous['artifacts']}
    requests={row['artifact_id']:row for row in changes}
    if len(requests)!=len(changes) or not requests or set(requests)-{row['id'] for row in by_path.values()}:
        raise ControlError('invalid_pronunciation_targets','音素修改目标必须是当前工程且不能重复。')
    patch_receipts=[];candidates=[]
    for index,old in enumerate(document['candidates']):
        cancel()
        original=safe_path(old['candidate_path'])
        artifact=by_path.get(str(original.resolve()))
        if not artifact or artifact['sha256']!=old['candidate_sha256'] or file_hash(original)!=old['candidate_sha256']:
            raise ControlError('stale_artifact','音素修改前的工程身份不匹配。')
        request=requests.get(artifact['id'])
        raw=io_path(original).read_bytes()
        if request:
            if request['expected_sha256']!=old['candidate_sha256']:
                raise ControlError('stale_artifact','音素核对版本已改变，请重新读取当前工程。')
            progress('核验并更新 '+old['format'].upper()+' 中的显式音素')
            plan=plan_pronunciation_patch(original,old['candidate_sha256'],request['edits'],
                                          export_runtime=export_runtime)
            patch_root=safe_path(root/'patches'/str(index),[root])
            receipt=apply_pronunciation_patch(original,plan,patch_root,
                                              export_runtime=export_runtime)
            patched=safe_path(receipt['candidate_path'],[root])
            raw=io_path(patched).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=receipt['candidate_sha256']:
                raise ControlError('candidate_mismatch','音素候选摘要不一致。')
            patch_receipts.append(receipt)
        sha=hashlib.sha256(raw).hexdigest()
        target=candidate_path_for(root,document['plan_sha256'],old['format'],sha)
        safe_path(target,[root])
        existing=_existing_file(target,'重核对工程')
        if existing is not None and existing!=raw:
            raise ControlError('candidate_collision','重核对候选发生内容碰撞。')
        if existing is None:_publish_exclusive(target,raw)
        row={**old,'candidate_path':str(target),'candidate_sha256':sha,'bytes':len(raw)}
        if request:row['loss_codes']=sorted(set(row.get('loss_codes',[])+['explicit_phonemes_unverified']))
        candidates.append(row)
    cancel()
    document.update(output_root=str(root),candidates=candidates,
        postprocessing=list(document.get('postprocessing',[]))+[{'kind':'explicit_pronunciation_patch',
            'rationale':rationale,'receipts':patch_receipts,'acoustic_verification':False}])
    if not any(row['code']=='explicit_phonemes_unverified' for row in document['conversion_losses']):
        document['conversion_losses'].append({'code':'explicit_phonemes_unverified',
            'message':'音素由调用方明确指定；仅证明写入和音符/歌词保留，未证明声库实际读音正确。'})
    document['receipt_sha256']=_digest({k:v for k,v in document.items() if k!='receipt_sha256'})
    receipt_path=root/'refined-export.receipt.json'
    serialized=(json.dumps(document,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8')
    existing=_existing_file(receipt_path,'重核对回执')
    if existing is not None and existing!=serialized:
        raise ControlError('receipt_collision','重核对回执发生内容碰撞。')
    if existing is None:_publish_exclusive(receipt_path,serialized)
    progress('独立复核修改音素后的音符、歌词与实际秒数')
    audit=audit_export(source_path,document,export_runtime=export_runtime,output_root=root/'audit',cancel=cancel)
    evidence=collect_pronunciation(candidates,export_runtime,cancel)
    if file_hash(source_path)!=document['source_sha256']:
        raise ControlError('stale_artifact','独立复核期间源MIDI已改变。')
    return {'receipt':document,'candidates':candidates,'independent_audit':audit,
            'receipt_path':str(receipt_path),'source_unchanged':True,
            'pronunciation_receipts':patch_receipts,'caller_rationale':rationale,
            'pronunciation_evidence':evidence}
