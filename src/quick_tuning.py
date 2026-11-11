"""Application-owned Quick tuning workflow. QML observes state and sends commands."""

from copy import deepcopy
import json
import math
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from common import ROOT, atomic_json, file_hash, native_path, output_directory
from i18n import tr
from preset_library import PresetLibrary
import style_catalog
from style_drafts import DraftStore
from style_presets import time_range_settings
import tuning_engine as engine
from tuning_timeline import ProjectNotes, TimelineDraftStore, draft_options, freeze_plan
from tuning_preset_documents import make_document, plan_application
import tuning_workflow as workflow


_REFERENCE_FLAGS = ('reference_dynamics', 'reference_tension', 'reference_breathiness', 'reference_voicing')
_SOURCES = ('preserve', 'reference', 'calibrated', 'retained')
_BRUSH_KEYS = ('performance_style', 'style_strength', 'style_pitch', 'style_detail',
               'style_technique', 'technique_strength', 'technique_pitch')
_SETTING_KEYS = _REFERENCE_FLAGS + ('dynamics_strength', 'expression_density', 'style_technique',
                                   'technique_strength', 'technique_pitch')


class SourceChanged(ValueError):
    """The loaded source or its task revision is no longer current."""


def _stop_check(stop):
    if stop.is_set():
        raise InterruptedError('调音操作已取消。')


def _verify_input(project, job, artifact, stop):
    _stop_check(stop)
    path = Path(project.project['path'])
    if not path.is_file() or file_hash(path, check=stop.is_set) != project.sha256:
        raise SourceChanged('源工程已改变，请重新载入；现有候选文件会保留。')
    if artifact:
        from quick_results import detail

        inspected = detail(job, stop.is_set)
        identity = ('id', 'revision', 'sha256', 'path')
        if not any(row['available'] and all(row.get(key) == artifact.get(key) for key in identity)
                   for row in inspected['artifacts']):
            raise SourceChanged('任务中的源工程或上游版本已改变，请从任务与结果重新交接。')


def _load_project(path, job, artifact, drafts, legacy, sessions, stop):
    _stop_check(stop)
    project = ProjectNotes.load(path, expected_sha256=artifact.get('sha256') if artifact else None)
    _verify_input(project, job, artifact, stop)
    restored = drafts.read(project.sha256)
    previous = legacy.read(project.sha256) if not restored['exists'] else {'data': None, 'revision': None}
    key = engine.digest([project.project['path'], project.sha256, job])[:24]
    root = sessions / key
    session = root / 'session.json'
    if Path(native_path(session)).is_file():
        data = workflow.read_session(session)
        if (data['input_sha256'], data['source'], data['job']) != (project.sha256, project.project['path'], job or None):
            raise ValueError('已有调音会话的源文件或任务不匹配。')
        try:
            result = workflow.verified_result(session)
        except (ValueError, OSError):
            result = None
    else:
        # An interrupted creation can leave a directory without a valid session.
        if Path(native_path(root)).exists():
            session = root.with_name(key + '-' + uuid4().hex[:8]) / 'session.json'
        result = None
    return project, restored, previous, session, result


def _preview(project, options, stop, send):
    """Compute real parameter differences for style-only plans without writing."""
    import agent_style

    _, raw = agent_style._read_source(project.project['path'])
    if agent_style._sha256_bytes(raw) != project.sha256:
        raise SourceChanged('预览期间源工程已改变，请重新载入。')
    data = agent_style._project(raw)
    indices = engine.selected_indices(data, options)
    if options['expression_mode'] != 'preserve':
        if options['expression_mode'] in ('reference', 'calibrated'):
            engine.sources_for(options, indices)
        return tr('已核对轨道和参数来源。参考音频、校准或沿用参数将在生成时处理。')
    changed, report = engine.process_project(data, options, cancel=lambda: _stop_check(stop), progress=send)
    curves = 0
    for index in indices:
        previous = data['tracks'][index]['mainGroup'].get('parameters', {})
        current = changed['tracks'][index]['mainGroup'].get('parameters', {})
        curves += sum(previous.get(key) != current.get(key) for key in previous.keys() | current.keys())
    label = tr('预览：{tracks} 轨 · {regions} 个逐轨片段 · {curves} 条曲线有变化。音符和时值保留。')
    label = label.format(tracks=len(indices), regions=len(options['style_regions']), curves=curves)
    if report['status'] == 'partial':
        label += tr(' 有局部参数无法安全应用，生成后请核对结果。')
    return label


