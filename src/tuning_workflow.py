"""Independent, recoverable SVP tuning sessions over immutable input snapshots."""


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value


import argparse
from contextlib import contextmanager
import copy
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from common import atomic_json,file_hash,native_path,preserve_changed_file
from tuning_engine import settings,digest,process_project

SCHEMA=1
COMMIT_SCHEMA=1
_LOCKS={};_LOCK_GUARD=threading.Lock()
CONTRIBUTORS=('tuning_workflow.py','tuning_engine.py','vocal_expression.py','vocal_timbre.py',
 'expression_component.py','expression_calibration.py','calibration_refinement.py','calibration_workflow.py',
 'calibration_session.py','style_presets.py','style_pitch.py','common.py')


def _read(path):
    return json.loads(Path(native_path(path)).read_text(encoding='utf-8-sig'))


def _raw(path):
    return Path(native_path(path)).read_bytes()


def _exists(path):
    return Path(native_path(path)).exists()


def _isfile(path):
    return Path(native_path(path)).is_file()


def _stat(path):
    return Path(native_path(path)).stat()


def _project(raw):
    value=json.loads(raw.decode('utf-8-sig').rstrip('\x00'))
    if not isinstance(value,dict) or not isinstance(value.get('tracks'),list) or not isinstance(value.get('time'),dict):
        raise ValueError('请选择有效的SVP工程。')
    return value


def _relocate_media(data,source_directory):
    """Keep referenced accompaniment identity when output lives elsewhere."""
    changes=[]
    for index,track in enumerate(data['tracks']):
        audio=track.get('mainRef',{}).get('audio',{})
        filename=audio.get('filename') if isinstance(audio,dict) else None
        if isinstance(filename,str) and filename and not Path(filename).is_absolute():
            resolved=(source_directory/filename).resolve()
            audio['filename']=str(resolved)
            changes.append({'track_index':index,'original_reference':filename,'output_reference':str(resolved),
                            'exists':resolved.is_file(),'audio_modified':False})
    return changes


def inspect_project(path):
    path=Path(path).resolve()
    if path.suffix.lower()!='.svp':raise ValueError('当前调音工作流支持SVP工程；其他工程请先转换并核对参数。')
    raw=_raw(path);data=_project(raw);tracks=[]
    for index,track in enumerate(data['tracks']):
        if not isinstance(track,dict):raise ValueError('SVP轨道格式无效。')
        tracks.append({'track_index':index,'name':track.get('name','演唱轨'),
            'note_count':len(track.get('mainGroup',{}).get('notes',[])),
            'instrumental':bool(track.get('mainRef',{}).get('isInstrumental'))})
    if not any(not row['instrumental'] for row in tracks):raise ValueError('SVP中没有演唱轨。')
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'tracks':tracks}


def job_projects(job):
    job=Path(job).resolve()
    if not (job/'request.json').is_file():raise ValueError('请选择已有准备任务目录。')
    receipt_path=job/'exports/receipts.json'
    receipts=_read(receipt_path) if receipt_path.is_file() else {}
    results=[];seen=set()
    def add(path,label,kind,expected=None):
        path=Path(path).resolve()
        if path in seen or path.suffix.lower()!='.svp' or not path.is_file():return
        seen.add(path);info=inspect_project(path)
        results.append({'path':str(path),'label':label,'kind':kind,'sha256':info['sha256'],
                        'verified':expected is not None and expected==info['sha256']})
    add(job/'exports/voices.svp','原导出工程','baseline',receipts.get('exports/voices.svp',{}).get('sha256'))
    from task_contract import selected
    try:
        manual=selected(job,'project',verify=True)
        if manual:add(manual,'已登记的调音工程（独立导入）','registered',file_hash(manual))
    except ValueError:
        pass
    for name,row in receipts.items():
        if isinstance(row,dict) and row.get('active',True) and canonical_kind(row.get('kind'))=='style_candidate':
            candidate=(job/name).resolve()
            if candidate.is_relative_to(job):add(candidate,'已有风格候选（独立导入）','style_candidate',row.get('sha256'))
    return results


