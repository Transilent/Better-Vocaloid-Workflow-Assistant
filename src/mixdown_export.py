"""Frozen, Qt-free execution plan for the simple mixdown export transaction."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import inspect
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping
import uuid

from audio_mixdown import MixdownCancelled, _install_new_file
from mixdown_runtime import (
    application_root,
    check_cancel,
    child_python,
    normal_path_key,
    same_path,
    worker_environment,
)


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    size: int
    mtime_ns: int

    def unchanged(self) -> bool:
        try:
            info = self.path.stat()
        except OSError:
            return False
        return (self.path.is_file()
                and int(info.st_size) == self.size
                and int(info.st_mtime_ns) == self.mtime_ns)

    def receipt_entry(self) -> dict[str, Any]:
        return {'path': str(self.path), 'bytes': self.size,
                'mtime_ns': self.mtime_ns}


@dataclass(frozen=True)
class ExportPlan:
    """One frozen render request and its source snapshots."""

    destination: Path
    temporary: Path
    sources: tuple[SourceSnapshot, ...]
    source_job: str | None
    _request_json: str = field(repr=False)
    _parameters_json: str = field(repr=False)

    @property
    def request(self) -> dict[str, Any]:
        """Return a fresh request; mutating it cannot alter the plan."""
        return json.loads(self._request_json)

    @property
    def parameters(self) -> dict[str, Any]:
        """Return fresh receipt parameters; mutating it cannot alter the plan."""
        return json.loads(self._parameters_json)

    @property
    def source_paths(self) -> tuple[Path, ...]:
        return tuple(source.path for source in self.sources)

    def signatures_unchanged(self) -> bool:
        return all(source.unchanged() for source in self.sources)

    def receipt_context(self) -> dict[str, Any]:
        parameters = self.parameters
        vocal_count = len(parameters.get('vocals') or [])
        vocal_entries = [source.receipt_entry()
                         for source in self.sources[:vocal_count]]
        backing = self.sources[vocal_count].receipt_entry()
        return {
            'parameters': parameters,
            'source_job': self.source_job,
            'inputs': {
                'vocals': vocal_entries,
                'instrumental_path': backing,
            },
        }


def _resolved_source(value: Any, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError(label + '路径为空。')
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(label + '文件不存在或不可读取。')
    return path


def plan_export(parameters: Mapping[str, Any], *,
                source_job: Any = None) -> ExportPlan:
    """Freeze the final request before a worker is launched.

    The worker receives a separate copy whose output is a hidden staging WAV.
    The final request is retained only for the registration receipt.
    """
    if not isinstance(parameters, Mapping):
        raise TypeError('混音导出参数必须是字典。')
    final_request = copy.deepcopy(dict(parameters))
    raw_vocals = final_request.get('vocals')
    if not isinstance(raw_vocals, list) or not raw_vocals:
        raise ValueError('请至少选择一路歌声干声 WAV。')
    vocal_paths = []
    normalized_vocals = []
    for index, raw in enumerate(raw_vocals, 1):
        if not isinstance(raw, Mapping):
            raise ValueError('第 {0} 路歌声参数无效。'.format(index))
        item = copy.deepcopy(dict(raw))
        path = _resolved_source(item.get('path'), '第 {0} 路歌声'.format(index))
        item['path'] = str(path)
        vocal_paths.append(path)
        normalized_vocals.append(item)
    instrumental = _resolved_source(final_request.get('instrumental_path'), '伴奏')
    final_request['vocals'] = normalized_vocals
    final_request['instrumental_path'] = str(instrumental)

    raw_output = final_request.get('output_path')
    if not isinstance(raw_output, (str, os.PathLike)) or not str(raw_output).strip():
        raise ValueError('请指定新的导出 WAV 文件名。')
    destination = Path(raw_output).expanduser().resolve()
    if destination.suffix.lower() != '.wav':
        raise ValueError('导出文件必须使用 .wav 扩展名。')
    if os.path.lexists(destination):
        raise FileExistsError('导出目标已存在；请换一个文件名，现有文件不会被覆盖。')
    sources = tuple(vocal_paths + [instrumental])
    if len({normal_path_key(path) for path in sources}) != len(sources):
        raise ValueError('同一音频文件不能重复作为歌声或伴奏输入。')
    if any(same_path(destination, path) for path in sources):
        raise ValueError('导出文件不能与任一源文件相同。')
    final_request['output_path'] = str(destination)

    while True:
        temporary = destination.with_name(
            '.' + destination.stem + '.' + uuid.uuid4().hex + '.part.wav')
        if (not os.path.lexists(temporary)
                and not any(same_path(temporary, path)
                            for path in (*sources, destination))):
            break

    render_request = copy.deepcopy(final_request)
    render_request['output_path'] = str(temporary.resolve())
    snapshots = []
    for path in sources:
        info = path.stat()
        snapshots.append(SourceSnapshot(path=path, size=int(info.st_size),
                                        mtime_ns=int(info.st_mtime_ns)))
    return ExportPlan(
        destination=destination,
        temporary=temporary.resolve(),
        sources=tuple(snapshots),
        source_job=str(source_job) if source_job else None,
        _request_json=json.dumps(render_request, ensure_ascii=False,
                                 separators=(',', ':'), sort_keys=True),
        _parameters_json=json.dumps(final_request, ensure_ascii=False,
                                    separators=(',', ':'), sort_keys=True),
    )


def _injected_backend(name: str):
    """Only explicit module doubles may bypass the isolated native worker."""
    module = sys.modules.get(name)
    return module if module is not None and not getattr(module, '__file__', None) else None


_RENDER_WORKER = r"""
import inspect
import json
import os
from pathlib import Path
import sys