class QuickTuning(QObject):
    changed = Signal()
    notesChanged = Signal()
    matchesChanged = Signal()
    tracksChanged = Signal()
    regionsChanged = Signal()
    catalogChanged = Signal()
    presetLibraryChanged = Signal()

    def __init__(self, parent, worker, *, root=ROOT):
        super().__init__(parent)
        self.worker, self.root = worker, Path(root)
        self.project = None
        self.job, self.artifact = '', None
        self.session, self.candidate = None, None
        self.state, self.message = 'idle', ''
        self._candidate_stale = False
        self._plan = None
        self._summary = ''
        self._confirmed = False
        self._preset = None
        self._mode, self._active, self._selected = 'single', '', []
        self._start, self._end, self._left, self._span = 0., 0., 0., 18.
        self._query = ''
        self._matches, self._notes, self._regions = [], [], []
        self._options = self._defaults()
        self._legacy = None
        self._legacy_revision = None
        self._draft_revision = None
        self._dirty = False
        self._drafts = TimelineDraftStore(self.root / 'cache/style-region-drafts/v2', lock_timeout=.2)
        self._legacy_drafts = DraftStore(self.root / 'cache/style-region-drafts', lock_timeout=.2)
        self._library = PresetLibrary(self.root / 'user-presets')
        self._presets = {}
        self._autosave = QTimer(self)
        self._autosave.setInterval(400)
        self._autosave.setSingleShot(True)
        self._autosave.timeout.connect(self._flush_draft)
        self.worker.changed.connect(self.changed)

    @staticmethod
    def _defaults():
        return engine.settings({'performance_style': style_catalog.DEFAULT_NEW_STYLE_ID, 'style_pitch': False,
                                'expression_density': 'original'})

    @property
    def _origin(self):
        return self.project.project.get('candidate_origin') if self.project else None

    @property
    def _targets(self):
        if not self.project:
            return []
        if self._mode == 'single':
            return [self._active] if self._active in self.project.editable else []
        return [key for key in self._selected if key in self.project.editable]

    def _indices(self, *, segments=False):
        keys = ([row['track_id'] for row in self._regions] if segments and self._regions else self._targets)
        return sorted({self.project.tracks[key]['index'] for key in keys}) if self.project else []

    def _execution(self):
        if not self.project:
            raise ValueError('请先载入 SVP 工程。')
        if self._origin and not self._confirmed:
            raise ValueError('请确认是否把已生成的候选当作新基线。')
        if self._legacy:
            raise ValueError('请先处理这个工程的旧版草稿。')
        if self._preset:
            if self._preset['missing']:
                raise ValueError(' '.join(self._preset['missing']))
            return deepcopy(self._preset['options']), deepcopy(self._preset)
        indices = self._indices(segments=True)
        if not indices:
            raise ValueError('请选择至少一条可编辑的演唱轨。')
        options = deepcopy(self._options)
        options['track_indices'] = indices
        options['style_regions'] = self.project.verify_regions(self._regions)
        if self._regions:
            # Brush changes are just preparation for the next region.
            for key in _BRUSH_KEYS:
                options[key] = deepcopy(engine.DEFAULTS[key])
        options['reference_sources'] = [row for row in options['reference_sources'] if row['track_index'] in indices]
        options['retained_sources'] = [row for row in options['retained_sources'] if row['track_index'] in indices]
        return engine.settings(options), None

    def _draft(self):
        options = deepcopy(self._options)
        options['track_indices'] = self._indices() or None
        return {'schema_version': 2, 'input_sha256': self.project.sha256, 'regions': self._regions,
                'options': options, 'preset': self._preset, 'confirmed': self._confirmed}

    def _flush_draft(self):
        if not self._dirty or not self.project:
            return True
        self._autosave.stop()
        try:
            receipt = self._drafts.write(self.project.sha256, self._draft_revision, self._draft(), writer='vwa-quick')
        except (OSError, ValueError) as error:
            self._error(error)
            return False
        self._draft_revision, self._dirty = receipt['revision'], False
        return True

    def _dirty_draft(self, *, invalidate=True):
        if self.project and not self._legacy:
            self._dirty = True
            self._autosave.start()
        if invalidate:
            self._plan, self._summary = None, ''
            self._candidate_stale = bool(self.candidate)
            if self.state in ('frozen', 'done', 'partial', 'registered'):
                self.state, self.message = 'ready', tr('已修改调音设置，请重新预览。原候选文件保留。')
        self.changed.emit()

    def _error(self, error):
        if isinstance(error, SourceChanged):
            self._plan, self._summary = None, ''
            self._candidate_stale = bool(self.candidate)
        self.state = 'cancelled' if isinstance(error, InterruptedError) else 'failed'
        self.message = tr(str(error)[:1400])
        self.changed.emit()

    def _command(self, kind, label, work, finish):
        if self.worker.kind:
            return False
        self.state, self.message = kind, tr(label)
        self.changed.emit()

        def progress(text):
            self.message = tr(str(text))
            self.changed.emit()

        def complete(value, error):
            if error is not None:
                self._error(error)
                return
            try:
                finish(value)
            except (ValueError, OSError, TypeError, KeyError) as failure:
                self._error(failure)
            self.changed.emit()

        return self.worker.start('tuning-' + kind, work, complete, progress)

    @Slot(str, result=str)
    def text(self, source):
        return tr(source)

    @Property('QVariantMap', notify=changed)
    def view(self):
        busy = bool(self.worker.kind)
        project = self.project.project if self.project else {}
        receipt = self.candidate or {}
        missing = ' '.join(self._preset.get('missing', [])) if self._preset else ''
        duration = self.project.maximum - self.project.minimum if self.project else 0.
        usable = bool(self._regions or self._targets) and not self._legacy and not missing
        return {
            'source': project.get('path', ''), 'title': Path(project['path']).stem if project else '',
            'sha256': project.get('sha256', ''), 'job': self.job,
            'session': str(self.session) if self.session and receipt else '',
            'mode': self._mode, 'activeTrack': self._active, 'selectedTracks': list(self._selected),
            'query': self._query, 'start': self._start, 'end': self._end,
            'viewportStart': self._left, 'viewportSpan': self._span, 'duration': duration,
            'timelineStart': self.project.minimum if self.project else 0.,
            'style': self._options['performance_style'], 'styleStrength': self._options['style_strength'],
            'stylePitch': self._options['style_pitch'], 'busy': busy, 'state': self.state, 'message': self.message,
            'canFreeze': bool(project and not busy and usable and (not self._origin or self._confirmed)),
            'canRun': bool(self._plan and not busy), 'canRegister': bool(self.job and receipt.get('receipt_verified')
                       and not self._candidate_stale and not busy and self.state != 'registered'),
            'candidate': receipt.get('output', ''), 'candidateStale': self._candidate_stale,
            'previewSummary': self._summary,
            'legacyDraft': ({
                'summary': tr('发现 {count} 个旧版全轨片段。转换后会另存逐轨草稿。')
                    .format(count=len(self._legacy['style_regions'])),
                'missingTracks': [] if self._legacy['track_indices'] else ['targets'],
            } if self._legacy else None),
            'candidateNeedsConfirmation': bool(self._origin), 'candidateConfirmed': self._confirmed,
            'presetName': self._preset['preset']['name'] if self._preset else '', 'presetPending': missing,
            'expressionMode': self._options['expression_mode'], 'calibration': self._options['reference_calibration'] or '',
            **{key: self._options[key] for key in _SETTING_KEYS},
        }

    @Property('QVariantList', notify=tracksChanged)
    def tracks(self):
        return list(self.project.tracks.values()) if self.project else []

    @Property('QVariantList', constant=True)
    def styles(self):
        return [{'id': key, 'label': tr(style_catalog.style_label(key)), 'color': style_catalog.style_color(key)}
                for key in style_catalog.VISIBLE_STYLES]

    @Property('QVariantList', constant=True)
    def techniques(self):
        return [{'id': key, 'label': tr(style_catalog.technique_label(key)), 'color': style_catalog.technique_color(key)}
                for key in style_catalog.TECHNIQUE_IDS]

    @Property('QVariantList', notify=changed)
    def bindings(self):
        if not self.project:
            return []
        reference = {row['track_index']: row for row in self._options['reference_sources']}
        retained = {row['track_index']: row for row in self._options['retained_sources']}
        return [{'trackId': key, 'trackName': self.project.tracks[key]['name'],
                 'reference': reference.get(index, {}).get('path', ''),
                 'referenceVoice': reference.get(index, {}).get('voice', ''),
                 'retained': retained.get(index, {}).get('project', ''),
                 'retainedIndex': retained.get(index, {}).get('source_track_index', 0)}
                for key in self.project.editable if (index := self.project.tracks[key]['index']) in self._indices(segments=True)]

    @Property('QVariantList', notify=regionsChanged)
    def regions(self):
        if not self.project:
            return []
        result = []
        for row in self._regions:
            recipe, key = row['recipe'], row['track_id']
            style, technique = recipe['preset'], recipe.get('technique', 'none')
            label = style_catalog.style_region_label(style) if style != 'none' else style_catalog.technique_region_label(technique)
            color = style_catalog.style_color(style) if style != 'none' else style_catalog.technique_color(technique)
            result.append({'id': row['id'], 'trackId': key, 'trackName': self.project.tracks[key]['name'],
                           'start': recipe['start'], 'end': recipe['end'], 'style': style,
                           'label': tr(label), 'color': color, 'strength': recipe['strength']})
        return result

    @Property('QVariantList', notify=matchesChanged)
    def matches(self):
        return self._matches

    @Property('QVariantList', notify=notesChanged)
    def notes(self):
        return self._notes

    @Property('QVariantList', notify=catalogChanged)
    def presets(self):
        return [{'key': key, 'label': row['name']} for key, row in self._presets.items()]

    def _set_catalog(self, snapshot):
        self._presets = {row['id'] + '@' + row['version']: row for row in snapshot['entries']
                         if row['type'] == 'tuning'}
        self.catalogChanged.emit()

    def refresh_presets(self):
        if self.worker.kind:
            return
        try:
            self._set_catalog(self._library.snapshot())
        except (ValueError, OSError) as error:
            self._error(error)

    def _update_notes(self):
        if self.project:
            keys = [self._active] if self._mode == 'single' else list(self.project.tracks)
            self._notes = self.project.visible(keys, self._left, self._left + self._span)
        else:
            self._notes = []
        self.notesChanged.emit()

    def _load(self, path, job='', artifact=None):
        if self.worker.kind or not self._flush_draft():
            return
        sessions = (Path(job) if job else output_directory()) / 'tuning-sessions'

        def finish(value):
            project, stored, old_draft, session, receipt = value
            previous = old_draft['data']
            payload = stored['data']
            if payload:
                project.verify_regions(payload['regions'])
            self.project, self.job, self.artifact = project, job, deepcopy(artifact)
            self.session, self.candidate = session, receipt
            self._options = deepcopy(payload['options']) if payload else self._defaults()
            self._preset = deepcopy(payload['preset']) if payload else None
            self._confirmed = bool(payload and payload['confirmed'])
            self._regions = deepcopy(payload['regions']) if payload else []
            self._legacy = previous if previous and previous.get('mode') == 'regions' and previous['style_regions'] else None
            self._legacy_revision = old_draft['revision'] if self._legacy else None
            if previous and not self._legacy:
                self._options.update(deepcopy(previous['tool']))
                self._options['track_indices'] = previous['track_indices'] or None
            self._draft_revision, self._dirty = stored['revision'], False
            active_indices = self._options['track_indices'] or []
            self._selected = [key for key in project.editable if project.tracks[key]['index'] in active_indices]
            if not self._selected:
                self._selected = list(project.editable[:1])
            self._active = next(iter(self._selected), next(iter(project.tracks)))
            self._mode = 'multi' if len(self._selected) > 1 else 'single'
            self._start = self._end = self._left = project.minimum
            self._span = min(18., max(1., project.maximum - project.minimum))
            self._query, self._matches = '', []
            self._plan, self._summary = None, ''
            self.state, self.message = 'ready', tr('点击歌词或拖出时间范围，再为选中的轨道添加风格。')
            self._candidate_stale = bool(receipt)
            if receipt:
                try:
                    options, preset = self._execution()
                    old_preset = receipt.get('preset_binding')
                    self._candidate_stale = (engine.settings(receipt['options']) != options
                                             or old_preset != preset)
                except ValueError:
                    pass
            self.tracksChanged.emit()
            self.matchesChanged.emit()
            self.regionsChanged.emit()
            self._update_notes()

        self._command('loading', '正在读取工程、歌词和草稿…',
                      lambda stop, _send: _load_project(path, job, artifact, self._drafts,
                                                        self._legacy_drafts, sessions, stop), finish)

    @Slot(QUrl)
    def chooseProject(self, url):
        if url.isLocalFile():
            self._load(str(Path(url.toLocalFile()).resolve()))

    def accept_task(self, job, artifact):
        if artifact['available'] and Path(artifact['path']).suffix.casefold() == '.svp':
            self._load(artifact['path'], str(Path(job).resolve()), artifact)

    @Slot(str)
    def setMode(self, mode):
        if self.project and not self.worker.kind and mode in ('single', 'multi') and mode != self._mode:
            old = self._indices()
            self._mode = mode
            self._changed_tracks(old)

    def _changed_tracks(self, old):
        changed = old != self._indices() and not self._regions
        if changed:
            self._rebind_preset()
        self._dirty_draft(invalidate=changed)
        self._update_notes()

    @Slot(str)
    def focusTrack(self, track):
        if self.project and track in self.project.tracks and not self.worker.kind and self._active != track:
            old = self._indices()
            self._active = track
            self._changed_tracks(old)

    @Slot(str)
    def toggleTrack(self, track):
        if self.project and track in self.project.editable and not self.worker.kind:
            old = self._indices()
            if track in self._selected:
                self._selected.remove(track)
            else:
                self._selected.append(track)
            self._changed_tracks(old)

    @Slot(str)
    def setQuery(self, query):
        value = query[:256]
        if value != self._query:
            self._query = value
            self._matches = self.project.matches(value) if self.project else []
            self.matchesChanged.emit()
            self.changed.emit()

    @Slot(str, str)
    def selectMatch(self, track, note):
        if not self.project or track not in self.project.tracks:
            return
        row = next((item for item in self._matches if item['trackId'] == track and item['id'] == note), None)
        row = row or self.project.notes.get(note)
        if row and row['trackId'] == track:
            self.focusTrack(track)
            self.selectSpan(row['start'], row['end'])
            self.setViewport(row['start'] - self._span * .22, self._span)

    @Slot(float, float)
    def selectSpan(self, first, last):
        if self.project and math.isfinite(first) and math.isfinite(last):
            a, b = sorted((first, last))
            self._start = min(max(a, self.project.minimum), self.project.maximum)
            self._end = min(max(b, self.project.minimum), self.project.maximum)
            self.changed.emit()

    @Slot(float, float)
    def setViewport(self, left, span):
        if self.project and math.isfinite(left) and math.isfinite(span):
            low, high = self.project.minimum, self.project.maximum
            span = min(max(.5, span), max(1., high - low))
            left = max(low, min(left, high - span))
            if (left, span) != (self._left, self._span):
                self._left, self._span = left, span
                self._update_notes()
                self.changed.emit()

    def _set_options(self, changes, *, detach=True):
        if self.worker.kind:
            return
        try:
            options = draft_options({**self._options, **changes})
            if options == self._options:
                return
            self._options = options
            if detach:
                self._preset = None
            else:
                self._rebind_preset()
            self._dirty_draft(invalidate=not self._regions or bool(set(changes) - set(_BRUSH_KEYS)))
        except (OSError, ValueError) as error:
            self._error(error)

    @Slot(str)
    def setStyle(self, name):
        self._set_options({'performance_style': name, 'style_detail': None})

    @Slot(float)
    def setStyleStrength(self, value):
        self._set_options({'style_strength': value})

    @Slot(bool)
    def setStylePitch(self, value):
        self._set_options({'style_pitch': value})

    @Slot(str, 'QVariant')
    def setTuningOption(self, key, value):
        if key in _SETTING_KEYS:
            self._set_options({key: value})

    @Slot(str)
    def setExpressionMode(self, mode):
        if mode in _SOURCES and mode != self._options['expression_mode']:
            self._set_options({'expression_mode': mode, 'reference_sources': [], 'retained_sources': [],
                               'reference_calibration': None, 'retained_project': None,
                               **{key: mode == 'reference' and key == 'reference_dynamics' for key in _REFERENCE_FLAGS}})

    @Slot(str, QUrl)
    def bindReference(self, track, url):
        if (self.project and track in self.project.editable and (url.isLocalFile() or url.isEmpty())
                and self._options['expression_mode'] in ('reference', 'calibrated')):
            index = self.project.tracks[track]['index']
            voice = 'vocals' if len(self.project.editable) == 1 else 'lead' if track == self.project.editable[0] else 'backing'
            old = next((row for row in self._options['reference_sources'] if row['track_index'] == index), None)
            if old:
                voice = old['voice']
            rows = [row for row in self._options['reference_sources'] if row['track_index'] != index]
            if not url.isEmpty():
                rows.append({'track_index': index, 'path': url.toLocalFile(), 'voice': voice})
            self._set_options({'reference_sources': rows}, detach=False)

    @Slot(str, str)
    def setReferenceVoice(self, track, voice):
        if (self.project and track in self.project.editable and voice in ('lead', 'backing', 'vocals')
                and self._options['expression_mode'] in ('reference', 'calibrated')):
            index = self.project.tracks[track]['index']
            rows = [{**row, 'voice': voice} if row['track_index'] == index else row
                    for row in self._options['reference_sources']]
            self._set_options({'reference_sources': rows}, detach=False)

    @Slot(QUrl)
    def bindCalibration(self, url):
        if self._options['expression_mode'] == 'calibrated' and (url.isLocalFile() or url.isEmpty()):
            self._set_options({'reference_calibration': url.toLocalFile() or None}, detach=False)

    @Slot(str, QUrl, int)
    def bindRetained(self, track, url, source_index):
        if (self.project and track in self.project.editable and (url.isLocalFile() or url.isEmpty())
                and source_index >= 0 and self._options['expression_mode'] == 'retained'):
            index = self.project.tracks[track]['index']
            rows = [row for row in self._options['retained_sources'] if row['track_index'] != index]
            if not url.isEmpty():
                rows.append({'track_index': index, 'project': url.toLocalFile(), 'source_track_index': source_index})
            self._set_options({'retained_sources': rows}, detach=False)

    @Slot(bool)
    def confirmCandidate(self, value):
        if not self.worker.kind and self._confirmed != value:
            self._confirmed = value
            self._rebind_preset()
            self._dirty_draft()

    def _brush(self):
        return {key: deepcopy(self._options[value]) for key, value in (
            ('preset', 'performance_style'), ('strength', 'style_strength'), ('pitch', 'style_pitch'),
            ('detail', 'style_detail'), ('technique', 'style_technique'),
            ('technique_strength', 'technique_strength'), ('technique_pitch', 'technique_pitch'))}

    @Slot()
    def addRegion(self):
        if not self.project or self.worker.kind or self._legacy:
            return
        try:
            time_range_settings((self._start, self._end))
            rows = [self.project.region(track, self._start, self._end, self._brush()) for track in self._targets]
            if not any(rows):
                raise ValueError('选区内没有所选演唱轨的音符。')
            updated = self._regions + [row for row in rows if row]
            self.project.verify_regions(updated)
            self._regions = updated
            self._preset = None
            self.regionsChanged.emit()
            self._dirty_draft()
            self.message = tr('已添加 {count} 个逐轨片段。').format(count=sum(row is not None for row in rows))
            self.state = 'ready'
            self.changed.emit()
        except (ValueError, OSError) as error:
            self._error(error)

    @Slot(str)
    def removeRegion(self, identity):
        if not self.worker.kind and any(row['id'] == identity for row in self._regions):
            self._regions = [row for row in self._regions if row['id'] != identity]
            self.regionsChanged.emit()
            self._dirty_draft()

    @Slot()
    def migrateLegacy(self):
        if self._legacy and not self.worker.kind:
            try:
                if self._legacy_drafts.read(self.project.sha256)['revision'] != self._legacy_revision:
                    raise ValueError('旧草稿已被更新，请重新载入工程查看最新版。')
                rows = self.project.expand_legacy(self._legacy, self._targets)
                self._options.update(deepcopy(self._legacy['tool']))
                self._regions, self._legacy, self._preset = rows, None, None
                self.regionsChanged.emit()
                self._dirty_draft()
                self.message = tr('已将旧片段按原轨道展开；旧草稿未改动。')
                self.changed.emit()
            except (ValueError, OSError) as error:
                self._error(error)

    @Slot()
    def freeze(self):
        if self.worker.kind or not self._flush_draft():
            return
        try:
            options, preset = self._execution()
            project, job, artifact = self.project, self.job, self.artifact
            plan = freeze_plan(project, self._regions, options, preset)
        except (ValueError, OSError) as error:
            self._error(error)
            return

        def work(stop, send):
            _verify_input(project, job, artifact, stop)
            return _preview(project, options, stop, send)

        def finish(summary):
            self._plan, self._summary = plan, summary
            self.state, self.message = 'frozen', tr('计划已冻结，可生成独立候选。')

        self._command('freezing', '正在核对输入、轨道和参数差异…', work, finish)

    @Slot()
    def run(self):
        if not self._plan or self.worker.kind:
            return
        expected, raw = self._plan
        project, job, artifact, session = self.project, self.job, self.artifact, self.session

        def work(stop, send):
            data = json.loads(raw)
            if engine.digest(data) != expected or data['input_sha256'] != project.sha256:
                raise ValueError('冻结计划已变化，请重新预览。')
            _verify_input(project, job, artifact, stop)
            if not Path(native_path(session)).is_file():
                workflow.create_session(project.project['path'], session.parent, job=job or None)
            spec = workflow.read_session(session)
            if (spec['input_sha256'], spec['source'], spec['job']) != (project.sha256, project.project['path'], job or None):
                raise ValueError('会话输入不再对应冻结计划。')
            plan_path = workflow._owned(session.parent, session.parent / ('quick-plan-' + expected + '.json'))
            if not plan_path.is_file():
                atomic_json(plan_path, {'digest': expected, 'plan': data})
            else:
                stored = json.loads(plan_path.read_text(encoding='utf-8'))
                if stored.get('digest') != expected or engine.digest(stored.get('plan')) != expected:
                    raise ValueError('已有逐轨计划的校验失败，未修改原工程或候选。')
            receipt = workflow.run_session(session, data['options'], cancel=lambda: _stop_check(stop),
                                           progress=send, preset_binding=data['preset'])
            try:
                _verify_input(project, job, artifact, stop)
                return receipt, ''
            except (OSError, ValueError) as stale:
                # Preserve a completed file even if the upstream job changed during rendering.
                return receipt, str(stale)

        def finish(value):
            receipt, stale = value
            self.candidate, self._candidate_stale = receipt, bool(stale)
            if stale:
                self.state, self.message = 'partial', tr('已保留候选，但当前不可登记。') + ' ' + tr(stale)
            elif not receipt.get('receipt_verified'):
                self.state, self.message = 'partial', tr('工程已保存，回执待补全；可用同一计划重试。')
            else:
                partial = receipt['status'] == 'partial'
                self.state = 'partial' if partial else 'done'
                self.message = tr('候选已生成，部分参数保留原样；请打开工程核对。' if partial else
                                  '候选已生成。请在编辑器中核对并合成歌声。')

        self._command('running', '正在生成独立调音候选…', work, finish)

    @Slot()
    def cancel(self):
        if self.worker.kind in ('tuning-loading', 'tuning-freezing', 'tuning-running'):
            self.worker.cancel()
            self.message = tr('正在取消…')
            self.changed.emit()

    def _verify_candidate(self, stop):
        _verify_input(self.project, self.job, self.artifact, stop)
        verified = workflow.verified_result(self.session)
        if not self.candidate or any(verified.get(key) != self.candidate.get(key)
                                     for key in ('signature', 'output', 'output_sha256')):
            raise ValueError('候选身份已变化，请重新生成或载入。')
        return verified

    @Slot()
    def registerResult(self):
        if not self.view['canRegister']:
            return

        def work(stop, _send):
            from task_store import known_job

            self._verify_candidate(stop)
            return workflow.register_result(self.session, job=known_job(self.job))

        def finish(_value):
            self.state, self.message = 'registered', tr('已登记到准备任务。合成后的歌声可在简易混音中导入。')

        self._command('registering', '正在验签并登记调音工程…', work, finish)

    @Slot()
    def openCandidate(self):
        if self.worker.kind or not self.candidate or not self.candidate.get('receipt_verified'):
            return

        def finish(result):
            self.state = 'ready' if self._candidate_stale else 'done'
            self.message = tr('已打开独立候选，请在编辑器中核对。')
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(result['output'])):
                raise ValueError('系统未能打开 SVP，请在 Synthesizer V 中选择该工程。')

        self._command('checking', '正在核验候选工程…', lambda stop, _send: self._verify_candidate(stop), finish)

    @Slot()
    def openMixdown(self):
        self.parent().navigate('audio_mixdown')

    def _bound(self, document, raw):
        indices = self._indices()
        bindings = {'vocal_tracks': indices, 'confirm_candidate_baseline': self._confirmed,
                    'reference_audio': [row for row in self._options['reference_sources'] if row['track_index'] in indices],
                    'retained_project': [row for row in self._options['retained_sources'] if row['track_index'] in indices]}
        if self._options['reference_calibration']:
            bindings['calibration_profile'] = self._options['reference_calibration']
        return plan_application(document, self.project.project, bindings, raw_document=raw)

    def _rebind_preset(self):
        if self._preset and self.project:
            self._preset = self._bound(self._preset['document'], self._preset['document_text'])

    @Slot(str)
    def applyPreset(self, key):
        if not self.project or key not in self._presets or self.worker.kind:
            return
        row = self._presets[key]

        def finish(value):
            if value['entry']['sha256'] != row['sha256']:
                raise ValueError('预设版本已改变，请重新选择。')
            plan = self._bound(value['document'], value['raw'])
            if self._regions:
                if plan['document']['payload']['expression_mode'] != 'preserve':
                    raise ValueError('此预设包含参数来源。请在没有逐轨片段的工程上绑定。')
                self._options.update({key: deepcopy(plan['controls'][key]) for key in _BRUSH_KEYS})
                self._dirty_draft(invalidate=False)
                self.message = tr('预设已成为下一片段的风格；现有片段保留。')
            else:
                self._preset = plan
                if plan['options'] is not None:
                    self._options = deepcopy(plan['options'])
                else:
                    self._options.update(deepcopy(plan['controls']))
                    self._options.update(reference_calibration=plan['bindings'].get('calibration_profile'),
                                         reference_sources=plan['bindings'].get('reference_audio', []),
                                         retained_sources=plan['bindings'].get('retained_project', []),
                                         retained_project=None)
                self._dirty_draft()
                self.message = tr('已绑定预设到当前工程。冻结前可查看或补齐所需的本地来源。')
            self.state = 'ready'

        self._command('preset', '正在读取预设版本…',
                      lambda _stop, _send: self._library.get_snapshot(row['id'], row['version']), finish)

    @Slot(str)
    def savePreset(self, name):
        if self.worker.kind:
            return
        try:
            document = make_document(self._options, name)
        except ValueError as error:
            self._error(error)
            return

        def work(_stop, _send):
            self._library.put_document(document, expected_revision=self._library.snapshot()['revision'])
            return self._library.snapshot()

        self._preset_task(work, '预设已添加到本机库。')

    @Slot(QUrl)
    def importPresets(self, url):
        if not url.isLocalFile():
            return

        def work(_stop, _send):
            self._library.import_file(url.toLocalFile(), expected_revision=self._library.snapshot()['revision'])
            return self._library.snapshot()

        self._preset_task(work, '预设已附加到本机库；现有预设未覆盖。')

    @Slot(QUrl, str)
    def exportPreset(self, url, key):
        if not url.isLocalFile() or key not in self._presets:
            return
        row = self._presets[key]

        def work(_stop, _send):
            self._library.export_file(row['id'], row['version'], url.toLocalFile())
            return self._library.snapshot()

        self._preset_task(work, '预设已导出。')

    def _preset_task(self, work, label):
        def finish(snapshot):
            self._set_catalog(snapshot)
            self.presetLibraryChanged.emit()
            self.state, self.message = 'ready', tr(label)

        self._command('preset', '正在处理本机预设库…', work, finish)

    def close(self):
        self._autosave.stop()
        self._flush_draft()
