"""Persistent, supervised link/MIDI -> review -> editable project coordination.

The host Agent makes musical and lyric decisions. This coordinator exposes the
next concrete call and commits only verified local candidates; it never guesses
that an unknown lyric is correct or silently invokes a semantic provider.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import time
from agent_control import (ControlError, atomic_json, digest, file_lock, file_hash,
                           io_path, safe_path)
from agent_supervision import summarize_export, validate_export_review, SupervisionError


class Workflow:
    def __init__(self, control):
        self.control = control

    def _lock(self, identifier):
        self.control._record_path('workflows', identifier, 'flow')
        return file_lock(self.control.state / 'locks' / (identifier + '.lock'))

    def _read(self, identifier):
        return self.control._record('workflows', identifier, 'flow')

    def _write(self, value, action, **changes):
        value = copy.deepcopy(value)
        value.update(changes, revision=value['revision'] + 1, updated=time.time())
        value.setdefault('history', []).append({'action': action, 'revision': value['revision'],
                                                'at': value['updated']})
        if len(value['history']) > 512:
            raise ControlError('workflow_history_full', '此会话修订过多，请从当前候选新建工作流。')
        atomic_json(self.control._record_path('workflows', value['id'], 'flow'), value)
        return value

    def _expected(self, identifier, revision, *, allow_closed=False, allow_stale=False):
        value = self._read(identifier)
        if type(revision) is not int or revision != value['revision']:
            raise ControlError('workflow_conflict', '工作流版本已改变，请重新读取当前状态。')
        if value['code_revision'] != self.control.code_revision and not allow_stale:
            raise ControlError('stale_workflow', '业务代码已更新，可克隆当前工作流并重新核对保留的 MIDI。')
        if value.get('lifecycle','active')!='active' and not allow_closed:
            raise ControlError('workflow_closed','工作流已关闭或归档；证据保留，可克隆后继续。')
        return value

    def _retain_revision(self,value,reason):
        path=safe_path(self.control.state/'workflow-evidence'/value['id']/('revision-'+str(value['revision'])+'.json'),
                       [self.control.state])
        if io_path(path).exists():
            from agent_control import read_json
            if read_json(path)!=value:
                raise ControlError('workflow_evidence_conflict','旧修订证据路径已有不同内容。')
        else:atomic_json(path,value)
        return {'revision':value['revision'],'stage':value['stage'],'reason':reason,
                'path':str(path),'sha256':file_hash(path),'score_artifact_id':value.get('score_artifact_id'),
                'operation_id':value.get('operation_id')}

    def rebind_score(self,identifier,revision,score_artifact_id,reason):
        """Return to review in the same flow; never discard failed export evidence."""
        if not isinstance(reason,str) or not reason.strip() or len(reason)>8000:
            raise ControlError('rebind_reason_required','请说明替换 MIDI 并重新核对的原因。')
        with self._lock(identifier):
            value=self._expected(identifier,revision)
            if value.get('operation_id'):
                operation=self.control.operation_get(value['operation_id'])
                if operation['state'] in ('queued','running'):
                    raise ControlError('workflow_busy','先取消并等待当前操作结束，再替换 MIDI。')
            artifact=self.control.artifact(score_artifact_id)
            if Path(artifact['path']).suffix.lower() not in ('.mid','.midi'):
                raise ControlError('unsupported_source','换入的输入必须是 MIDI。')
            if artifact['kind']!='input' and not safe_path(artifact['path']).is_relative_to(self.control.state/'outputs'):
                artifact=self.control.import_asset(artifact['path'])
            retained=self._retain_revision(value,reason.strip())
            from agent_segment_workflow import enabled,make_segments
            segment_changes=({'midi_segments':make_segments(self.control,artifact['id'],'midi'),
                'midi_segment_decisions':{},'style_segments':None,'style_segment_decisions':{}}
                if enabled(value) else {})
            clean={key:None for key in ('operation_id','active_plan_id','review_receipt','reviewed_sha256',
                'export_summary','export_review','previous_export_summary','project_artifact_id',
                'stage_error','unusable_operation_id')}
            self._write(value,'score_rebound',**clean,stage='review',score_artifact_id=artifact['id'],
                project_artifacts=[],final_artifacts=[],pronunciation_receipts=[],
                recovery_history=[*value.get('recovery_history',[]),retained],rebind_reason=reason.strip(),**segment_changes)
        return self.get(identifier)

    def close(self,identifier,revision,reason,archive=False):
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ControlError('close_reason_required','关闭或归档需要说明原因。')
        with self._lock(identifier):
            value=self._expected(identifier,revision,allow_closed=True,allow_stale=True)
            if value.get('operation_id'):
                operation=self.control.operation_get(value['operation_id'])
                if operation['state'] in ('queued','running'):
                    raise ControlError('workflow_busy','操作仍在运行；先取消并等待结束再关闭。')
            self._retain_revision(value,reason.strip())
            self._write(value,'archived' if archive else 'closed',
                lifecycle='archived' if archive else 'closed',close_reason=reason.strip(),closed_at=time.time())
        return self.get(identifier)

    def list(self,limit=20,offset=0,status=None,query=''):
        from agent_control import read_json
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:
            raise ControlError('invalid_page','工作流列表 limit 为1–100，offset非负。')
        if not isinstance(query,str) or len(query)>1000:
            raise ControlError('invalid_query','查询词需为最多1000字符的文本。')
        wanted=set(status.split(',')) if isinstance(status,str) and status else set()
        rows=[]
        for path in (self.control.state/'workflows').glob('flow_*.json'):
            path=safe_path(path,[self.control.state/'workflows'])
            value=read_json(path);stage=value['stage'];lifecycle=value.get('lifecycle','active')
            operation=self.control.operation_get(value['operation_id']) if value.get('operation_id') else None
            actual=operation.get('state') if operation else stage
            if wanted and not wanted.intersection((stage,lifecycle,actual)):continue
            request=value.get('request',{})
            text=' '.join(str(item) for item in (value['id'],request.get('source',''),request.get('brief',''),
                value.get('job_id',''),value.get('score_artifact_id',''),value.get('project_artifact_id',''),
                *[a.get('id','') for a in value.get('final_artifacts',[])]))
            if query.casefold() not in text.casefold():continue
            rows.append({'id':value['id'],'revision':value['revision'],'stage':stage,'lifecycle':lifecycle,
                'operation_state':actual,'source':request.get('source','')[:1000],
                'brief':request.get('brief','')[:300],'score_artifact_id':value.get('score_artifact_id'),
                'project_artifact_id':value.get('project_artifact_id'),'updated':value.get('updated',0),
                'current_code':value['code_revision']==self.control.code_revision})
        rows.sort(key=lambda row:(row['updated'],row['id']),reverse=True)
        return {'workflows':rows[offset:offset+limit],'total':len(rows),
            'has_more':offset+limit<len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None}

    def clone(self,identifier,revision,score_artifact_id=None,reason='',idempotency_key=None,review_mode=None):
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ControlError('clone_reason_required','请说明克隆原因。')
        with self._lock(identifier):
            value=self._read(identifier)
            if value['revision']!=revision:
                raise ControlError('workflow_conflict','工作流版本已改变，请重新读取。')
            score=score_artifact_id or value.get('score_artifact_id')
            request=value['request']
            key=idempotency_key or digest({'clone_of':identifier,'revision':revision,'score':score,'reason':reason})
            if 'flow_'+digest(key)[:32]==identifier:
                raise ControlError('invalid_idempotency_key','克隆需使用新的工作流重试键。')
            result=self.create(source='' if score else request.get('source',''),options=request.get('options',{}),
                brief=request.get('brief',''),reference_lyrics=request.get('reference_lyrics',''),
                score_artifact_id=score,formats=request.get('formats'),idempotency_key=key,
                review_mode=review_mode or request.get('review_mode','legacy'))
        child=result['id']
        with self._lock(child):
            created=self._read(child)
            if not created.get('cloned_from'):
                self._write(created,'cloned',cloned_from={'workflow_id':identifier,'revision':revision,'reason':reason.strip()},
                            pitch_source_job_id=value.get('job_id') or value.get('pitch_source_job_id'),
                            export_options=copy.deepcopy(value.get('export_options', {})))
        return self.get(child)

    def delivery(self,identifier,revision,destination=None,name=None):
        with self._lock(identifier):
            value=self._expected(identifier,revision,allow_closed=True,allow_stale=True)
            from agent_delivery import build_delivery
            return build_delivery(self.control,value,destination,name)

    _PUBLIC_INITIAL_EXPORT_KEYS = frozenset({
        'language', 'default_bpm', 'svp_version', 'include_instrumental',
        'track_names', 'project_name', 'preserve_pitch', 'pitch_bindings',
        'pitch_artifact_id', 'pitch_source_job_id', 'instrumental_artifact_id',
        'instrumental_source_job_id',
    })

    @staticmethod
    def _normalize_initial_export_options(value):
        """Validate public export intent without resolving or mutating it."""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ControlError('invalid_export_options', '初始导出选项必须是 JSON 对象。')
        try:
            normalized = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError, OverflowError) as error:
            raise ControlError('invalid_export_options', '初始导出选项只能包含有限的 JSON 数据。') from error

        def reject_internal_paths(current):
            if isinstance(current, dict):
                for key, item in current.items():
                    if key == 'instrumental_path':
                        raise ControlError('unsafe_media_path', '请使用伴奏 artifact 或来源任务 ID，不传内部媒体路径。')
                    if key == 'pitch_sources':
                        raise ControlError('unsafe_pitch_path', '请使用 pitch_bindings 或 pitch_source_job_id，不传内部连续音高路径。')
                    if key == 'path' or key.endswith('_path'):
                        raise ControlError('unsafe_export_path', '导出选项必须保留公开的 artifact 或任务 ID，不能包含内部路径。')
                    reject_internal_paths(item)
            elif isinstance(current, list):
                for item in current:
                    reject_internal_paths(item)

        reject_internal_paths(normalized)
        unknown = set(normalized) - Workflow._PUBLIC_INITIAL_EXPORT_KEYS
        if unknown:
            raise ControlError('invalid_export_options', '导出选项含有未支持字段：' + ', '.join(sorted(unknown)))

        def finite_number(name, item, minimum, maximum):
            try:
                finite = type(item) in (int, float) and math.isfinite(float(item))
            except OverflowError:
                finite = False
            if not finite or not minimum <= float(item) <= maximum:
                raise ControlError('invalid_export_options', name + ' 必须是范围内的有限数值。')

        def public_identifier(name, item):
            if (not isinstance(item, str) or not item or item != item.strip()
                    or len(item) > 256 or '\x00' in item
                    or '/' in item or '\\' in item or Path(item).is_absolute()):
                raise ControlError('invalid_export_options', name + ' 必须是公开的 artifact 或任务 ID，不能是路径。')

        if 'language' in normalized and normalized['language'] not in ('zh', 'ja'):
            raise ControlError('invalid_export_options', '导出选项 language 必须是 zh 或 ja。')
        if 'default_bpm' in normalized:
            finite_number('导出选项 default_bpm', normalized['default_bpm'], 1.0, 1000.0)
        if 'svp_version' in normalized and normalized['svp_version'] not in ('1.9', '1.11', '2.0'):
            raise ControlError('invalid_export_options', '导出选项 svp_version 必须是 1.9、1.11 或 2.0。')
        for name in ('preserve_pitch', 'include_instrumental'):
            if name in normalized and type(normalized[name]) is not bool:
                raise ControlError('invalid_options', '导出选项 ' + name + ' 必须是布尔值。')

        bindings_present = 'pitch_bindings' in normalized
        bindings = normalized.get('pitch_bindings')
        single_pitch_present = 'pitch_artifact_id' in normalized
        single_pitch = normalized.get('pitch_artifact_id')
        pitch_job_present = 'pitch_source_job_id' in normalized
        pitch_job = normalized.get('pitch_source_job_id')
        if single_pitch_present:
            public_identifier('pitch_artifact_id', single_pitch)
        if pitch_job_present:
            public_identifier('pitch_source_job_id', pitch_job)
        if bindings_present and single_pitch_present:
            raise ControlError('conflicting_pitch_source', 'pitch_bindings 与 pitch_artifact_id 只能选一个。')
        if bindings_present and pitch_job_present:
            raise ControlError('conflicting_pitch_source', '显式音高绑定与 pitch_source_job_id 只能选一个。')
        if single_pitch_present and pitch_job_present:
            raise ControlError('conflicting_pitch_source', '音高 artifact 与来源任务只能选一个。')
        if normalized.get('preserve_pitch', True) is False and any(
                key in normalized for key in ('pitch_bindings', 'pitch_artifact_id', 'pitch_source_job_id')):
            raise ControlError('conflicting_pitch_source', '关闭连续音高时不能同时绑定音高来源。')
        if bindings_present:
            if not isinstance(bindings, list) or not bindings or len(bindings) > 128:
                raise ControlError('invalid_pitch_bindings', 'pitch_bindings 必须是非空列表，最多 128 轨。')
            indices = set()
            for row in bindings:
                if (not isinstance(row, dict)
                        or set(row) - {'pitch_artifact_id', 'vocal_track_index', 'time_origin_seconds'}):
                    raise ControlError('invalid_pitch_bindings', '每条音高绑定只能包含 artifact、演唱轨序号和可选秒偏移。')
                public_identifier('pitch_bindings[].pitch_artifact_id', row.get('pitch_artifact_id'))
                index = row.get('vocal_track_index', 0)
                if type(index) is not int or index < 0 or index in indices:
                    raise ControlError('invalid_pitch_bindings', '演唱轨序号必须是不重复的非负整数。')
                finite_number('pitch_bindings[].time_origin_seconds',
                              row.get('time_origin_seconds', 0.0), -86400.0, 86400.0)
                indices.add(index)

        instrumental_artifact_present = 'instrumental_artifact_id' in normalized
        instrumental_artifact = normalized.get('instrumental_artifact_id')
        instrumental_job_present = 'instrumental_source_job_id' in normalized
        instrumental_job = normalized.get('instrumental_source_job_id')
        if instrumental_artifact_present:
            public_identifier('instrumental_artifact_id', instrumental_artifact)
        if instrumental_job_present:
            public_identifier('instrumental_source_job_id', instrumental_job)
        if instrumental_artifact_present and instrumental_job_present:
            raise ControlError('conflicting_instrumental_source', '伴奏 artifact 与来源任务只能选一个。')
        if normalized.get('include_instrumental') is False and (
                instrumental_artifact_present or instrumental_job_present):
            raise ControlError('conflicting_instrumental_source', '关闭伴奏时不能同时指定伴奏来源。')

        if 'track_names' in normalized:
            track_names = normalized['track_names']
            if not isinstance(track_names, list) or not track_names:
                raise ControlError('invalid_export_options', '导出选项 track_names 必须是非空字符串列表。')
            for name in track_names:
                if (not isinstance(name, str) or not name.strip() or len(name) > 256
                        or '\x00' in name or '\r' in name or '\n' in name):
                    raise ControlError('invalid_export_options', 'track_names 中的名称必须是非空短文本，且不能包含换行。')
        if 'project_name' in normalized:
            project_name = normalized['project_name']
            if (not isinstance(project_name, str) or not project_name.strip() or len(project_name) > 256
                    or '\x00' in project_name or '\r' in project_name or '\n' in project_name
                    or '/' in project_name or '\\' in project_name):
                raise ControlError('invalid_export_options', '导出选项 project_name 必须是不含路径分隔符的非空短文本。')
        return normalized

    @staticmethod
    def _score_vocal_tracks(score):
        """Count worker-verified scopes; the SDK server has no mido/agent_score import."""
        tracks = {}
        pairing_unverified = False
        for note in score.get('notes', []):
            pairing_unverified = pairing_unverified or bool(note.get('pairing_ambiguous'))
            if note.get('end_tick') is None or int(note.get('duration_tick') or 0) <= 0:
                continue
            key = (note['track_index'], note.get('channel', 0))
            tracks[key] = tracks.get(key, 0) + 1
        return [
            {'midi_track_index': track_index, 'channel': channel, 'note_count': note_count}
            for (track_index, channel), note_count in sorted(tracks.items())
        ], not pairing_unverified

    def _preflight_export_options(self, value, options, *, require_reviewed=False):
        """Check public options against the exact reviewed score before rendering."""
        options = self._normalize_initial_export_options(options) or {}
        artifact_id = value.get('score_artifact_id')
        if not artifact_id:
            return options, [], False
        artifact = self.control.artifact(artifact_id)
        if require_reviewed:
            reviewed_sha256 = value.get('reviewed_sha256')
            receipt = value.get('review_receipt')
            if not reviewed_sha256 or not isinstance(receipt, dict):
                raise ControlError('review_required', '导出前必须完成并保存当前 MIDI 的核对结果。')
            if (receipt.get('artifact_id') != artifact_id
                    or receipt.get('source_sha256') != reviewed_sha256
                    or receipt.get('caller_reviewed') is not True):
                raise ControlError('review_receipt_invalid',
                                   '核对回执与当前 MIDI 或工作流版本不一致，请重新核对。')
        if value.get('reviewed_sha256') and artifact['sha256'] != value['reviewed_sha256']:
            raise ControlError('review_invalidated', '核对后 MIDI 已改变，请重新核对。')

        track_names = options.get('track_names')
        track_count_requested = (
            (track_names is not None and 'svp' in value['request']['formats'])
            or 'pitch_artifact_id' in options
            or bool(options.get('pitch_bindings')))
        if track_count_requested:
            score = self.control.score_read(artifact_id)
            if score.get('source_sha256') != artifact['sha256']:
                raise ControlError('stale_artifact', '乐谱在参数预检期间改变，请重新读取工作流。')
            tracks, track_count_verified = self._score_vocal_tracks(score)
        else:
            tracks, track_count_verified = [], None
        if (track_count_verified and 'svp' in value['request']['formats']
                and track_names is not None and len(track_names) != len(tracks)):
            raise ControlError(
                'track_names_mismatch',
                'track_names 只对应演唱轨；伴奏需通过 instrumental_artifact_id 或 '
                'instrumental_source_job_id 单独指定。',
                {'expected': len(tracks), 'actual': len(track_names),
                 'midi_track_scopes': copy.deepcopy(tracks),
                 'track_count_source': 'control.score_read',
                 'order_verified_by_preflight': False,
                 'remediation': '按 LibreSVIP 实际演唱轨顺序提供等量名称；'
                                '本预检只核验数量，伴奏名称不写入 track_names。'},
            )

        requested_pitch = []
        if 'pitch_artifact_id' in options:
            requested_pitch.append((0, options['pitch_artifact_id']))
        for row in options.get('pitch_bindings', []):
            index = row.get('vocal_track_index', 0)
            if track_count_verified and index >= len(tracks):
                raise ControlError(
                    'invalid_pitch_bindings',
                    'pitch_bindings 的 vocal_track_index 超出当前 MIDI 演唱轨范围。',
                    {'expected_max': len(tracks) - 1, 'actual': index,
                     'midi_track_scopes': copy.deepcopy(tracks),
                     'track_count_source': 'control.score_read'},
                )
            requested_pitch.append((index, row['pitch_artifact_id']))
        for index, pitch_artifact_id in requested_pitch:
            try:
                pitch = self.control.artifact(pitch_artifact_id)
            except ControlError as error:
                raise ControlError(
                    'wrong_pitch_source',
                    '连续音高来源不存在或已改变；请重新导入并核对参考音高。',
                    {'artifact_id': pitch_artifact_id, 'vocal_track_index': index,
                     'cause': error.code},
                ) from error
            if pitch.get('kind') != 'reference_pitch':
                raise ControlError(
                    'wrong_pitch_source',
                    'pitch_artifact_id 与 pitch_bindings 只能引用已校验的参考音高产物。',
                    {'artifact_id': pitch_artifact_id,
                     'vocal_track_index': index, 'actual_kind': pitch.get('kind')},
                )
        return options, tracks, track_count_verified

    def _start_export(self, value, export_options=None, *, operation=None, retry=False):
        """Create a fresh plan from reviewed source; callers still own export review."""
        public_options = (export_options if export_options is not None
                          else value.get('export_options', {}))
        public_options, tracks, track_count_verified = self._preflight_export_options(
            value, public_options, require_reviewed=True)
        options = {'language': value['request']['options'].get('language', 'zh'),
                   **public_options}
        if (value.get('job_id') and options.get('include_instrumental', True) is not False
                and not any(key in options for key in
                            ('instrumental_artifact_id', 'instrumental_source_job_id'))):
            options['instrumental_source_job_id'] = value['job_id']
        pitch_job = value.get('job_id') or value.get('pitch_source_job_id')
        if (pitch_job and options.get('preserve_pitch', True)
                and not any(key in options for key in
                            ('pitch_bindings', 'pitch_artifact_id', 'pitch_source_job_id'))):
            options['pitch_source_job_id'] = pitch_job

        artifact = self.control.artifact(value['score_artifact_id'])
        plan = self.control.make_plan('export', artifact['id'],
                                      formats=value['request']['formats'], options=options)
        previous_operation = operation
        attempt = int(value.get('attempt', 0)) + (1 if retry else 0)
        operation = self.control.start_plan(
            plan['id'], 'export',
            value['id'] + '-export-' + str(attempt) + '-' + str(value['revision']))
        changes = {'stage': 'exporting', 'active_plan_id': plan['id'],
                   'operation_id': operation['id'], 'attempt': attempt,
                   'export_source_sha256': artifact['sha256'],
                   'export_preflight': {
                       'track_count_verified': track_count_verified,
                       'midi_track_scopes': copy.deepcopy(tracks),
                       'order_verified_by_preflight': False,
                   },
                   'unusable_operation_id': None, 'stage_error': None}
        if export_options is not None:
            changes['export_options'] = public_options
        if retry and previous_operation is not None:
            previous = {
                'attempt': value.get('attempt', 0),
                'revision': value['revision'],
                'plan_id': value.get('active_plan_id'),
                'operation_id': value.get('operation_id'),
                'state': previous_operation.get('state'),
                'error': copy.deepcopy(previous_operation.get('error')),
                'stage_error': copy.deepcopy(value.get('stage_error')),
                'export_options': copy.deepcopy(value.get('export_options', {})),
            }
            changes['export_attempt_history'] = (
                copy.deepcopy(value.get('export_attempt_history', [])) + [previous])[-32:]
        self._write(value, 'export_started' if not retry else 'export_repaired', **changes)

    def create(self, source='', options=None, brief='', reference_lyrics='',
               score_artifact_id=None, formats=None, idempotency_key=None, preparation_job_id=None,review_mode='full',
               initial_export_options=None):
        initial_export_options = self._normalize_initial_export_options(initial_export_options)
        if review_mode not in ('full','legacy','segments'):
            raise ControlError('invalid_review_mode','核对模式为full整曲、segments逐段或兼容legacy。')
        if sum(bool(item) for item in (source,score_artifact_id,preparation_job_id))!=1:
            raise ControlError('invalid_source', '请选择歌曲来源、已导入MIDI或已有受管准备任务之一。')
        if not isinstance(brief, str) or not isinstance(reference_lyrics, str):
            raise ControlError('invalid_context', '创作背景和参考歌词必须是文本。')
        formats = ['svp'] if formats is None else formats
        if not isinstance(formats, list) or not formats or len(set(formats)) != len(formats) or set(formats) - {'svp', 'ustx', 'vsqx'}:
            raise ControlError('invalid_formats', '工程格式支持 svp、ustx、vsqx，不能重复。')
        if options is not None and not isinstance(options, dict):
            raise ControlError('invalid_options', '准备选项必须是对象。')
        if brief and 'svp' not in formats:
            raise ControlError('style_requires_svp', '语义风格需要同时导出 SVP 工程。')
        request = {'source': source, 'options': options or {}, 'brief': brief,
                   'reference_lyrics': reference_lyrics, 'score_artifact_id': score_artifact_id,
                   'formats': formats}
        if preparation_job_id:request['preparation_job_id']=preparation_job_id
        request['review_mode'] = review_mode
        if idempotency_key is None and initial_export_options:
            derived = {'request': request, 'initial_export_options': initial_export_options}
        else:
            derived = request
        key = idempotency_key or digest(derived)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ControlError('invalid_idempotency_key', '工作流重试键需为 1–128 字符。')
        identifier = 'flow_' + digest(key)[:32]
        with self._lock(identifier):
            path = self.control._record_path('workflows', identifier, 'flow')
            if io_path(path).is_file():
                previous = self._read(identifier)
                if previous['request'] != request:
                    raise ControlError('idempotency_conflict', '工作流重试键已用于不同请求。')
                if previous.get('initial_export_options', {}) != (initial_export_options or {}):
                    raise ControlError('idempotency_conflict', '工作流重试键已绑定不同的初始导出选项。')
                return self.get(identifier)
            if score_artifact_id:
                artifact = self.control.artifact(score_artifact_id)
                if Path(artifact['path']).suffix.lower() not in ('.mid', '.midi'):
                    raise ControlError('unsupported_source', '已准备输入需为 MIDI 工程。')
                if artifact['kind'] != 'input' and not safe_path(artifact['path']).is_relative_to(self.control.state/'outputs'):
                    score_artifact_id = self.control.import_asset(artifact['path'])['id']
                plan = None
                stage = 'review'
            elif preparation_job_id:
                self.control._record('preparations',preparation_job_id,'job')
                self.control.job_path(preparation_job_id)
                plan=None;stage='created'
            else:
                plan = self.control.preparation_plan(source, options or {})
                stage = 'created'
            value = {'schema': 1, 'id': identifier, 'revision': 0, 'stage': stage,
                     'code_revision': self.control.code_revision, 'request': request,
                     'preparation_plan_id': plan['id'] if plan else None,
                     'score_artifact_id': score_artifact_id, 'created': time.time(),
                     'updated': time.time(), 'attempt': 0, 'history': []}
            if preparation_job_id:value['job_id']=preparation_job_id
            if initial_export_options is not None:
                value['initial_export_options'] = copy.deepcopy(initial_export_options)
                value['export_options'] = copy.deepcopy(initial_export_options)
            if review_mode=='segments' and score_artifact_id:
                from agent_segment_workflow import make_segments
                value['midi_segments']=make_segments(self.control,score_artifact_id,'midi')
            atomic_json(path, value)
        return self.get(identifier)

    def get(self, identifier, include_details=False, detail=None, include_note_ids=False, include_raw_evidence=False):
        value = self._read(identifier)
        stage = value['stage']
        arguments = {'workflow_id': identifier, 'expected_revision': value['revision']}
        operation = None
        if stage in ('preparing', 'reviewing', 'exporting', 'styling','refining'):
            operation = self.control.operation_get(value['operation_id'])
            if operation['state'] in ('queued', 'running'):
                next_step = {'tool': 'vwa_workflow_get', 'arguments': {'workflow_id': identifier},
                             'after_seconds': 1, 'reason': 'owned_operation_running'}
            elif operation['state'] == 'succeeded' and value.get('unusable_operation_id') != operation['id']:
                next_step = {'tool': 'vwa_workflow_advance', 'arguments': arguments,
                             'reason': 'consume_verified_operation_result'}
            else:
                next_step = {'tool': 'vwa_workflow_advance', 'arguments': {**arguments, 'retry': True},
                             'reason': 'inspect_failure_then_retry',
                             'error': value.get('stage_error') or operation.get('error')}
        elif stage == 'review':
            next_step = {'tool': 'vwa_workflow_review', 'arguments': {'workflow_id': identifier},
                         'reason': 'agent_must_review_notes_and_lyrics'}
        elif stage == 'project_ready' and not self._export_accepted(value):
            summary = value.get('export_summary') or {}
            next_step = {'tool':'vwa_workflow_advance','arguments':arguments,
                'reason':'review_export_report' if summary.get('can_accept') else 'resolve_export_blockers',
                'report_sha256':summary.get('report_sha256'),
                'pronunciation_reads':[{'tool':'vwa_pronunciation_read','arguments':{'artifact_id':row['artifact_id']}}
                    for row in summary.get('candidates',[]) if row['format'] in ('svp','vsqx')],
                'required_input':{'export_review':{'decision':['accept','needs_revision'],
                    'rationale':'核对导出后的音符、歌词与实际时间后填写理由。',
                    'accepted_loss_codes':summary.get('required_loss_codes',[])}}}
        elif stage == 'project_ready' and value['request']['brief']:
            next_step = {'tool': 'vwa_context_read', 'arguments': {'artifact_id': value['project_artifact_id']},
                         'then': 'vwa_workflow_advance with an explicit style_proposal, or finish=true',
                         'reason': 'caller_supplied_semantic_style'}
        elif stage == 'ready':
            next_step = {'tool': None, 'reason': 'editable_project_ready',
                         'external_handoff': 'voice_synthesis_if_requested'}
        else:
            next_step = {'tool': 'vwa_workflow_advance', 'arguments': arguments, 'reason': stage}
        if value.get('lifecycle','active')!='active':
            next_step={'tool':'vwa_workflow_clone','arguments':{**arguments,'reason':'从保留证据新建可编辑工作流'},
                       'reason':'workflow_'+value['lifecycle']}
        elif operation and operation['state'] in ('failed','cancelled','interrupted') and value.get('score_artifact_id'):
            error = value.get('stage_error') or operation.get('error')
            error_code = error.get('code') if isinstance(error, dict) else None
            if stage == 'exporting' and error_code in (
                    'track_names_mismatch','invalid_pitch_bindings','wrong_pitch_source'):
                next_step['repair'] = {
                    'tool': 'vwa_workflow_advance',
                    'required_arguments': {
                        **arguments, 'retry': True,
                        'export_options': copy.deepcopy(value.get('export_options', {})),
                    },
                    'reason': 'reuse_reviewed_source_with_corrected_export_options',
                    'instruction': '修正当前导出参数后显式重试；无需重新核对音符和歌词。',
                }
            next_step['alternatives']=[{'tool':'vwa_workflow_rebind_score',
                'required_arguments':{**arguments,'score_artifact_id':'art_corrected','reason':'修正后重新核对'},
                'reason':'retry_repeats_the_saved_plan_rebind_uses_a_new_score'}]
        from agent_segment_workflow import enabled,progress
        if enabled(value):
            kind='style' if value.get('style_segments') and stage in ('project_ready','styling','ready') else 'midi'
            value={**value,'review_progress':progress(value,kind)}
            if stage=='review' and value['review_progress']['current']:
                next_step['arguments']['segment_id']=value['review_progress']['current']['id']
            if stage=='project_ready' and self._export_accepted(value) and value['request']['brief']:
                p=progress(value,'style')
                next_step=({'tool':'vwa_workflow_advance','arguments':arguments,'reason':'all_style_segments_checked'}
                    if p['complete'] else {'tool':'vwa_workflow_style_review',
                        'arguments':{'workflow_id':identifier},'reason':'review_next_style_segment'})
            if not include_details and detail!='full':
                value=copy.deepcopy(value)
                for key in ('midi_segments','style_segments','midi_segment_decisions','style_segment_decisions'):
                    value.pop(key,None)
        if not include_details and detail!='full':
            value = copy.deepcopy(value)
            if value.get('review_receipt'):
                receipt = value['review_receipt']
                value['review_receipt'] = {k:receipt[k] for k in
                    ('artifact_id','source_sha256','rationale','caller_reviewed') if k in receipt}
                value['review_receipt']['details_available'] = True
            if operation:
                raw = operation
                operation = {k:operation[k] for k in
                    ('id','state','progress','error','cancel_requested','created','updated') if k in operation}
                operation['request'] = {'action':raw.get('request',{}).get('action')}
                operation['details_tool'] = {'tool':'vwa_operations_get',
                                            'arguments':{'operation_id':operation['id']}}
        result={**value, 'operation': operation, 'next': next_step,
                'completed': stage == 'ready', 'semantic_provider': 'caller_supplied_plan'}
        if detail is not None:
            from agent_views import workflow_view
            result=workflow_view(result,'full' if include_details else detail,include_note_ids,include_raw_evidence)
        return result

    @staticmethod
    def _export_accepted(value):
        summary = value.get('export_summary') or {}
        decision = value.get('export_review') or {}
        return bool(summary.get('report_sha256') and decision.get('decision')=='accept'
                    and decision.get('report_sha256')==summary['report_sha256'])

    def review(self, identifier, limit=100, offset=0, detail=None, include_note_ids=False, include_raw_evidence=False,segment_id=None):
        value = self._read(identifier)
        if value['stage'] not in ('review', 'reviewed'):
            raise ControlError('wrong_stage', '先等待准备任务完成并推进到核对阶段。')
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ControlError('invalid_page', '音符分页 limit 为 1–200，offset 非负。')
        artifact = self.control.artifact(value['score_artifact_id'])
        language = value['request']['options'].get('language', 'zh')
        bundle = self.control.call_worker('review_bundle', {'path': artifact['path'],
            'language': language, 'reference_text': value['request']['reference_lyrics'] or None})
        all_notes=bundle['score']['notes']
        notes=all_notes
        from agent_segment_workflow import enabled,select,progress
        segment=select(value,'midi',segment_id) if enabled(value) else None
        if segment:
            allowed=set(segment['note_ids']);notes=[row for row in all_notes if row['note_id'] in allowed]
            first_tick,last_tick=segment.get('start_tick',0),segment.get('end_tick',0)
            for section in ('score_review','lyric_review'):
                bundle[section]['hints']=[row for row in bundle[section].get('hints',[])
                    if (not row.get('note_ids') and not row.get('note_id')) or row.get('note_id') in allowed
                    or bool(set(row.get('note_ids',[]))&allowed)]
            def scoped_events(rows):
                return [row for row in rows if first_tick<=row.get('tick',row.get('onset_tick',-1))<last_tick
                    and row.get('track_index',segment['track_index'])==segment['track_index']]
            for key in ('lyric_events','orphan_lyric_events','ambiguous_lyric_events'):
                bundle['lyrics'][key]=scoped_events(bundle['lyrics'].get(key,[]))
            for track in bundle['lyrics'].get('tracks',[]):
                track['lyric_events']=scoped_events(track.get('lyric_events',[]))
        page = notes[offset:offset+limit]
        page_ids = {row['note_id'] for row in page}
        total_rows = len(notes)
        bundle['score']['notes'] = page
        for section in ('score','lyrics'):
            data = bundle[section]
            if section == 'lyrics':
                data['notes'] = [row for row in data.get('notes',[]) if row['note_id'] in page_ids]
                for key in ('unknown_notes','ambiguous_notes'):
                    data[key] = [row for row in data.get(key,[]) if row['note_id'] in page_ids]
                for key in ('lyric_events','orphan_lyric_events','ambiguous_lyric_events','meta_evidence'):
                    rows = data.get(key,[])
                    total_rows = max(total_rows,len(rows))
                    data[key+'_total'] = len(rows)
                    data[key] = rows[offset:offset+limit]
                    if key == 'meta_evidence':
                        for track in data[key]:
                            track['meta_events_total'] = len(track.get('meta_events',[]))
                            track['meta_events'] = track.get('meta_events',[])[:20]
            for track in data.get('tracks',[]):
                track['note_ids'] = [item for item in track.get('note_ids',[]) if item in page_ids]
                events = track.get('lyric_events',[])
                track['lyric_events_total'] = len(events)
                track['lyric_events'] = events[offset:offset+limit]
        for section in ('score_review','lyric_review'):
            hints = bundle[section].get('hints',[])
            total_rows = max(total_rows,len(hints))
            bundle[section]['hint_count'] = len(hints)
            bundle[section]['hints'] = hints[offset:offset+limit]
            for hint in bundle[section]['hints']:
                refs = hint.get('note_ids',[])
                hint['note_ids_total'] = len(refs)
                hint['note_ids'] = refs[:200]
        references = copy.deepcopy(value.get('references', {}))
        reference_evidence = {}
        if references.get('pitch_artifact_id'):
            reference_evidence['pitch'] = self.control.reference_pitch_review(artifact['id'],
                references['pitch_artifact_id'], references.get('track_indices'),
                references.get('time_origin', 0.0),len(page) or limit,
                all_notes.index(page[0]) if segment and page else offset)
        if references.get('audio_artifact_id'):
            audio = self.control.reference_audio_read(references['audio_artifact_id'])
            reference_evidence['audio'] = audio
            starts = [row['onset_seconds']-references.get('audio_time_origin',0.0)
                      for row in page if row.get('onset_seconds') is not None]
            if starts:
                start = max(0.0, min(starts)-.2)
                end = min(audio['duration_seconds'],start+30,
                    max(row['onset_seconds']+(row.get('duration_seconds') or 0)
                        -references.get('audio_time_origin',0.0) for row in page
                        if row.get('onset_seconds') is not None)+.2)
                if end > start:
                    reference_evidence['audio_excerpt_next'] = {'tool':'vwa_reference_audio_read',
                        'arguments':{'artifact_id':references['audio_artifact_id'],
                                     'start':start,'end':end,'make_excerpt':True}}
        available = self.control.discover_references(value['job_id']) if value.get('job_id') else []
        if references.get('lyrics_artifact_id') and page:
            starts=[row['onset_seconds'] for row in page if row.get('onset_seconds') is not None]
            ends=[row['end_seconds'] for row in page if row.get('end_seconds') is not None]
            first=min(starts) if starts else 0.;last=min(max(ends),first+30.) if ends else first
            if last>first:
                reference_evidence['lyrics_timeline']=self.control.lyric_timeline_review(artifact['id'],
                    references['lyrics_artifact_id'],references.get('lyrics_time_origin',0.),first,last,
                    references.get('track_indices') or ([segment['track_index']] if segment else None),limit,0)
                reference_evidence['lyrics_timeline']['version_note']=references.get('lyric_version_note','')
                if detail!='full' and not include_raw_evidence:
                    timeline=reference_evidence['lyrics_timeline']
                    timeline.pop('notes',None)
                    timeline['note_records_in']='evidence.score.notes'
                    for hint in timeline.get('hints',[]):
                        for key in ('note_text','note_onset_seconds','automatic_patch','reason'):
                            hint.pop(key,None)
        result={'workflow_id': identifier, 'revision': value['revision'],
                'artifact_id': artifact['id'], 'evidence': bundle, 'total_notes': len(notes),
                'references':references,'reference_evidence':reference_evidence,
                'available_references':available,
                'total_evidence_rows':total_rows,
                'next_offset': offset+limit if offset+limit < total_rows else None,
                'full_evidence_tools':['vwa_score_read','vwa_score_review','vwa_lyrics_read','vwa_lyrics_review'],
                'decision_tool': 'vwa_workflow_apply_review',
                'instructions': '先核对音符与歌词；以具体修改或保留理由提交决策。提示不是已证实错误。'}
        if detail is not None:
            from agent_views import review_view
            result=review_view(result,detail,include_note_ids,include_raw_evidence)
        if segment:
            result.update(segment={k:v for k,v in segment.items() if k!='note_ids'},
                segment_note_count=len(notes),total_notes=len(all_notes),review_progress=progress(value,'midi'),
                decision_arguments={'workflow_id':identifier,'expected_revision':value['revision'],
                    'segment_id':segment['id'],'rationale':'请填写本段的具体核对结论。'})
        return result

    def references_set(self, identifier, revision, pitch_artifact_id=None, audio_artifact_id=None,
                       track_indices=None, time_origin=0.0, audio_time_origin=0.0, rationale='',
                       lyrics_artifact_id=None,lyrics_time_origin=0.,lyric_version_note=''):
        if not isinstance(rationale,str) or not rationale.strip() or len(rationale)>8000:
            raise ControlError('reference_rationale_required','请记录参考来源、对齐依据或清除理由。')
        if not isinstance(lyric_version_note,str):
            raise ControlError('lyric_version_note_required','歌词版本核对说明需为文本。')
        for origin in (time_origin,audio_time_origin,lyrics_time_origin):
            if isinstance(origin,bool) or not isinstance(origin,(int,float)) or not math.isfinite(origin) or abs(origin)>86400:
                raise ControlError('invalid_time_origin','参考第0帧在MIDI时间中的位置必须是有限秒数。')
        if track_indices is not None and (not isinstance(track_indices,list) or not track_indices
                or any(type(i) is not int or i<0 for i in track_indices) or len(set(track_indices))!=len(track_indices)):
            raise ControlError('invalid_tracks','参考音轨索引需为不重复的非负整数。')
        with self._lock(identifier):
            value=self._expected(identifier,revision)
            if value['stage'] not in ('review','reviewed'):
                raise ControlError('wrong_stage','只能在MIDI核对阶段设置参考。')
            for reference,kind in ((pitch_artifact_id,'reference_pitch'),(audio_artifact_id,'reference_audio')):
                if reference and self.control.artifact(reference)['kind']!=kind:
                    raise ControlError('wrong_reference_kind','参考产物类型不匹配。')
            if lyrics_artifact_id:
                if self.control.artifact(lyrics_artifact_id)['kind']!='reference_lyric_timeline':
                    raise ControlError('wrong_reference_kind','歌词来源需为已登记的歌词时间轴。')
                if not isinstance(lyric_version_note,str) or not lyric_version_note.strip() or len(lyric_version_note)>4000:
                    raise ControlError('lyric_version_note_required','请记录原唱/版本核对和时间偏移依据，未确认的部分应明确说明。')
            if pitch_artifact_id:
                # Validate scope/alignment against this exact MIDI before accepting the binding.
                self.control.reference_pitch_review(value['score_artifact_id'],pitch_artifact_id,
                    track_indices,time_origin,1,0)
            references={'pitch_artifact_id':pitch_artifact_id,'audio_artifact_id':audio_artifact_id,
                'lyrics_artifact_id':lyrics_artifact_id,'lyrics_time_origin':float(lyrics_time_origin),
                'lyric_version_note':lyric_version_note.strip(),
                'track_indices':track_indices,'time_origin':float(time_origin),
                'audio_time_origin':float(audio_time_origin),'rationale':rationale.strip(),
                'provenance':'caller_supplied_unverified','inference_performed':False}
            from agent_segment_workflow import enabled
            extra={'midi_segment_decisions':{}} if enabled(value) else {}
            self._write(value,'references_set',references=references,stage='review',**extra)
        return self.get(identifier)

    def apply_review(self, identifier, revision, rationale, note_edits=None, lyric_edits=None,
                     structure_edits=None, move_attached_lyrics=False,segment_id=None):
        if type(move_attached_lyrics) is not bool:
            raise ControlError('invalid_options','move_attached_lyrics需为布尔值。')
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 8000:
            raise ControlError('review_rationale_required', '需要记录核对结论与修改/保留理由。')
        with self._lock(identifier):
            value = self._expected(identifier, revision)
            if value['stage'] not in ('review', 'reviewed'):
                raise ControlError('wrong_stage', '当前阶段不能提交音符/歌词核对。')
            arguments = {'workflow_id': identifier, 'expected_revision': revision,
                         'rationale': rationale, 'note_edits': note_edits or [],
                         'lyric_edits': lyric_edits or [], 'structure_edits':structure_edits or [],
                         'artifact_id': value['score_artifact_id']}
            if move_attached_lyrics:arguments['move_attached_lyrics']=True
            from agent_segment_workflow import enabled,select,authorize_edits
            if enabled(value):
                segment=select(value,'midi',segment_id)
                authorize_edits(segment,note_edits or [],lyric_edits or [],structure_edits or [])
                arguments['segment']=segment
            operation = self.control.start_operation('workflow_review', arguments,
                                                     identifier + '-review-' + str(revision))
            self._write(value, 'review_started', stage='reviewing', operation_id=operation['id'])
        return self.get(identifier)

    def advance(self, identifier, revision, style_proposal=None, retry=False, finish=False,
                export_review=None,style_plan_id=None,export_options=None):
        if style_plan_id is not None and style_proposal is not None:
            raise ControlError('conflicting_style_input','风格方案ID和新proposal只能选一个。')
        with self._lock(identifier):
            value = self._expected(identifier, revision)
            stage = value['stage']
            prepared_options = None
            if export_options is not None:
                if stage not in ('created','review','reviewed','exporting'):
                    raise ControlError('wrong_stage','导出选项只能在开始导出、已核对或失败导出处设置。')
                prepared_options = self._normalize_initial_export_options(export_options)
                if stage == 'review' and value.get('score_artifact_id'):
                    self._preflight_export_options(
                        value, prepared_options,
                        require_reviewed=prepared_options.get('track_names') is not None
                        and 'svp' in value['request']['formats'])
                if stage in ('created','review'):
                    value=self._write(value,'export_options_set',export_options=prepared_options)
            if stage == 'created':
                created = {'job_id':value['job_id']} if value.get('job_id') else self.control.preparation_create(value['preparation_plan_id'])
                operation = self.control.preparation_start(created['job_id'], None, identifier+'-prepare-0')
                self._write(value, 'preparation_started', stage='preparing', job_id=created['job_id'],
                            operation_id=operation['id'])
            elif stage in ('preparing', 'reviewing', 'exporting', 'styling','refining'):
                operation = self.control.operation_get(value['operation_id'])
                if operation['state'] in ('queued', 'running'):
                    return self.get(identifier)
                if stage == 'exporting':
                    retryable = (operation['state'] != 'succeeded'
                                 or value.get('unusable_operation_id') == operation['id'])
                    if retryable:
                        if not retry:
                            if export_options is not None:
                                raise ControlError(
                                    'retry_required',
                                    '失败导出修正参数后需同时明确传入 retry=true。')
                            return self.get(identifier)
                        self._start_export(value, prepared_options, operation=operation, retry=True)
                        return self.get(identifier)
                    if export_options is not None:
                        raise ControlError(
                            'operation_result_consumption_required',
                            '当前导出操作已成功，请先消费结果；不能直接重建计划。')
                if operation['state'] != 'succeeded' or value.get('unusable_operation_id') == operation['id']:
                    if not retry:
                        return self.get(identifier)
                    if stage == 'reviewing':
                        # A fresh review decision revalidates the current score.
                        self._write(value, 'review_retry_required', stage='review',
                                    last_error=operation.get('error'))
                    else:
                        attempt = value['attempt'] + 1
                        if stage == 'preparing':
                            new = self.control.preparation_start(value['job_id'], None, identifier+'-prepare-'+str(attempt))
                        elif stage=='refining':
                            new=self.control.start_operation('workflow_pronunciation',operation['request']['arguments'],
                                                             identifier+'-refine-'+str(attempt)+'-'+str(revision))
                        else:
                            new = self.control.start_plan(value['active_plan_id'],
                                'export' if stage == 'exporting' else 'style', identifier+'-'+stage+'-'+str(attempt))
                        changes = {'operation_id':new['id'], 'attempt':attempt,
                                   'unusable_operation_id':None, 'stage_error':None}
                        self._write(value, 'operation_retried', **changes)
                elif stage == 'preparing':
                    result = operation['result']
                    files = result['receipt'].get('files', [])
                    candidates = [row for row in files if Path(row['path']).suffix.lower() in ('.mid', '.midi')]
                    candidates.sort(key=lambda row: (Path(row['path']).name not in ('voices.mid', 'vocals.mid'), row['path']))
                    if not candidates:
                        self._bad_result(value, 'missing_midi', '准备任务没有可验证 MIDI，不能进入核对阶段。')
                    row = candidates[0]
                    if file_hash(row['path']) != row['sha256']:
                        raise ControlError('stale_artifact', '准备输出已经改变，需重新核对准备任务。')
                    imported = self.control.import_asset(row['path'])
                    extra={}
                    from agent_segment_workflow import enabled,make_segments
                    if enabled(value):extra['midi_segments']=make_segments(self.control,imported['id'],'midi')
                    self._write(value, 'prepared_midi_imported', stage='review', score_artifact_id=imported['id'],**extra)
                elif stage == 'reviewing':
                    result = operation['result']
                    if not isinstance(result, dict) or not result.get('artifact_id') or not result.get('source_sha256'):
                        self._bad_result(value, 'missing_review_result', '核对操作缺少已核验的 MIDI 身份。')
                    from agent_segment_workflow import enabled,consume_midi
                    changes=consume_midi(value,result) if enabled(value) else {'stage':'reviewed'}
                    self._write(value, 'review_accepted', score_artifact_id=result['artifact_id'],
                                review_receipt=result, reviewed_sha256=result['source_sha256'],**changes)
                elif stage in ('exporting','refining'):
                    result = operation['result']
                    projects = result['artifacts']
                    if not projects:
                        self._bad_result(value, 'missing_project', '导出没有返回可验证工程。')
                    try:
                        source = self.control.artifact(value['score_artifact_id'])
                        for project in projects:
                            self.control.artifact(project['id'])
                        summary = summarize_export(result, source['sha256'], operation['id'])
                    except (SupervisionError, ControlError) as error:
                        self._bad_result(value, error.code, str(error))
                    selected = next((row for row in projects if Path(row['path']).suffix.lower()=='.svp'), projects[0])
                    from agent_segment_workflow import enabled,make_segments
                    extra={}
                    if enabled(value) and value['request']['brief']:
                        extra={'style_segments':make_segments(self.control,selected['id'],'style'),'style_segment_decisions':{}}
                    self._write(value, 'project_exported', stage='project_ready',
                                project_artifact_id=selected['id'], project_artifacts=projects,
                                export_summary=summary, export_review=None,
                                pronunciation_receipts=result.get('receipt',{}).get('pronunciation_receipts',[]),**extra)
                else:
                    if not operation.get('result',{}).get('artifacts'):
                        self._bad_result(value, 'missing_styled_project', '风格操作没有返回可验证工程。')
                    selected = operation['result']['artifacts'][0]
                    self._write(value, 'styled_project_ready', stage='ready',
                                project_artifact_id=selected['id'], final_artifacts=operation['result']['artifacts'])
            elif stage == 'review':
                return self.get(identifier)
            elif stage == 'reviewed':
                self._start_export(value, prepared_options)
            elif stage == 'project_ready':
                if not self._export_accepted(value):
                    if export_review is None:
                        return self.get(identifier)
                    decision = validate_export_review(value.get('export_summary'), export_review)
                    source = self.control.artifact(value['score_artifact_id'])
                    if source['sha256'] != decision['source_sha256']:
                        raise ControlError('export_review_conflict', '导出核对的源 MIDI 已改变。')
                    for row in decision['candidates']:
                        candidate = self.control.artifact(row['artifact_id'])
                        if candidate['sha256'] != row['sha256']:
                            raise ControlError('export_review_conflict', '导出候选已改变，需重新核对。')
                    decision['reviewed_at'] = time.time()
                    if decision['decision'] == 'needs_revision':
                        self._write(value, 'export_revision_requested', stage='review', export_review=decision,
                            previous_export_summary=copy.deepcopy(value['export_summary']))
                        return self.get(identifier)
                    value = self._write(value, 'export_review_accepted', export_review=decision)
                from agent_segment_workflow import enabled,combined_style,progress
                if enabled(value) and value['request']['brief']:
                    if style_proposal is not None or style_plan_id is not None:
                        raise ControlError('segmented_style_required','逐段模式需先用workflow_style_review/decide完成各段判断，再自动汇总。')
                    if not progress(value,'style')['complete']:
                        if finish:raise ControlError('style_segments_incomplete','风格片段尚未逐段检查完成。')
                        return self.get(identifier)
                    style_proposal=combined_style(value)
                    if style_proposal is None:finish=True
                if style_proposal is not None or style_plan_id is not None:
                    if style_plan_id is None:
                        plan = self.control.make_plan('style', value['project_artifact_id'], proposal=style_proposal)
                    else:
                        plan=self.control.plan_get(style_plan_id)
                        if plan['kind']!='style' or plan['artifact_id']!=value['project_artifact_id']:
                            raise ControlError('wrong_style_source','风格方案必须绑定当前工作流的SVP工程。')
                    operation = self.control.start_plan(plan['id'], 'style', identifier+'-style-'+str(value['revision']))
                    self._write(value, 'style_started', stage='styling', active_plan_id=plan['id'], operation_id=operation['id'])
                elif finish or not value['request']['brief']:
                    self._write(value, 'project_ready', stage='ready',
                                final_artifacts=value['project_artifacts'])
            elif stage != 'ready':
                raise ControlError('wrong_stage', '无法识别工作流阶段。')
        return self.get(identifier)

    def pronunciation_apply(self,identifier,revision,changes,rationale):
        if not isinstance(rationale,str) or not rationale.strip() or len(rationale)>8000:
            raise ControlError('review_rationale_required','请记录明确修改音素的来源和理由。')
        if not isinstance(changes,list) or not 1<=len(changes)<=3:
            raise ControlError('invalid_pronunciation_targets','一次可修改1–3个当前工程。')
        if any(not isinstance(row,dict) or set(row)!={'artifact_id','expected_sha256','edits'}
               or not isinstance(row['artifact_id'],str) or not isinstance(row['expected_sha256'],str)
               or len(row['expected_sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['expected_sha256'])
               or not isinstance(row['edits'],list) or not 1<=len(row['edits'])<=256 for row in changes):
            raise ControlError('invalid_pronunciation_targets','音素目标需明确artifact、预期SHA和非空编辑列表。')
        if len({row['artifact_id'] for row in changes})!=len(changes):
            raise ControlError('invalid_pronunciation_targets','不能重复提交同一工程。')
        with self._lock(identifier):
            value=self._expected(identifier,revision)
            if value['stage']!='project_ready':
                raise ControlError('wrong_stage','请先完成工程导出，再核对并修改其中的显式音素。')
            current={row['id'] for row in value['project_artifacts']}
            for change in changes:
                if change['artifact_id'] not in current:
                    raise ControlError('invalid_pronunciation_targets','只能修改当前工作流导出的工程。')
                artifact=self.control.artifact(change['artifact_id'])
                if Path(artifact['path']).suffix.lower() not in ('.svp','.vsqx'):
                    raise ControlError('unsupported_pronunciation_patch','显式音素修正支持SVP和VSQX。')
                if artifact['sha256']!=change['expected_sha256']:
                    raise ControlError('stale_artifact','工程已改变，请重新读取音素证据。')
            args={'workflow_id':identifier,'score_artifact_id':value['score_artifact_id'],
                'export_operation_id':value['export_summary']['operation_id'],
                'export_report_sha256':value['export_summary']['report_sha256'],
                'changes':copy.deepcopy(changes),'rationale':rationale.strip()}
            operation=self.control.start_operation('workflow_pronunciation',args,identifier+'-pronunciation-'+str(revision))
            self._write(value,'pronunciation_started',stage='refining',operation_id=operation['id'],export_review=None)
        return self.get(identifier)

    def execute_pronunciation(self,operation_id,arguments,output,cancel,progress):
        with self._lock(arguments['workflow_id']):
            value=self._read(arguments['workflow_id'])
            if value['stage']!='refining' or value.get('operation_id')!=operation_id:
                raise ControlError('workflow_conflict','当前音素操作不再属于此工作流。')
            if value['export_summary']['report_sha256']!=arguments['export_report_sha256']:
                raise ControlError('export_review_conflict','导出报告已改变，请重新核对。')
        source=self.control.artifact(arguments['score_artifact_id'])
        original=self.control.operation_get(arguments['export_operation_id'])
        if original['state']!='succeeded':
            raise ControlError('invalid_export_result','缺少成功的当前工程操作。')
        previous=original['result']
        for row in previous['artifacts']:self.control.artifact(row['id'])
        summary=summarize_export(previous,source['sha256'],arguments['export_operation_id'])
        if summary['report_sha256']!=arguments['export_report_sha256']:
            raise ControlError('export_review_conflict','当前工程清单与报告不一致。')
        from agent_refinement import refine_export
        runtime=self.control.config.get('export_runtime',str(self.control.app/'dependencies/export'))
        result=refine_export(source['path'],previous,arguments['changes'],output,export_runtime=runtime,
                             cancel=cancel,progress=progress,rationale=arguments['rationale'])
        artifacts=[]
        for row in result['candidates']:
            candidate=safe_path(row['candidate_path'],[output])
            if file_hash(candidate)!=row['candidate_sha256']:
                raise ControlError('candidate_mismatch','新工程摘要与音素回执不一致。')
            artifacts.append(self.control.register_artifact(candidate))
        return {'receipt':result,'artifacts':artifacts,'handoff':'awaiting_export_review',
                'acoustic_verification':False}

    def _bad_result(self, value, code, message):
        self._write(value, 'operation_result_rejected', unusable_operation_id=value['operation_id'],
                    stage_error={'code':code,'message':message})
        raise ControlError(code, message)

    def execute_review(self, operation_id, arguments, output, cancel, progress):
        """Structure, lyrics, then pitch/time; rebind unchanged notes between candidates."""
        with self._lock(arguments['workflow_id']):
            value = self._read(arguments['workflow_id'])
            if value['stage'] != 'reviewing' or value.get('operation_id') != operation_id:
                raise ControlError('workflow_conflict', '此核对操作不再属于当前工作流。')
        original = self.control.artifact(arguments['artifact_id'])
        current = original
        from agent_score import read_score, plan_patch, apply_patch
        before = read_score(original['path'])
        note_edits = copy.deepcopy(arguments['note_edits'])
        lyric_edits = copy.deepcopy(arguments['lyric_edits'])
        structure_edits = arguments.get('structure_edits',[])
        receipts = []
        def remap(edits, previous, following):
            if not edits:
                return
            def geometry(note):
                return tuple(note[k] for k in ('track_index','channel','pitch','onset_tick','duration_tick'))
            after_ids = {}
            for note in following['notes']:
                after_ids.setdefault(geometry(note),[]).append(note['note_id'])
            old = {note['note_id']:note for note in previous['notes']}
            for edit in edits:
                prior=old.get(edit['note_id'])
                matches=after_ids.get(geometry(prior),[]) if prior else []
                if not matches:
                    raise ControlError('stale_note_id','音符身份无法关联到修正后的 MIDI。')
                if len(matches)!=1:
                    raise ControlError('ambiguous_note_mapping','同形音符无法安全关联组合修改。')
                edit['note_id']=matches[0]
        deleted={edit.get('note_id') for edit in structure_edits if edit.get('operation')=='delete'}
        if deleted & {edit.get('note_id') for edit in note_edits+lyric_edits}:
            raise ControlError('review_edit_conflict','删除目标不能同时提交歌词或音高修改。')
        cancel()
        if structure_edits:
            from agent_structure import plan_structure_patch,apply_structure_patch
            progress('核验并修正音符结构及歌词归属')
            plan=plan_structure_patch(current['path'],current['sha256'],structure_edits)
            receipt=apply_structure_patch(current['path'],plan,output/'structure')
            current=self.control.register_artifact(receipt['candidate_path'])
            receipts.append(receipt)
            after=read_score(current['path'])
            remap(note_edits,before,after)
            remap(lyric_edits,before,after)
            before=after
        cancel()
        if lyric_edits:
            from agent_lyrics import plan_lyrics_patch, apply_lyrics_patch
            progress('核验并修正 MIDI 歌词')
            plan = plan_lyrics_patch(current['path'], current['sha256'], lyric_edits)
            receipt = apply_lyrics_patch(current['path'], plan, output / 'lyrics')
            current = self.control.register_artifact(receipt['candidate_path'])
            receipts.append(receipt)
            if note_edits:
                after = read_score(current['path'])
                remap(note_edits,before,after)
        cancel()
        if note_edits:
            progress('核验并修正 MIDI 音符')
            plan = plan_patch(current['path'], current['sha256'], note_edits,
                              move_attached_lyrics=arguments.get('move_attached_lyrics',False))
            receipt = apply_patch(current['path'], plan, output / 'notes')
            current = self.control.register_artifact(receipt['candidate_path'])
            receipts.append(receipt)
        cancel()
        from agent_lyrics import review_lyrics
        from agent_monophony import monophonic_conflicts
        conflicts=monophonic_conflicts(read_score(current['path']))
        segment=arguments.get('segment')
        scoped_conflicts=conflicts
        if segment:
            scoped_conflicts=[row for row in conflicts if row.get('start_seconds') is not None
                and row['start_seconds']<segment['end_seconds'] and row['end_seconds']>segment['start_seconds']]
        if scoped_conflicts:
            raise ControlError('monophonic_overlap','MIDI含目标声部内的重叠音符，请按冲突双方位置修正后重新核对。',
                {'conflicts':scoped_conflicts,'artifact_id':current['id'],'source_sha256':current['sha256']})
        language = value['request']['options'].get('language','zh')
        result = {'artifact_id':current['id'], 'source_sha256':current['sha256'],
                  'original_artifact_id':original['id'], 'rationale':arguments['rationale'],
                  'receipts':receipts, 'lyric_review':review_lyrics(current['path'],
                      value['request']['reference_lyrics'] or None, language),
                  'caller_reviewed':True, 'semantic_model_used':False}
        if segment:
            from agent_segments import plan_segments
            result.update(segment_plan=plan_segments(read_score(current['path'])),monophonic_conflicts=conflicts,
                segment_completion={'segment_id':segment['id'],'fingerprint':segment['fingerprint'],
                    'edited':bool(note_edits or lyric_edits or structure_edits)})
        atomic_json(output / 'review-receipt.json', result)
        return result
