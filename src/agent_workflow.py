"""Persistent, supervised link/MIDI -> review -> editable project coordination.

The host Agent makes musical and lyric decisions. This coordinator exposes the
next concrete call and commits only verified local candidates; it never guesses
that an unknown lyric is correct or silently invokes a semantic provider.
"""
from __future__ import annotations

import copy
from pathlib import Path
import time
from agent_control import (ControlError, atomic_json, digest, file_lock, file_hash,
                           io_path, safe_path)


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

    def _expected(self, identifier, revision):
        value = self._read(identifier)
        if type(revision) is not int or revision != value['revision']:
            raise ControlError('workflow_conflict', '工作流版本已改变，请重新读取当前状态。')
        if value['code_revision'] != self.control.code_revision:
            raise ControlError('stale_workflow', '业务代码已更新，请从已保留的当前 MIDI 新建工作流。')
        return value

    def create(self, source='', options=None, brief='', reference_lyrics='',
               score_artifact_id=None, formats=None, idempotency_key=None):
        if bool(source) == bool(score_artifact_id):
            raise ControlError('invalid_source', '请选择歌曲链接/本地音乐，或一个已导入的 MIDI，二者选一。')
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
        key = idempotency_key or digest(request)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ControlError('invalid_idempotency_key', '工作流重试键需为 1–128 字符。')
        identifier = 'flow_' + digest(key)[:32]
        with self._lock(identifier):
            path = self.control._record_path('workflows', identifier, 'flow')
            if io_path(path).is_file():
                previous = self._read(identifier)
                if previous['request'] != request:
                    raise ControlError('idempotency_conflict', '工作流重试键已用于不同请求。')
                return self.get(identifier)
            if score_artifact_id:
                artifact = self.control.artifact(score_artifact_id)
                if Path(artifact['path']).suffix.lower() not in ('.mid', '.midi'):
                    raise ControlError('unsupported_source', '已准备输入需为 MIDI 工程。')
                if artifact['kind'] != 'input' and not safe_path(artifact['path']).is_relative_to(self.control.state/'outputs'):
                    score_artifact_id = self.control.import_asset(artifact['path'])['id']
                plan = None
                stage = 'review'
            else:
                plan = self.control.preparation_plan(source, options or {})
                stage = 'created'
            value = {'schema': 1, 'id': identifier, 'revision': 0, 'stage': stage,
                     'code_revision': self.control.code_revision, 'request': request,
                     'preparation_plan_id': plan['id'] if plan else None,
                     'score_artifact_id': score_artifact_id, 'created': time.time(),
                     'updated': time.time(), 'attempt': 0, 'history': []}
            atomic_json(path, value)
        return self.get(identifier)

    def get(self, identifier, include_details=False):
        value = self._read(identifier)
        stage = value['stage']
        arguments = {'workflow_id': identifier, 'expected_revision': value['revision']}
        operation = None
        if stage in ('preparing', 'reviewing', 'exporting', 'styling'):
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
        elif stage == 'project_ready' and value['request']['brief']:
            next_step = {'tool': 'vwa_context_read', 'arguments': {'artifact_id': value['project_artifact_id']},
                         'then': 'vwa_workflow_advance with an explicit style_proposal, or finish=true',
                         'reason': 'caller_supplied_semantic_style'}
        elif stage == 'ready':
            next_step = {'tool': None, 'reason': 'editable_project_ready',
                         'external_handoff': 'voice_synthesis_if_requested'}
        else:
            next_step = {'tool': 'vwa_workflow_advance', 'arguments': arguments, 'reason': stage}
        if not include_details:
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
        return {**value, 'operation': operation, 'next': next_step,
                'completed': stage == 'ready', 'semantic_provider': 'caller_supplied_plan'}

    def review(self, identifier, limit=100, offset=0):
        value = self._read(identifier)
        if value['stage'] not in ('review', 'reviewed'):
            raise ControlError('wrong_stage', '先等待准备任务完成并推进到核对阶段。')
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ControlError('invalid_page', '音符分页 limit 为 1–200，offset 非负。')
        artifact = self.control.artifact(value['score_artifact_id'])
        language = value['request']['options'].get('language', 'zh')
        bundle = self.control.call_worker('review_bundle', {'path': artifact['path'],
            'language': language, 'reference_text': value['request']['reference_lyrics'] or None})
        notes = bundle['score']['notes']
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
        return {'workflow_id': identifier, 'revision': value['revision'],
                'artifact_id': artifact['id'], 'evidence': bundle, 'total_notes': len(notes),
                'total_evidence_rows':total_rows,
                'next_offset': offset+limit if offset+limit < total_rows else None,
                'full_evidence_tools':['vwa_score_read','vwa_score_review','vwa_lyrics_read','vwa_lyrics_review'],
                'decision_tool': 'vwa_workflow_apply_review',
                'instructions': '先核对音符与歌词；以具体修改或保留理由提交决策。提示不是已证实错误。'}

    def apply_review(self, identifier, revision, rationale, note_edits=None, lyric_edits=None):
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 8000:
            raise ControlError('review_rationale_required', '需要记录核对结论与修改/保留理由。')
        with self._lock(identifier):
            value = self._expected(identifier, revision)
            if value['stage'] not in ('review', 'reviewed'):
                raise ControlError('wrong_stage', '当前阶段不能提交音符/歌词核对。')
            arguments = {'workflow_id': identifier, 'expected_revision': revision,
                         'rationale': rationale, 'note_edits': note_edits or [],
                         'lyric_edits': lyric_edits or [], 'artifact_id': value['score_artifact_id']}
            operation = self.control.start_operation('workflow_review', arguments,
                                                     identifier + '-review-' + str(revision))
            self._write(value, 'review_started', stage='reviewing', operation_id=operation['id'])
        return self.get(identifier)

    def advance(self, identifier, revision, style_proposal=None, retry=False, finish=False):
        with self._lock(identifier):
            value = self._expected(identifier, revision)
            stage = value['stage']
            if stage == 'created':
                created = self.control.preparation_create(value['preparation_plan_id'])
                operation = self.control.preparation_start(created['job_id'], None, identifier+'-prepare-0')
                self._write(value, 'preparation_started', stage='preparing', job_id=created['job_id'],
                            operation_id=operation['id'])
            elif stage in ('preparing', 'reviewing', 'exporting', 'styling'):
                operation = self.control.operation_get(value['operation_id'])
                if operation['state'] in ('queued', 'running'):
                    return self.get(identifier)
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
                        else:
                            new = self.control.start_plan(value['active_plan_id'],
                                'export' if stage == 'exporting' else 'style', identifier+'-'+stage+'-'+str(attempt))
                        self._write(value, 'operation_retried', operation_id=new['id'], attempt=attempt,
                                    unusable_operation_id=None, stage_error=None)
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
                    self._write(value, 'prepared_midi_imported', stage='review', score_artifact_id=imported['id'])
                elif stage == 'reviewing':
                    result = operation['result']
                    if not isinstance(result, dict) or not result.get('artifact_id') or not result.get('source_sha256'):
                        self._bad_result(value, 'missing_review_result', '核对操作缺少已核验的 MIDI 身份。')
                    self._write(value, 'review_accepted', stage='reviewed', score_artifact_id=result['artifact_id'],
                                review_receipt=result, reviewed_sha256=result['source_sha256'])
                elif stage == 'exporting':
                    result = operation['result']
                    projects = result['artifacts']
                    if not projects:
                        self._bad_result(value, 'missing_project', '导出没有返回可验证工程。')
                    selected = next((row for row in projects if Path(row['path']).suffix.lower()=='.svp'), projects[0])
                    self._write(value, 'project_exported', stage='project_ready',
                                project_artifact_id=selected['id'], project_artifacts=projects)
                else:
                    if not operation.get('result',{}).get('artifacts'):
                        self._bad_result(value, 'missing_styled_project', '风格操作没有返回可验证工程。')
                    selected = operation['result']['artifacts'][0]
                    self._write(value, 'styled_project_ready', stage='ready',
                                project_artifact_id=selected['id'], final_artifacts=operation['result']['artifacts'])
            elif stage == 'review':
                return self.get(identifier)
            elif stage == 'reviewed':
                artifact = self.control.artifact(value['score_artifact_id'])
                if artifact['sha256'] != value['reviewed_sha256']:
                    raise ControlError('review_invalidated', '核对后 MIDI 已改变，请重新核对。')
                plan = self.control.make_plan('export', artifact['id'], formats=value['request']['formats'],
                    options={'language':value['request']['options'].get('language','zh')})
                operation = self.control.start_plan(plan['id'], 'export', identifier+'-export-'+str(revision))
                self._write(value, 'export_started', stage='exporting', active_plan_id=plan['id'], operation_id=operation['id'])
            elif stage == 'project_ready':
                if style_proposal is not None:
                    plan = self.control.make_plan('style', value['project_artifact_id'], proposal=style_proposal)
                    operation = self.control.start_plan(plan['id'], 'style', identifier+'-style-'+str(revision))
                    self._write(value, 'style_started', stage='styling', active_plan_id=plan['id'], operation_id=operation['id'])
                elif finish or not value['request']['brief']:
                    self._write(value, 'project_ready', stage='ready',
                                final_artifacts=value['project_artifacts'])
            elif stage != 'ready':
                raise ControlError('wrong_stage', '无法识别工作流阶段。')
        return self.get(identifier)

    def _bad_result(self, value, code, message):
        self._write(value, 'operation_result_rejected', unusable_operation_id=value['operation_id'],
                    stage_error={'code':code,'message':message})
        raise ControlError(code, message)

    def execute_review(self, operation_id, arguments, output, cancel, progress):
        """Lyrics first, then remap pitch/timing edits by unchanged note geometry."""
        with self._lock(arguments['workflow_id']):
            value = self._read(arguments['workflow_id'])
            if value['stage'] != 'reviewing' or value.get('operation_id') != operation_id:
                raise ControlError('workflow_conflict', '此核对操作不再属于当前工作流。')
        original = self.control.artifact(arguments['artifact_id'])
        current = original
        from agent_score import read_score, plan_patch, apply_patch
        before = read_score(original['path'])
        note_edits = copy.deepcopy(arguments['note_edits'])
        receipts = []
        cancel()
        if arguments['lyric_edits']:
            from agent_lyrics import plan_lyrics_patch, apply_lyrics_patch
            progress('核验并修正 MIDI 歌词')
            plan = plan_lyrics_patch(current['path'], current['sha256'], arguments['lyric_edits'])
            receipt = apply_lyrics_patch(current['path'], plan, output / 'lyrics')
            current = self.control.register_artifact(receipt['candidate_path'])
            receipts.append(receipt)
            if note_edits:
                after = read_score(current['path'])
                def geometry(note):
                    return tuple(note[k] for k in ('track_index','channel','pitch','onset_tick','duration_tick'))
                after_ids = {}
                for note in after['notes']:
                    key = geometry(note)
                    if key in after_ids:
                        raise ControlError('ambiguous_note_mapping', '同形音符无法安全关联音符与歌词修改。')
                    after_ids[key] = note['note_id']
                old = {note['note_id']:note for note in before['notes']}
                for edit in note_edits:
                    if edit['note_id'] not in old or geometry(old[edit['note_id']]) not in after_ids:
                        raise ControlError('stale_note_id', '音符身份无法关联到歌词修正后的 MIDI。')
                    edit['note_id'] = after_ids[geometry(old[edit['note_id']])]
        cancel()
        if note_edits:
            progress('核验并修正 MIDI 音符')
            plan = plan_patch(current['path'], current['sha256'], note_edits)
            receipt = apply_patch(current['path'], plan, output / 'notes')
            current = self.control.register_artifact(receipt['candidate_path'])
            receipts.append(receipt)
        cancel()
        from agent_lyrics import review_lyrics
        language = value['request']['options'].get('language','zh')
        result = {'artifact_id':current['id'], 'source_sha256':current['sha256'],
                  'original_artifact_id':original['id'], 'rationale':arguments['rationale'],
                  'receipts':receipts, 'lyric_review':review_lyrics(current['path'],
                      value['request']['reference_lyrics'] or None, language),
                  'caller_reviewed':True, 'semantic_model_used':False}
        atomic_json(output / 'review-receipt.json', result)
        return result
