"""Startup-only runtime migration for compatible legacy update clients."""
try:
    from i18n import tr as _tr, set_language
except ImportError:
    # The allowlisted first hop contains only app.py, this installer and the
    # version record. Old clients do not yet have the new translation module.
    def _tr(source, *values):
        return source.format(*values) if values else source
    def set_language(value):
        pass
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QHBoxLayout
try:
    from ui_input import install_input_wheel_guard
except ImportError:
    from PyQt5.QtCore import QEvent, QObject
    class _LegacyWheelGuard(QObject):
        def eventFilter(self, watched, event):
            if isinstance(watched, QComboBox) and event.type() == QEvent.Wheel:
                event.accept()
                return True
            return False
    def install_input_wheel_guard(application):
        if not hasattr(application, '_vwa_legacy_wheel_guard'):
            application._vwa_legacy_wheel_guard = _LegacyWheelGuard(application)
            application.installEventFilter(application._vwa_legacy_wheel_guard)

ROOT = Path(__file__).resolve().parent


class UpgradeWorker(QThread):
    progress = pyqtSignal(int, int, str)
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, item, source, root=ROOT):
        super().__init__()
        self.item, self.source, self.root = item, source, root
        self.cancelled = threading.Event()

    def run(self):
        try:
            from tools.update_apply import prepare_runtime_upgrade
            plan = prepare_runtime_upgrade(self.item, self.root, self.progress.emit, self.cancelled.is_set, self.source)
            self.ready.emit(plan)
        except Exception as exc:
            self.failed.emit(str(exc))


class UpgradeDialog(QDialog):
    def __init__(self, root=ROOT):
        super().__init__()
        application = QApplication.instance()
        if isinstance(application, QApplication):
            install_input_wheel_guard(application)
        self.root = Path(root)
        try:
            appearance = json.loads((self.root/'cache/workspace.json').read_text(encoding='utf-8')).get('appearance', {})
            set_language(appearance.get('ui_language'))
        except (OSError, ValueError, AttributeError):
            set_language('zh-CN')
        self.item = json.loads((self.root/'version.json').read_text(encoding='utf-8'))['runtime_upgrade']
        self.worker = None
        self.close_requested = False
        self.install_started = False
        self.setWindowTitle(_tr('VWA 更新 · ') + self.item['version'])
        self.resize(660,320)
        self.setStyleSheet('QDialog { background:#f3f6f3; } QWidget { color:#263d32; font:18px "Microsoft YaHei"; } '
                          'QPushButton,QComboBox { padding:10px 16px; } QProgressBar { min-height:24px; }')
        body = QVBoxLayout(self)
        body.setContentsMargins(28,24,28,24);body.setSpacing(20)
        title = QLabel(_tr('正在更新 VWA'));title.setStyleSheet('font-size:24px; font-weight:600;')
        body.addWidget(title)
        info = QLabel(_tr('更新完成后将自动重启。\n任务、设置、登录和模型会保留。'))
        info.setWordWrap(True);body.addWidget(info)
        self.source = QComboBox()
        try:
            from download_sources import SOURCE_OPTIONS, normalize_source
        except ImportError:
            from tools.update_apply import DOWNLOAD_SOURCE_OPTIONS as SOURCE_OPTIONS
            def normalize_source(value):
                value = 'direct' if value == 'official' else value
                if value not in {code for _, code in SOURCE_OPTIONS}:
                    raise ValueError('下载方式无效。')
                return value
        for title, value in SOURCE_OPTIONS:
            self.source.addItem(_tr(title), value)
        try:
            config = json.loads((self.root/'config.json').read_text(encoding='utf-8'))
            saved_source = config.get('download_source','auto') if isinstance(config, dict) else 'auto'
        except (OSError,ValueError):
            saved_source = 'auto'
        try: saved_source = normalize_source(saved_source)
        except ValueError: saved_source = 'auto'
        self.source.setCurrentIndex(max(0,self.source.findData(saved_source)))
        body.addWidget(self.source)
        self.progress = QProgressBar();self.progress.setRange(0,1000);body.addWidget(self.progress)
        self.message = QLabel(_tr('准备下载…'));self.message.setWordWrap(True);body.addWidget(self.message)
        row = QHBoxLayout()
        self.retry = QPushButton(_tr('开始 / 重试'));self.retry.clicked.connect(self.start);row.addWidget(self.retry)
        self.cancel = QPushButton(_tr('取消 / 退出'));self.cancel.clicked.connect(self.close);row.addWidget(self.cancel)
        body.addLayout(row)
        try:
            receipt = json.loads((self.root/'cache/updates/last-result.json').read_text(encoding='utf-8'))
            if not isinstance(receipt, dict): receipt = {}
        except (OSError, ValueError):
            receipt = {}
        if receipt.get('state') == 'failed' and receipt.get('version') == self.item['version']:
            error = receipt.get('error')
            self.message.setText(_tr('上次安装未完成：') + (error if isinstance(error, str) else _tr('请检查更新日志。')) + _tr('\n处理后点击开始 / 重试。'))
        else:
            self.source.setEnabled(False)
            QTimer.singleShot(0,self.start)

    def start(self):
        if self.worker and self.worker.isRunning(): return
        self.close_requested = False
        self.retry.setEnabled(False);self.source.setEnabled(False)
        self.install_started = False
        self.message.setText(_tr('正在准备升级文件…'))
        self.worker = UpgradeWorker(self.item,self.source.currentData(),self.root)
        self.worker.progress.connect(self.update_progress)
        self.worker.failed.connect(self.failed)
        self.worker.ready.connect(self.install)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def update_progress(self,done,total,message):
        self.progress.setValue(int(done*1000/total) if total else 0)
        self.message.setText(message+f' {done/1024**2:.1f} / {total/1024**2:.1f} MB')

    def failed(self,message):
        self.message.setText(message + _tr('\n可切换下载源重试，或退出后下次继续升级。'))

    def finished(self):
        self.retry.setEnabled(True);self.source.setEnabled(True)
        if self.install_started:
            QApplication.instance().quit()
        elif self.close_requested:
            self.close()

    def install(self,plan):
        if self.close_requested: return
        runtime = self.root/'dependencies/vocal2midi/python/pythonw.exe'
        command = [str(runtime),'-B',str(Path(plan).parent/'apply_helper.py'),'--plan',str(plan),
                   '--parent',str(os.getpid()),'--launcher-parent',os.environ.get('VWA_LAUNCHER_PID',os.environ.get('BVWA_LAUNCHER_PID','0'))]
        try:
            subprocess.Popen(command,cwd=self.root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                             env=dict(os.environ,PYTHONUTF8='1',PYTHONNOUSERSITE='1'))
        except OSError as exc:
            self.failed('未能启动安装程序：'+str(exc))
            return
        # The finished handler is connected before the thread starts.
        self.install_started = True

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.close_requested = True
            self.worker.cancelled.set()
            self.message.setText(_tr('正在取消下载…'))
            event.ignore()
            return
        event.accept()


def main():
    application = QApplication.instance() or QApplication([])
    install_input_wheel_guard(application)
    window = UpgradeDialog();window.show()
    return application.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
