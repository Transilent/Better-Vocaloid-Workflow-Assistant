"""A cancellable worker lane that delivers only the latest progress to Qt."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal
from operation_diagnostics import bind, clock_text, describe, failure_fields, meter, phase, unbind
from i18n import tr


@dataclass
class _Job:
    kind: str
    stop: Event
    finish: object
    progress: object
    future: object = None
    latest: object = None
    operation: dict = None
    started: float = 0.
    last_progress: float = 0.
    last_event: float = 0.
    last_signal: float = 0.
    detail: str = ''
    signature: tuple = ()
    stage: str = ''
    measure: dict = None
    public: dict = None
    outcome: object = None


def _activity(value, kind):
    if isinstance(value, str):
        return value, '', {}
    if not isinstance(value, dict):
        return '', '', {}
    stage = phase(value.get('phase') or value.get('current') or value.get('stage'))
    text = value.get('message') or value.get('detail') or ''
    if type(value.get('media_seconds')) is int and 0 <= value['media_seconds'] <= 10**7:
        text = tr('{0} · 已处理到 {1}', tr(text), clock_text(value['media_seconds']))
    unit = value.get('unit')
    if unit not in ('bytes', 'items', 'media', 'fraction'):
        unit = 'items'
    if kind == 'preparation':
        progress = value.get('stage_progress') or {}
        ratio = meter(progress.get('done'), progress.get('total'), progress.get('unit', 'items'))
    elif 'fraction' in value and value.get('phase') in ('mix', 'render'):
        ratio = meter(value.get('fraction'), 1, 'fraction')
    else:
        ratio = meter(value.get('done'), value.get('total'), unit)
    if type(value.get('media_seconds')) is int and 0 <= value['media_seconds'] <= 10**7:
        ratio['media_seconds'] = value['media_seconds']
    return str(text)[:420], stage, ratio


class WorkerLane(QObject):
    changed = Signal()
    activityChanged = Signal()

    def __init__(self, parent, name, *, journal=None, namespace=''):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._lock = Lock()
        self._job = None
        self._closed = False
        self.completing_failure = False
        self._journal = journal
        self._namespace = namespace
        self._timer = QTimer(self)
        self._timer.setInterval(42)
        self._timer.timeout.connect(self._tick)

    @property
    def kind(self):
        return self._job.kind if self._job is not None else ''

    @property
    def activity(self):
        job = self._job
        if job is None:
            return {}
        now = monotonic()
        return {**job.operation, **job.public, **job.measure, 'detail': job.detail, 'phase': job.stage,
                'elapsed': int(now - job.started), 'quiet': int(now - job.last_progress),
                'cancelling': job.stop.is_set(), 'label': describe(job.operation['kind'])[0]}

    def start(self, kind, work, finish, progress=None, *, label='', route='', facts=None, outcome=None):
        if self._closed or self._job is not None:
            return False
        observed_kind = self._namespace + kind
        stamp = monotonic()
        operation = {'id': uuid4().hex[:12], 'kind': observed_kind,
                     'route': route or describe(observed_kind)[1]}
        job = _Job(kind, Event(), finish, progress, operation=operation, started=stamp,
                   last_progress=stamp, last_event=stamp, detail=label, measure={}, public=dict(facts or {}),
                   outcome=outcome)
        if self._journal is not None:
            self._journal.event('started', operation=operation, **job.public)

        def send(value):
            with self._lock:
                job.latest = value

        def run():
            token = bind(self._journal, operation)
            try:
                return work(job.stop, send)
            finally:
                unbind(token)

        job.future = self._executor.submit(run)
        self._job = job
        self._timer.start()
        self.changed.emit()
        self.activityChanged.emit()
        return True

    def cancel(self):
        job = self._job
        if job is not None and not job.stop.is_set():
            job.stop.set()
            if self._journal is not None:
                self._journal.event('cancel_requested', operation=job.operation, elapsed=self.activity['elapsed'],
                                    phase=job.stage, **job.measure)
            self.activityChanged.emit()

    def _observe(self, job, value):
        detail, stage, measure = _activity(value, job.kind)
        if not detail and not stage and not measure:
            return
        if detail:
            job.detail = detail
        if stage:
            job.stage = stage
        changed = (detail, job.stage, measure)
        if changed != job.signature:
            now = monotonic()
            phase_changed = (job.stage, measure.get('unit'), measure.get('total')) != (
                job.signature[1] if job.signature else '', job.measure.get('unit'), job.measure.get('total'))
            job.last_progress, job.signature, job.measure = now, changed, measure
            if phase_changed or now - job.last_signal >= .25:
                job.last_signal = now
                self.activityChanged.emit()
            if self._journal is not None and (phase_changed or now - job.last_event >= 15):
                job.last_event = now
                self._journal.event('progress', operation=job.operation, phase=job.stage,
                                    elapsed=int(now - job.started), **job.measure)

    def _record_end(self, job, state, error=None, *, raw='', code=''):
        if self._journal is None:
            return
        evidence = failure_fields(error, raw=raw, code=code) if state == 'failed' else {}
        if state == 'cancelled':
            evidence = {'code': 'cancelled'}
        self._journal.event('finished', operation=job.operation, state=state, phase=job.stage, **job.public,
                            elapsed=int(monotonic() - job.started),
                            since_progress=int(monotonic() - job.last_progress), **job.measure, **evidence)

    def _tick(self):
        job = self._job
        if job is None:
            return
        finished = job.future.done()
        with self._lock:
            latest, job.latest = job.latest, None
        if latest is not None:
            self._observe(job, latest)
            if job.progress is not None:
                try:
                    job.progress(latest)
                except Exception as error:
                    if self._journal is not None:
                        self._journal.event('failure', operation=job.operation, phase=job.stage,
                                            state='failed', **failure_fields(error, code='callback'))
        if not finished:
            return
        self._job = None
        self._timer.stop()
        value, error = None, None
        try:
            value = job.future.result()
        except Exception as exc:
            error = exc
        try:
            self.completing_failure = error is not None
            job.finish(value, error)
            if isinstance(error, InterruptedError):
                self._record_end(job, 'cancelled')
            elif error is not None:
                self._record_end(job, 'failed', error)
            elif job.outcome is not None:
                state, raw = job.outcome(value)
                self._record_end(job, state, raw=raw if isinstance(raw, str) else '',
                                 error=raw if isinstance(raw, BaseException) else None)
            else:
                self._record_end(job, 'succeeded')
        except Exception as callback_error:
            self._record_end(job, 'failed', callback_error, code='callback')
        finally:
            self.completing_failure = False
            self.changed.emit()
            self.activityChanged.emit()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self.cancel()
        job = self._job
        self._executor.shutdown(wait=True, cancel_futures=True)
        if job is not None:
            self._record_end(job, 'interrupted')
        self._job = None
