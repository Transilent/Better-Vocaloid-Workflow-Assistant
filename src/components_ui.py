"""Optional model controls, with cancellable installation outside the GUI thread."""
from i18n import tr as _tr
import threading
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QMenu, QProgressBar, QToolButton,
                             QVBoxLayout, QWidget)
from desktop_theme import button, card, label, page, scroll_content
from common import load_config
from ui_components import AdaptiveColumns, choice
import download_sources
import optional_components as components


def download_source_options():
    """Load the shared source catalog with legacy priority modes first."""
    options = download_sources.SOURCE_OPTIONS
    if callable(options):
        options = options()
    ordered, seen = [], set()
    for row in options or ():
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            continue
        title, value = row
        if not isinstance(title, str) or not title or not isinstance(value, str) or value in seen:
            continue
        ordered.append((title, value))
        seen.add(value)
    by_value = {value: (title, value) for title, value in ordered}
    result = [by_value[value] for value in ('auto', 'direct', 'mirror') if value in by_value]
    result.extend(row for row in ordered if row[1] not in {'auto', 'direct', 'mirror'})
    if not result:
        raise ValueError('下载来源列表为空。')
    return result


def chinese_button_text(state):
    installed = bool(state and (state.get('ready') or state.get('installed')))
    return '校验 / 修复' if installed else '下载中文模型'


def _busy(page, method='busy'):
    callback = getattr(page, method, None)
    if not callable(callback):
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def blocking_reason(owner):
    """Name a running operation that must finish before installing a model."""
    runner = getattr(owner, 'runner', None)
    if (getattr(owner, 'processing_active', False)
            or (runner is not None and runner.isRunning())):
        return '音乐处理'
    checks = (
        (getattr(owner, 'storage', None), 'busy', '缓存管理'),
        (getattr(owner, 'updates', None), 'busy', '更新下载'),
        (getattr(owner, 'publishing', None), 'busy', '发布准备'),
        (getattr(owner, 'video_mixdown', None), 'busy', '视频合成'),
        (getattr(owner, 'video_mixdown', None), 'preview_busy', '视频预览'),
        (getattr(owner, 'tuning', None), 'busy', '调音'),
        (getattr(owner, 'calibration_dialog', None), 'busy', '校准'),
    )
    for page, method, label in checks:
        if _busy(page, method):
            return label
    publishing = getattr(owner, 'publishing', None)
    if _busy(publishing, 'live_browser_active'):
        return '登录或上传浏览器'
    return ''


def _format_size(value):
    value = max(0, int(value or 0))
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.2f} {unit}' if unit != 'B' else f'{value} B'
        value /= 1024
    return str(value)


class Installer(QThread):
    progress = pyqtSignal(object, object, str)
    value = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, runtime, force=False, source='auto'):
        super().__init__()
        self.runtime, self.force = runtime, force
        self.source = source
        self.stop = threading.Event()

    def run(self):
        try:
            if self.runtime == 'chinese':
                from feature_components import install
                value = install('chinese', self.progress.emit, self.stop.is_set, source=self.source)
            elif self.runtime == 'expression':
                from expression_component import install
                value = install(self.progress.emit, self.stop.is_set, source=self.source)
            else:
                value = components.install(self.runtime, self.progress.emit, self.stop.is_set,
                                           force=self.force, source=self.source)
            self.value.emit(value)
        except Exception as exc:
            self.failed.emit(str(exc))


