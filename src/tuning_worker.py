"""Owned, cancellable execution of frozen tuning inputs outside the GUI."""
import copy
import threading

from PyQt5.QtCore import QThread, pyqtSignal


class TuningWorker(QThread):
    message = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, session, options, parent=None, *, preset_binding=None):
        super().__init__(parent)
        self.session = session
        self.options = copy.deepcopy(options)
        self.preset_binding = copy.deepcopy(preset_binding)
        self.stop = threading.Event()

    def check(self):
        if self.stop.is_set():
            raise InterruptedError('已停止；工程与处理记录已保留，可继续。')

    def run(self):
        try:
            from tuning_workflow import run_session
            extra = {} if self.preset_binding is None else {'preset_binding': self.preset_binding}
            self.succeeded.emit(run_session(self.session, self.options, cancel=self.check,
                                            progress=self.message.emit, **extra))
        except Exception as error:
            self.failed.emit(str(error))
