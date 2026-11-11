"""Application-owned seven-track calibration; the SV handoff survives navigation."""

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

import calibration_session as sessions
import expression_component
from calibration_workflow import inspect_inputs, recovery_hint
from common import file_hash
from expression_calibration import CONTROLS
from i18n import tr
from quick_work import WorkerLane


_TRIALS = {'neutral': '基准', 'loudness': '力度', 'tension': '张力',
           'breathiness': '气声', 'voicing': '发声', 'gender': '音色', 'toneShift': '音色偏移'}
_CHANNELS = ('standard', 'sv-extra', 'sv-isolated')


def _tracks(path):
    rows = []
    for index, track in enumerate(sessions.project_data(path)['tracks']):
        if not track.get('mainRef', {}).get('isInstrumental'):
            rows.append({'index': index, 'name': str(track.get('name') or f'演唱轨 {len(rows) + 1}'),
                         'notes': len(track.get('mainGroup', {}).get('notes', []))})
    if not rows:
        raise ValueError('工程中没有演唱轨。')
    return rows


def _check_stop(stop):
    if stop is not None and stop.is_set():
        raise InterruptedError('已停止校准。会话和已导出的文件会保留。')


def _resume(path, stop):
    spec = sessions.read_session(path)
    _check_stop(stop)
    sessions.unchanged_inputs(spec)
    tracks = _tracks(spec['project'])
    track = next((i for i, row in enumerate(tracks) if row['index'] == spec['track_index']), None)
    if track is None:
        raise ValueError('会话对应的演唱轨不存在。')
    trial = sessions.project_data(path.parent / 'calibration.svp')
    if {row.get('name') for row in trial.get('tracks', [])} != set(spec['outputs'].values()):
        raise ValueError('七轨试音工程与校准会话不匹配。')
    result = sessions.verified_fit_result(path, cancel=lambda: _check_stop(stop))
    return path, spec, tracks, track, result