def _owned(root,path):
    root=Path(root).resolve();path=Path(path).absolute()
    if not path.resolve().is_relative_to(root):raise ValueError('调音会话路径越界，已停止。')
    from task_store import checked_tree
    checked_tree(path,root)
    return path


def _commit_path(root,signature):
    return _owned(root,root/'commits'/('tuning-'+signature[:24]+'.json'))


def _object(path):
    try:
        value=_read(path)
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):
        return None


def _observer_error(kind,error):
    return {'kind':kind,'error_type':type(error).__name__,'error':str(error)}


def _notify(progress,text):
    try:progress(text)
    except Exception as error:return _observer_error('progress',error)
    return None


def _status(root,data):
    try:atomic_json(root/'status.json',data)
    except Exception as error:return _observer_error('status',error)
    return None


def _verified_receipt(path,target,signature,spec,options,dependencies):
    try:
        data=_read(path);parsed=_project(_raw(target))
        if (not isinstance(data,dict) or not isinstance(data.get('report'),dict)
                or data.get('signature')!=signature or data.get('source_sha256')!=spec['input_sha256']
                or data.get('options')!=options or data.get('dependencies')!=dependencies
                or Path(data.get('output','')).resolve()!=target.resolve()
                or data.get('output_sha256')!=file_hash(target) or data.get('output_bytes')!=_stat(target).st_size
                or data.get('serialized_content_sha256')!=digest(parsed)):
            return None
        return data
    except (OSError,ValueError,TypeError,KeyError):
        return None


def _intent(root,path,session,spec,options,dependencies,signature,target):
    value=_object(path)
    if not isinstance(value,dict):
        return None
    try:
        relative=Path(value['output'])
        if relative.is_absolute() or _owned(root,root/relative).resolve()!=target.resolve():
            return None
        parsed=_project(_raw(target))
        report=value['report']
        if (value.get('schema')!=COMMIT_SCHEMA or canonical_kind(value.get('kind'))!='vwa.tuning-commit'
                or value.get('session')!=str(Path(session).resolve())
                or value.get('session_id')!=spec['id'] or value.get('signature')!=signature
                or value.get('source_sha256')!=spec['input_sha256']
                or value.get('options')!=options or value.get('dependencies')!=dependencies
                or value.get('output_sha256')!=file_hash(target)
                or value.get('output_bytes')!=_stat(target).st_size
                or value.get('serialized_content_sha256')!=digest(parsed)
                or not isinstance(report,dict) or not isinstance(report.get('status'),str)):
            return None
        return value
    except (OSError,ValueError,TypeError,KeyError):
        return None


def _intent_result(path,spec,options,dependencies,signature,target,receipt,intent):
    return {'schema':SCHEMA,'kind':'vwa.tuning-result','session':str(path),'signature':signature,
        'source_sha256':spec['input_sha256'],'output':str(target),'output_sha256':file_hash(target),
        'output_bytes':_stat(target).st_size,'options':copy.deepcopy(options),'dependencies':copy.deepcopy(dependencies),
        'report':copy.deepcopy(intent['report']),'status':intent['report']['status'],'cached':True,'receipt':str(receipt),
        'preserved_copy':intent.get('preserved_copy'),'preserved_receipt':intent.get('preserved_receipt'),
        'project_commit_count':0,'media_references':copy.deepcopy(intent.get('media_references',[])),
        'serialized_content_sha256':intent['serialized_content_sha256'],'recovered_from_intent':True,
        'commit_state':'committed','receipt_verified':False}


