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
