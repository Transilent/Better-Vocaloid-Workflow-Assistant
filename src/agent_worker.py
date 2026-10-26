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
        return {'id': arguments['job_id'], 'path': str(path), **snapshot(path)}
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
    path = safe_path(arguments['path'], [control.state, *control.assets, *control.jobs])
    if action in ('score_read', 'score_review', 'score_plan'):
        from agent_score import read_score, review_score, plan_patch
        if action == 'score_read':
            return read_score(path)
        if action == 'score_review':
            return review_score(path)
        return plan_patch(path, arguments['expected_sha256'], arguments['edits'])
    if action in ('context_read', 'style_plan'):
        from agent_style import read_context, plan_style
        if action == 'context_read':
            return read_context(path, arguments.get('track_indices'))
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
        return plan_export(path, arguments.get('formats'), arguments.get('options'))
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
        value = {'ok': False, 'code': getattr(error, 'code', 'business_error'), 'message': str(error)}
    atomic_json(response_path, value)


if __name__ == '__main__':
    main()