for extra in reversed(os.environ.get('VWA_MIXDOWN_EXTRA_PATHS', '').split(os.pathsep)):
    if extra:
        sys.path.insert(0, extra)

request_path = Path(sys.argv[1])
cancel_path = Path(sys.argv[2])
request = json.loads(request_path.read_text(encoding='utf-8'))

def cancel():
    return cancel_path.exists()

def emit(event):
    value = dict(event)
    value.setdefault('event', 'progress')
    print(json.dumps(value, ensure_ascii=False), flush=True)

try:
    from audio_mixdown import render
    parameters = inspect.signature(render).parameters
    if 'progress' in parameters:
        result = render(request, cancel=cancel, progress=emit)
    else:
        result = render(request, cancel=cancel)
    if not isinstance(result, dict):
        raise TypeError('render did not return a dictionary')
    print(json.dumps({'event': 'done', 'fraction': 1.0, 'report': result},
                     ensure_ascii=False), flush=True)
except BaseException as error:
    print(type(error).__name__ + ': ' + str(error), file=sys.stderr, flush=True)
    raise SystemExit(1)
"""


def render_once(request: Mapping[str, Any],
                check: Callable[[], Any] | None,
                progress: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Run the shared renderer and forward progress outside the native UI."""
    check_cancel(check)
    injected = _injected_backend('audio_mixdown')
    if injected is not None:
        from audio_mixdown import render
        kwargs = {'cancel': check}
        if progress is not None and 'progress' in inspect.signature(render).parameters:
            kwargs['progress'] = progress
        report = render(copy.deepcopy(dict(request)), **kwargs)
        check_cancel(check)
        if not isinstance(report, dict):
            raise ValueError('混音后端没有返回结果字典。')
        return dict(report)

    root = Path(tempfile.mkdtemp(prefix='vwa-audio-render-'))
    request_path = root / 'request.json'
    cancel_path = root / 'cancel.flag'
    stderr_path = root / 'stderr.txt'
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    process = None
    reader = None
    output_lines = queue.Queue()
    try:
        with stderr_path.open('wb') as stderr:
            process = subprocess.Popen(
                [str(child_python()), '-B', '-u', '-c', _RENDER_WORKER,
                 str(request_path), str(cancel_path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
                env=worker_environment(),
                cwd=str(application_root() or Path(__file__).resolve().parents[1]),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                text=True, encoding='utf-8', errors='replace', bufsize=1)

            def read_output():
                try:
                    for line in process.stdout:
                        output_lines.put(line)
                finally:
                    try:
                        process.stdout.close()
                    except Exception:
                        pass

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            final_report = None
            while True:
                if check and check():
                    cancel_path.touch()
                    grace = time.monotonic() + 2.0
                    while process.poll() is None and time.monotonic() < grace:
                        time.sleep(0.05)
                    if process.poll() is None:
                        try:
                            from process_control import terminate_tree
                            terminate_tree(process)
                        except Exception:
                            process.kill()
                    raise MixdownCancelled('混音操作已取消。')
                try:
                    line = output_lines.get(timeout=0.05)
                except queue.Empty:
                    line = None
                if line:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        event = None
                    if isinstance(event, dict):
                        if event.get('event') == 'progress':
                            if progress is not None:
                                try:
                                    progress(event)
                                except Exception:
                                    pass
                        elif event.get('event') == 'done':
                            payload = event.get('report')
                            if isinstance(payload, dict):
                                final_report = payload
                        elif 'event' not in event:
                            final_report = event
                if (process.poll() is not None and reader is not None
                        and not reader.is_alive() and output_lines.empty()):
                    break
        stderr_text = stderr_path.read_text(encoding='utf-8', errors='replace').strip()
        if check and check():
            raise MixdownCancelled('混音操作已取消。')
        if process.returncode:
            detail = stderr_text.splitlines()[-1] if stderr_text else '混音渲染子进程失败。'
            raise RuntimeError(detail)
        if final_report is None:
            raise ValueError('混音后端没有返回可读结果。')
        return dict(final_report)
    finally:
        if process is not None and process.poll() is None:
            try:
                from process_control import terminate_tree
                terminate_tree(process)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass
        if reader is not None:
            reader.join(timeout=1.0)
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def validate_render_source_hashes(report: Mapping[str, Any],
                                  sources: tuple[Path, ...] | list[Path]) -> dict[str, str]:
    """Require the backend to identify every input it actually consumed."""
    hashes = report.get('source_sha256')
    if not isinstance(hashes, dict):
        raise ValueError('混音后端没有返回源音频 SHA-256 指纹。')
    normalized = {normal_path_key(path): str(value)
                  for path, value in hashes.items()}
    for source in sources:
        value = normalized.get(normal_path_key(source))
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError('混音后端没有返回完整源音频指纹：' + Path(source).name)
        try:
            int(value, 16)
        except ValueError:
            raise ValueError('混音后端返回的源音频指纹无效：' + Path(source).name) from None
    return normalized


def validate_render(report: dict[str, Any],
                    output: str | os.PathLike[str]) -> dict[str, Any]:
    output = Path(output).resolve()
    if not output.is_file() or output.stat().st_size <= 0:
        raise ValueError('混音后端没有生成有效的 WAV 文件。')
    returned = report.get('output_path')
    if returned and Path(returned).resolve() != output:
        raise ValueError('混音后端返回了预期之外的文件路径。')
    report['output_path'] = str(output)
    return report


def publish_without_overwrite(temporary: str | os.PathLike[str],
                              destination: str | os.PathLike[str]) -> None:
    """Reuse the renderer's atomic new-file publication transaction."""
    _install_new_file(Path(temporary), Path(destination))


def execute(plan: ExportPlan, check: Callable[[], Any] | None = None,
            progress: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Execute one frozen export plan, cleaning staging on every exit path."""
    if not isinstance(plan, ExportPlan):
        raise TypeError('execute 需要 ExportPlan。')
    check_cancel(check)
    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = render_once(copy.deepcopy(plan.request), check, progress=progress)
        check_cancel(check)
        validate_render(report, plan.temporary)
        validate_render_source_hashes(report, plan.source_paths)
        check_cancel(check)
        if not plan.signatures_unchanged():
            raise ValueError('处理期间源 WAV 发生了变化；没有发布成品，请重新检查输入。')
        if os.path.lexists(plan.destination):
            raise FileExistsError('导出目标在渲染期间出现；没有覆盖该文件。')
        publish_without_overwrite(plan.temporary, plan.destination)
        report.update(output_path=str(plan.destination),
                      source_files_unchanged=True)
        return report
    finally:
        try:
            plan.temporary.unlink()
        except OSError:
            pass


__all__ = [
    'ExportPlan',
    'SourceSnapshot',
    'execute',
    'plan_export',
    'publish_without_overwrite',
    'render_once',
    'validate_render',
    'validate_render_source_hashes',
]