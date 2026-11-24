"""Qt projection of the chorus project and its resident audio transport."""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import replace
from math import nextafter
from pathlib import Path
from tempfile import TemporaryDirectory
import os

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from chorus_precision import mapped_time, phrase_for
from chorus_project import (ALIGNMENT_ENGINES, LYRIC_LANGUAGES, ROLES, VARIANTS, ChorusProject, Edit, Lyric,
                            Recording, Score, number)
from chorus_speech import alignment_ready
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


@contextmanager
def _analysis_sources(project, rows, cache, features, check, send):
    from chorus_analysis import feature_track
    from chorus_audio import analysis_recording, recording_key

    audio, measured, added = {}, {}, {}
    with TemporaryDirectory(dir=cache, prefix='sources-') as folder:
        for index, track in enumerate(rows):
            check()
            source = analysis_recording(project, track, Path(folder), check)
            key = recording_key(project, track)
            audio[track.id] = source
            if features is None:
                continue
            if key not in features and key not in added:
                _meter(send, 'analyze', index, len(rows), f'分析当前录音 · {track.name}')
                added[key] = feature_track(source, check)
            measured[track.id] = features[key] if key in features else added[key]
        yield audio, measured, added


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
        self._speech = OrderedDict()
        self._lyric_inputs = {}
        self._suggestions = ()
        self._resolved = set()
        self._issue = -1
        self._undo, self._redo = [], []
        self._selected = ''
        self._view_first = 0.
        self._view_span = 1.
        self._selection = (0., 0.)
        self._mode = 'all'
        self._inspector_open = True
        self._inspector_section = 'batch'
        self._batch_strength = {'timing': 100., 'pitch': 72.}
        self._pitch_draw = False
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
        phrase = phrase_for(project, self._selection)
        reference = project.reference(selected, phrase)
        middle = sum(self._selection) / 2
        shift = middle - mapped_time(project, selected, middle) if selected else 0.
        current, timing = self._timing_pose(selected, middle) if selected else (None, None)
        knots = (current.points if current and current.manual else
                 timing.points if timing and timing.points else current.points if current else ())
        if not knots and timing and len(timing.anchors) >= 2:
            knots = ((timing.start, timing.start - (timing.value or 0.)),
                     *((x, y) for _, x, y in timing.anchors if timing.start < x < timing.end),
                     (timing.end, timing.end - (timing.value or 0.)))
        labels = {round(y, 5): label for label, _, y in timing.anchors} if timing else {}
        anchors = [{'label': labels.get(round(y, 5), str(i + 1)), 'time': x, 'source': y}
                   for i, (x, y) in enumerate(knots[1:-1])]
        shared, local = ((phrase.lyric(), phrase.lyric(self._selected)) if phrase else (None, None))
        gain = next((edit.value for edit in project.edits_for(self._selected, 'gain')
                     if edit.start <= middle < edit.end), 0.)
        comp = next((edit.source for edit in project.edits_for(self._selected, 'take')
                     if edit.start <= (mapped_time(project, selected, middle) if selected else middle) < edit.end), '')
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
            'phrase': {'id': phrase.id if phrase else '', 'ref': phrase.reference if phrase else '',
                       'shared': self._lyric_row(shared), 'local': self._lyric_row(local)},
            'alignmentEngine': project.alignment_engine,
            'ensembleLanguage': project.ensemble_language,
            'timingStrength': self._batch_strength['timing'],
            'pitchStrength': self._batch_strength['pitch'],
            'anchors': anchors, 'timingRoute': ('手工锚点' if current and current.manual and current.points else
                                               timing.route if timing else current.basis if current else ''),
            'timingDetail': timing.detail if timing else current.basis if current else '', 'gain': gain, 'comp': comp,
            'canApplyTiming': bool(timing and timing.can_apply and not (current and current.manual)
                                   and self._suggestions.index(timing) not in self._resolved),
            'viewFirst': self._view_first, 'viewSpan': self._view_span,
            'rangeFirst': self._selection[0], 'rangeLast': self._selection[1], 'mode': self._mode,
            'inspector': self._inspector_open, 'inspectorSection': self._inspector_section,
            'pitchDraw': self._pitch_draw,
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

    @staticmethod
    def _lyric_row(line):
        return {'text': line.text, 'language': line.language, 'confirmed': line.confirmed} if line else {}

    def _track_row(self, track):
        project = self.project
        return {'id': track.id, 'name': track.name, 'role': track.role, 'part': track.part,
                'offset': track.offset, 'duration': track.duration, 'muted': track.muted,
                'scoreLane': track.score_lane, 'timeRef': track.timing_ref, 'lead': project.guide == track.id,
                'edits': sum(edit.track == track.id for edit in project.edits),
                'timing': [{'start': edit.start, 'end': edit.end, 'shift': edit.value,
                            'points': [list(point) for point in edit.points]}
                           for edit in project.edits_for(track.id, 'timing')],
                'takes': [{'start': edit.start, 'end': edit.end, 'id': edit.source}
                          for edit in project.edits_for(track.id, 'take')]}

    @Property('QVariantList', notify=tracksChanged)
    def tracks(self):
        return [self._track_row(track) for track in self.project.tracks]

    @Property('QVariantList', notify=phrasesChanged)
    def phrases(self):
        rows = []
        for index, phrase in enumerate(self.project.phrases, 1):
            line = phrase.lyric() or next(iter(phrase.lyrics), None)
            rows.append({'start': phrase.start, 'end': phrase.end,
                         'name': line.text if line else phrase.label or f'唱段 {index}',
                         'confirmed': bool(line and line.confirmed)})
        return rows

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

    def _selected_vocal(self):
        track = self.project.track(self._selected)
        if track is None or track.role != 'vocal' or self._selection[0] >= self._selection[1]:
            return None
        return track

    def _timing_pose(self, track, time):
        current = next((row for row in self.project.edits_for(track.id, 'timing')
                        if row.start <= time < row.end), None)
        candidate = next((row for row in self._suggestions if row.track == track.id
                          and row.kind == 'timing' and row.start <= time < row.end), None)
        return current, candidate

    def _take_span(self, track, first, last):
        a = mapped_time(self.project, track, first)
        b = mapped_time(self.project, track, nextafter(last, first))
        return max(0., a - track.offset), min(track.duration, b - track.offset)

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
        self._speech.clear()
        self._lyric_inputs.clear()
        self._features = features
        self._path = path
        self._saved = project
        self._undo.clear()
        self._redo.clear()
        self._mode, self._variant, self._solo = 'all', 'original', ''
        self._inspector_section, self._pitch_draw = 'batch', False
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
        self._import_audio(role, urls)

    @Slot(QUrl)
    def importPhraseReference(self, url):
        if not self._selected_vocal() or self._selection[1] <= self._selection[0]:
            self._say('请选择要核对的人声唱段。', error=True)
            return
        self._import_audio('guide', (url,), self._selection)

    def _import_audio(self, role, urls, reference_span=None):
        from chorus_precision import update_phrase

        initial = self.project

        def bind_reference(project, track):
            try:
                updated = update_phrase(project, reference_span, reference=track.id)
            except ValueError as error:
                self._say(error, error=True)
                return
            self._commit(updated)
            self._say('已设为本唱段参照')

        try:
            paths = tuple(dict.fromkeys(_path(value) for value in urls))
            if reference_span:
                loaded = next((track for track in initial.tracks if track.role == 'guide'
                               and Path(track.path) == paths[0]), None)
                if loaded:
                    loaded.check_source()
                    bind_reference(initial, loaded)
                    return
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
                if reference_span:
                    track = replace(track, muted=True, phrase_reference_only=True)
                features[track.sha] = feature_track(track, check)
                tracks.append(track)
            check()
            return tracks, features

        def done(result):
            tracks, features = result
            self._features.update(features)
            if reference_span:
                if self.project != initial:
                    self._say('工程已变化，请重新导入此唱段的参照。')
                    return
                bind_reference(initial.add(tracks), tracks[0])
                return
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
        track = self.project.track(identity)
        if track and role in ROLES and track.role != role:
            if any(row.source == identity for row in self.project.edits):
                self._say('这条备选录音正在使用，请先还原相应唱段。', error=True)
                return
            project = self.project.update_track(identity, role=role)
            if role != 'vocal':
                project = replace(project, edits=tuple(row for row in project.edits if row.track != identity))
            if role in ('take', 'backing') and project.guide == identity:
                updated = replace(project, guide='', tracks=())
                project = replace(updated.add(project.tracks), rate=project.rate)
            self._commit(project)

    @Slot(str, str)
    def bindScore(self, identity, lane):
        score = self.project.score
        if self.project.track(identity) and (not lane or score and any(item.id == lane for item in score.lanes)):
            self._commit(self.project.update_track(identity, score_lane=lane))

    @Slot(str)
    def setGuide(self, identity):
        track = self.project.track(identity)
        if track and track.role in ('vocal', 'guide'):
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

    @Slot(str)
    def inspectorSection(self, section):
        if section in ('phrase', 'batch', 'track') and section != self._inspector_section:
            self._inspector_section = section
            self.changed.emit()

    @Slot()
    def toggleInspector(self):
        self._inspector_open = not self._inspector_open
        self.changed.emit()

    @Slot(bool)
    def pitchDraw(self, enabled):
        if self._pitch_draw != enabled:
            self._pitch_draw = enabled
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

    @Slot(str, str, str, bool)
    def setLyrics(self, scope, text, language, confirmed):
        from chorus_precision import update_phrase

        identity = self._selected if scope == 'track' else ''
        if scope == 'track' and not self.project.track(identity):
            return
        try:
            project = update_phrase(self.project, self._selection,
                                    line=Lyric(identity, text.strip(), language, bool(confirmed)))
            self._lyric_inputs.pop((identity, self._selection), None)
            self._commit(project)
            self._say('已存入工程' if text.strip() else '已清除该轨歌词')
        except ValueError as error:
            self._say(error, error=True)

    @Slot(str, result=bool)
    def alignmentReady(self, engine):
        return engine in ('tifa', 'hubertfa') and alignment_ready(engine)

    @Slot(str)
    def setAlignmentEngine(self, engine):
        if engine in ALIGNMENT_ENGINES and not self.worker.kind and engine != self.project.alignment_engine:
            self._commit(replace(self.project, alignment_engine=engine))

    @Slot(str)
    def setEnsembleLanguage(self, language):
        if language in ('', *LYRIC_LANGUAGES) and not self.worker.kind and language != self.project.ensemble_language:
            self._commit(replace(self.project, ensemble_language=language))

    @Slot(str, float)
    def setBatchStrength(self, kind, value):
        if kind in self._batch_strength and not self.worker.kind:
            value = max(0., min(100., number(value)))
            if value != self._batch_strength[kind]:
                self._batch_strength[kind] = value
                self.changed.emit()

    @Slot(str, str, str)
    def lyricInput(self, scope, text, language):
        identity = self._selected if scope == 'track' else ''
        self._lyric_inputs[identity, self._selection] = {'text': text, 'language': language}

    @Slot(str, result='QVariantMap')
    def pendingLyricInput(self, scope):
        identity = self._selected if scope == 'track' else ''
        return self._lyric_inputs.get((identity, self._selection), {})

    @Slot(str)
    def phraseReference(self, identity):
        from chorus_precision import update_phrase

        track = self.project.track(identity)
        if identity and (track is None or track.role == 'backing' or identity == self._selected):
            return
        try:
            self._commit(update_phrase(self.project, self._selection, reference=identity))
            self._say('已更新此唱段的时间参照')
        except ValueError as error:
            self._say(error, error=True)

    @Slot(str, bool)
    def recognizeLyrics(self, language, whole):
        from chorus_speech import SpeechClip, speech_batch
        from chorus_precision import merge_speech_drafts

        project = self.project
        track = project.track(self._selected)
        selection = (project.start, project.end) if whole else self._selection
        if not track or track.role == 'backing' or language not in LYRIC_LANGUAGES or selection[1] <= selection[0]:
            self._say('请选择有人声的音轨和唱段。', error=True)
            return
        cache = self._cache_dir()
        cached = self._features.copy()

        def work(stop, send):
            from chorus_analysis import phrase_regions
            from chorus_project import Phrase

            check = lambda: _check(stop)
            with _analysis_sources(project, (track,), cache, cached, check, send) as (audio, metrics, added):
                anchor = phrase_for(project, selection)
                phrases = (phrase_regions(project, track, metrics[track.id], selection) if whole
                           else (anchor or Phrase(*selection),))
                phrases = tuple(phrase for phrase in phrases
                                if not ((line := phrase.lyric(track.id)) and line.confirmed))
                clips = [SpeechClip(audio[track.id], phrase.start, phrase.end, language) for phrase in phrases]
                found = speech_batch(clips, 'asr', cache, check,
                                     lambda i, label: _meter(send, 'analyze', i, len(clips), label))
            return phrases, found, added

        def done(result):
            phrases, found, added = result
            self._features.update(added)
            if self.project != project:
                self._say('工程已变化，请在当前版本重新识别。')
                return
            try:
                updated, count = merge_speech_drafts(project, track.id, language, phrases, found)
            except ValueError as error:
                self._say(error, error=True)
                return
            self._commit(updated)
            if not phrases:
                self._say('范围内的歌词已核对。')
            elif count:
                self._say(f'写入 {count} 段歌词草稿。请核对后确认。')
            else:
                detail = next((row.get('detail') for row in found if row.get('detail')), '没有识别到可用歌词。')
                self._say(detail)

        self._start('lyrics', '正在识别所选轨歌词', work, done)

    @Slot(str, result='QVariantList')
    def peaks(self, identity):
        track = self.project.track(identity)
        feature = self._features.get(track.sha) if track else None
        return feature.peaks if feature else []

    @Slot(str, result='QVariantMap')
    def curve(self, identity):
        from chorus_audio import recording_key

        track = self.project.track(identity)
        source = self._curves.get(recording_key(self.project, track)) if track else None
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
                covered = (grid >= edit.start) & (grid < edit.end)
                if edit.points:
                    x, y = zip(*edit.points)
                    moved[covered] = np.interp(grid[covered], x, y) - track.offset
                else:
                    moved[covered] -= edit.value
        fixed = project_curve(moved)
        if variant in ('pitch', 'processed'):
            for pitch in self.project.edits_for(identity, 'pitch'):
                covered = (moved >= pitch.start) & (moved < pitch.end) & np.isfinite(fixed)
                if pitch.points:
                    x, y = zip(*pitch.points)
                    fixed[covered] += np.interp(moved[covered], x, y) / 100.
                else:
                    fixed[covered] += pitch.value / 100.
        return {'first': self._view_first, 'step': step,
                'midi': np.nan_to_num(original, nan=-1.).tolist(),
                'target': np.nan_to_num(fixed, nan=-1.).tolist()}

    @Slot(str, str, bool, bool, bool, float)
    @Slot(str, str, bool, bool, bool, float, bool, float)
    def analyze(self, kind, scope, full, commit, include_review, strength, ensemble=False, pitch_strength=None):
        from chorus_audio import recording_key
        from chorus_precision import remembered_phrases

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
        language = project.ensemble_language if ensemble and full and scope == 'all' and kind != 'pitch' else ''
        cached = self._features.copy()
        voice_tracks = tuple(track for track in project.tracks if track.role in ('vocal', 'guide', 'take'))
        curves = {track.id: self._curves[key] for track in voice_tracks
                  if (key := recording_key(project, track)) in self._curves}
        speech = dict(self._speech)
        strength = max(0., min(1., number(strength) / 100.))
        pitch_strength = strength if pitch_strength is None else max(0., min(1., number(pitch_strength) / 100.))
        cache = self._cache_dir()

        def work(stop, send):
            from chorus_analysis import proposals
            from chorus_pitch import estimate_many

            check = lambda: _check(stop)
            failure = ''
            relevant = {ref.id for track in targets if (ref := project.reference(track))}
            relevant.update(phrase.reference for phrase in project.phrases if phrase.reference
                            and phrase.start < selection[1] and phrase.end > selection[0])
            relevant.update(track.id for track in targets)
            with _analysis_sources(project, voice_tracks, cache, cached, check, send) as (audio, features, added):
                missing = tuple(audio[track.id] for track in voice_tracks if track.id not in curves and
                                (track.id in relevant if kind == 'both' else track in targets))
                if kind != 'timing' and missing:
                    try:
                        curves.update(estimate_many(missing, cache, check, lambda i, label:
                                                    _meter(send, 'analyze', i, len(missing), label)))
                    except (OSError, RuntimeError) as error:
                        failure = str(error)
                        if kind == 'pitch':
                            raise
                result = proposals(project, targets, selection, kind, strength, .60, features, curves,
                                   check, lambda i, label: _meter(send, 'analyze', i, len(targets), f'比较唱段 · {label}'),
                                   speech=(speech, cache), audio=audio,
                                   ensemble_language=language, pitch_strength=pitch_strength,
                                   phone_progress=lambda i, n, label: _meter(send, 'align', i, n, label))
            check()
            return (*result, curves, speech, added, failure)

        def done(result):
            phrases, suggestions, found, measured, added, failure = result
            self._features.update(added)
            self._curves.update({recording_key(project, track): found[track.id]
                                 for track in voice_tracks if track.id in found})
            used = ((key, row) for key, row in measured.items() if row.get('phones') or row.get('text'))
            for key, row in used:
                self._speech[key] = row
                self._speech.move_to_end(key)
            while len(self._speech) > 6000:
                self._speech.popitem(last=False)
            if self.project != project:
                self._say('工程已变化，请按当前版本重新生成建议。')
                return
            self._suggestions, self._resolved, self._issue = suggestions, set(), -1
            if full:
                self._commit(replace(self.project, phrases=phrases if language else remembered_phrases(self.project, phrases)),
                             keep_suggestions=True)
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
        project = self.project
        count = 0
        for _, row in picked:
            updated = apply_suggestions(project, (row,), manual=individual)
            count += updated != project
            project = updated
        self._commit(project, keep_suggestions=True)
        self._resolved.update(i for i, _ in picked)
        if count:
            self.setVariant('processed')
        return count

    @Slot(bool)
    def applySuggestions(self, include_review):
        self._say(f'已应用 {self._apply(include_review)} 项处理')

    @Slot()
    def acceptIssue(self):
        if 0 <= self._issue < len(self._suggestions):
            self._apply(True, (self._issue,))

    @Slot()
    def acceptPhraseTiming(self):
        track = self._selected_vocal()
        if track:
            current, candidate = self._timing_pose(track, sum(self._selection) / 2.)
            if candidate and not (current and current.manual):
                self._say(f'已应用 {self._apply(True, (self._suggestions.index(candidate),))} 项对齐')

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
        first, last = row.start - bias, row.end - bias
        self._commit(self.project.replace_region(track.id, row.kind, first, last,
                                                 (Edit(track.id, row.kind, first, last, 0., manual=True),)),
                     keep_suggestions=True)
        self._resolved.add(selected)
        self._say('当前唱段已保留原样')

    @Slot(str, float)
    def editSelection(self, kind, value):
        if kind not in ('timing', 'pitch', 'gain'):
            return
        track = self.project.track(self._selected)
        first, last = self._selection
        if track is None or track.role != 'vocal' or first >= last:
            self._say('请先在人声音轨上选中一个范围。', error=True)
            return
        if kind == 'pitch':
            first, last = self._take_span(track, first, last)
            value = max(-1200., min(1200., number(value)))
        elif kind == 'timing':
            value = max(-3., min(3., number(value) / 1000.))
        else:
            value = max(-30., min(18., number(value)))
        if first >= last:
            self._say('选择的区间与音轨没有交集。', error=True)
            return
        edits = (Edit(track.id, kind, first, last, value, manual=True),)
        self._commit(self.project.replace_region(track.id, kind, first, last, edits), keep_suggestions=True)
        self.setVariant('processed')
        self._say('已更新所选范围')

    @Slot(str)
    def clearSelectionEdits(self, kind):
        track = self._selected_vocal()
        if not track or kind not in ('timing', 'pitch'):
            return
        first, last = self._take_span(track, *self._selection) if kind == 'pitch' else self._selection
        if last > first:
            project = self.project.replace_region(track.id, kind, first, last, ())
            self._commit(project)
            self._say('已清除选区的编辑，可以重新分析。')

    @Slot(float)
    def addTimeAnchor(self, time):
        track = self._selected_vocal()
        if track is None:
            self._say('请先在人声音轨上选中一个范围。', error=True)
            return
        first, last = self._selection
        current, _ = self._timing_pose(track, (first + last) / 2.)
        if current:
            first, last = current.start, current.end
        mapping = current.source_time if current else lambda value: value
        points = current.points if current and current.points else ((first, mapping(first)), (last, mapping(last)))
        if time <= first + .015 or time >= last - .015 or any(abs(time - x) < .015 for x, _ in points):
            self._say('把播放头移到唱段内部、两个锚点之间再添加。')
            return
        updated = tuple(sorted((*points, (time, mapping(time)))))
        edit = Edit(track.id, 'timing', first, last, time - mapping(time), updated, manual=True)
        self._commit(self.project.replace_region(track.id, 'timing', first, last, (edit,)),
                     keep_suggestions=True)
        self._say('已添加时间锚点，可在下方波形中拖动。')

    @Slot(float)
    def slideSelection(self, milliseconds):
        track = self._selected_vocal()
        if not track:
            return
        delta = max(-3., min(3., number(milliseconds) / 1000.))
        first, last = self._selection
        current, _ = self._timing_pose(track, sum(self._selection) / 2.)
        if not current or not current.points or first < current.start or last > current.end:
            self.editSelection('timing', 1000. * (delta + (current.value if current else 0.)))
            return
        clipped = current.clip(first, last)
        edit = replace(clipped, points=tuple((x, y - delta) for x, y in clipped.points),
                       value=clipped.value + delta, manual=True)
        self._commit(self.project.replace_region(track.id, 'timing', first, last, (edit,)),
                     keep_suggestions=True)
        self.setVariant('processed')
        self._say('已移动并锁定所选范围')

    @Slot()
    def markNoteTiming(self):
        from chorus_audio import recording_key
        from chorus_precision import mark_note_edges

        track = self._selected_vocal()
        if not track:
            return
        project, position, selection = self.project, self._position, self._selection
        current, _ = self._timing_pose(track, position)
        if current is None:
            first, last = selection
            for edit in project.edits_for(track.id, 'timing'):
                if edit.end <= position:
                    first = max(first, edit.end)
                elif edit.start > position:
                    last = min(last, edit.start)
            selection = first, last
        key = recording_key(project, track)

        def apply(curve):
            if self.project != project or self._selected != track.id:
                self._say('工程已变化，请按当前版本重新生成建议。')
                return
            try:
                marked = mark_note_edges(track, curve, position, selection, current)
                self._commit(project.replace_region(track.id, 'timing', marked.start, marked.end, (marked,)))
                self._say('已标记音符首尾。拖动锚点可以改变音符时长。')
            except ValueError as error:
                self._say(error, error=True)

        if key in self._curves:
            apply(self._curves[key])
            return

        cache = self._cache_dir()

        def work(stop, send):
            from chorus_pitch import estimate_many

            check = lambda: _check(stop)
            with _analysis_sources(project, (track,), cache, self._features.copy(), check, send) as (audio, _, _):
                return estimate_many((audio[track.id],), cache, check,
                                     lambda i, label: _meter(send, 'analyze', i, 1, label))[track.id]

        def done(curve):
            self._curves[key] = curve
            apply(curve)

        self._start('note-timing', '正在提取音符首尾', work, done)

    @Slot(float, float)
    def moveTimeAnchor(self, origin, destination):
        from chorus_precision import move_anchor

        track = self._selected_vocal()
        if track is None:
            return
        current, candidate = self._timing_pose(track, origin)
        if not current or not current.manual:
            if candidate and candidate.points:
                current = Edit(track.id, 'timing', candidate.start, candidate.end, candidate.value, candidate.points)
            elif candidate and candidate.anchors and (current is None or not current.points):
                knots = ((candidate.start, candidate.start), *((x, y) for _, x, y in candidate.anchors),
                         (candidate.end, candidate.end))
                current = Edit(track.id, 'timing', candidate.start, candidate.end, 0., knots)
        if not current or len(current.points) < 3:
            return
        try:
            edit = replace(move_anchor(track.id, (current.start, current.end), current.points, origin, destination),
                           basis=current.basis)
            self._commit(self.project.replace_region(track.id, 'timing', edit.start, edit.end, (edit,)),
                         keep_suggestions=True)
            self.setVariant('processed')
            self._say('已锁定这一段时间锚点。')
        except ValueError as error:
            self._say(error, error=True)

    @Slot('QVariantList')
    def drawPitch(self, stroke):
        from chorus_audio import recording_key
        from chorus_precision import pitch_from_stroke

        track = self.project.track(self._selected)
        if not track or track.role != 'vocal':
            return
        curve = self._curves.get(recording_key(self.project, track))
        if curve is None:
            self._say('先提取本轨音高，再画曲线。', error=True)
            return
        points = sorted({number(point['time']): number(point['note']) for point in stroke}.items())
        variant = self._active_variant if self._intent and self._active_variant else self._variant
        edits = pitch_from_stroke(self.project, track, curve, points, variant in ('timing', 'processed'))
        if edits:
            self._commit_pitch(track, edits, '已保存手绘音高')
        else:
            self._say('这一笔没有覆盖可修音的人声。')

    def _commit_pitch(self, track, edits, label):
        project = self.project
        for edit in edits:
            project = project.replace_region(track.id, 'pitch', edit.start, edit.end, (edit,))
        self._commit(project, keep_suggestions=True)
        self.setVariant('processed')
        self._say(label)

    @Slot(str, float)
    def followReference(self, mode, strength):
        from chorus_audio import recording_key
        from chorus_precision import reference_pitch

        project = self.project
        track = self._selected_vocal()
        reference = project.reference(track, phrase_for(project, self._selection))
        if mode not in ('contour', 'absolute') or not track or not reference or reference.id == track.id:
            self._say('选中人声唱段，并指定一条不同的时间参照。', error=True)
            return
        tracks = track, reference
        known = {row.id: self._curves[key] for row in tracks if (key := recording_key(project, row)) in self._curves}
        span, cache = self._selection, self._cache_dir()
        strength = max(0., min(1., number(strength) / 100.))

        def work(stop, send):
            from chorus_pitch import estimate_many

            check = lambda: _check(stop)
            with _analysis_sources(project, tracks, cache, None, check, send) as (audio, _, _):
                missing = tuple(audio[row.id] for row in tracks if row.id not in known)
                if missing:
                    known.update(estimate_many(missing, cache, check, lambda i, label:
                                               _meter(send, 'analyze', i, len(missing), label)))
            check()
            return reference_pitch(project, track, reference, span, known, mode, strength)

        def done(edits):
            self._curves.update({recording_key(project, row): known[row.id] for row in tracks})
            if self.project != project:
                self._say('工程已变化，请按当前版本重新生成。')
            elif edits:
                self._commit_pitch(track, edits, '已应用参照走势' if mode == 'contour' else '已跟随参照音高')
            else:
                self._say('本轨与参照在选段中没有重叠的有效音高。')

        self._start('reference-pitch', '正在比较两轨音高', work, done)

    @Slot(str)
    def chooseTake(self, identity):
        track = self._selected_vocal()
        take = self.project.track(identity)
        if track is None or (identity and (take is None or take.role != 'take')):
            return
        a, b = self._take_span(track, *self._selection)
        first, last = a + track.offset, b + track.offset
        if first >= last or (take and (first < take.offset - .002 or
                                       last > take.offset + take.duration + .002)):
            self._say('备选录音没有覆盖此选段。可先调整备选轨起点或缩小选区。', error=True)
            return
        edits = (Edit(track.id, 'take', first, last, 0., source=identity, manual=True),) if identity else ()
        self._commit(self.project.replace_region(track.id, 'take', first, last, edits))
        self._say('已切换此段录音' if take else '已还原此段录音')

    @Slot(QUrl, bool)
    @Slot(QUrl, bool, bool)
    @Slot(QUrl, bool, bool, str)
    def exportStems(self, folder, whole, include_mix=False, variant=''):
        try:
            path = _path(folder)
        except (OSError, ValueError) as error:
            self._say(error, error=True)
            return
        project = self.project
        variant = variant or self._variant
        if variant not in VARIANTS:
            self._say('请选择有效的导出版本。', error=True)
            return
        selection = (project.start, project.end) if whole else self._selection
        cache = self._cache_dir()

        def work(stop, send):
            from chorus_audio import export_delivery

            return export_delivery(project, path, variant, selection, cache, lambda: _check(stop),
                                   lambda i, n, name: _meter(send, 'export', i, n, f'渲染分轨 · {name}'),
                                   include_mix=include_mix)

        def done(result):
            self._export, receipt = result
            suffix = '，含合唱干声' if 'vocal_mix' in receipt else ''
            self._say(f"已导出 {len(receipt['tracks'])} 路同起点 WAV{suffix}")

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
        from chorus_audio import monitor_sources, render_key

        if self._closed or not self.project.frames:
            self._requested = None
            self._stop_session()
            return
        project, variant, solo = self.project, self._variant, self._solo
        key = render_key(project, variant, solo)
        self._requested = key
        if key in self._cache:
            try:
                for track in monitor_sources(project, solo):
                    track.check_source()
                self._cache.move_to_end(key)
                self._load_buffer(project, variant, key, self._cache[key])
            except (OSError, ValueError, RuntimeError) as error:
                self._audio_error(error)
            return
        if self.monitor_worker.kind:
            return

        def work(stop, send):
            from chorus_audio import MonitorBuffer, render_monitor

            return MonitorBuffer(render_monitor(project, variant, solo, ROOT / 'work', lambda: _check(stop),
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