def create_session(project,destination,*,job=None):
    project=Path(project).resolve();info=inspect_project(project)
    raw=_raw(project)
    if hashlib.sha256(raw).hexdigest()!=info['sha256']:raise ValueError('导入期间工程发生变化，请重试。')
    associated=Path(job).resolve() if job else None
    if associated and not (associated/'request.json').is_file():raise ValueError('关联准备任务不存在。')
    destination=Path(destination).absolute()
    if destination.exists():raise FileExistsError('调音会话目录已存在，请选择新目录或继续原会话。')
    destination.mkdir(parents=True,exist_ok=False);root=destination.resolve()
    snapshot=_owned(root,root/'inputs/source.svp');snapshot.parent.mkdir()
    with snapshot.open('xb') as stream:stream.write(raw)
    if _raw(project)!=raw or _raw(snapshot)!=raw:raise ValueError('导入期间工程发生变化，未创建有效会话。')
    spec={'schema':SCHEMA,'kind':'vwa.tuning-session','id':uuid.uuid4().hex,
          'created_at':datetime.now(timezone.utc).isoformat(),'source':str(project),'source_sha256':info['sha256'],
          'input_file':'inputs/source.svp','input_sha256':info['sha256'],'tracks':info['tracks'],
          'job':str(associated) if associated else None}
    atomic_json(root/'session.json',spec)
    atomic_json(root/'status.json',{'state':'ready','session':spec['id']})
    return root/'session.json'


def read_session(session):
    session=Path(session).resolve()
    if session.is_dir():session=session/'session.json'
    spec=_read(session)
    if not isinstance(spec,dict) or spec.get('schema')!=SCHEMA or canonical_kind(spec.get('kind'))!='vwa.tuning-session':
        raise ValueError('调音会话格式无效。')
    snapshot=_owned(session.parent,session.parent/spec['input_file'])
    if file_hash(snapshot)!=spec.get('input_sha256'):raise ValueError('调音输入快照已改变，请重新导入工程。')
    return spec


def _check_source(root,spec):
    snapshot=_owned(root,root/spec['input_file'])
    if file_hash(snapshot)!=spec['input_sha256']:raise ValueError('调音输入快照已改变，未提交。')
    original=Path(spec['source'])
    if original.exists() and file_hash(original)!=spec['source_sha256']:
        raise ValueError('原始工程已在外部改变；请为新版另建调音会话，原会话结果仍保留。')
    return snapshot


def _normalize(options):
    result=settings(options)
    for key in ('reference_calibration','retained_project'):
        if result[key]:result[key]=str(Path(result[key]).resolve())
    for row in result['reference_sources']:
        if isinstance(row,dict) and isinstance(row.get('path'),str):row['path']=str(Path(row['path']).resolve())
    for row in result['retained_sources']:
        if isinstance(row,dict) and isinstance(row.get('project'),str):row['project']=str(Path(row['project']).resolve())
    return result


def _dependencies(options):
    paths=[Path(row['path']) for row in options['reference_sources']]
    if options['retained_project']:paths.append(Path(options['retained_project']))
    paths.extend(Path(row['project']) for row in options['retained_sources'])
    profile=options['reference_calibration']
    if profile:
        from expression_calibration import read_profile
        spec=read_profile(profile);paths.append(Path(profile))
        for row in spec['tracks'].values():
            paths.extend((Path(row['baseline']),*(Path(probe['audio']) for probe in row['probes'])))
    files={str(path.resolve()):file_hash(path) for path in sorted(set(paths))}
    code={name:file_hash(ROOT/name) for name in CONTRIBUTORS}
    return {'files':files,'code':code}


@contextmanager
def _exclusive(root):
    with _LOCK_GUARD:lock=_LOCKS.setdefault(str(root),threading.Lock())
    if not lock.acquire(blocking=False):raise RuntimeError('此调音会话正在运行，请等待或停止当前处理。')
    stream=None;acquired=False
    try:
        stream=_owned(root,root/'run.lock').open('a+b')
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        acquired=True;yield
    finally:
        if stream is not None:
            if acquired:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            stream.close()
        lock.release()


