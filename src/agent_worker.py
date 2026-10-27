"""Fixed VWA business dispatch in the application's existing Python environment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
from importlib import metadata
import platform
from agent_control import Control, ControlError, atomic_json, read_json, safe_path


def dispatch(control, action, arguments):
    if action == 'health':
        packages = {}
        for name in ('mido', 'numpy', 'PyQt5'):
            try:
                packages[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                packages[name] = None
        return {'python': platform.python_version(), 'executable': sys.executable,
                'packages': packages, 'code_revision': control.code_revision,
                'source_commit': control.config.get('source_commit'), 'model_execution': False}
    if action == 'job_snapshot':
        path = safe_path(arguments['path'], control.jobs)
        for name in ('request.json', 'status.json', 'progress.json', 'run.lock'):
            safe_path(path / name, [path])
        from progress_state import snapshot
        from agent_preparation import source_summary
        return {'id': arguments['job_id'], 'path': str(path), **snapshot(path),'source_summary':source_summary(path)}
    if action == 'preparation_plan':
        from agent_preparation import plan_preparation
        source = arguments['source']
        if Path(source).is_absolute():
            safe_path(source, control.assets)
        return plan_preparation(source, arguments.get('options'))
    if action == 'preparation_create':
        from agent_preparation import create_preparation
        parent = safe_path(arguments['parent'], control.jobs)
        return create_preparation(arguments['plan'], parent)
    if action=='preparation_reuse_plan':
        from agent_preparation import plan_preparation_reuse
        job=safe_path(arguments['source_job'],control.jobs)
        return plan_preparation_reuse(job,arguments.get('options'),arguments.get('reuse_stages'))
    if action=='preparation_reuse_create':
        from agent_preparation import create_preparation_reuse
        plan=arguments['plan']
        safe_path(plan['source_job'],control.jobs)
        return create_preparation_reuse(plan,safe_path(arguments['parent'],control.jobs))
    path = safe_path(arguments['path'], [control.state, *control.assets, *control.jobs])
    if action=='media_validate':
        from agent_media import audio_info
        return audio_info(path)
    if action=='segment_plan':
        from agent_segments import plan_segments
        if arguments.get('kind')=='style':
            from agent_style import read_context
            score=read_context(path)
        else:
            from agent_score import read_score
            score=read_score(path)
        return plan_segments(score)
    if action=='style_segment_context':
        from agent_style import read_context
        from agent_segments import segment_notes,adjacent_context
        context=read_context(path);segment=arguments['segment']
        if segment['id'] not in {row['id'] for row in __import__('agent_segments').plan_segments(context)['segments']}:
            raise ControlError('stale_segment','工程片段身份已改变，请重新读取。')
        notes=segment_notes(context,segment)
        tracks=[{k:row[k] for k in ('track_index','name','instrumental','eligible','ineligibility_reasons') if k in row}
                for row in context.get('tracks',[]) if row.get('track_index')==segment['track_index']]
        return {'source_sha256':context['source_sha256'],'notes':notes,'tracks':tracks,
            'adjacent_context':adjacent_context(context,segment),'presets':context.get('presets',[]),
            'techniques':context.get('techniques',[]),'scope':'current_segment_only',
            'whole_song_note_count':len(context['notes'])}
    if action in ('score_read', 'score_review', 'score_plan'):
        from agent_score import read_score, review_score, plan_patch
        if action == 'score_read':
            return read_score(path)
        if action == 'score_review':
            return review_score(path)
        return plan_patch(path, arguments['expected_sha256'], arguments['edits'],
                          move_attached_lyrics=arguments.get('move_attached_lyrics',False))
    if action in ('context_read', 'style_plan'):
        from agent_style import read_context, plan_style
        if action == 'context_read':
            return read_context(path, arguments.get('track_indices'),limit=arguments.get('limit'),
                offset=arguments.get('offset',0),detail=arguments.get('detail','full'),
                include_note_ids=arguments.get('include_note_ids',True))
        return plan_style(path, arguments['proposal'])
    if action in ('lyrics_read', 'lyrics_review', 'lyrics_plan'):
        from agent_lyrics import read_lyrics, review_lyrics, plan_lyrics_patch
        if action == 'lyrics_read':
            return read_lyrics(path, arguments.get('language','zh'))
        if action == 'lyrics_review':
            return review_lyrics(path, arguments.get('reference_text'), arguments.get('language','zh'))
        return plan_lyrics_patch(path, arguments['expected_sha256'], arguments['edits'])
    if action == 'export_plan':
        from agent_exports import plan_export
        if arguments.get('options',{}).get('instrumental_path'):
            safe_path(arguments['options']['instrumental_path'],[control.state,*control.jobs,*control.assets])
        return plan_export(path, arguments.get('formats'), arguments.get('options'))
    if action in ('pronunciation_read','pronunciation_plan'):
        from agent_pronunciation import read_pronunciation,plan_pronunciation_patch
        runtime=control.config.get('export_runtime',str(control.app/'dependencies/export'))
        if action=='pronunciation_read':
            return read_pronunciation(path,limit=arguments.get('limit',100),offset=arguments.get('offset',0),
                                      export_runtime=runtime)
        return plan_pronunciation_patch(path,arguments['expected_sha256'],arguments['edits'],export_runtime=runtime)
    if action in ('draft_read','draft_publish','draft_plan'):
        if control.shared_drafts is None:
            raise ControlError('shared_drafts_disabled','没有配置共享草稿目录。')
        from agent_drafts import read_draft,publish_draft,plan_from_draft
        key=arguments['input_sha256']
        if action=='draft_read':return read_draft(path,control.shared_drafts,key)
        if action=='draft_publish':
            return publish_draft(path,control.shared_drafts,key,arguments['plan'],
                                 arguments.get('expected_revision'),arguments['writer'])
        return plan_from_draft(path,control.shared_drafts,key,arguments['expected_revision'],arguments['brief'])
    if action == 'structure_plan':
        from agent_structure import plan_structure_patch
        return plan_structure_patch(path,arguments['expected_sha256'],arguments['edits'])
    if action == 'reference_validate':
        from agent_reference import inspect_reference_audio,inspect_reference_pitch
        if arguments['kind']=='pitch':return inspect_reference_pitch(path)
        if arguments['kind']=='audio':return inspect_reference_audio(path)
        raise ControlError('invalid_reference_kind','参考类型无效。')
    if action == 'reference_pitch_review':
        from agent_reference import review_reference_pitch
        pitch=safe_path(arguments['pitch_path'],[control.state])
        return review_reference_pitch(path,pitch,arguments.get('track_indices'),
            arguments.get('time_origin',0.0),arguments.get('limit',100),arguments.get('offset',0))
    if action == 'reference_audio_read':
        from agent_reference import inspect_reference_audio
        return inspect_reference_audio(path,arguments.get('start'),arguments.get('end'))
    if action == 'review_bundle':
        from agent_score import read_score, review_score
        from agent_lyrics import read_lyrics, review_lyrics
        language = arguments.get('language','zh')
        return {'score':read_score(path), 'score_review':review_score(path),
                'lyrics':read_lyrics(path,language),
                'lyric_review':review_lyrics(path,arguments.get('reference_text'),language)}
    raise ControlError('unsupported_action', '不支持该业务调用。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--operation')
    parser.add_argument('--request')
    parser.add_argument('--response')
    args = parser.parse_args()
    control = Control(args.config)
    if args.operation:
        control.run_operation(args.operation)
        return
    request_path = safe_path(args.request, [control.state / 'calls'])
    response_path = safe_path(args.response, [request_path.parent])
    try:
        request = read_json(request_path)
        if request['code_revision'] != control.code_revision:
            raise ControlError('stale_code', '业务代码已改变，请重新启动 MCP 服务。')
        data = dispatch(control, request['action'], request['arguments'])
        value = {'ok': True, 'data': data}
    except Exception as error:
        from agent_errors import error_payload
        value = {'ok': False, **error_payload(error)}
    atomic_json(response_path, value)


if __name__ == '__main__':
    main()
