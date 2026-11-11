"""A cancellable worker lane that delivers only the latest progress to Qt."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass
class _Job:
    kind: str
    stop: Event
    finish: object
    progress: object
    future: object = None
    latest: object = None


class WorkerLane(QObject):
    changed = Signal()

    def __init__(self, parent, name):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._lock = Lock()
        self._job = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(42)
        self._timer.timeout.connect(self._tick)

    @property
    def kind(self):
        return self._job.kind if self._job is not None else ''

    def start(self, kind, work, finish, progress=None):
        if self._closed or self._job is not None:
            return False
        job = _Job(kind, Event(), finish, progress)

        def send(value):
            with self._lock:
                job.latest = value

        job.future = self._executor.submit(work, job.stop, send)
        self._job = job
        self._timer.start()
        self.changed.emit()
        return True

    def cancel(self):
        if self._job is not None:
            self._job.stop.set()

    def _tick(self):
        job = self._job
        if job is None:
            return
        with self._lock:
            latest, job.latest = job.latest, None
        if latest is not None and job.progress is not None:
            job.progress(latest)
        if not job.future.done():
            return
        self._job = None
        self._timer.stop()
        value, error = None, None
        try:
            value = job.future.result()
        except Exception as exc:
            error = exc
        try:
            job.finish(value, error)
        finally:
            self.changed.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._job = None
