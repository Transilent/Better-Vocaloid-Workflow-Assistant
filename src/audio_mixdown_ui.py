"""Simple vocal/instrumental mixdown dialog with cancellable background renders."""
from datetime import datetime
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from PyQt5.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                             QGridLayout, QHBoxLayout, QLineEdit,
                             QProgressBar, QScrollArea, QSlider, QVBoxLayout, QWidget)

from desktop_theme import button, card, fold, label
from i18n import tr as _tr
from ui_components import AdaptiveColumns, FlowLayout


_PREVIEW_PREFIX = 'vwa-audio-mixdown-'
_OPTIONAL_PACKAGES = None
_MAX_VOCAL_TRACKS = 3
_VOCAL_FILTER = _tr('歌声干声 WAV (*.wav)')
_INSTRUMENTAL_FILTER = _tr('音频 (*.wav *.flac *.mp3 *.ogg *.m4a *.aac)')
_WAV_FILTER = _tr('WAV 音频 (*.wav)')
# Stable object names for offscreen UI checks and host-level automation.
CONTROL_NAMES = {
    'task_entry': 'taskFilesMixdownButton',
    'dialog': 'mixdownDialog',
    'support_panel': 'mixdownSupportPanel',
    'support_state': 'mixdownSupportState',
    'install_dependency': 'mixdownInstallDependency',
    'vocal_tracks': 'mixdownVocalTracks',
    'add_vocal_track': 'mixdownAddVocalTrack',
    'vocal_path': 'mixdownVocalPath',
    'vocal_2_path': 'mixdownVocal2Path',
    'vocal_3_path': 'mixdownVocal3Path',
    'instrumental_path': 'mixdownInstrumentalPath',
    'output_path': 'mixdownOutputPath',
    'browse_vocal': 'mixdownBrowseVocal',
    'browse_vocal_2': 'mixdownBrowseVocal2',
    'browse_vocal_3': 'mixdownBrowseVocal3',
    'browse_instrumental': 'mixdownBrowseInstrumental',
    'browse_output': 'mixdownBrowseOutput',
    'vocal_state': 'mixdownVocalState',
    'vocal_2_state': 'mixdownVocal2State',
    'vocal_3_state': 'mixdownVocal3State',
    'instrumental_state': 'mixdownInstrumentalState',
    'output_state': 'mixdownOutputState',
    'vocal_db': 'mixdownVocalDb',
    'vocal_2_db': 'mixdownVocal2Db',
    'vocal_3_db': 'mixdownVocal3Db',
    'instrumental_db': 'mixdownInstrumentalDb',
    'vocal_slider': 'mixdownVocalLevelSlider',
    'vocal_2_slider': 'mixdownVocal2LevelSlider',
    'vocal_3_slider': 'mixdownVocal3LevelSlider',
    'instrumental_slider': 'mixdownInstrumentalLevelSlider',
    'offset': 'mixdownOffset',
    'offset_2': 'mixdownOffset2',
    'offset_3': 'mixdownOffset3',
    'eq_enabled': 'mixdownEqEnabled',
    'compression_enabled': 'mixdownCompressionEnabled',
    'reverb_enabled': 'mixdownReverbEnabled',
    'low_cut': 'mixdownLowCut',
    'presence': 'mixdownPresence',
    'compression': 'mixdownCompression',
    'reverb': 'mixdownReverb',
    'advanced_toggle': 'mixdownAdvancedToggle',
    'preview_start': 'mixdownPreviewStart',
    'preview_duration': 'mixdownPreviewDuration',
    'preview_range_toggle': 'mixdownPreviewRangeToggle',
    'preview_render': 'mixdownPreviewRender',
    'preview_before': 'mixdownPreviewBefore',
    'preview_after': 'mixdownPreviewAfter',
    'preview_status': 'mixdownPreviewStatus',
    'preview_before_state': 'mixdownPreviewBeforeState',
    'preview_after_state': 'mixdownPreviewAfterState',
    'result_title': 'mixdownResultTitle',
    'result_status': 'mixdownResultStatus',
    'export': 'mixdownExport',
    'cancel': 'mixdownCancel',
    'register': 'mixdownRegister',
    'register_video': 'mixdownRegisterAndVideo',
    'status': 'mixdownStatus',
    'progress': 'mixdownProgress',
    'close': 'mixdownClose',
}


