"""Qt projection of the chorus project and its resident audio transport."""
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from chorus_project import ChorusProject, Edit, Recording, Score, ROLES, VARIANTS, number
from common import ROOT
from quick_work import WorkerLane


def _path(value):
    if isinstance(value, QUrl):
        if not value.isLocalFile():
            raise ValueError('请选择本机文件。')
        value = value.toLocalFile()
    if not value:
        raise ValueError('请选择文件或目录。')
    return Path(str(value)).expanduser().resolve()


def _check(stop):
    if stop.is_set():
        raise InterruptedError('操作已取消。')


def _meter(send, phase, done, total, message):
    send({'phase': phase, 'done': done, 'total': total, 'unit': 'items', 'message': message})


class QuickChorus(QObject):
    changed = Signal()
    tracksChanged = Signal()
    phrasesChanged = Signal()
    positionChanged = Signal()
    activityChanged = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        journal = getattr(parent, 'journal', None)
        self.worker = WorkerLane(self, 'vwa-chorus', journal=journal)
        self.monitor_worker = WorkerLane(self, 'vwa-chorus-monitor', journal=journal)
        self.worker.activityChanged.connect(self.activityChanged)
        self.worker.changed.connect(self.changed)
        self.monitor_worker.activityChanged.connect(self.activityChanged)
        self.monitor_worker.changed.connect(self.changed)
        self.project = ChorusProject.fresh()
        self._saved = self.project
        self._path = None
        self._features = {}
        self._curves = {}
        self._suggestions = ()
        self._resolved = set()
        self._issue = -1
        self._undo, self._redo = [], []
        self._selected = ''
        self._view_first = 0.
        self._view_span = 1.
        self._selection = (0., 0.)
        self._mode = 'all'
        self._variant = 'original'
        self._active_variant = ''
        self._solo = ''
        self._intent = False
        self._loop = False
        self._position = 0.
        self._session = None
        self._session_geometry = None
        self._current_key = None
        self._requested = None
        self._seek_serial = None
        self._cache = OrderedDict()
        self._notice = ''
        self._error = False
        self._export = None
        self._scratch = None
        self._closed = False
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self._clock)

    @Property(bool, notify=changed)
    def dirty(self):
        return self.project != self._saved

    @property
    def source_paths(self):
        project = self.project
        return tuple(track.path for track in project.tracks) + ((project.score.path,) if project.score else ())

    @Property(float, notify=positionChanged)
    def position(self):
        return self._position

    @Property('QVariantMap', notify=activityChanged)
    def activity(self):
        return self.worker.activity or self.monitor_worker.activity

    @Property('QVariantMap', notify=changed)
    def view(self):
        project = self.project
        selected = project.track(self._selected)
        reference = project.reference(selected)
        middle = sum(self._selection) / 2
        shift = next((edit.value for edit in project.edits_for(self._selected, 'timing')
                      if edit.start <= middle < edit.end), 0.)
        score = project.score
        issues = []
        for index, row in enumerate(self._suggestions):
            if row.review and index not in self._resolved:
                track = project.track(row.track)
                if track is not None:
                    issues.append({'id': index, 'track': track.name, 'kind': row.kind, 'time': row.start,
                                   'reason': row.detail, 'canApply': row.can_apply})
        return {
            'title': project.title, 'path': str(self._path or ''), 'dirty': self.dirty,
            'first': project.start, 'last': project.end, 'rate': project.rate,
            'count': len(project.tracks), 'vocals': sum(track.role == 'vocal' for track in project.tracks),
            'guide': project.guide, 'selected': self._selected, 'take': self._track_row(selected) if selected else {},
            'reference': self._track_row(reference) if reference else {}, 'shiftMs': shift * 1000,
            'viewFirst': self._view_first, 'viewSpan': self._view_span,
            'rangeFirst': self._selection[0], 'rangeLast': self._selection[1], 'mode': self._mode,
            'variant': self._variant, 'activeVariant': self._active_variant, 'solo': self._solo,
            'playing': self._intent, 'loop': self._loop, 'loading': bool(self.monitor_worker.kind),
            'busy': bool(self.worker.kind), 'notice': self._notice, 'error': self._error,
            'undo': bool(self._undo), 'redo': bool(self._redo), 'export': str(self._export or ''),
            'score': Path(score.path).name if score else '', 'scoreOffset': score.offset if score else 0.,
            'scoreLanes': [{'id': lane.id, 'name': lane.name, 'notes': len(lane.notes)} for lane in score.lanes]
            if score else [],
            'issues': issues, 'issue': self._issue,
            'canAcceptIssue': (0 <= self._issue < len(self._suggestions)
                               and self._issue not in self._resolved and self._suggestions[self._issue].can_apply),
            'suggestions': sum(row.can_apply and index not in self._resolved
                               for index, row in enumerate(self._suggestions)),
        }

    def _track_row(self, track):
        project = self.project
        return {'id': track.id, 'name': track.name, 'role': track.role, 'part': track.part,
                'offset': track.offset, 'duration': track.duration, 'muted': track.muted,
                'scoreLane': track.score_lane, 'timeRef': track.timing_ref, 'lead': project.guide == track.id,
                'edits': sum(edit.track == track.id for edit in project.edits),
                'timing': [{'start': edit.start, 'end': edit.end, 'shift': edit.value}
                           for edit in project.edits_for(track.id, 'timing')]}

    @Property('QVariantList', notify=tracksChanged)
    def tracks(self):
        return [self._track_row(track) for track in self.project.tracks]

    @Property('QVariantList', notify=phrasesChanged)
    def phrases(self):
        return [{'start': phrase.start, 'end': phrase.end, 'name': phrase.label or f'唱段 {index + 1}'}
                for index, phrase in enumerate(self.project.phrases)]

    def _say(self, text, *, error=False):
        self._notice, self._error = str(text or ''), error
        self.changed.emit()

    def _bounds(self, first, last):
        low, high = self.project.start, self.project.end
        return max(low, min(high, min(first, last))), max(low, min(high, max(first, last)))

    def _require_track(self, identity):
        track = self.project.track(identity)
        if track is None:
            raise ValueError('请选择一条音轨。')
        return track

    def _commit(self, next_project, *, keep_suggestions=False):
        if next_project == self.project:
            return
        self._undo.append(self._revision())
        self._redo.clear()
        self._restore(next_project, keep_suggestions)

    def _revision(self):
        return (self.project, self._suggestions, frozenset(self._resolved), self._issue)

    def _load_revision(self, revision):
        project, self._suggestions, resolved, self._issue = revision
        self._resolved = set(resolved)
        self._restore(project, keep_suggestions=True)

    def _restore(self, project, keep_suggestions=False):
        previous, self.project = self.project, project
        if not keep_suggestions:
            self._suggestions, self._resolved, self._issue = (), set(), -1
        if not project.track(self._selected):
            self._selected = next((track.id for track in project.tracks if track.role == 'vocal'),
                                  project.tracks[0].id if project.tracks else '')
        if not project.track(self._solo):
            self._solo = ''
        if (previous.start, previous.end) != (project.start, project.end):
            if self._mode == 'all':
                self._view_first, self._view_span = project.start, max(.01, project.end - project.start)
            self._selection = self._bounds(*self._selection)
            if self._selection[0] == self._selection[1]:
                self._selection = (project.start, project.end)
            self._set_position(min(project.end, max(project.start, self._position)))
        self.changed.emit()
        self.tracksChanged.emit()
        if previous.phrases != project.phrases:
            self.phrasesChanged.emit()
        if self._session or self._intent:
            self._request_audio()

    def _replace(self, project, features, path):
        self._stop_session()
        self._requested = None
        self._cache.clear()
        self._curves.clear()
        self._features = features
        self._path = path
        self._saved = project
        self._undo.clear()
        self._redo.clear()
        self._mode, self._variant, self._solo = 'all', 'original', ''
        self._selection = (project.start, project.end)
        self._view_first, self._view_span = project.start, max(.01, project.end - project.start)
        self._export = None
        self._set_position(project.start)
        self._restore(project)

    def _start(self, kind, label, work, done):
        if self.worker.kind:
            self._say('请等待当前合唱操作完成，或先取消。')
            return False
        self._notice, self._error = '', False

        def finish(value, error):
            if isinstance(error, InterruptedError):
                self._say('已取消。')
            elif error is not None:
                self._say(error, error=True)
            else:
                done(value)

        return self.worker.start('chorus-' + kind, work, finish, label=label, route='chorus')

    @Slot(bool)
    def newProject(self, replace_dirty=False):
        if self.worker.kind or self.dirty and not replace_dirty:
            return
        self._replace(ChorusProject.fresh(), {}, None)
        self._say('新建合唱工程')

    @Slot(QUrl, bool)
    def openProject(self, url, replace_dirty=False):
        if self.dirty and not replace_dirty:
            return
        try:
            path = _path(url)
        except (OSError, ValueError) as error:
            self._say(error, error=True)
            return

        def work(stop, send):
            from chorus_analysis import feature_track

            check = lambda: _check(stop)
            job = ChorusProject.open(path, check, lambda name: send(f'核对素材 · {name}'))
            features = {}
            for index, track in enumerate(job.tracks):
                _meter(send, 'analyze', index, len(job.tracks), f'读取波形 · {track.name}')
                features[track.sha] = feature_track(track, check)
            check()
            return job, features

        def done(result):
            job, features = result
            self._replace(job, features, path)
            self._say('已打开合唱工程')

        self._start('open', '正在打开合唱工程', work, done)

    @Slot(QUrl)
    def saveProject(self, url=QUrl()):
        try:
            path = _path(url) if not url.isEmpty() else self._path
            if path is None:
                raise ValueError('请选择保存位置。')
            if path == self._path or path.name.endswith('.vwachorus'):
                target = path
            else:
                target = path.with_name(path.name + '.vwachorus')
            self.project.save(target)
            self._path, self._saved = target, self.project
            self._say('已保存合唱工程')
        except (OSError, ValueError) as error:
            self._say(error, error=True)

    @Slot(str)
    def nameProject(self, name):
        self._commit(replace(self.project, title=name.strip() or '未命名合唱'), keep_suggestions=True)

    @Slot(str, 'QVariantList')
    def importAudio(self, role, urls):
        try:
            paths = tuple(dict.fromkeys(_path(value) for value in urls))
            known = {Path(track.path) for track in self.project.tracks if track.role == role}
            paths = tuple(path for path in paths if path not in known)
            if not paths:
                self._say('所选音频已在此类音轨中。')
                return
        except (OSError, ValueError) as error:
            self._say(error, error=True)
            return

        def work(stop, send):
            from chorus_analysis import feature_track

            check = lambda: _check(stop)
            tracks, features = [], {}
            for index, path in enumerate(paths):
                check()
                _meter(send, 'analyze', index, len(paths), f'读取波形 · {path.name}')
                track = Recording.read(path, role)
                features[track.sha] = feature_track(track, check)
                tracks.append(track)
            check()
            return tracks, features

        def done(result):
            tracks, features = result
            self._features.update(features)
            project = self.project.add(tracks)
            if role == 'guide':
                project = replace(project, guide=tracks[0].id)
            self._commit(project)
            self._say(f'已导入 {len(tracks)} 路音频')

        self._start('import', '正在导入录音', work, done)

    @Slot(QUrl)
    def importScore(self, url):
        try:
            path = _path(url)
        except (OSError, ValueError) as error:
            self._say(error, error=True)
            return

        def work(stop, _send):
            score = Score.read(path)
            _check(stop)
            return score

        def done(score):
            project = replace(self.project, score=score, tracks=tuple(
                replace(track, score_lane='') for track in self.project.tracks))
            self._commit(project)
            self._say(f'已载入 {len(score.lanes)} 条谱轨，请把人声关联到对应声部。')

        self._start('score', '正在读取谱轨和速度', work, done)

    @Slot(float)
    def offsetScore(self, span):
        if self.project.score:
            self._commit(replace(self.project, score=replace(self.project.score, offset=number(span))))

    @Slot(str)
    def selectTrack(self, identity):
        if self.project.track(identity):
            self._selected, self._issue = identity, -1
            self.changed.emit()

    @Slot(str, str)
    def trackPart(self, identity, name):
        if self.project.track(identity):
            self._commit(self.project.update_track(identity, part=name.strip()), keep_suggestions=True)

    @Slot(str, str)
    def trackName(self, identity, name):
        if self.project.track(identity) and name.strip():
            self._commit(self.project.update_track(identity, name=name.strip()), keep_suggestions=True)

    @Slot(str, float)
    def offsetTrack(self, identity, seconds):
        if self.project.track(identity):
            self._commit(self.project.update_track(identity, offset=number(seconds)))

    @Slot(str, str)
    def trackRole(self, identity, role):
        if self.project.track(identity) and role in ROLES:
            self._commit(self.project.update_track(identity, role=role))

    @Slot(str, str)
    def bindScore(self, identity, lane):
        score = self.project.score
        if self.project.track(identity) and (not lane or score and any(item.id == lane for item in score.lanes)):
            self._commit(self.project.update_track(identity, score_lane=lane))

    @Slot(str)
    def setGuide(self, identity):
        if self.project.track(identity):
            self._commit(replace(self.project, guide=identity))

    @Slot(str, str)
    def bindTime(self, identity, reference):
        if self.project.track(identity) and (not reference or self.project.track(reference)) and identity != reference:
            self._commit(self.project.update_track(identity, timing_ref=reference))

    @Slot(str)
    def removeTrack(self, identity):
        if self.project.track(identity):
            self._commit(self.project.remove(identity))

    @Slot(str)
    def mute(self, identity):
        track = self.project.track(identity)
        if track:
            self._commit(self.project.update_track(identity, muted=not track.muted), keep_suggestions=True)

    @Slot(str)
    def solo(self, identity):
        if self.project.track(identity):
            self._solo = '' if self._solo == identity else identity
            self.changed.emit()
            if self._session or self._intent:
                self._request_audio()

    @Slot()
    def undo(self):
        if self._undo:
            self._redo.append(self._revision())
            self._load_revision(self._undo.pop())
            self._say('已撤销')

    @Slot()
    def redo(self):
        if self._redo:
            self._undo.append(self._revision())
            self._load_revision(self._redo.pop())
            self._say('已重做')

    @Slot(str)
    def setMode(self, mode):
        if mode not in ('all', 'phrase', 'pitch'):
            return
        self._mode = mode
        if mode == 'all':
            self._view_first, self._view_span = self.project.start, max(.01, self.project.end - self.project.start)
        else:
            left, right = self._selection
            if self._selection == (self.project.start, self.project.end) and right - left > 18.:
                match = next((phrase for phrase in self.project.phrases
                              if phrase.start <= self._position < phrase.end), None)
                left, right = (match.start, match.end) if match else self._bounds(self._position - 2., self._position + 5.)
                self._selection = left, right
            self._set_viewport(left - .30, max(.6, right - left + .60))
        self.changed.emit()

    def _set_viewport(self, first, span):
        total = max(.01, self.project.end - self.project.start)
        self._view_span = max(min(.35, total), min(span, total))
        self._view_first = max(self.project.start, min(first, self.project.start + total - self._view_span))

    @Slot(float, float)
    def viewport(self, first, span):
        self._set_viewport(number(first), number(span))
        self.changed.emit()

    @Slot(float, float)
    def selectRange(self, first, last):
        self._selection = self._bounds(number(first), number(last))
        self._issue = -1
        self.changed.emit()

    @Slot(float, float)
    def focusPhrase(self, first, last):
        self.selectRange(first, last)
        self.setMode('phrase')
        self.seek(first)

    @Slot(int)
    def focusIssue(self, index):
        if not 0 <= index < len(self._suggestions):
            return
        row = self._suggestions[index]
        self._selected, self._issue = row.track, index
        self._selection = self._bounds(row.start, row.end)
        self.setMode('pitch' if row.kind == 'pitch' else 'phrase')
        self.seek(row.start)

    @Slot(str, result='QVariantList')
    def peaks(self, identity):
        track = self.project.track(identity)
        feature = self._features.get(track.sha) if track else None
        return feature.peaks if feature else []

    @Slot(str, result='QVariantMap')
    def curve(self, identity):
        track = self.project.track(identity)
        source = self._curves.get(track.sha) if track else None
        if source is None:
            return {'first': 0., 'step': .01, 'midi': [], 'target': []}
        import numpy as np

        midi, step = source
        grid = self._view_first + np.arange(max(1, int(self._view_span / step) + 1)) * step
        local = grid - track.offset

        def project_curve(take_time):
            frames = np.rint(take_time / step).astype('int64')
            found = np.full(grid.shape, np.nan, dtype=np.float32)
            valid = (frames >= 0) & (frames < len(midi))
            found[valid] = midi[frames[valid]]
            return found

        original = project_curve(local)
        variant = self._active_variant if self._intent and self._active_variant else self._variant
        moved = local.copy()
        if variant in ('timing', 'processed'):
            for edit in self.project.edits_for(identity, 'timing'):
                moved[(grid >= edit.start) & (grid < edit.end)] -= edit.value
        fixed = project_curve(moved)
        if variant in ('pitch', 'processed'):
            for pitch in self.project.edits_for(identity, 'pitch'):
                fixed[(moved >= pitch.start) & (moved < pitch.end)] += pitch.value / 100
        return {'first': self._view_first, 'step': step,
                'midi': np.nan_to_num(original, nan=-1.).tolist(),
                'target': np.nan_to_num(fixed, nan=-1.).tolist()}

    @Slot(str, str, bool, bool, bool, float)
    def analyze(self, kind, scope, full, commit, include_review, strength):
        if kind not in ('timing', 'pitch', 'both'):
            return
        project = self.project
        selected = project.track(self._selected)
        targets = tuple(track for track in project.tracks if track.role == 'vocal' and (
            scope == 'all' or selected is not None and (
                track.id == selected.id or scope == 'part' and selected.part and track.part == selected.part)))
        selection = (project.start, project.end) if full else self._selection
        if not targets or selection[1] - selection[0] < .03:
            self._say('请选择有人声的处理范围。', error=True)
            return
        features = {track.id: self._features[track.sha] for track in project.tracks}
        curves = {track.id: self._curves[track.sha] for track in targets if track.sha in self._curves}
        strength = max(0., min(1., number(strength) / 100.))
        cache = self._cache_dir()

        def work(stop, send):
            from chorus_analysis import proposals
            from chorus_pitch import estimate_many

            check = lambda: _check(stop)
            failure = ''
            for track in project.tracks:
                check()
                track.check_source()
            missing = tuple(track for track in targets if track.id not in curves)
            if kind != 'timing' and missing:
                try:
                    curves.update(estimate_many(missing, cache, check,
                                                lambda i, label: _meter(send, 'analyze', i, len(missing), label)))
                except (OSError, RuntimeError) as error:
                    failure = str(error)
                    if kind == 'pitch':
                        raise
            result = proposals(project, targets, selection, kind, strength, .60, features, curves,
                               check, lambda i, label: _meter(send, 'analyze', i, len(targets), f'比较唱段 · {label}'))
            check()
            return (*result, curves, failure)

        def done(result):
            phrases, suggestions, found, failure = result
            self._curves.update({track.sha: found[track.id] for track in targets if track.id in found})
            if self.project != project:
                self._say('工程已变化，音高分析已缓存。请重新生成与当前版本对应的建议。')
                return
            self._suggestions, self._resolved, self._issue = suggestions, set(), -1
            if full:
                self._commit(replace(self.project, phrases=phrases), keep_suggestions=True)
            if commit:
                count = self._apply(include_review)
                self._say(f'已应用 {count} 项处理' + (f'。{failure}' if failure else ''), error=bool(failure))
            else:
                count = sum(row.can_apply for row in suggestions)
                self._say(f'已生成 {count} 项建议' + (f'。{failure}' if failure else ''), error=bool(failure))

        self._start('analysis', '正在比较音轨', work, done)

    def _apply(self, include_review, indices=None):
        from chorus_analysis import apply_suggestions

        individual = indices is not None
        indices = range(len(self._suggestions)) if indices is None else indices
        picked = [(i, self._suggestions[i]) for i in indices if i not in self._resolved
                  and self._suggestions[i].can_apply and (include_review or not self._suggestions[i].review)]
        if not picked:
            return 0
        project = apply_suggestions(self.project, [row for _, row in picked])
        self._commit(project, keep_suggestions=True)
        self._resolved.update(i for i, row in picked if not row.review or individual)
        self.setVariant('processed')
        return len(picked)

    @Slot(bool)
    def applySuggestions(self, include_review):
        self._say(f'已应用 {self._apply(include_review)} 项处理')

    @Slot()
    def acceptIssue(self):
        if 0 <= self._issue < len(self._suggestions):
            self._apply(True, (self._issue,))

    @Slot()
    def keepIssue(self):
        if not 0 <= self._issue < len(self._suggestions):
            return
        row = self._suggestions[self._issue]
        track = self.project.track(row.track)
        if track is None:
            return
        bias = track.offset if row.kind == 'pitch' else 0.
        selected = self._issue
        self._commit(self.project.replace_region(track.id, row.kind, row.start - bias, row.end - bias, ()),
                     keep_suggestions=True)
        self._resolved.add(selected)
        self._say('当前唱段已保留原样')

    @Slot(str, float)
    def editSelection(self, kind, value):
        if kind not in ('timing', 'pitch'):
            return
        track = self.project.track(self._selected)
        first, last = self._selection
        if track is None or track.role != 'vocal' or first >= last:
            self._say('请先在人声音轨上选中一个范围。', error=True)
            return
        if kind == 'pitch':
            first, last = max(0., first - track.offset), min(track.duration, last - track.offset)
        else:
            value /= 1000.
        if first >= last:
            self._say('选择的区间与音轨没有交集。', error=True)
            return
        edits = (Edit(track.id, kind, first, last, number(value)),) if value else ()
        self._commit(self.project.replace_region(track.id, kind, first, last, edits), keep_suggestions=True)
        self.setVariant('processed')
        self._say('已更新所选范围')

    @Slot(QUrl, bool)
    def exportStems(self, folder, whole):
        try:
            path = _path(folder)
        except (OSError, ValueError) as error:
            self._say(error, error=True)
            return
        project, variant = self.project, self._variant
        selection = (project.start, project.end) if whole else self._selection

        def work(stop, send):
            from chorus_audio import export_stems

            return export_stems(project, path, variant, selection, lambda: _check(stop),
                                 lambda i, n, name: _meter(send, 'export', i, n, f'渲染分轨 · {name}'))

        def done(result):
            self._export, receipt = result
            self._say(f"已导出 {len(receipt['tracks'])} 路同起点 WAV")

        self._start('export', '正在导出分轨', work, done)

    @Slot()
    def openExport(self):
        if self._export:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._export)))

    @Slot()
    def cancel(self):
        self.worker.cancel()

    def _cache_dir(self):
        if self._scratch is None:
            directory = ROOT / 'work'
            directory.mkdir(parents=True, exist_ok=True)
            self._scratch = TemporaryDirectory(dir=directory, prefix='chorus-')
        return Path(self._scratch.name)

    @Slot(str)
    def setVariant(self, variant):
        if variant in VARIANTS:
            self._variant = variant
            self.changed.emit()
            if self._session or self._intent:
                self._request_audio()

    @Slot(bool)
    def setLoop(self, enabled):
        self._loop = bool(enabled)
        self.changed.emit()

    @Slot()
    def togglePlay(self):
        if self._intent:
            self._intent = False
            if self._session:
                self._session.pause()
            self.changed.emit()
            return
        if self.project.frames <= 0:
            return
        first, last = self._selection
        if self._loop and first < last and not first <= self._position < last:
            self.seek(first)
        elif self._position >= self.project.end - 1 / self.project.rate:
            self.seek(self.project.start)
        self._intent = True
        self._request_audio()
        self.changed.emit()

    def _set_position(self, position):
        if position != self._position:
            self._position = position
            self.positionChanged.emit()

    @Slot(float)
    def seek(self, seconds):
        target = max(self.project.start, min(number(seconds), self.project.end))
        self._set_position(target)
        if self._session and self._session_geometry == self._geometry():
            self._seek_serial = self._session.seek(self._frame(target))

    def _frame(self, value):
        return max(0, min(self.project.frames, round(value * self.project.rate) - self.project.first_frame))

    def _geometry(self):
        project = self.project
        return project.first_frame, project.frames, project.rate

    def _request_audio(self):
        from chorus_audio import render_key

        if self._closed or not self.project.frames:
            self._requested = None
            self._stop_session()
            return
        project, variant, solo = self.project, self._variant, self._solo
        key = render_key(project, variant, solo)
        self._requested = key
        if key in self._cache:
            try:
                for track in project.tracks:
                    if not track.muted and (not solo or track.id == solo):
                        track.check_source()
                self._cache.move_to_end(key)
                self._load_buffer(project, variant, key, self._cache[key])
            except (OSError, ValueError, RuntimeError) as error:
                self._audio_error(error)
            return
        if self.monitor_worker.kind:
            return

        def work(stop, send):
            from chorus_audio import MonitorBuffer, render_mix

            return MonitorBuffer(render_mix(project, variant, solo, lambda: _check(stop),
                                            lambda i, n, name: _meter(send, 'render', i, n, f'准备试听 · {name}')))

        def finish(buffer, error):
            if self._closed:
                return
            if self._requested != key:
                if self._requested is not None:
                    self._request_audio()
                return
            if error is not None:
                self._audio_error(error)
                return
            self._cache[key] = buffer
            while len(self._cache) > 1 and (len(self._cache) > 4 or
                                            sum(item.audio.nbytes for item in self._cache.values()) > 190 * 1024**2):
                self._cache.popitem(last=False)
            try:
                self._load_buffer(project, variant, key, buffer)
            except (OSError, RuntimeError, ValueError) as error:
                self._audio_error(error)

        self.monitor_worker.start('chorus-monitor', work, finish, label='正在准备试听音频', route='chorus')

    def _load_buffer(self, project, variant, key, buffer):
        from chorus_audio import AuditionPCM
        from mixdown_native_output import NativeOutput
        from mixdown_realtime_core import native_output_path
        from mixdown_realtime_session import RealtimeAuditionSession

        if self._session and self._session_geometry != self._geometry():
            intent = self._intent
            self._stop_session()
            self._intent = intent
        if not self._session and self._intent:
            dll = native_output_path()
            if not dll:
                raise RuntimeError('缺少原生音频输出组件。')
            snapshot = AuditionPCM(project.rate, buffer.audio, self._frame(self._position))
            self._session = RealtimeAuditionSession(snapshot, lambda: NativeOutput(
                dll, project.rate, 2, 16384, 512, 'wasapi'),
                silent=os.environ.get('VWA_REALTIME_SILENT_PROBE') == '1').start()
            self._session_geometry = self._geometry()
            self._timer.start()
        elif self._session and key != self._current_key:
            self._session.set_parameters({'pcm': buffer})
        if self._session and self._intent:
            self._session.play()
        self._active_variant, self._current_key = variant, key
        self.changed.emit()

    def _audio_error(self, error):
        if self._session is None or self._active_variant == self._variant:
            self._stop_session()
        else:
            self._variant = self._active_variant
        self._say(error, error=True)

    def _clock(self):
        session = self._session
        if session is None:
            return
        snapshot = session.snapshot()
        if snapshot.get('error'):
            self._stop_session()
            self._say(snapshot['error'], error=True)
            return
        if self._session_geometry != self._geometry():
            return
        native = snapshot.get('native')
        if not native:
            return
        if self._seek_serial is not None:
            seek = snapshot.get('last_seek') or {}
            if seek.get('seek_id') != self._seek_serial or seek.get('phase') == 'prepared':
                return
            self._seek_serial = None
        origin, frames, rate = self._session_geometry
        frame = min(frames, int(native.get('media_frame', 0)))
        self._set_position((origin + frame) / rate)
        first, last = self._selection
        if self._intent and self._loop and first < last and self._position >= last:
            self.seek(first)
        elif self._intent and snapshot['state'] == 'ended':
            self._intent = False
            self.changed.emit()

    def _stop_session(self):
        self._timer.stop()
        session, self._session = self._session, None
        self._intent = False
        self._seek_serial = None
        self._session_geometry = self._current_key = None
        self._active_variant = ''
        if session:
            session.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.worker.close()
        self.monitor_worker.close()
        self._stop_session()
        if self._scratch:
            self._scratch.cleanup()
