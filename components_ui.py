"""Optional model controls, with cancellable installation outside the GUI thread."""
import threading
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QProgressBar, QMenu, QToolButton
from desktop_theme import button, card, label, page
import optional_components as components


class Installer(QThread):
    progress = pyqtSignal(object, object, str)
    value = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, runtime, force=False):
        super().__init__()
        self.runtime, self.force = runtime, force
        self.stop = threading.Event()

    def run(self):
        try:
            self.value.emit(components.install(self.runtime, self.progress.emit, self.stop.is_set, force=self.force))
        except Exception as exc:
            self.failed.emit(str(exc))


class ComponentsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.worker = owner, None
        container, layout = page('模型与组件', '基础方案已内置。按需要下载新模型，安装后可离线处理音乐。')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        base, body = card('Karaoke 2', '主唱／和声分离 · 已内置，无需额外下载', '01')
        body.addWidget(label('适合快速处理。先分出总人声和伴奏，再拆主唱与和声。', 'muted'))
        body.addWidget(button('使用内置方案', lambda: self.use('dual')))
        layout.addWidget(base)
        extra, body = card('BS-RoFormer', '主唱／和声分离 · 可选组件', '02')
        body.addWidget(label('保留原模型权重；中日文使用同一流程。静音主唱轨可能是合理结果，完成后请试听。', 'muted'))
        self.badge = label('', 'success')
        body.addWidget(self.badge)
        row = QHBoxLayout()
        row.addWidget(label('运行库'))
        self.runtime = QComboBox()
        self.runtime.addItem('NVIDIA GPU / CUDA', 'cuda')
        self.runtime.addItem('仅 CPU', 'cpu')
        row.addWidget(self.runtime, 1)
        body.addLayout(row)
        self.size = label('', 'muted')
        body.addWidget(self.size)
        self.runtime.currentIndexChanged.connect(self.refresh)
        self.message = label('')
        body.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        body.addWidget(self.progress)
        actions = QHBoxLayout()
        self.download = button('下载并安装', self.activate, 'primary')
        self.cancel = button('取消下载', self.request_cancel, 'danger')
        self.cancel.hide()
        actions.addWidget(self.download)
        actions.addWidget(self.cancel)
        actions.addStretch()
        self.more = QToolButton()
        self.more.setText('更多')
        menu = QMenu(self.more)
        menu.addAction('重新安装所选运行库', lambda: self.start_install(force=True))
        self.more.setMenu(menu)
        self.more.setPopupMode(QToolButton.InstantPopup)
        actions.addWidget(self.more)
        body.addLayout(actions)
        layout.addWidget(extra)
        layout.addWidget(label('网络中断后可重试续传。运行库保存在助手目录中，复制完整助手目录时可一并迁移。', 'muted'))
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
        self.size.setText(f'下载约 {total / 10**9:.2f} GB · 安装约 {spec["install_bytes"][runtime] / 10**9:.2f} GB · 请预留 {((total + spec["install_bytes"][runtime]) / 10**9 + .54):.1f} GB 空间')
        self.badge.setText('已安装 · ' + ('NVIDIA GPU / CUDA' if installed['runtime'] == 'cuda' else 'CPU') if installed['ready'] else '尚未安装')
        same = installed['ready'] and installed['runtime'] == runtime
        self.download.setText('使用此模型' if same else '下载并安装')
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

    def start_install(self, force=False):
        if self.busy():
            return
        if self.owner.runner and self.owner.runner.isRunning():
            self.message.setText('请等待当前音乐任务结束后再安装组件。')
            return
        self.worker = Installer(self.runtime.currentData(), force)
        self.worker.progress.connect(self.update_progress)
        self.worker.value.connect(lambda value: self.message.setText('安装完成。选择“使用此模型”即可开始处理。'))
        self.worker.failed.connect(self.message.setText)
        self.worker.finished.connect(self.finished)
        self.download.setEnabled(False)
        self.runtime.setEnabled(False)
        self.more.setEnabled(False)
        self.cancel.show()
        self.cancel.setEnabled(True)
        self.progress.show()
        self.message.setText('正在准备下载…')
        self.worker.start()
        self.owner.update_component_state()

    def update_progress(self, done, total, text):
        self.progress.setValue(int(done * 100 / max(total, 1)))
        self.message.setText(f'{text} · {done / 10**9:.2f} / {total / 10**9:.2f} GB' if '下载' in text else text)

    def finished(self):
        self.download.setEnabled(True)
        self.runtime.setEnabled(True)
        self.more.setEnabled(True)
        self.cancel.hide()
        self.refresh()

    def request_cancel(self):
        if self.worker:
            self.worker.stop.set()
            self.message.setText('正在取消，已下载数据会保留。')
            self.cancel.setEnabled(False)

    def busy(self):
        return self.worker is not None and self.worker.isRunning()