def run_session(session,options,*,cancel=lambda:None,progress=lambda text:None):
    path=Path(session).resolve();path=path/'session.json' if path.is_dir() else path
    root=path.parent;spec=read_session(path);options=_normalize(options)
    with _exclusive(root):
        pending=None;signature=None;committed=False
        try:
            cancel();snapshot=_check_source(root,spec);raw=_raw(snapshot);data=_project(raw)
            deps=_dependencies(options)
            signature=digest({'schema':SCHEMA,'input':spec['input_sha256'],'options':options,'dependencies':deps})
            target=_owned(root,root/'outputs'/('tuned-'+signature[:16]+'.svp'))
            receipt_path=_owned(root,target.with_suffix('.svp.tuning.json'))
            intent_path=_commit_path(root,signature)
            for guarded in (target,receipt_path):
                if _exists(guarded) and not _isfile(guarded):raise ValueError('调音输出路径不是文件，未覆盖。')
            old=_object(receipt_path)
            _status(root,{'state':'running','signature':signature})
            atomic_json(root/'last-options.json',options)
            verified_old=_verified_receipt(receipt_path,target,signature,spec,options,deps) if _isfile(target) else None
            if verified_old is not None:
                _check_source(root,spec)
                if _dependencies(options)!=deps:raise ValueError('调音依赖发生变化，请重试。')
                cancel();result={**verified_old,'cached':True,'receipt':str(receipt_path),'output':str(target),
                    'project_commit_count':0,'commit_state':'receipted','receipt_verified':True,
                    'recovered_from_intent':False,'observer_errors':[]}
                status_error=_status(root,{'state':'done','signature':signature,'output':str(target),'cached':True})
                if status_error:result['observer_errors'].append(status_error)
                observer=_notify(progress,'已复用验证通过的独立调音结果；没有重复叠加。')
                if observer:result['observer_errors'].append(observer)
                return result
            prepared=_intent(root,intent_path,path,spec,options,deps,signature,target) if _isfile(target) else None
            if prepared is not None:
                _check_source(root,spec)
                if _dependencies(options)!=deps:raise ValueError('调音依赖发生变化，请重试。')
                # This output crossed the commit boundary in an earlier run;
                # metadata repair must not turn it into an ordinary failure.
                cancel();committed=True
                result=_intent_result(path,spec,options,deps,signature,target,receipt_path,prepared)
                preserved_receipt=preserve_changed_file(root,receipt_path) if _isfile(receipt_path) else None
                result['preserved_receipt']=preserved_receipt
                observer=_notify(progress,'正在补全已提交调音工程的回执；不会重复叠加。')
                result['observer_errors']=[observer] if observer else []
                receipt_payload={**result,'commit_state':'receipted','receipt_verified':True}
                receipt_error=None
                try:atomic_json(receipt_path,receipt_payload)
                except Exception as error:receipt_error=error
                repaired=_verified_receipt(receipt_path,target,signature,spec,options,deps) if receipt_error is None else None
                if repaired is not None:
                    result=receipt_payload;result['receipt_verified']=True;state='done'
                else:
                    result['receipt_verified']=False;result['commit_state']='committed_unreceipted'
                    result['receipt_error']=_observer_error('receipt',receipt_error or RuntimeError('回执写入后校验失败'))
                    state='committed_unreceipted'
                status_error=_status(root,{'state':state,'signature':signature,'output':str(target),'cached':True,
                    'receipt_verified':result['receipt_verified']})
                if status_error:result['observer_errors'].append(status_error)
                return result
            progress('从固定输入快照开始处理')
            output,report=process_project(data,options,cancel=cancel,progress=progress,cache=root/'cache/features')
            if not isinstance(report,dict) or not isinstance(report.get('status'),str):
                raise ValueError('调音引擎没有返回有效报告，未提交。')
            media=_relocate_media(output,Path(spec['source']).parent)
            payload=raw if output==data else (json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
            parsed=_project(payload)
            if parsed!=output:raise ValueError('调音工程JSON序列化回读不一致，未提交。')
            payload_sha=hashlib.sha256(payload).hexdigest()
            Path(native_path(target.parent)).mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=native_path(target.parent),prefix=target.name+'.',suffix='.pending',delete=False) as stream:
                pending=Path(stream.name);stream.write(payload)
            if file_hash(pending)!=payload_sha:raise ValueError('调音临时文件写入校验失败。')
            _check_source(root,spec)
            if _dependencies(options)!=deps:raise ValueError('调音期间参考音频、配置或算法发生变化，未提交，请重试。')
            cancel();expected=old.get('output_sha256') if isinstance(old,dict) else None
            before_existing=file_hash(target) if _exists(target) else None
            preserved=preserve_changed_file(root,target,expected,payload_sha)
            preserved_receipt=preserve_changed_file(root,receipt_path) if _isfile(receipt_path) else None
            _owned(root,target);_owned(root,receipt_path)
            if (file_hash(target) if _exists(target) else None)!=before_existing:
                raise ValueError('提交前现有工程发生变化，未覆盖；请重试。')
            if _exists(target) and file_hash(target) not in (expected,payload_sha) and not preserved:
                raise ValueError('现有工程发生变化，未覆盖。')
            result={'schema':SCHEMA,'kind':'vwa.tuning-result','session':str(path),'signature':signature,
                'source_sha256':spec['input_sha256'],'output':str(target),'output_sha256':payload_sha,
                'output_bytes':len(payload),'options':copy.deepcopy(options),'dependencies':copy.deepcopy(deps),
                'report':copy.deepcopy(report),'status':report['status'],'cached':False,'receipt':str(receipt_path),
                'preserved_copy':preserved,'preserved_receipt':preserved_receipt,'project_commit_count':1,
                'media_references':media,'serialized_content_sha256':digest(output),'recovered_from_intent':False,
                'commit_state':'committed','receipt_verified':False,'commit_intent':intent_path.relative_to(root).as_posix()}
            intent_doc={'schema':COMMIT_SCHEMA,'kind':'vwa.tuning-commit','session':str(path),'session_id':spec['id'],
                'signature':signature,'source_sha256':spec['input_sha256'],'options':copy.deepcopy(options),
                'dependencies':copy.deepcopy(deps),'output':target.relative_to(root).as_posix(),'output_sha256':payload_sha,
                'output_bytes':len(payload),'serialized_content_sha256':digest(output),'report':copy.deepcopy(report),
                'preserved_copy':preserved,'preserved_receipt':preserved_receipt,'media_references':media}
            atomic_json(intent_path,intent_doc)
            # Recheck cancellation and inputs while the transaction is still abortable.
            cancel();_check_source(root,spec)
            if _dependencies(options)!=deps:raise ValueError('调音提交前参考音频、配置或算法发生变化，未提交，请重试。')
            if (file_hash(target) if _exists(target) else None)!=before_existing:
                raise ValueError('提交意图写入后现有工程发生变化，未覆盖；请重试。')
            if file_hash(pending)!=payload_sha:raise ValueError('调音临时文件在提交前发生变化，未覆盖；请重试。')
            pending.replace(Path(native_path(target)));pending=None
            published=Path(native_path(target))
            if (not published.is_file() or file_hash(target)!=payload_sha or published.stat().st_size!=len(payload)
                    or _project(_raw(target))!=output):
                raise OSError('调音工程发布后哈希校验失败。')
            committed=True
            observer=_notify(progress,'独立调音工程已提交：'+report['status'])
            result['observer_errors']=[observer] if observer else []
            receipt_payload={**result,'commit_state':'receipted','receipt_verified':True}
            receipt_error=None
            try:atomic_json(receipt_path,receipt_payload)
            except Exception as error:receipt_error=error
            verified=_verified_receipt(receipt_path,target,signature,spec,options,deps) if receipt_error is None else None
            if verified is not None:
                result=receipt_payload;result['receipt_verified']=True;state='done'
            else:
                result['receipt_verified']=False;result['commit_state']='committed_unreceipted'
                result['receipt_error']=_observer_error('receipt',receipt_error or RuntimeError('回执写入后校验失败'))
                state='committed_unreceipted'
            status_error=_status(root,{'state':state,'signature':signature,'output':str(target),'cached':False,
                'receipt_verified':result['receipt_verified']})
            if status_error:result['observer_errors'].append(status_error)
            return result
        except BaseException as error:
            state='cancelled' if isinstance(error,InterruptedError) else 'failed'
            if committed and _isfile(target):
                receipt_ok=_verified_receipt(receipt_path,target,signature,spec,options,deps) is not None
                intent_ok=_intent(root,intent_path,path,spec,options,deps,signature,target) is not None
                if receipt_ok:state='done'
                elif intent_ok:state='committed_unreceipted'
            _status(root,{'state':state,'signature':signature,'error_type':type(error).__name__,'error':str(error)})
            raise
        finally:
            if pending is not None:pending.unlink(missing_ok=True)