def _set_role(widget, role):
    value = role or ''
    if widget.property('role') == value:
        return
    widget.setProperty('role', value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class ComponentsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.worker, self.active_feature = owner, None, None
        self.last_install_error = ''
        container, layout = page(_tr('模型与组件'), _tr('先使用内置方案；可选模型按实际可用性安装、校验或恢复，处理中的下载可安全取消。'))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        content = scroll_content(layout)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        base, body = card('Karaoke 2', _tr('主唱／和声分离 · 内置 · 无需下载'), '01')
        body.addWidget(label(_tr('快速分出人声和伴奏，再拆分主唱与和声。适合先完成一次可编辑的基础工程。'), 'muted'))
        body.addWidget(label(_tr('内置方案可用'), 'success'))
        body.addWidget(button(_tr('使用内置方案'), lambda: self.use('dual'), 'primary'))

        extra, body = card('BS-RoFormer', _tr('主唱／和声分离 · 可选运行库'), '02')
        body.addWidget(label(_tr('中文和日语共用此分离模型。选择运行库后安装；完成后点“使用此模型”即可切换。'), 'muted'))
        self.badge = label(_tr('正在检查组件状态…'), 'muted')
        self.badge.setAccessibleName(_tr('BS-RoFormer 组件状态'))
        body.addWidget(self.badge)

        body.addWidget(label(_tr('运行库'), 'muted'))
        self.runtime = QComboBox()
        self.runtime.addItem('NVIDIA GPU / CUDA', 'cuda')
        self.runtime.addItem(_tr('仅 CPU'), 'cpu')
        self.runtime_choice = choice(self.runtime, columns=2, compact=True)
        body.addWidget(self.runtime_choice)

        body.addWidget(label(_tr('下载方式'), 'muted'))
        self.source = QComboBox()
        for title, value in download_source_options():
            self.source.addItem(_tr(title), value)
        configured = load_config().get('download_source', 'auto')
        try:
            configured = download_sources.normalize_source(configured)
        except ValueError:
            configured = 'auto'
        self.source.setCurrentIndex(max(0, self.source.findData(configured)))
        self.source.setAccessibleName(_tr('下载方式'))
        self.source.show()
        body.addWidget(self.source)
        body.addWidget(label(_tr('自动尝试备用源，下载后校验文件完整性。'), 'muted'))
        body.addWidget(label(_tr('指定镜像仅用于对应来源；其他文件直连。优先加速源会按文件来源自动匹配。'), 'muted'))

        self.size = label('', 'muted')
        body.addWidget(self.size)
        self.runtime.currentIndexChanged.connect(self.refresh)
        self.message = label('', 'muted')
        self.message.setWordWrap(True)
        self.message.setAccessibleName(_tr('组件安装状态'))
        body.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        body.addWidget(self.progress)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.download = button(_tr('下载并安装'), self.activate, 'primary')
        self.cancel = button(_tr('取消下载'), self.request_cancel, 'danger')
        self.cancel.hide()
        actions.addWidget(self.download, 1)
        actions.addWidget(self.cancel)
        self.more = QToolButton()
        self.more.setText(_tr('更多'))
        menu = QMenu(self.more)
        menu.addAction(_tr('重新安装所选运行库'), lambda: self.start_install(force=True))
        self.more.setMenu(menu)
        self.more.setPopupMode(QToolButton.InstantPopup)
        actions.addWidget(self.more)
        body.addLayout(actions)
        layout.addWidget(AdaptiveColumns(base, extra, breakpoint=940, ratio=(1, 1)))

        language, body = card(_tr('中文歌词识别'), _tr('Qwen3-ASR · 可选中文模型'), '03')
        body.addWidget(label(_tr('用于识别中文歌词；日语识别和纯音符提取可继续使用。模型不随精简版预装。'), 'muted'))
        self.language_status = label(_tr('正在检查模型…'), 'muted')
        self.language_status.setWordWrap(True)
        self.language_status.setAccessibleName(_tr('中文歌词识别模型状态'))
        body.addWidget(self.language_status)
        self.language_progress = QProgressBar()
        self.language_progress.setRange(0, 100)
        self.language_progress.setValue(0)
        self.language_progress.setVisible(False)
        self.language_progress.setAccessibleName(_tr('中文模型下载进度'))
        body.addWidget(self.language_progress)
        language_actions = QHBoxLayout()
        language_actions.setSpacing(10)
        self.language_download = button(_tr('下载中文模型'), lambda: self.start_install(feature='chinese'), 'primary')
        self.language_cancel = button(_tr('取消下载'), lambda: self.request_cancel('chinese'), 'danger')
        self.language_cancel.hide()
        language_actions.addWidget(self.language_download, 1)
        language_actions.addWidget(self.language_cancel)
        body.addLayout(language_actions)

        expression, body = card(_tr('参考演唱音色'), _tr('WORLD · 张力、气声与发声强度'), '04')
        body.addWidget(label(_tr('从分离人声提取相对音色变化，生成可编辑的 SVP 曲线。实验功能，请结合实际试听判断效果。'), 'muted'))
        self.expression_status = label(_tr('正在检查参考组件…'), 'muted')
        self.expression_status.setAccessibleName(_tr('参考演唱音色组件状态'))
        body.addWidget(self.expression_status)
        self.expression_download = button(_tr('下载 / 校验 / 恢复 WORLD 组件'), lambda: self.start_install(feature='expression'))
        body.addWidget(self.expression_download)
        layout.addWidget(AdaptiveColumns(language, expression, breakpoint=940, ratio=(1, 1)))

        layout.addWidget(label(_tr('下载可取消，已下载内容会保留，再次安装会继续。请先结束音乐处理、调音、校准、视频操作、更新或缓存管理，再安装组件。'), 'muted'))
        layout.addStretch()

        installed = components.status()
        if installed['ready']:
            self.runtime.setCurrentIndex(self.runtime.findData(installed['runtime']))
        self.refresh()

    def refresh(self):
        spec = components.manifest()
        runtime = self.runtime.currentData()
        total = sum(item['bytes'] for item in spec['weights'] + spec['shared_wheels'] + [spec['torch_wheels'][runtime]])
        installed = components.status()
        self.size.setText(
            _tr('下载约 {0} GB · 安装约 {1} GB · 请预留 {2} GB 空间', f'{total / 10 ** 9:.2f}', f"{spec['install_bytes'][runtime] / 10 ** 9:.2f}", f"{(total + spec['install_bytes'][runtime]) / 10 ** 9 + 0.54:.1f}"))
        selected_name = 'NVIDIA GPU / CUDA' if runtime == 'cuda' else 'CPU'
        if installed['ready']:
            installed_name = 'NVIDIA GPU / CUDA' if installed['runtime'] == 'cuda' else 'CPU'
            role = 'success'
            if installed['runtime'] == runtime:
                self.badge.setText(_tr('运行库就绪 · {0}', f'{installed_name}'))
            else:
                self.badge.setText(_tr('已安装 {0} 运行库 · 当前选择 {1} 尚未安装', f'{installed_name}', f'{selected_name}'))
                role = 'muted'
        else:
            role = 'muted'
            self.badge.setText(_tr('尚未就绪 · 可安装，或先校验已有文件后恢复'))
        _set_role(self.badge, role)
        same = installed['ready'] and installed['runtime'] == runtime
        self.download.setText(_tr('使用此模型') if same else _tr('下载并安装'))

        from dependency_profiles import status
        chinese = status(['chinese'])['chinese']
        ready = bool(chinese.get('ready'))
        state = chinese.get('state')
        size = int(chinese.get('bytes') or 0)
        downloaded = int(chinese.get('downloaded_bytes') or (size if ready else chinese.get('installed_bytes') or 0))
        downloaded = min(size, max(0, downloaded))
        self.language_download.setText(chinese_button_text(chinese))
        if state == 'error':
            text = '中文模型状态无法读取，请运行依赖检查。'
            role, percent, show_progress = 'muted', 0, False
        elif ready:
            text = f'中文模型已安装 · {_format_size(size)} · 可校验或修复'
            role, percent, show_progress = 'success', 100, False
        elif chinese.get('installed') or state == 'damaged':
            text = (f'中文模型需要修复 · {chinese.get("present_files", 0)} / {chinese.get("files", 0)} 个文件'
                    f' · {_format_size(size)}')
            role, percent, show_progress = 'muted', 0, False
        elif downloaded:
            text = f'中文模型尚未就绪 · 已保留 {_format_size(downloaded)} / {_format_size(size)} · 可从断点继续'
            role, percent, show_progress = 'muted', round(downloaded * 100 / max(size, 1)), True
        else:
            text = f'中文模型未安装 · 下载约 {_format_size(size)} · 可断点续传'
            role, percent, show_progress = 'muted', 0, False
        if self.last_install_error and not ready:
            text += ' · 上次未完成：' + self.last_install_error[:180]
        if not (self.busy() and self.active_feature == 'chinese'):
            self.language_status.setText(text)
            self.language_status.setToolTip(chinese.get('error') or self.last_install_error)
            self.language_download.setEnabled(state != 'error')
            self.language_progress.setValue(percent)
            self.language_progress.setVisible(show_progress)
        _set_role(self.language_status, role)
        self.language_download.setToolTip(
            _tr('已准备 {0} / {1} 个文件。', f"{chinese.get('complete_files', 0)}", f"{chinese.get('files', 0)}"))

        from expression_component import status as expression_state, SPEC
        expression = expression_state()
        self.expression_status.setText(
            (_tr('参考音色组件已就绪') if expression['ready'] else _tr('参考音色组件尚未就绪')) + _tr(' · 下载约 {0} KiB', f"{SPEC['bytes'] / 1024:.0f}"))
        _set_role(self.expression_status, 'success' if expression['ready'] else 'muted')
        if hasattr(self.owner, 'separator_model'):
            self.owner.update_component_state()

    def use(self, model):
        self.owner.voice_mode.setCurrentIndex(self.owner.voice_mode.findData('dual'))
        self.owner.separator_model.setCurrentIndex(self.owner.separator_model.findData(model))
        self.owner.navigate(0)

    def activate(self):
        state = components.status()
        if state['ready'] and state['runtime'] == self.runtime.currentData():
            self.use('roformer')
        else:
            self.start_install()

    def start_install(self, force=False, feature=None):
        if self.busy():
            return
        reason = blocking_reason(self.owner)
        if reason:
            self.message.setText(_tr('请等待{0}结束后再安装组件。', f'{reason}'))
            return
        active = feature or self.runtime.currentData()
        self.active_feature = active
        self.last_install_error = ''
        self.worker = Installer(active, force, self.source.currentData())
        self.worker.progress.connect(self.update_progress)
        self.worker.value.connect(self.install_completed)
        self.worker.failed.connect(self.install_failed)
        self.worker.finished.connect(self.finished)
        self.download.setEnabled(False)
        self.runtime.setEnabled(False)
        self.source.setEnabled(False)
        self.more.setEnabled(False)
        self.language_download.setEnabled(False)
        self.expression_download.setEnabled(False)
        self.language_cancel.setVisible(active == 'chinese')
        self.cancel.setVisible(active != 'chinese')
        self.language_cancel.setEnabled(True)
        self.cancel.setEnabled(True)
        if active == 'chinese':
            self.language_progress.setValue(0)
            self.language_progress.show()
            self.language_status.setText(_tr('正在准备下载中文模型…'))
            self.progress.hide()
        else:
            self.progress.setValue(0)
            self.progress.show()
            self.language_progress.hide()
            self.message.setText(_tr('正在准备组件…'))
        self.worker.start()
        self.owner.update_component_state()

    def install_completed(self, value):
        self.last_install_error = ''
        if self.active_feature == 'chinese':
            self.message.setText(_tr('中文模型已就绪，可离线使用。'))
        elif self.active_feature == 'expression':
            self.message.setText(_tr('参考音色组件已就绪。可在调音页选择要保留的演唱参数。'))
        else:
            self.message.setText(_tr('安装完成。选择“使用此模型”即可开始处理。'))

    def install_failed(self, message):
        self.last_install_error = message if self.active_feature == 'chinese' else ''
        self.message.setText(message)
        if self.active_feature == 'chinese':
            self.language_status.setText(_tr('中文模型安装未完成：') + message)

    def update_progress(self, done, total, text):
        percent = min(100, max(0, int(done) * 100 // max(int(total), 1)))
        detail = f'{text} · {_format_size(done)} / {_format_size(total)}'
        if self.active_feature == 'chinese':
            self.language_progress.setValue(percent)
            self.language_progress.show()
            self.language_status.setText(detail)
            self.message.setText(detail)
        else:
            self.progress.setValue(percent)
            self.message.setText(detail)

    def finished(self):
        self.download.setEnabled(True)
        self.runtime.setEnabled(True)
        self.source.setEnabled(True)
        self.more.setEnabled(True)
        self.language_download.setEnabled(True)
        self.expression_download.setEnabled(True)
        self.language_cancel.hide()
        self.cancel.hide()
        self.refresh()

    def request_cancel(self, feature=None):
        if not self.worker:
            return
        if feature is not None and feature != self.active_feature:
            return
        self.worker.stop.set()
        self.message.setText(_tr('正在取消，已下载数据会保留。'))
        if self.active_feature == 'chinese':
            self.language_status.setText(_tr('正在取消下载，已下载部分会保留。'))
            self.language_cancel.setEnabled(False)
        else:
            self.cancel.setEnabled(False)

    def busy(self):
        return self.worker is not None and self.worker.isRunning()