def _set_role(widget, role):
    role = role or ''
    if widget.property('role') == role:
        return
    widget.setProperty('role', role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class _Cancelled(RuntimeError):
    pass


def _application_root():
    """Locate the app tree containing component manifest and packaged installer."""
    try:
        from common import ROOT
        candidates = (Path(ROOT), Path(ROOT).parent)
    except Exception:
        candidates = (Path(__file__).resolve().parent, Path(__file__).resolve().parents[1])
    for candidate in candidates:
        if ((candidate / 'components/pedalboard.json').is_file()
                and (candidate / 'tools/install_pedalboard_component.py').is_file()):
            return candidate.resolve()
    return None


def _component_manifest():
    root = _application_root()
    if root is None:
        return root, None, None
    manifest = root / 'components/pedalboard.json'
    try:
        value = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return root, None, None
    return root, value, root / 'tools/install_pedalboard_component.py'


def _optional_packages(root, spec, packages=None):
    if packages:
        path = Path(packages).resolve()
    elif spec:
        try:
            path = (root / spec['install']['root']
                    / spec['install']['packages']).resolve()
            if not (root / spec['install']['root']).resolve().is_relative_to(root.resolve()):
                return None
        except (KeyError, TypeError, OSError, ValueError):
            return None
    else:
        path = None
    return path if path and path.is_dir() else None


def _child_python():
    """Use a console Python for clean subprocesses, with an explicit fallback."""
    executable = Path(sys.executable).resolve()
    sibling = executable.with_name('python.exe')
    if sibling.is_file():
        return sibling
    return executable


def _worker_environment(packages=None):
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    paths = []
    if packages:
        paths.append(str(Path(packages).resolve()))
    app_root = _application_root()
    if app_root:
        paths.append(str(app_root))
        source = app_root / 'src'
        if source.is_dir():
            paths.append(str(source))
    paths.append(str(Path(__file__).resolve().parent))
    paths.extend(str(path) for path in sys.path if path)
    previous = env.get('PYTHONPATH')
    if previous:
        paths.extend(part for part in previous.split(os.pathsep) if part)
    ordered = list(dict.fromkeys(paths))
    # Embedded Python ignores PYTHONPATH because python312._pth is present.
    # The child bootstrap explicitly inserts this custom list before imports.
    env['VWA_MIXDOWN_EXTRA_PATHS'] = os.pathsep.join(ordered)
    env['PYTHONPATH'] = os.pathsep.join(ordered)
    return env


def _activate_pedalboard(packages=None):
    """Verify Pedalboard in a clean Python process and remember its optional path."""
    global _OPTIONAL_PACKAGES
    if packages:
        path = Path(packages).resolve()
        if not path.is_dir():
            return False, '已安装的 Pedalboard 组件目录不存在。'
        _OPTIONAL_PACKAGES = str(path)
    fake = sys.modules.get('pedalboard')
    if fake is not None and not getattr(fake, '__file__', None):
        version = str(getattr(fake, '__version__', ''))
        if version != '0.9.26':
            return False, 'Pedalboard 版本不符：需要 0.9.26，当前为 ' + (version or '未知') + '。'
        return True, version
    code = (
        "import os,sys; "
        "[sys.path.insert(0,p) for p in reversed(os.environ.get('VWA_MIXDOWN_EXTRA_PATHS','').split(os.pathsep)) if p]; "
        "import pedalboard; "
        "v=str(getattr(pedalboard,'__version__','')); "
        "assert v=='0.9.26', '版本不符: '+v; "
        "assert all(hasattr(pedalboard,n) for n in ('Pedalboard','HighpassFilter','PeakFilter','Compressor','Reverb')), '组件不完整'; "
        "print(v)"
    )
    try:
        completed = subprocess.run(
            [str(_child_python()), '-B', '-c', code], capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=_worker_environment(_OPTIONAL_PACKAGES),
            timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, 'Pedalboard 组件检查失败：' + str(error)
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()
        return False, 'Pedalboard 组件检查失败：' + (detail[-1] if detail else '未知错误')
    return True, completed.stdout.strip() or '0.9.26'


def _run_component_installer(action, root, installer, check, force=False):
    """Run the packaged component installer without a shell or PowerShell."""
    if not installer or not Path(installer).is_file():
        raise FileNotFoundError('混音组件安装器未随当前版本提供。')
    argv = [str(_child_python()), '-B', str(installer), action, '--app-root', str(root)]
    if action == 'install':
        argv.extend(['--python', str(_child_python())])
    if action == 'install' and force:
        argv.append('--force')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace', env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        while process.poll() is None:
            _check_cancel(check)
            time.sleep(0.10)
        stdout, stderr = process.communicate()
    finally:
        if process.poll() is None:
            try:
                from process_control import terminate_tree
                terminate_tree(process)
            except Exception:
                try:
                    process.kill()
                except OSError:
                    pass
            process.wait(timeout=5)
    if process.returncode:
        detail = (stderr or stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else '混音组件安装器返回失败。')
    try:
        value = json.loads(stdout)
    except ValueError:
        raise ValueError('混音组件安装器没有返回可读状态。')
    if not isinstance(value, dict):
        raise ValueError('混音组件安装器返回状态无效。')
    return value


class MixdownTask(QThread):
    """One owned render/register operation; the worker owns all cleanup."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            result = self.function(self.cancel_event.is_set)
        except _Cancelled:
            self.cancelled.emit()
        except Exception as error:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)


def _check_cancel(check):
    if check and check():
        raise _Cancelled('混音操作已取消。')


_RENDER_WORKER = r"""
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

try:
    from audio_mixdown import render
    result = render(request, cancel=cancel)
    if not isinstance(result, dict):
        raise TypeError('render did not return a dictionary')
    print(json.dumps(result, ensure_ascii=False))
except BaseException as error:
    print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
    raise SystemExit(1)
"""


def _render_once(request, check):
    """Run the shared renderer asynchronously without loading native Qt into QThread.

    Pedalboard and PyQt both carry native Qt libraries. Importing and processing
    Pedalboard inside this GUI process can deadlock in a QThread depending on DLL
    load order, so the owned QThread supervises a clean Python child process.
    """
    _check_cancel(check)
    injected = sys.modules.get('audio_mixdown')
    if (injected is not None and
            (not getattr(injected, '__file__', None)
             or os.environ.get('VWA_TESTING') or os.environ.get('BVWA_TESTING'))):
        from audio_mixdown import render
        report = render(dict(request), cancel=check)
        _check_cancel(check)
        if not isinstance(report, dict):
            raise ValueError('混音后端没有返回结果字典。')
        return dict(report)

    root = Path(tempfile.mkdtemp(prefix='vwa-audio-render-'))
    request_path = root / 'request.json'
    cancel_path = root / 'cancel.flag'
    stdout_path = root / 'stdout.json'
    stderr_path = root / 'stderr.txt'
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
    process = None
    try:
        with request_path.open('rb') as stdin, stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            process = subprocess.Popen(
                [str(_child_python()), '-B', '-c', _RENDER_WORKER, str(request_path), str(cancel_path)],
                stdin=stdin, stdout=stdout, stderr=stderr,
                env=_worker_environment(_OPTIONAL_PACKAGES),
                cwd=str(_application_root() or Path(__file__).resolve().parents[1]),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            while process.poll() is None:
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
                    raise _Cancelled('混音操作已取消。')
                time.sleep(0.05)
        stdout = stdout_path.read_text(encoding='utf-8', errors='replace').strip()
        stderr = stderr_path.read_text(encoding='utf-8', errors='replace').strip()
        if check and check():
            raise _Cancelled('混音操作已取消。')
        if process.returncode:
            detail = stderr.splitlines()[-1] if stderr else '混音渲染子进程失败。'
            raise RuntimeError(detail)
        try:
            report = json.loads(stdout)
        except ValueError:
            raise ValueError('混音后端没有返回可读结果。')
        if not isinstance(report, dict):
            raise ValueError('混音后端没有返回结果字典。')
        return report
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
        shutil.rmtree(root, ignore_errors=True)


def _source_signature(path):
    info = Path(path).stat()
    return info.st_size, info.st_mtime_ns


def _file_sha256(path, check=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            _check_cancel(check)
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _normal_path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _validate_render_source_hashes(report, sources):
    """Require the backend to identify every input it actually consumed."""
    hashes = report.get('source_sha256')
    if not isinstance(hashes, dict):
        raise ValueError('混音后端没有返回源音频 SHA-256 指纹。')
    normalized = {_normal_path_key(path): value for path, value in hashes.items()}
    for source in sources:
        value = normalized.get(_normal_path_key(source))
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError('混音后端没有返回完整源音频指纹：' + Path(source).name)
        try:
            int(value, 16)
        except ValueError:
            raise ValueError('混音后端返回的源音频指纹无效：' + Path(source).name) from None
    return normalized


def _validate_render(report, output):
    output = Path(output).resolve()
    if not output.is_file() or output.stat().st_size <= 0:
        raise ValueError('混音后端没有生成有效的 WAV 文件。')
    returned = report.get('output_path')
    if returned and Path(returned).resolve() != output:
        raise ValueError('混音后端返回了预期之外的文件路径。')
    report['output_path'] = str(output)
    return report


def _publish_without_overwrite(temporary, destination):
    """Atomically give the finished WAV its requested name; never replace a file."""
    temporary, destination = Path(temporary), Path(destination)
    if destination.exists():
        raise FileExistsError('导出目标已存在；为保护现有文件，请换一个文件名。')
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise FileExistsError('导出目标已存在；为保护现有文件，请换一个文件名。')
    except OSError as error:
        # Windows os.rename does not replace an existing destination. Hard links
        # can be unavailable on a mounted filesystem, so this is a narrow fallback.
        if os.name != 'nt':
            raise OSError('无法以不覆盖方式发布混音文件：' + str(error))
        try:
            os.rename(temporary, destination)
        except FileExistsError:
            raise FileExistsError('导出目标已存在；为保护现有文件，请换一个文件名。')
    else:
        try:
            temporary.unlink()
        except OSError:
            # The destination is already durable; a hidden staging link left on
            # an unusual filesystem must not turn a successful publish into failure.
            pass


def _same_path(left, right):
    if left is None or right is None:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False


def _discard_preview_directory(path):
    if not path:
        return
    try:
        candidate = Path(path).absolute()
        root = Path(tempfile.gettempdir()).resolve()
        if candidate.parent.resolve() != root or not candidate.name.startswith(_PREVIEW_PREFIX):
            return
        if candidate.is_symlink() or candidate.is_junction():
            return
        shutil.rmtree(candidate, ignore_errors=True)
    except (OSError, ValueError):
        pass


def _task_source(job, names):
    if not job:
        return None
    job = Path(job)
    for name in names:
        path = job / name
        if path.is_file():
            return path.resolve()
    return None


def _suggested_output(job):
    directory = (Path(job) / 'rendered') if job else Path.cwd()
    stem = 'simple-mixdown-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    path = directory / (stem + '.wav')
    index = 2
    while path.exists():
        path = directory / (stem + '-' + str(index) + '.wav')
        index += 1
    return path


def _report_detail(report):
    parts = []
    try:
        parts.append(_tr('时长：{0:.3f} 秒').format(float(report['duration_seconds'])))
    except (KeyError, TypeError, ValueError):
        parts.append(_tr('时长：后端未报告'))
    try:
        peak = float(report['peak_dbfs'])
        value = _tr('静音') if peak == -math.inf else '{:.2f} dBFS'.format(peak)
        parts.append(_tr('峰值：') + value)
    except (KeyError, TypeError, ValueError):
        parts.append(_tr('峰值：后端未报告'))
    return '；'.join(parts)


class AudioMixdownDialog(QDialog):
    """Mix up to three dry vocal WAVs and one backing without replacing sources."""

    def __init__(self, owner, job, parent=None, embedded=False):
        if embedded:
            super().__init__(parent, Qt.Widget)
            self.setWindowFlags(Qt.Widget)
        else:
            super().__init__(parent)
        self.owner = owner
        self.embedded = bool(embedded)
        self._job = Path(job).resolve() if job else None
        self._ui_ready = False
        self._auto_instrumental = None
        self.worker = None
        self._busy_kind = None
        self._close_pending = False
        self._completion_action = None
        self._pending_navigation = False
        self._syncing = False
        self._result = None
        self._export_context = None
        self._export_receipt = None
        self._preview_directory = None
        self._preview_result = None
        self.navigate_requested = False
        self.video_requested = False
        self.registered = False
        self._support_ready = False
        self._support_error = ''
        self._component_packages = None
        self._component_spec = None
        self._component_root = None
        self._component_installer = None

        self.setObjectName(CONTROL_NAMES['dialog'])
        self.setWindowTitle(_tr('制作简易混音'))
        self.setModal(not self.embedded)
        self.setMinimumSize(640, 560)
        self.resize(920, 840)
        self.setSizeGripEnabled(not self.embedded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(26, 22, 26, 24)
        body.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        body.addWidget(label(_tr('简易混音'), 'heading'))
        body.addWidget(label(
            _tr('先选声音，再调平衡；基础流程不用懂效果参数。需要细调时再展开高级处理。源文件始终只读。'),
            'muted'))

        steps_widget = QWidget()
        steps = FlowLayout(steps_widget, spacing=8)
        for index, title in enumerate((
                _tr('选择 1–3 路歌声'), _tr('逐路平衡'), _tr('人声处理'),
                _tr('试听'), _tr('导出登记'))):
            if index:
                steps.addWidget(label('›', 'muted'))
            step = label(_tr('{0}  {1}').format(index + 1, title), 'muted')
            step.setStyleSheet('font-weight: 600;')
            steps.addWidget(step)
        body.addWidget(steps_widget)

        self.support_panel = QWidget()
        self.support_panel.setObjectName(CONTROL_NAMES['support_panel'])
        support_outer = QVBoxLayout(self.support_panel)
        support_outer.setContentsMargins(0, 0, 0, 0)
        support_surface = QWidget()
        support_surface.setObjectName('step-surface')
        support_outer.addWidget(support_surface)
        support_layout = QVBoxLayout(support_surface)
        support_layout.setContentsMargins(16, 13, 16, 13)
        support_layout.setSpacing(8)
        support_title = label(_tr('混音组件'), 'muted')
        support_title.setStyleSheet('font-weight: 600;')
        self.support_state = label(_tr('正在检查混音组件…'), 'muted')
        self.support_state.setObjectName(CONTROL_NAMES['support_state'])
        self.install_dependency_button = button(
            _tr('安装混音组件（约 3.5 MiB）'), self.install_dependency, 'primary')
        self.install_dependency_button.setObjectName(CONTROL_NAMES['install_dependency'])
        self.install_dependency_button.setAccessibleName(_tr('一键安装混音组件'))
        support_layout.addWidget(support_title)
        support_layout.addWidget(self.support_state)
        support_layout.addWidget(self.install_dependency_button)
        body.addWidget(self.support_panel)

        source_card, source_body = card(
            _tr('选择素材'),
            _tr('可选择 1–3 路声库或编辑器导出的歌声 WAV，再选择一个伴奏。未填写的可选歌声不会参与混音；原唱分离不会被自动当成声库干声。'),
            '1')
        self.fields = {}
        self._choose_buttons = {}
        self.source_states = {}
        self.vocal_tracks = []
        vocal_host = QWidget()
        vocal_host.setObjectName(CONTROL_NAMES['vocal_tracks'])
        vocal_layout = QVBoxLayout(vocal_host)
        vocal_layout.setContentsMargins(0, 0, 0, 0)
        vocal_layout.setSpacing(9)
        for index in range(_MAX_VOCAL_TRACKS):
            number = index + 1
            suffix = '' if index == 0 else '_' + str(number)
            field_key = 'vocal' if index == 0 else 'vocal_' + str(number)
            title = _tr('歌声 {0}').format(number)
            panel = QWidget()
            panel.setObjectName('step-surface')
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(14, 12, 14, 12)
            panel_layout.setSpacing(7)
            heading_row = QHBoxLayout()
            heading = label(title, 'muted')
            heading.setStyleSheet('font-weight: 600;')
            heading_row.addWidget(heading, 1)
            remove = button(_tr('移除此路'), lambda checked=False, index=index:
                            self.remove_vocal_track(index))
            remove.setObjectName('mixdownRemoveVocal' + str(number))
            remove.setAccessibleName(_tr('移除第 {0} 路歌声').format(number))
            remove.hide()
            heading_row.addWidget(remove)
            panel_layout.addLayout(heading_row)

            path_row = QHBoxLayout()
            field = QLineEdit()
            field.setObjectName(CONTROL_NAMES.get(
                'vocal_path' + suffix, 'mixdownVocal' + str(number) + 'Path'))
            field.setAccessibleName(_tr('第 {0} 路歌声干声').format(number))
            field.setPlaceholderText(
                _tr('声库或编辑器导出的 WAV') if index == 0
                else _tr('可选：另一路歌声 WAV'))
            field.setClearButtonEnabled(True)
            choose = button(_tr('选择…'),
                            lambda checked=False, key=field_key: self._choose_path(key))
            choose.setObjectName(CONTROL_NAMES.get(
                'browse_vocal' + suffix, 'mixdownBrowseVocal' + str(number)))
            choose.setAccessibleName(_tr('选择第 {0} 路歌声').format(number))
            state = label(_tr('尚未选择'), 'muted')
            state.setObjectName(CONTROL_NAMES.get(
                'vocal_state' + suffix, 'mixdownVocal' + str(number) + 'State'))
            path_row.addWidget(field, 1)
            path_row.addWidget(choose)
            panel_layout.addLayout(path_row)
            panel_layout.addWidget(state)
            vocal_layout.addWidget(panel)
            self.fields[field_key] = field
            self._choose_buttons[field_key] = choose
            self.source_states[field_key] = state
            self.vocal_tracks.append({
                'index': index, 'field_key': field_key, 'panel': panel,
                'path': field, 'choose': choose, 'state': state,
                'remove': remove, 'gain_db': None, 'slider': None,
                'offset': None, 'level_panel': None,
            })
        source_body.addWidget(vocal_host)
        self.add_vocal_button = button(_tr('添加第 2 路歌声'), self.add_vocal_track)
        self.add_vocal_button.setObjectName(CONTROL_NAMES['add_vocal_track'])
        self.add_vocal_button.setAccessibleName(_tr('添加另一路歌声'))
        source_body.addWidget(self.add_vocal_button)

        backing_panel = QWidget()
        backing_panel.setObjectName('step-surface')
        backing_grid = QGridLayout(backing_panel)
        backing_grid.setContentsMargins(14, 12, 14, 12)
        backing_grid.setHorizontalSpacing(10)
        backing_grid.setVerticalSpacing(7)
        backing_grid.setColumnStretch(1, 1)
        backing_title = _tr('伴奏')
        backing_field = QLineEdit()
        backing_field.setObjectName(CONTROL_NAMES['instrumental_path'])
        backing_field.setAccessibleName(backing_title)
        backing_field.setPlaceholderText(_tr('WAV、FLAC、MP3、M4A 等常见音频'))
        backing_field.setClearButtonEnabled(True)
        backing_choose = button(_tr('选择…'),
                                lambda checked=False: self._choose_path('instrumental'))
        backing_choose.setObjectName(CONTROL_NAMES['browse_instrumental'])
        backing_choose.setAccessibleName(_tr('选择伴奏'))
        backing_state = label(_tr('尚未选择'), 'muted')
        backing_state.setObjectName(CONTROL_NAMES['instrumental_state'])
        backing_grid.addWidget(label(backing_title), 0, 0)
        backing_grid.addWidget(backing_field, 0, 1)
        backing_grid.addWidget(backing_choose, 0, 2)
        backing_grid.addWidget(backing_state, 1, 1, 1, 2)
        source_body.addWidget(backing_panel)
        self.fields['instrumental'] = backing_field
        self._choose_buttons['instrumental'] = backing_choose
        self.source_states['instrumental'] = backing_state
        body.addWidget(source_card)

        balance_card, balance_body = card(
            _tr('平衡音量与位置'),
            _tr('每一路歌声都可独立调音量与拍点位置；EQ、压缩和混响仍统一作用于全部歌声，伴奏保持独立。'),
            '2')

        def pair_level(slider, spin):
            slider.valueChanged.connect(lambda value, spin=spin: spin.setValue(value / 10.0))
            spin.valueChanged.connect(lambda value, slider=slider: slider.setValue(int(round(value * 10.0))))

        def vocal_level_panel(track):
            number = track['index'] + 1
            panel = QWidget()
            panel.setObjectName('step-surface')
            panel.setProperty('mixdown-role', 'vocal-' + str(number))
            row = QVBoxLayout(panel)
            row.setContentsMargins(16, 14, 16, 14)
            row.setSpacing(7)
            row.addWidget(label(_tr('歌声 {0}').format(number), 'card-title'))
            row.addWidget(label(
                _tr('主唱与咬字') if number == 1 else _tr('和声或叠唱；音量、位置独立'),
                'muted'))
            row.addWidget(track['slider'])
            level_row = QHBoxLayout()
            level_row.addWidget(label(_tr('小声'), 'muted'))
            level_row.addStretch(1)
            level_row.addWidget(track['gain_db'])
            row.addLayout(level_row)
            offset_row = QHBoxLayout()
            offset_row.addWidget(label(_tr('拍点位置')), 1)
            offset_row.addWidget(track['offset'], 2)
            row.addLayout(offset_row)
            return panel

        for track in self.vocal_tracks:
            number = track['index'] + 1
            suffix = '' if track['index'] == 0 else '_' + str(number)
            gain = QDoubleSpinBox()
            gain.setObjectName(CONTROL_NAMES.get(
                'vocal_db' + suffix, 'mixdownVocal' + str(number) + 'Db'))
            gain.setAccessibleName(_tr('第 {0} 路歌声干声音量').format(number))
            gain.setRange(-24.0, 12.0)
            gain.setDecimals(1)
            gain.setSingleStep(0.5)
            gain.setSuffix(' dB')
            gain.setValue(0.0)
            slider = QSlider(Qt.Horizontal)
            slider.setObjectName(CONTROL_NAMES.get(
                'vocal_slider' + suffix, 'mixdownVocal' + str(number) + 'LevelSlider'))
            slider.setAccessibleName(_tr('第 {0} 路歌声音量滑块').format(number))
            slider.setRange(-240, 120)
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(50)
            slider.setMinimumWidth(120)
            offset = QDoubleSpinBox()
            offset.setObjectName(CONTROL_NAMES.get(
                'offset' + suffix, 'mixdownOffset' + str(number)))
            offset.setAccessibleName(_tr('第 {0} 路歌声位置偏移秒数').format(number))
            offset.setRange(-30.0, 30.0)
            offset.setDecimals(3)
            offset.setSingleStep(0.01)
            offset.setSuffix(' 秒')
            offset.setValue(0.0)
            pair_level(slider, gain)
            track.update({'gain_db': gain, 'slider': slider, 'offset': offset})
            track['level_panel'] = vocal_level_panel(track)
            if track['index'] == 0:
                self.vocal_db = gain
                self.vocal_slider = slider
                self.offset = offset

        self.instrumental_db = QDoubleSpinBox()
        self.instrumental_db.setObjectName(CONTROL_NAMES['instrumental_db'])
        self.instrumental_db.setAccessibleName(_tr('伴奏音量'))
        self.instrumental_db.setRange(-24.0, 12.0)
        self.instrumental_db.setDecimals(1)
        self.instrumental_db.setSingleStep(0.5)
        self.instrumental_db.setSuffix(' dB')
        self.instrumental_db.setValue(0.0)
        self.instrumental_slider = QSlider(Qt.Horizontal)
        self.instrumental_slider.setObjectName(CONTROL_NAMES['instrumental_slider'])
        self.instrumental_slider.setAccessibleName(_tr('伴奏音量滑块'))
        for slider in (self.instrumental_slider,):
            slider.setRange(-240, 120)
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(50)
            slider.setMinimumWidth(120)

        pair_level(self.instrumental_slider, self.instrumental_db)

        def level_panel(title, hint, slider, spin, object_name):
            panel = QWidget()
            panel.setObjectName('step-surface')
            panel.setProperty('mixdown-role', object_name)
            row = QVBoxLayout(panel)
            row.setContentsMargins(16, 14, 16, 14)
            row.setSpacing(8)
            row.addWidget(label(title, 'card-title'))
            row.addWidget(label(hint, 'muted'))
            row.addWidget(slider)
            footer = QHBoxLayout()
            footer.addWidget(label(_tr('小声'), 'muted'))
            footer.addStretch(1)
            footer.addWidget(spin)
            row.addLayout(footer)
            return panel

        levels = AdaptiveColumns(
            self.vocal_tracks[0]['level_panel'],
            level_panel(_tr('伴奏'), _tr('音乐与节奏'), self.instrumental_slider,
                        self.instrumental_db, 'instrumental'),
            breakpoint=660, ratio=(1, 1))
        balance_body.addWidget(levels)
        for track in self.vocal_tracks[1:]:
            balance_body.addWidget(track['level_panel'])
        balance_body.addWidget(label(
            _tr('歌声太早：填正数延后；太晚：填负数提前。每一路的偏移会分别写入请求与回执。'), 'muted'))
        body.addWidget(balance_card)

        processing_card, processing_body = card(
            _tr('人声处理与空间'),
            _tr('三项默认关闭。建议先试听干声与伴奏的平衡，再一项一项打开，避免一次改变太多。'),
            '3')
        self.eq_enabled = QCheckBox(_tr('人声更清晰（EQ）'))
        self.eq_enabled.setObjectName(CONTROL_NAMES['eq_enabled'])
        self.eq_enabled.setAccessibleName(_tr('启用均衡器'))
        self.compression_enabled = QCheckBox(_tr('动态更稳定（压缩）'))
        self.compression_enabled.setObjectName(CONTROL_NAMES['compression_enabled'])
        self.compression_enabled.setAccessibleName(_tr('启用压缩'))
        self.reverb_enabled = QCheckBox(_tr('增加空间感（混响）'))
        self.reverb_enabled.setObjectName(CONTROL_NAMES['reverb_enabled'])
        self.reverb_enabled.setAccessibleName(_tr('启用混响'))
        for control, text in (
                (self.eq_enabled, _tr('轻微削减低频并提升一点咬字存在感。')),
                (self.compression_enabled, _tr('让强弱更均匀，避免人声忽大忽小。')),
                (self.reverb_enabled, _tr('加入少量空间，不让声音完全干贴在伴奏上。'))):
            processing_body.addWidget(control)
            processing_body.addWidget(label(text, 'muted'))

        self.reverb_profile = QComboBox()
        self.reverb_profile.setObjectName('mixdownReverbProfile')
        self.reverb_profile.setAccessibleName(_tr('混响空间'))
        for title, key in ((_tr('短空间'), 'small'), (_tr('自然空间'), 'natural'),
                           (_tr('宽阔空间'), 'wide')):
            self.reverb_profile.addItem(title, key)
        self.reverb_profile.setCurrentIndex(1)
        space_row = QHBoxLayout()
        space_row.addWidget(label(_tr('空间类型')))
        space_row.addWidget(self.reverb_profile, 1)
        processing_body.addLayout(space_row)

        self.low_cut = QDoubleSpinBox()
        self.low_cut.setObjectName(CONTROL_NAMES['low_cut'])
        self.low_cut.setAccessibleName(_tr('低频切除频率'))
        self.low_cut.setRange(20.0, 500.0)
        self.low_cut.setDecimals(0)
        self.low_cut.setSingleStep(5.0)
        self.low_cut.setSuffix(' Hz')
        self.low_cut.setValue(80.0)
        self.presence = QDoubleSpinBox()
        self.presence.setObjectName(CONTROL_NAMES['presence'])
        self.presence.setAccessibleName(_tr('咬字存在感增益'))
        self.presence.setRange(-6.0, 6.0)
        self.presence.setDecimals(1)
        self.presence.setSingleStep(0.5)
        self.presence.setSuffix(' dB')
        self.presence.setValue(1.0)
        self.compression = QDoubleSpinBox()
        self.compression.setObjectName(CONTROL_NAMES['compression'])
        self.compression.setAccessibleName(_tr('压缩强度百分比'))
        self.compression.setRange(0.0, 100.0)
        self.compression.setDecimals(0)
        self.compression.setSingleStep(5.0)
        self.compression.setSuffix(' %')
        self.compression.setValue(25.0)
        self.reverb = QDoubleSpinBox()
        self.reverb.setObjectName(CONTROL_NAMES['reverb'])
        self.reverb.setAccessibleName(_tr('混响混合百分比'))
        self.reverb.setRange(0.0, 100.0)
        self.reverb.setDecimals(0)
        self.reverb.setSingleStep(5.0)
        self.reverb.setSuffix(' %')
        self.reverb.setValue(10.0)

        advanced_content = QWidget()
        advanced_content.setObjectName('mixdownAdvancedPanel')
        advanced_grid = QGridLayout(advanced_content)
        advanced_grid.setContentsMargins(0, 8, 0, 0)
        advanced_grid.setHorizontalSpacing(12)
        advanced_grid.setVerticalSpacing(8)
        advanced_grid.setColumnStretch(1, 1)
        advanced_grid.setColumnStretch(3, 1)
        advanced_grid.addWidget(label(_tr('低频切除')), 0, 0)
        advanced_grid.addWidget(self.low_cut, 0, 1)
        advanced_grid.addWidget(label(_tr('咬字存在感')), 0, 2)
        advanced_grid.addWidget(self.presence, 0, 3)
        advanced_grid.addWidget(label(_tr('压缩强度')), 1, 0)
        advanced_grid.addWidget(self.compression, 1, 1)
        advanced_grid.addWidget(label(_tr('混响混合')), 1, 2)
        advanced_grid.addWidget(self.reverb, 1, 3)
        advanced_grid.addWidget(label(
            _tr('保守起点：低切 80 Hz、存在感 +1.0 dB、压缩 25%、混响 10%。'), 'muted'),
            2, 0, 1, 4)
        processing_body.addWidget(label(_tr('需要细调时再展开；关闭上面的开关即可完全旁通。'), 'muted'))
        self.advanced_toggle = fold(_tr('高级处理值'), advanced_content, processing_body, expanded=False)
        self.advanced_toggle.setObjectName(CONTROL_NAMES['advanced_toggle'])
        self.advanced_toggle.setAccessibleName(_tr('展开高级处理值'))
        self.advanced_toggle.setProperty('role', 'muted')
        body.addWidget(processing_card)

        preview_card, preview_body = card(
            _tr('片段试听'),
            _tr('先渲染同一段“处理前 / 处理后”，再用系统播放器比较。处理前保留音量与位置。'),
            '4')
        preview_range_content = QWidget()
        preview_range_content.setObjectName('mixdownPreviewRangePanel')
        preview_range = QGridLayout(preview_range_content)
        preview_range.setContentsMargins(0, 8, 0, 0)
        preview_range.setHorizontalSpacing(12)
        preview_range.setVerticalSpacing(8)
        self.preview_start = QDoubleSpinBox()
        self.preview_start.setObjectName(CONTROL_NAMES['preview_start'])
        self.preview_start.setAccessibleName(_tr('试听起点秒数'))
        self.preview_start.setRange(0.0, 86400.0)
        self.preview_start.setDecimals(3)
        self.preview_start.setSingleStep(0.5)
        self.preview_start.setSuffix(' 秒')
        self.preview_start.setValue(0.0)
        self.preview_duration = QDoubleSpinBox()
        self.preview_duration.setObjectName(CONTROL_NAMES['preview_duration'])
        self.preview_duration.setAccessibleName(_tr('试听时长秒数'))
        self.preview_duration.setRange(0.1, 120.0)
        self.preview_duration.setDecimals(2)
        self.preview_duration.setSingleStep(1.0)
        self.preview_duration.setSuffix(' 秒')
        self.preview_duration.setValue(12.0)
        preview_range.addWidget(label(_tr('片段起点')), 0, 0)
        preview_range.addWidget(self.preview_start, 0, 1)
        preview_range.addWidget(label(_tr('片段时长')), 0, 2)
        preview_range.addWidget(self.preview_duration, 0, 3)
        preview_range.addWidget(label(_tr('先默认截取开头 12 秒；可展开改成副歌。'), 'muted'), 1, 0, 1, 4)
        self.preview_range_toggle = fold(
            _tr('试听范围'), preview_range_content, preview_body, expanded=False)
        self.preview_range_toggle.setObjectName(CONTROL_NAMES['preview_range_toggle'])
        self.preview_range_toggle.setAccessibleName(_tr('展开试听范围'))
        self.preview_range_toggle.setProperty('role', 'muted')

        preview_actions = FlowLayout(spacing=10)
        self.preview_button = button(_tr('渲染 A/B 试听片段'), self.preview, 'primary')
        self.preview_button.setObjectName(CONTROL_NAMES['preview_render'])
        self.preview_button.setAccessibleName(_tr('渲染处理前后试听片段'))
        preview_actions.addWidget(self.preview_button)
        self.preview_report = label(_tr('尚未生成试听片段。'), 'muted')
        self.preview_report.setObjectName(CONTROL_NAMES['preview_status'])
        preview_body.addLayout(preview_actions)
        preview_body.addWidget(self.preview_report)

        def compare_panel(title, hint, button_text, callback, button_name, state_name):
            panel = QWidget()
            panel.setObjectName('step-surface')
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(16, 14, 16, 14)
            panel_layout.setSpacing(7)
            heading = label(title, 'muted')
            heading.setStyleSheet('font-weight: 600;')
            panel_layout.addWidget(heading)
            panel_layout.addWidget(label(hint, 'muted'))
            action = button(button_text, callback)
            action.setObjectName(button_name)
            action.setAccessibleName(title + _tr('试听'))
            action.setEnabled(False)
            state = label(_tr('等待渲染'), 'muted')
            state.setObjectName(state_name)
            panel_layout.addWidget(action)
            panel_layout.addWidget(state)
            return panel, action, state

        before_panel, self.preview_before_button, self.preview_before_state = compare_panel(
            _tr('处理前'), _tr('保留音量与位置，关闭 EQ、压缩和混响。'),
            _tr('播放处理前'), lambda: self.play_preview('before'),
            CONTROL_NAMES['preview_before'], CONTROL_NAMES['preview_before_state'])
        after_panel, self.preview_after_button, self.preview_after_state = compare_panel(
            _tr('处理后'), _tr('使用上方当前的人声与空间设置。'),
            _tr('播放处理后'), lambda: self.play_preview('after'),
            CONTROL_NAMES['preview_after'], CONTROL_NAMES['preview_after_state'])
        preview_body.addWidget(AdaptiveColumns(
            before_panel, after_panel, breakpoint=620, ratio=(1, 1)))
        body.addWidget(preview_card)

        export_card, export_body = card(
            _tr('导出与登记'),
            _tr('导出到一个新的 WAV。完成不会自动登记；确认试听后，再明确登记。登记不把任务中的分轨或 MIDI 伪造成这次混音的上游。'),
            '5')
        output_grid = QGridLayout()
        output_grid.setContentsMargins(0, 0, 0, 0)
        output_grid.setHorizontalSpacing(10)
        output_grid.setVerticalSpacing(8)
        output_grid.setColumnStretch(1, 1)
        output = QLineEdit()
        output.setObjectName(CONTROL_NAMES['output_path'])
        output.setAccessibleName(_tr('新的混音 WAV 文件名'))
        output.setPlaceholderText(_tr('选择新的 WAV 文件名'))
        output.setClearButtonEnabled(True)
        output_choose = button(_tr('选择…'), lambda checked=False: self._choose_path('output'))
        output_choose.setObjectName(CONTROL_NAMES['browse_output'])
        output_choose.setAccessibleName(_tr('选择新的混音输出文件'))
        self.fields['output'] = output
        self._choose_buttons['output'] = output_choose
        self.output_state = label(_tr('尚未指定输出文件'), 'muted')
        self.output_state.setObjectName(CONTROL_NAMES['output_state'])
        output_grid.addWidget(label(_tr('新的混音 WAV')), 0, 0)
        output_grid.addWidget(output, 0, 1)
        output_grid.addWidget(output_choose, 0, 2)
        output_grid.addWidget(self.output_state, 1, 1, 1, 2)
        export_body.addLayout(output_grid)

        export_actions = FlowLayout(spacing=10)
        self.export_button = button(_tr('导出 WAV'), self.export, 'primary')
        self.export_button.setObjectName(CONTROL_NAMES['export'])
        self.export_button.setAccessibleName(_tr('导出新的混音 WAV'))
        self.cancel_button = button(_tr('取消当前处理'), self.cancel, 'danger')
        self.cancel_button.setObjectName(CONTROL_NAMES['cancel'])
        self.cancel_button.setEnabled(False)
        export_actions.addWidget(self.export_button)
        export_body.addLayout(export_actions)

        result_panel = QWidget()
        result_panel.setObjectName('step-surface')
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(16, 14, 16, 14)
        result_layout.setSpacing(7)
        self.result_title = label(_tr('尚无导出结果'), 'muted')
        self.result_title.setStyleSheet('font-weight: 600;')
        self.result_title.setObjectName(CONTROL_NAMES['result_title'])
        self.result_text = label(_tr('选择两个输入 WAV 和新输出文件名后导出。'), 'muted')
        self.result_text.setObjectName(CONTROL_NAMES['result_status'])
        self.result_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_text)
        export_body.addWidget(result_panel)

        export_body.addWidget(label(
            _tr('登记是单独的一步：只登记本次导出的文件，不改动源 WAV；实际选择的源路径、指纹与处理参数会另存为回执。'),
            'muted'))
        register_actions = FlowLayout(spacing=10)
        self.register_button = button(_tr('只登记为任务 Mixdown'), lambda: self.register(False))
        self.register_button.setObjectName(CONTROL_NAMES['register'])
        self.register_button.setAccessibleName(_tr('登记本次导出为任务混音文件'))
        self.to_video_button = button(_tr('登记并进入视频合成'), self.continue_to_video, 'primary')
        self.to_video_button.setObjectName(CONTROL_NAMES['register_video'])
        self.to_video_button.setAccessibleName(_tr('登记本次导出并进入视频合成'))
        self.register_button.setEnabled(False)
        self.to_video_button.setEnabled(False)
        register_actions.addWidget(self.register_button)
        register_actions.addWidget(self.to_video_button)
        export_body.addLayout(register_actions)
        body.addWidget(export_card)
        body.addStretch(1)

        footer = QWidget()
        footer.setObjectName('footer')
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 10, 18, 10)
        footer_layout.setSpacing(7)
        self.status = label(_tr('从选择素材开始；每一步都可以返回检查。'), 'muted')
        self.status.setObjectName(CONTROL_NAMES['status'])
        footer_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setObjectName(CONTROL_NAMES['progress'])
        self.progress.setRange(0, 0)
        self.progress.hide()
        footer_layout.addWidget(self.progress)
        footer_actions = FlowLayout(spacing=10)
        footer_actions.addWidget(self.cancel_button)
        self.close_button = button(_tr('关闭'), self.accept)
        self.close_button.setObjectName(CONTROL_NAMES['close'])
        self.close_button.setVisible(not self.embedded)
        footer_actions.addWidget(self.close_button)
        footer_layout.addLayout(footer_actions)
        outer.addWidget(footer)

        track_controls = [control for track in self.vocal_tracks
                          for control in (track['gain_db'], track['slider'], track['offset'])]
        self._editable_widgets = list(self.fields.values()) + list(self._choose_buttons.values()) + [
            *track_controls, self.instrumental_db, self.instrumental_slider,
            self.eq_enabled, self.low_cut, self.presence,
            self.compression_enabled, self.compression, self.reverb_enabled, self.reverb,
            self.reverb_profile,
            self.advanced_toggle, self.preview_start, self.preview_duration,
            self.preview_range_toggle]

        self._syncing = True
        try:
            # Never infer an external voicebank render from the task's original
            # source separation. The user must choose the dry vocal explicitly.
            instrumental = _task_source(self.job, ('audio/instrumental.wav',))
            if instrumental:
                self.fields['instrumental'].setText(str(instrumental))
                self._auto_instrumental = instrumental
            self._auto_output = str(_suggested_output(self.job))
            self.fields['output'].setText(self._auto_output)
            self.fields['output'].setToolTip(_tr('导出前会再次检查；任何已存在文件都不会被覆盖。'))
        finally:
            self._syncing = False

        for key, field in self.fields.items():
            field.textChanged.connect(self.output_changed if key == 'output' else self.parameters_changed)
        for control in (*track_controls, self.instrumental_db, self.low_cut,
                        self.presence, self.compression, self.reverb):
            control.valueChanged.connect(self.parameters_changed)
        for control in (self.preview_start, self.preview_duration):
            control.valueChanged.connect(self.preview_parameters_changed)
        for control in (self.eq_enabled, self.compression_enabled, self.reverb_enabled):
            control.toggled.connect(self.parameters_changed)
        self.reverb_profile.currentIndexChanged.connect(self.parameters_changed)
        self._set_vocal_track_count(1, announce=False)
        self._update_source_states()
        self._update_output_state()
        self._refresh_component_support()
        self._sync_processing_controls()
        self._ui_ready = True
        self._sync_controls()

    @property
    def job(self):
        return self._job

    @job.setter
    def job(self, value):
        self._job = Path(value).resolve() if value else None
        if self._ui_ready:
            self._job_context_changed()

    def set_job(self, job):
        """Associate or clear a task without changing editable mix inputs."""
        if self.busy():
            return False
        self.job = job
        return True

    def _job_context_changed(self):
        if not self._ui_ready:
            return
        input_changed = False
        instrumental = _task_source(self.job, ('audio/instrumental.wav',))
        if instrumental:
            current = self._path('instrumental')
            if current is None or _same_path(current, self._auto_instrumental):
                self._syncing = True
                try:
                    input_changed = not _same_path(current, instrumental)
                    self.fields['instrumental'].setText(str(instrumental))
                    self._auto_instrumental = instrumental
                finally:
                    self._syncing = False
        if input_changed:
            self.parameters_changed()
        if self._result is None and self.fields['output'].text() == self._auto_output:
            replacement = _suggested_output(self.job)
            self._syncing = True
            try:
                self.fields['output'].setText(str(replacement))
                self._auto_output = str(replacement)
            finally:
                self._syncing = False
        self.status.setText(
            _tr('已关联任务；导出后可按需登记。') if self.job
            else _tr('独立文件模式；无需任务也可导出并交给视频合成。'))
        _set_role(self.status, 'muted')
        self._update_source_states()
        self._update_output_state()
        self._sync_controls()

    def active_vocal_tracks(self):
        count = getattr(self, '_vocal_track_count', 1)
        return list(self.vocal_tracks[:count])

    def _set_vocal_track_count(self, count, announce=True):
        count = max(1, min(_MAX_VOCAL_TRACKS, int(count)))
        self._vocal_track_count = count
        self._syncing = True
        try:
            for track in self.vocal_tracks:
                active = track['index'] < count
                track['panel'].setVisible(active)
                track['level_panel'].setVisible(active)
                track['remove'].setVisible(active and track['index'] > 0
                                            and track['index'] == count - 1)
                if not active:
                    track['path'].clear()
                    track['gain_db'].setValue(0.0)
                    track['offset'].setValue(0.0)
            next_count = count + 1
            self.add_vocal_button.setVisible(next_count <= _MAX_VOCAL_TRACKS)
            self.add_vocal_button.setText(
                _tr('添加第 {0} 路歌声').format(next_count)
                if next_count <= _MAX_VOCAL_TRACKS else _tr('最多 3 路歌声'))
        finally:
            self._syncing = False
        if announce and self._ui_ready:
            self.parameters_changed()

    def add_vocal_track(self):
        if self.busy():
            return
        count = getattr(self, '_vocal_track_count', 1)
        if count >= _MAX_VOCAL_TRACKS:
            return
        self._set_vocal_track_count(count + 1)
        self.status.setText(_tr('已增加一路可选歌声；未填写路径时不会参与混音。'))
        _set_role(self.status, 'muted')

    def remove_vocal_track(self, index=None):
        if self.busy():
            return
        count = getattr(self, '_vocal_track_count', 1)
        if index is None:
            index = count - 1
        if index <= 0 or index >= count:
            return
        if index != count - 1:
            self.status.setText(_tr('请从最后一路歌声开始移除。'))
            _set_role(self.status, 'error')
            return
        self._set_vocal_track_count(count - 1)
        self.status.setText(_tr('已移除最后一路歌声；其路径、音量和偏移已清空。'))
        _set_role(self.status, 'muted')

    def busy(self):
        return self.worker is not None

    def _refresh_component_support(self):
        root, spec, installer = _component_manifest()
        self._component_root, self._component_spec = root, spec
        self._component_installer = installer
        packages = _optional_packages(root, spec) if root and spec else None
        self._component_packages = packages
        ready, detail = _activate_pedalboard(packages)
        self._support_ready = ready
        self._support_error = '' if ready else detail
        self.support_panel.setVisible(not ready)
        if ready:
            self.support_state.setText(_tr('混音组件已就绪'))
            _set_role(self.support_state, 'success')
            self.support_state.setToolTip('Pedalboard ' + detail)
            self.install_dependency_button.hide()
            return
        size = ''
        try:
            size = _tr('（下载约 {0:.1f} MiB）').format(float(spec['wheel']['bytes']) / 1024 / 1024)
        except (KeyError, TypeError, ValueError):
            size = ''
        self.support_state.setText(
            _tr('首次使用请安装混音组件') + size + _tr('，安装后即可试听和导出。'))
        _set_role(self.support_state, 'muted')
        self.support_state.setToolTip(detail)
        available = bool(installer and installer.is_file())
        self.install_dependency_button.setVisible(available)
        self.install_dependency_button.setEnabled(available and not self.busy())
        self.install_dependency_button.setText(
            _tr('修复混音组件') if packages else _tr('安装混音组件（约 3.5 MiB）'))

    def install_dependency(self):
        if self.busy():
            return
        if not self._component_installer or not self._component_installer.is_file():
            self.support_state.setText(_tr('当前版本没有可用的混音组件安装器；请稍后重新检查。'))
            _set_role(self.support_state, 'error')
            return
        root, installer = self._component_root, self._component_installer
        force = bool(self._component_packages)

        def run(check):
            return _run_component_installer('install', root, installer, check, force=force)

        self.support_panel.show()
        self.support_state.setText(_tr('正在下载、校验并安装混音组件…'))
        _set_role(self.support_state, 'muted')
        self.status.setText(_tr('正在后台安装混音组件；可在底栏取消。'))
        _set_role(self.status, 'muted')
        self._launch('component', run)

    def _sync_processing_controls(self):
        active = self.busy()
        self.low_cut.setEnabled(not active and self.eq_enabled.isChecked())
        self.presence.setEnabled(not active and self.eq_enabled.isChecked())
        self.compression.setEnabled(not active and self.compression_enabled.isChecked())
        self.reverb.setEnabled(not active and self.reverb_enabled.isChecked())
        self.reverb_profile.setEnabled(not active and self.reverb_enabled.isChecked())

    def _source_ready(self, key):
        path = self._path(key)
        optional_vocal = key.startswith('vocal_')
        if path is None:
            return optional_vocal
        if not path.is_file():
            return False
        return key == 'instrumental' or path.suffix.lower() == '.wav'

    def _sources_ready(self):
        try:
            self._validated_sources()
        except (OSError, ValueError):
            return False
        return True

    def _active_source_paths(self):
        paths = [self._path(track['field_key']) for track in self.active_vocal_tracks()]
        paths.append(self._path('instrumental'))
        return [path for path in paths if path is not None]

    def _output_ready(self):
        output = self._path('output')
        if not output or output.suffix.lower() != '.wav' or output.exists():
            return False
        return not any(_same_path(output, path) for path in self._active_source_paths())

    def _update_source_states(self):
        for track in self.active_vocal_tracks():
            number = track['index'] + 1
            path = self._path(track['field_key'])
            state = track['state']
            if not path:
                state.setText(_tr('尚未选择') if number == 1
                              else _tr('可选 · 未填写时不参与混音'))
                _set_role(state, 'muted')
            elif not path.is_file():
                state.setText(_tr('找不到文件，请重新选择'))
                _set_role(state, 'error')
            elif path.suffix.lower() != '.wav':
                state.setText(_tr('歌声干声需要 WAV 文件'))
                _set_role(state, 'error')
            else:
                state.setText(_tr('已找到：') + path.name)
                _set_role(state, 'success')
        path = self._path('instrumental')
        state = self.source_states['instrumental']
        if not path:
            state.setText(_tr('尚未选择'))
            _set_role(state, 'muted')
        elif not path.is_file():
            state.setText(_tr('找不到文件，请重新选择'))
            _set_role(state, 'error')
        else:
            state.setText(_tr('已找到：') + path.name)
            _set_role(state, 'success')

    def _update_output_state(self):
        output = self._path('output')
        if not output:
            message, role = _tr('尚未指定输出文件'), 'muted'
        elif output.suffix.lower() != '.wav':
            message, role = _tr('请使用 .wav 扩展名'), 'error'
        elif output.exists():
            message, role = _tr('文件已存在；导出会拒绝覆盖'), 'error'
        elif any(_same_path(output, path) for path in self._active_source_paths()):
            message, role = _tr('不能与源文件相同'), 'error'
        else:
            message, role = _tr('将创建新文件，不会覆盖已有输出'), 'success'
        self.output_state.setText(message)
        _set_role(self.output_state, role)

    def _sync_controls(self):
        active = self.busy()
        for widget in self._editable_widgets:
            widget.setEnabled(not active)
        self._sync_processing_controls()
        sources_ready = self._sources_ready()
        self.preview_button.setEnabled(not active and sources_ready and self._support_ready)
        ready = bool(self._preview_result and not active)
        self.preview_before_button.setEnabled(ready)
        self.preview_after_button.setEnabled(ready)
        self.export_button.setEnabled(not active and sources_ready and self._support_ready
                                  and self._output_ready())
        self.cancel_button.setVisible(active and self._busy_kind in ('preview', 'export', 'component'))
        self.cancel_button.setEnabled(active and self._busy_kind in ('preview', 'export', 'component')
                                       and not self.worker.cancel_event.is_set())
        if hasattr(self, 'install_dependency_button'):
            self.install_dependency_button.setEnabled(
                not active and not self._support_ready and bool(self._component_installer))
        self.progress.setVisible(active)
        self.progress.setRange(0, 0)
        exported = bool(self._result and not active)
        can_register = bool(exported and self.job and (self.job / 'request.json').is_file())
        self.register_button.setVisible(bool(self.job))
        self.register_button.setEnabled(can_register)
        self.to_video_button.setEnabled(exported)
        self.to_video_button.setText(
            _tr('登记并进入视频合成') if self.job else _tr('进入视频合成'))
        self.add_vocal_button.setEnabled(not active and self._vocal_track_count < _MAX_VOCAL_TRACKS)
        for track in self.active_vocal_tracks():
            track['remove'].setEnabled(not active)
        # The close action stays available while busy so it can request cooperative
        # cancellation and wait for the owned thread to finish.
        self.close_button.setEnabled(True)

    def parameters_changed(self, *args):
        if self._syncing or self.busy():
            return
        had_result = self._result is not None
        self._result = None
        self._export_context = None
        self._export_receipt = None
        self._update_source_states()
        self._update_output_state()
        if had_result:
            self.result_title.setText(_tr('参数已变化'))
            self.result_text.setText(_tr('请重新导出；磁盘上此前的成品不会被删除。'))
            _set_role(self.result_title, 'muted')
        self.invalidate_preview(_tr('参数已变化，请重新渲染 A/B 试听片段。'))
        self._sync_controls()

    def output_changed(self, *args):
        if self._syncing or self.busy():
            return
        had_result = self._result is not None
        self._result = None
        self._export_context = None
        self._export_receipt = None
        self._update_output_state()
        if had_result:
            self.result_title.setText(_tr('输出位置已变化'))
            self.result_text.setText(_tr('请重新导出；此前文件仍保留在原位置。'))
            _set_role(self.result_title, 'muted')
        self._sync_controls()

    def preview_parameters_changed(self, *args):
        if self._syncing or self.busy():
            return
        self.invalidate_preview(_tr('试听范围已变化，请重新渲染 A/B 试听片段。'))
        self._sync_controls()

    def invalidate_preview(self, message):
        self._preview_result = None
        self.preview_before_button.setEnabled(False)
        self.preview_after_button.setEnabled(False)
        self.preview_report.setText(message)
        _set_role(self.preview_report, 'muted')
        self.preview_before_state.setText(_tr('待重新渲染'))
        self.preview_after_state.setText(_tr('待重新渲染'))
        _set_role(self.preview_before_state, 'muted')
        _set_role(self.preview_after_state, 'muted')
        directory, self._preview_directory = self._preview_directory, None
        _discard_preview_directory(directory)

    def _choose_path(self, key):
        if self.busy():
            return
        current = self.fields[key].text().strip()
        if key == 'output':
            path, _ = QFileDialog.getSaveFileName(
                self, _tr('选择新 WAV 文件名'), current, _WAV_FILTER)
            if path and not path.lower().endswith('.wav'):
                path += '.wav'
        else:
            is_vocal = key == 'vocal' or key.startswith('vocal_')
            path, _ = QFileDialog.getOpenFileName(
                self, _tr('选择') + self.fields[key].accessibleName(), current,
                _VOCAL_FILTER if is_vocal else _INSTRUMENTAL_FILTER)
        if path:
            self.fields[key].setText(str(Path(path).expanduser().resolve()))

    def _path(self, key):
        text = self.fields[key].text().strip().strip('"')
        return Path(text).expanduser().resolve() if text else None

    def _vocal_specs(self):
        specs = []
        for track in self.active_vocal_tracks():
            path = self._path(track['field_key'])
            if path is None:
                continue
            specs.append({
                'path': str(path),
                'gain_db': float(track['gain_db'].value()),
                'offset_seconds': float(track['offset'].value()),
            })
        return specs

    def _validated_sources(self):
        vocals = self._vocal_specs()
        instrumental = self._path('instrumental')
        if not vocals:
            raise ValueError('请至少选择一路歌声干声 WAV。')
        if len(vocals) > _MAX_VOCAL_TRACKS:
            raise ValueError('最多只能混入 3 路歌声。')
        if not instrumental:
            raise ValueError('请选择伴奏 WAV 或常见音频文件。')
        for number, vocal in enumerate(vocals, 1):
            path = Path(vocal['path'])
            if not path.is_file():
                raise ValueError('第 {0} 路歌声文件不存在或不可读取。'.format(number))
            if path.suffix.lower() != '.wav':
                raise ValueError('第 {0} 路歌声必须选择 WAV 文件。'.format(number))
        if not instrumental.is_file():
            raise ValueError('伴奏文件不存在或不可读取。')
        vocal_keys = [_normal_path_key(item['path']) for item in vocals]
        if len(set(vocal_keys)) != len(vocal_keys):
            raise ValueError('同一歌声文件不能重复添加。')
        backing_key = _normal_path_key(instrumental)
        if backing_key in vocal_keys:
            raise ValueError('歌声与伴奏必须选择不同文件。')
        return vocals, instrumental

    def _validated_paths(self):
        vocals, instrumental = self._validated_sources()
        output = self._path('output')
        if not output:
            raise ValueError('请指定新的导出 WAV 文件名。')
        if output.suffix.lower() != '.wav':
            raise ValueError('导出文件必须使用 .wav 扩展名。')
        if output.exists():
            raise FileExistsError('导出目标已存在；请换一个文件名，现有文件不会被覆盖。')
        if any(_same_path(output, path) for path in
               [item['path'] for item in vocals] + [instrumental]):
            raise ValueError('导出文件不能与任一源文件相同。')
        return vocals, instrumental, output

    def _request(self, output, *, preview=False, bypass_processing=False):
        use_eq = self.eq_enabled.isChecked() and not bypass_processing
        use_compression = self.compression_enabled.isChecked() and not bypass_processing
        use_reverb = self.reverb_enabled.isChecked() and not bypass_processing
        vocals = self._vocal_specs()
        request = {
            'vocals': vocals,
            'instrumental_path': str(self._path('instrumental')),
            'output_path': str(Path(output).resolve()),
            'instrumental_db': float(self.instrumental_db.value()),
            'eq_low_cut_hz': float(self.low_cut.value()) if use_eq else 0.0,
            'eq_presence_db': float(self.presence.value()) if use_eq else 0.0,
            'compression_strength': float(self.compression.value()) / 100.0 if use_compression else 0.0,
            'reverb_mix': float(self.reverb.value()) / 100.0 if use_reverb else 0.0,
            'reverb_profile': self.reverb_profile.currentData(),
        }
        if preview:
            request['preview_start_seconds'] = float(self.preview_start.value())
            request['preview_duration_seconds'] = float(self.preview_duration.value())
        return request

    def _launch(self, kind, function):
        if self.busy():
            self.status.setText(_tr('已有混音任务正在运行；请等待完成或先取消。'))
            _set_role(self.status, 'error')
            return False
        worker = MixdownTask(function, self)
        self.worker = worker
        self._busy_kind = kind
        worker.succeeded.connect(lambda result, kind=kind: self.succeeded(kind, result))
        worker.failed.connect(lambda text, kind=kind: self.failed(kind, text))
        worker.cancelled.connect(lambda kind=kind: self.cancelled(kind))
        worker.finished.connect(self.finished)
        self._sync_controls()
        worker.start()
        return True

    def _signatures_unchanged(self, expected):
        return all(Path(path).is_file() and _source_signature(path) == signature
                   for path, signature in expected.items())

    def export(self):
        if self.busy():
            return
        try:
            vocals, instrumental, output = self._validated_paths()
            source_paths = [Path(item['path']).resolve() for item in vocals] + [instrumental]
            temporary = output.with_name(
                '.' + output.stem + '.' + uuid.uuid4().hex + '.part.wav')
            if any(_same_path(temporary, path) for path in (*source_paths, output)):
                raise ValueError('临时导出路径与输入或目标冲突，已拒绝开始。')
            request = self._request(temporary)
            final_request = self._request(output)
            expected = {path: _source_signature(path) for path in source_paths}
            vocal_inputs = []
            for vocal in vocals:
                path = Path(vocal['path']).resolve()
                vocal_inputs.append({
                    'path': str(path), 'bytes': expected[path][0],
                    'mtime_ns': expected[path][1],
                })
            self._export_context = {
                'parameters': final_request,
                'inputs': {
                    'vocals': vocal_inputs,
                    'instrumental_path': {'path': str(instrumental),
                                          'bytes': expected[instrumental][0],
                                          'mtime_ns': expected[instrumental][1]},
                },
            }
            self._export_receipt = None
        except (OSError, ValueError) as error:
            self.status.setText(_tr('无法开始导出：') + str(error))
            _set_role(self.status, 'error')
            return

        def run(check):
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                report = _render_once(request, check)
                _validate_render(report, temporary)
                _validate_render_source_hashes(report, source_paths)
                _check_cancel(check)
                if not self._signatures_unchanged(expected):
                    raise ValueError('处理期间源 WAV 发生了变化；没有发布成品，请重新检查输入。')
                if output.exists():
                    raise FileExistsError('导出目标在渲染期间出现；没有覆盖该文件。')
                _publish_without_overwrite(temporary, output)
                report.update(output_path=str(output), source_files_unchanged=True)
                return report
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass

        self.result_title.setText(_tr('正在导出'))
        self.result_text.setText(_tr('正在后台处理；关闭窗口会先请求停止。'))
        _set_role(self.result_title, 'muted')
        self.status.setText(_tr('正在导出新的 WAV，源文件不会被修改…'))
        _set_role(self.status, 'muted')
        self._launch('export', run)

    def preview(self):
        if self.busy():
            return
        try:
            vocals, instrumental = self._validated_sources()
            source_paths = [Path(item['path']).resolve() for item in vocals] + [instrumental]
            expected = {path: _source_signature(path) for path in source_paths}
            if self.preview_duration.value() <= 0:
                raise ValueError('试听片段时长必须大于 0。')
        except (OSError, ValueError) as error:
            self.status.setText(_tr('无法生成试听片段：') + str(error))
            _set_role(self.status, 'error')
            return

        self.invalidate_preview(_tr('正在准备新的 A/B 片段…'))
        directory = Path(tempfile.mkdtemp(prefix=_PREVIEW_PREFIX))
        before_path, after_path = directory / 'before.wav', directory / 'after.wav'
        before = self._request(before_path, preview=True, bypass_processing=True)
        after = self._request(after_path, preview=True, bypass_processing=False)

        def run(check):
            try:
                before_report = _render_once(before, check)
                _validate_render(before_report, before_path)
                _validate_render_source_hashes(before_report, source_paths)
                _check_cancel(check)
                after_report = _render_once(after, check)
                _validate_render(after_report, after_path)
                _validate_render_source_hashes(after_report, source_paths)
                if not self._signatures_unchanged(expected):
                    raise ValueError('处理期间源 WAV 发生了变化，请重新选择后再试听。')
                return {'before': before_report, 'after': after_report,
                        'directory': str(directory)}
            except Exception:
                _discard_preview_directory(directory)
                raise

        self._preview_directory = directory
        self.preview_report.setText(_tr('正在后台预渲染处理前与处理后片段…'))
        self.preview_before_state.setText(_tr('正在渲染'))
        self.preview_after_state.setText(_tr('正在渲染'))
        self.status.setText(_tr('正在渲染 A/B 片段；完成后可用系统播放器试听。'))
        _set_role(self.status, 'muted')
        self._launch('preview', run)

    def _build_export_receipt(self, report):
        context = dict(self._export_context or {})
        output = Path(report.get('output_path', '')).resolve()
        info = output.stat()
        parameters = dict(context.get('parameters') or {})
        parameters['output_path'] = str(output)
        inputs = dict(context.get('inputs') or {})
        hashes = report.get('source_sha256') or {}
        hashes = {_normal_path_key(path): value for path, value in hashes.items()}

        def with_hash(entry):
            saved = dict(entry)
            saved['sha256'] = hashes.get(_normal_path_key(saved['path']))
            return saved

        vocals = [with_hash(entry) for entry in inputs.get('vocals', [])]
        backing = with_hash(inputs['instrumental_path'])
        saved_inputs = {'vocals': vocals, 'instrumental_path': backing}
        return {
            'schema': 'vwa.audio_mixdown.receipt/v2',
            'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'backend': 'audio_mixdown.render',
            'registration': 'explicit_user_action',
            'dependencies': [],
            'inputs': saved_inputs,
            'parameters': parameters,
            'output': {'path': str(output), 'bytes': info.st_size,
                       'mtime_ns': info.st_mtime_ns, 'sha256': report.get('output_sha256')},
            'render_result': {
                'duration_seconds': report.get('duration_seconds'),
                'peak_dbfs': report.get('peak_dbfs'),
            },
        }

    def succeeded(self, kind, result):
        if kind == 'export':
            self._result = result
            self._export_receipt = self._build_export_receipt(result)
            self.result_title.setText(
                _tr('导出完成；尚未登记到任务') if self.job
                else _tr('导出完成；可直接进入视频合成'))
            self.result_text.setText(
                _report_detail(result) + '\n' + _tr('文件：') + str(result.get('output_path', '')))
            _set_role(self.result_title, 'success')
            self.status.setText(
                _tr('WAV 已安全导出。检查试听后，可显式登记为任务 Mixdown。')
                if self.job else _tr('WAV 已安全导出；无需任务即可进入视频合成。'))
            _set_role(self.status, 'success')
            return
        if kind == 'preview':
            self._preview_result = {'before': result['before'], 'after': result['after']}
            self._preview_directory = Path(result['directory'])
            self.preview_report.setText(
                _tr('处理前：') + _report_detail(result['before']) + '\n' +
                _tr('处理后：') + _report_detail(result['after']) + '\n' +
                _tr('将使用系统默认播放器；播放器窗口可能复用显示。'))
            self.preview_before_state.setText(_tr('已就绪 · 可播放'))
            self.preview_after_state.setText(_tr('已就绪 · 可播放'))
            _set_role(self.preview_report, 'success')
            _set_role(self.preview_before_state, 'success')
            _set_role(self.preview_after_state, 'success')
            self.status.setText(_tr('A/B 片段已生成，可分别播放比较。'))
            _set_role(self.status, 'success')
            return
        if kind == 'component':
            packages = result.get('packages')
            ready, detail = _activate_pedalboard(packages)
            self._support_ready = ready
            self._component_packages = Path(packages).resolve() if packages and Path(packages).is_dir() else None
            self.support_panel.setVisible(not ready)
            if ready:
                self.support_state.setText(_tr('混音组件已就绪'))
                self.support_state.setToolTip('Pedalboard ' + detail)
                _set_role(self.support_state, 'success')
                self.install_dependency_button.hide()
                self.status.setText(_tr('混音组件已安装并通过导入检查，可以直接预渲染或导出。'))
                _set_role(self.status, 'success')
            else:
                self._support_error = detail
                self.support_state.setText(detail)
                _set_role(self.support_state, 'error')
                self.status.setText(_tr('混音组件安装完成，但导入检查失败：') + detail)
                _set_role(self.status, 'error')
            return
        if kind == 'register':
            self.registered = True
            destination = Path(result).resolve()
            if self._pending_navigation:
                try:
                    handed_off = self._handoff_to_video(destination)
                except (OSError, ValueError, TypeError) as error:
                    self.status.setText(_tr('已登记 Mixdown，但进入视频合成失败：') + str(error))
                    return
                self.navigate_requested = True
                if not handed_off:
                    self.status.setText(
                        _tr('已登记 Mixdown，但视频页已有其他 Mixdown；未覆盖当前编辑输入。'))
                    _set_role(self.status, 'error')
                    return
            self.status.setText(_tr('已显式登记任务 Mixdown；真实输入与参数回执已保存，未添加伪造上游。'))
            _set_role(self.status, 'success')
            if not self.embedded:
                self._completion_action = 'accept'

    def _handoff_to_video(self, mixdown):
        video = getattr(self.owner, 'video_mixdown', None)
        navigate = getattr(self.owner, 'navigate', None)
        if video is None:
            raise ValueError('当前窗口没有可用的视频合成页。')
        busy = getattr(video, 'busy', None)
        preview_busy = getattr(video, 'preview_busy', None)
        if ((callable(busy) and busy())
                or (callable(preview_busy) and preview_busy())):
            raise ValueError('视频合成页正在处理或预览，请完成或取消后再传入 Mixdown。')
        loaded = None
        if self.job and callable(getattr(video, 'use_source_job', None)):
            # Loading the task can fill its source video. If the task lacks a
            # video, preserve any existing editable value and only seed an empty
            # field so the user can still choose the video manually.
            loaded = video.use_source_job(self.job, mixdown)
        fields = getattr(video, 'fields', None)
        mixdown_field = fields.get('mixdown') if isinstance(fields, dict) else None
        if mixdown_field is None:
            raise ValueError('视频合成页没有可写入的 Mixdown 字段。')
        destination = str(Path(mixdown).resolve())
        if loaded is not False:
            mixdown_field.setText(destination)
        elif not mixdown_field.text().strip():
            mixdown_field.setText(destination)
        elif not _same_path(mixdown_field.text(), destination):
            return False
        if not self.job or loaded is False:
            # Existing video files may remain useful, but a previous task must
            # not silently claim an independently exported mix as its own.
            if hasattr(video, 'source_job'):
                video.source_job = None
            refresh_context = getattr(video, '_set_context', None)
            if callable(refresh_context):
                refresh_context()
        if not callable(navigate):
            raise ValueError('当前窗口没有可用的页面导航。')
        page = getattr(self.owner, 'pages', None)
        index = page.indexOf(video) if page is not None else -1
        navigate(index if index >= 0 else 8)
        self.video_requested = True
        return True

    def open_video(self):
        """Send the current export to video composition without a task."""
        if self.busy():
            return False
        if not self._result or not Path(self._result.get('output_path', '')).is_file():
            self.status.setText(_tr('请先安全导出 WAV，再进入视频合成。'))
            _set_role(self.status, 'error')
            return False
        try:
            handed_off = self._handoff_to_video(self._result['output_path'])
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(_tr('进入视频合成失败：') + str(error))
            _set_role(self.status, 'error')
            return False
        self.navigate_requested = True
        if not handed_off:
            self.status.setText(_tr('已进入视频合成，但保留页面原有的 Mixdown；未覆盖正在编辑的输入。'))
            _set_role(self.status, 'error')
            return False
        self.status.setText(_tr('已将本次 WAV 传给视频合成；任务关联是可选的。'))
        _set_role(self.status, 'success')
        return True

    def continue_to_video(self):
        if self.job:
            self.register(True)
            return self.busy()
        else:
            return self.open_video()

    def failed(self, kind, text):
        if kind == 'export':
            self.result_title.setText(_tr('导出失败'))
            self.result_text.setText(text)
            _set_role(self.result_title, 'error')
        elif kind == 'preview':
            self.preview_report.setText(_tr('试听片段生成失败：') + text)
            self.preview_before_state.setText(_tr('生成失败'))
            self.preview_after_state.setText(_tr('生成失败'))
            _set_role(self.preview_report, 'error')
            _set_role(self.preview_before_state, 'error')
            _set_role(self.preview_after_state, 'error')
        elif kind == 'component':
            self._support_error = text
            self.support_panel.show()
            self.support_state.setText(_tr('混音组件安装未完成：') + text)
            _set_role(self.support_state, 'error')
        self.status.setText(_tr('混音操作失败：') + text)
        _set_role(self.status, 'error')

    def cancelled(self, kind):
        if kind == 'export':
            self.result_title.setText(_tr('导出已取消'))
            self.result_text.setText(_tr('未发布新的导出文件；源文件与已有文件保持不变。'))
            _set_role(self.result_title, 'muted')
        elif kind == 'preview':
            self.preview_report.setText(_tr('试听片段生成已取消。'))
            self.preview_before_state.setText(_tr('已取消'))
            self.preview_after_state.setText(_tr('已取消'))
            _set_role(self.preview_report, 'muted')
        elif kind == 'component':
            self.support_panel.show()
            self.support_state.setText(_tr('混音组件安装已取消；已下载内容可由安装器继续校验。'))
            _set_role(self.support_state, 'muted')
        self.status.setText(_tr('混音操作已取消。'))
        _set_role(self.status, 'muted')

    def finished(self):
        worker = self.sender()
        if worker is not None and worker is not self.worker:
            return
        self.worker = None
        self._busy_kind = None
        if worker is not None:
            worker.deleteLater()
        self._sync_controls()
        if self._completion_action == 'accept' and not self.embedded:
            self._completion_action = None
            self.accept()
        elif self._close_pending:
            self._close_pending = False
            self.reject()

    def cancel(self):
        if not self.busy():
            return
        if self._busy_kind == 'register':
            self.status.setText(_tr('正在写入任务登记，请等待当前文件检查完成。'))
            _set_role(self.status, 'muted')
            return
        self.worker.cancel()
        self.cancel_button.setEnabled(False)
        if self._busy_kind == 'component':
            self.status.setText(_tr('正在取消组件安装…'))
        else:
            self.status.setText(_tr('已请求取消；会等待混音后端返回并清理本次临时 WAV。'))
        _set_role(self.status, 'muted')

    def play_preview(self, key):
        item = (self._preview_result or {}).get(key)
        if not item:
            return
        path = Path(item.get('output_path', ''))
        if not path.is_file():
            self.preview_report.setText(_tr('试听片段已不存在，请重新渲染。'))
            _set_role(self.preview_report, 'error')
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status.setText(_tr('系统播放器未能打开试听片段。'))
            _set_role(self.status, 'error')
        else:
            self.status.setText(_tr('已将试听片段交给系统默认播放器：') + path.name)
            _set_role(self.status, 'success')

    def register(self, navigate=False):
        if self.busy():
            return
        if not self._result or not Path(self._result.get('output_path', '')).is_file():
            self.status.setText(_tr('请先安全导出 WAV，再显式登记。'))
            return
        if not self.job or not (self.job / 'request.json').is_file():
            self.status.setText(_tr('当前混音没有可登记的现有任务。'))
            return
        try:
            from task_store import locked
            storage = getattr(self.owner, 'storage', None)
            publishing = getattr(self.owner, 'publishing', None)
            storage_busy = callable(getattr(storage, 'busy', None)) and storage.busy()
            publishing_busy = callable(getattr(publishing, 'busy', None)) and publishing.busy()
            occupied = (locked(self.job) or getattr(self.owner, 'processing_active', False)
                        or storage_busy or publishing_busy)
            if not occupied:
                from results_ui import active_task_operations
                operations = active_task_operations(self.owner, self.job)
                occupied = bool(operations)
            if occupied:
                raise ValueError('该任务仍被处理、页面预览、缓存管理或发布流程占用，请结束后再登记。')
        except (OSError, ValueError) as error:
            self.status.setText(_tr('暂时不能登记：') + str(error))
            _set_role(self.status, 'error')
            return
        destination = Path(self._result['output_path']).resolve()
        self._pending_navigation = bool(navigate)
        job = self.job
        receipt = dict(self._export_receipt or {})

        def run(check):
            _check_cancel(check)
            from task_store import locked
            if locked(job):
                raise ValueError('任务在登记开始前进入运行状态；本次没有写入任务登记。')
            expected_sha256 = (receipt.get('output') or {}).get('sha256')
            if expected_sha256 and _file_sha256(destination, check) != expected_sha256:
                raise ValueError('导出 WAV 在登记前发生变化；请重新导出后再登记。')
            from task_contract import bind, update_metadata
            # The external dry-vocal mixdown is not an automatic child of the
            # task's separated audio, MIDI or score. Keep provenance explicit.
            artifact_id = bind(job, 'mixdown', destination, dependencies=[])
            _check_cancel(check)
            saved = dict(receipt)
            saved['artifact_id'] = artifact_id
            saved['registered_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
            update_metadata(job, audio_mixdown=saved)
            _check_cancel(check)
            return str(destination)

        self.status.setText(_tr('正在显式登记到任务并校验 WAV…'))
        self._launch('register', run)

    def _retire_preview(self):
        directory, self._preview_directory = self._preview_directory, None
        self._preview_result = None
        _discard_preview_directory(directory)

    def done(self, result):
        if self.busy():
            self._close_pending = True
            self.cancel()
            return
        self._retire_preview()
        super().done(result)

    def closeEvent(self, event):
        if self.busy():
            event.ignore()
            self._close_pending = True
            self.cancel()
            return
        self._retire_preview()
        super().closeEvent(event)


MixdownDialog = AudioMixdownDialog

__all__ = ['AudioMixdownDialog', 'CONTROL_NAMES', 'MixdownDialog', 'MixdownTask']