def register_result(session,*,job=None):
    path=Path(session).resolve();path=path/'session.json' if path.is_dir() else path
    root=path.parent;spec=read_session(path);state=_read(root/'status.json')
    if state.get('state')!='done':raise ValueError('请先完成并核对调音结果，回执待补全时不能登记。')
    signature=state.get('signature')
    if not isinstance(signature,str):raise ValueError('调音状态缺少完整签名，不能登记。')
    output=_owned(root,Path(state.get('output','')))
    target=_owned(root,root/'outputs'/('tuned-'+signature[:16]+'.svp'))
    receipt_path=_owned(root,target.with_suffix('.svp.tuning.json'))
    if output.resolve()!=target.resolve() or not target.is_file():
        raise ValueError('调音状态没有指向已提交的独立候选，不能登记。')
    receipt=_read(receipt_path)
    if (not isinstance(receipt,dict) or receipt.get('schema')!=SCHEMA or canonical_kind(receipt.get('kind'))!='vwa.tuning-result'
            or receipt.get('session')!=str(path) or receipt.get('signature')!=signature
            or receipt.get('receipt')!=str(receipt_path) or receipt.get('report',{}).get('status')!=receipt.get('status')):
        raise ValueError('调音回执结构或会话身份不完整，不能登记。')
    options=_normalize(receipt.get('options'))
    dependencies=_dependencies(options)
    expected=digest({'schema':SCHEMA,'input':spec['input_sha256'],'options':options,'dependencies':dependencies})
    if signature!=expected or receipt.get('dependencies')!=dependencies:
        raise ValueError('调音回执与当前输入、选项或算法依赖不匹配，不能登记。')
    _check_source(root,spec)
    if file_hash(target)!=receipt.get('output_sha256'):
        raise ValueError('调音结果已改变，不能登记；请先核对独立候选。')
    verified=_verified_receipt(receipt_path,target,signature,spec,options,dependencies)
    if verified is None or state.get('receipt_verified') is False:
        raise ValueError('调音回执未通过完整验签，不能登记。')
    job=Path(job or spec.get('job') or '').resolve()
    if not (job/'request.json').is_file():raise ValueError('请选择要登记到的已有准备任务。')
    from task_contract import bind
    bind(job,'project',target)
    return target


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',required=True,type=Path)
    parser.add_argument('--options',required=True,type=Path)
    arguments=parser.parse_args()
    result=run_session(arguments.session,_read(arguments.options),progress=lambda text:print(text,flush=True))
    print(json.dumps(result,ensure_ascii=False),flush=True)