class QuickCalibration(QObject):
    changed = Signal()

    def __init__(self, parent, tuning):
        super().__init__(parent)
        self.worker = WorkerLane(self, 'vwa-calibration')
        self.worker.changed.connect(self.changed)
        self.tuning = tuning
        tuning.changed.connect(self.changed)
        self.project = self.reference = self.folder = self.renders = self.job = ''
        self.tracks, self.track = [], 0
        self.voice, self.channels, self.strength = 'lead', 'standard', .6
        self.session, self.spec, self.result, self.review = None, None, None, None
        self.message, self.hint = '', ''
        self.phase, self.files = 'setup', {}

    @Property('QVariantMap', notify=changed)
    def view(self):
        fit = (self.result or {}).get('calibration_fit', {})
        trial_rows = [{'id': key, 'name': tr(_TRIALS.get(key, key)), 'state': self.files.get(key, 'missing')}
                      for key in ('neutral', *CONTROLS)]
        return {'busy': bool(self.worker.kind), 'project': self.project, 'reference': self.reference,
                'folder': self.folder, 'renders': self.renders, 'tracks': self.tracks, 'track': self.track,
                'voice': self.voice, 'channels': self.channels, 'strength': self.strength,
                'session': str(self.session or ''), 'phase': self.phase, 'message': self.message,
                'hint': self.hint, 'trials': trial_rows,
                'stable': sum(row['state'] in ('stable', 'validated') for row in trial_rows),
                'canUseTrack': bool(self.tuning.calibration_seed()),
                'canInspect': bool(self.project and self.reference and self.tracks),
                'review': self.review or {}, 'result': (self.result or {}).get('output', ''),
                'neutral': (self.result or {}).get('neutral', ''), 'measured': fit.get('voicebank_calibrated') is True,
                'canBind': bool(fit.get('voicebank_calibrated') is True and self.tuning.can_bind_calibration(self.spec)),
                'report': (self.result or {}).get('report_path', '')}

    def _command(self, kind, label, work, finish):
        if self.worker.kind:
            return
        self.message, self.hint = tr(label), ''

        def progress(value):
            if isinstance(value, dict):
                self.phase, self.files = value['phase'], value['files']
                self.message = value.get('message') or (tr('正在拟合声库参数…') if self.phase == 'fitting' else
                                tr('等待分轨导出：{count} / 7 份文件已稳定。').format(count=value['done']))
            else:
                self.message = tr(str(value))
            self.changed.emit()

        def complete(value, error):
            if error is None:
                try:
                    finish(value)
                except (OSError, ValueError, TypeError, KeyError) as failure:
                    error = failure
            if error is not None:
                self.message = tr(str(error)[:1400])
                self.hint = '' if isinstance(error, InterruptedError) else tr(recovery_hint(str(error)))
                self.phase = 'ready' if self.session else 'setup'
            self.changed.emit()

        self.worker.start(kind, work, complete, progress)

    def _choose_project(self, path, seed=None):
        if self.session or self.worker.kind or Path(path).suffix.lower() != '.svp':
            return
        path = str(Path(path).resolve())

        def work(stop, _send):
            _check_stop(stop)
            rows = _tracks(path)
            if seed and file_hash(path, check=stop.is_set) != seed['sha256']:
                raise ValueError('当前调音工程已改变，请重新打开工程。')
            return rows

        def finish(rows):
            self.project, self.tracks = path, rows
            self.track, self.job, self.review = 0, '', None
            self.reference = ''
            self.voice = 'vocals' if len(rows) == 1 else 'lead'
            if seed:
                self.track = next(i for i, row in enumerate(rows) if row['index'] == seed['index'])
                self.job, self.reference, self.voice = seed['job'], seed['reference'], seed['voice']
            self.message = tr('选择参考演唱和输出位置，检查输入后准备试音。')

        self._command('loading', '正在读取工程与演唱轨…', work, finish)

    @Slot(QUrl)
    def chooseProject(self, url):
        if url.isLocalFile():
            self._choose_project(url.toLocalFile())

    @Slot()
    def useCurrentTrack(self):
        seed = self.tuning.calibration_seed()
        if seed:
            self._choose_project(seed['path'], seed)

    @Slot(QUrl)
    def chooseReference(self, url):
        if not self.session and not self.worker.kind and url.isLocalFile():
            self.reference, self.review = url.toLocalFile(), None
            self.changed.emit()

    @Slot(QUrl)
    def chooseFolder(self, url):
        if not self.session and not self.worker.kind and url.isLocalFile():
            self.folder = url.toLocalFile()
            self.changed.emit()

    @Slot(QUrl)
    def chooseRenders(self, url):
        if self.session and not self.worker.kind and url.isLocalFile():
            self.renders, self.files = url.toLocalFile(), {}
            self.changed.emit()

    @Slot(int)
    def selectTrack(self, track):
        if not self.session and not self.worker.kind and 0 <= track < len(self.tracks) and track != self.track:
            self.track, self.review = track, None
            self.changed.emit()

    @Slot(str)
    def selectVoice(self, voice):
        if not self.session and not self.worker.kind and voice in ('lead', 'backing', 'vocals'):
            self.voice, self.review = voice, None
            self.changed.emit()

    @Slot(str)
    def selectChannels(self, mode):
        if not self.worker.kind and mode in _CHANNELS:
            self.channels, self.files = mode, {}
            self.changed.emit()

    @Slot(float)
    def setStrength(self, strength):
        if not self.worker.kind and 0 < strength <= 1:
            self.strength = strength
            self.changed.emit()

    def _adopt(self, value):
        self.session, self.spec, self.tracks, self.track, self.result = value
        self.project, self.reference = self.spec['project'], self.spec['reference']
        self.folder, self.renders = str(self.session.parent), self.spec['render_folder']
        self.voice = self.spec['voice']
        self.strength = (self.result['calibration_fit']['strength'] if self.result else self.spec['strength'])
        self.channels = self.spec.get('render_channel_mode', 'standard')
        self.phase = 'done' if self.result else 'ready'
        self.files = dict.fromkeys(self.spec['outputs'], 'validated') if self.result else {}
        self.message = self._result_message() if self.result else tr('试音工程已就绪。请在 SV 分轨导出，再继续等待并拟合。')

    def _result_message(self):
        return (tr('已核验拟合工程。请在 SV 打开校准和基准工程对比试听。')
                if self.result['calibration_fit'].get('voicebank_calibrated') else
                tr('已生成并核验工程，但未取得可用的声库拟合。可打开回执查看原因。'))

    @Slot(bool)
    def checkInputs(self, prepare):
        if self.session or self.worker.kind or not self.view['canInspect'] or prepare and not self.folder:
            return
        project, reference, track, voice = self.project, self.reference, self.track, self.voice
        job, strength, folder = self.job, self.strength, self.folder

        def work(stop, _send):
            _check_stop(stop)
            check = inspect_inputs(project, reference, track_index=track, job=job or None, voice=voice)
            check['fit_ready'] = expression_component.status()['ready']
            _check_stop(stop)
            if not prepare or not check['ready']:
                return check, None
            parent = Path(folder).resolve(strict=True)
            if not parent.is_dir():
                raise ValueError('请选择已存在的输出文件夹。')
            dest = parent / f"calibration-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
            path = sessions.create_session(project, reference, dest, track_index=track, voice=voice,
                                           strength=strength, register=False)
            # A completed session is adopted even if Stop was pressed during its short commit.
            return check, _resume(path, None)

        def finish(value):
            self.review, prepared = value
            if prepared:
                self._adopt(prepared)
            else:
                self.message = (tr('检查通过。可生成七轨试音工程。') if self.review['ready'] else
                                tr('请先处理检查中标为“需要处理”的项目。'))

        self._command('preparing', '正在检查乐谱、歌词和参考演唱…', work, finish)

    @Slot(QUrl)
    def resumeSession(self, url):
        if not url.isLocalFile() or self.worker.kind:
            return
        path = Path(url.toLocalFile()).resolve()
        if path.name != 'session.json':
            self.message = tr('请选择校准目录中的 session.json。')
            self.changed.emit()
            return

        def finish(value):
            self.review = None
            self._adopt(value)

        self._command('resuming', '正在核对会话、试音和已有结果…', lambda stop, _send: _resume(path, stop), finish)

    @Slot()
    def fit(self):
        if not self.session or self.worker.kind:
            return
        path, folder, channels, strength = self.session, self.renders, self.channels, self.strength

        def work(stop, send):
            def cancel():
                _check_stop(stop)

            cancel()
            if not expression_component.status()['ready']:
                raise ValueError('拟合需要 WORLD 分析器。请在“模型与组件”安装或修复参考演唱音色，再继续已有会话。')
            spec = sessions.read_session(path)
            recover = (sessions.recoverable_fit_output(path, strength=strength, channel_mode=channels)
                       if Path(folder).resolve() == Path(spec['render_folder']).resolve() else None)
            if recover:
                saved = sessions.fit_session(path, cancel=cancel, progress=send, strength=strength,
                                             output_name=recover, register=False)
            else:
                name = 'calibrated.svp'
                if (path.parent / name).exists() or (path.parent / 'fit-report.json').exists():
                    name = f"calibrated-{uuid4().hex[:10]}.svp"
                latest = {}

                def on_state(value):
                    latest.update(value)
                    latest.pop('message', None)
                    send(dict(latest))

                def progress(message):
                    send({**latest, 'message': message} if latest else message)

                saved = sessions.wait_and_fit(path, folder, cancel=cancel, progress=progress, on_state=on_state,
                                              channel_mode=channels, output_name=name, strength=strength, register=False)
            # The committed output and report are validated even if cancellation arrived during commit.
            result = sessions.verified_fit_result(path, output_name=Path(saved['output']).name)
            return sessions.read_session(path), result

        def finish(value):
            self.spec, self.result = value
            self.files = dict.fromkeys(self.spec['outputs'], 'validated')
            self.phase = 'done' if self.result else 'ready'
            self.message = (self._result_message() if self.result else
                            tr('工程已保留，结果回执尚未核验。点击继续可尝试恢复回执。'))

        self.phase, self.files = 'waiting', {}
        self._command('fitting', '正在等待七份试音文件…', work, finish)

    @Slot()
    def newSession(self):
        if not self.worker.kind and self.session:
            self.folder = str(self.session.parent.parent)
            self.session = self.spec = self.result = self.review = None
            self.renders, self.phase, self.files = '', 'setup', {}
            self.message, self.hint = tr('可调整输入后生成新会话。已有校准文件保留。'), ''
            self.changed.emit()

    @Slot()
    def cancel(self):
        if self.worker.kind:
            self.worker.cancel()
            self.message = tr('正在停止…')
            self.changed.emit()

    @Slot(str)
    def openFile(self, kind):
        if not self.session:
            return
        session = self.session
        if kind in ('trial', 'renders', 'folder'):
            path = {'trial': session.parent / 'calibration.svp', 'renders': Path(self.renders),
                    'folder': session.parent}[kind]
            if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.message = tr('系统无法打开，请按显示的路径手动打开。')
                self.changed.emit()
        elif not self.worker.kind and self.result and kind in ('output', 'neutral', 'report_path'):
            name = Path(self.result['output']).name

            def finish(result):
                if not result or not result.get(kind):
                    self.result = result
                    raise ValueError('校准结果或基准工程已改变，请重新核对。')
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(result[kind])):
                    raise ValueError('系统无法打开，请在 SV 或文件管理器中手动打开。')
                self.message = self._result_message()

            self._command('checking', '正在核验文件…',
                          lambda stop, _send: sessions.verified_fit_result(session, output_name=name,
                                                                          cancel=lambda: _check_stop(stop)), finish)

    @Slot()
    def bindResult(self):
        if self.session and self.result and not self.worker.kind and self.view['canBind']:
            self.tuning.bind_calibration_result(self.session, self.spec, self.result)

    def close(self):
        self.worker.close()
