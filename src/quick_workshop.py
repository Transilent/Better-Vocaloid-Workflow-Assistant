"""Application-owned workbench drafts, trials and calibration preset binding."""

import math
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from calibration_presets import TYPE as CALIBRATION_TYPE, LocalCalibrationBindings, resolve_trials
from common import ROOT
from i18n import tr
from expression_controls import STYLE_CONTROL_NAMES
from preset_documents import STYLE_ACTION_BOUNDS, STYLE_STRUCTURE_CONTRACT, STYLE_TIMING_BOUNDS
from preset_library import PresetLibrary
from quick_media_handoff import verify_preparation_vocal
from style_presets import freeze_recipe


_STYLE_DEFAULTS = {'mode': 'project', 'track': -1, 'start': '', 'end': '', 'audio': '', 'offset': '0',
                   'measurePitch': False, 'useGain': True, 'usePitch': True, 'name': '', 'strength': .6}
_PANES = ('style', 'calibration', 'presets')
_CONNECTION_ACTIONS = ('connection', 'repeated', 'rising', 'falling')


def _cancel(stop):
    if stop.is_set():
        raise InterruptedError('操作已取消。')


class QuickWorkshop(QObject):
    changed = Signal()
    paneChanged = Signal()

    def __init__(self, parent, tuning, support, worker, *, root=ROOT):
        super().__init__(parent)
        self.tuning, self.support, self.worker = tuning, support, worker
        self.library = PresetLibrary(Path(root) / 'user-presets')
        self.bindings = LocalCalibrationBindings(self.library.root)
        self._pane = 'style'
        self._fields = dict(_STYLE_DEFAULTS)
        self._reference = self._result = self._analysis_key = self._trial = self._recipe = None
        self._comparison = None
        self._comparison_track = -1
        self._audio_binding = None
        self._reference_review = None
        self._reference_panel = 'positions'
        self._chosen_positions = set()
        self._point = 0
        from style_authoring import structure_draft

        self._method = 'design'
        self._template = 'none'
        self._designs = {'none': {'name': '', **structure_draft()}}
        self._design_channel = 'loudness'
        self._design_panel = 'expression'
        self._connection_action = 'connection'
        self._edited_preset_sha = ''
        self._message = ''
        self._selection = {}
        self._associated = ''
        tuning.changed.connect(self.changed)
        worker.changed.connect(self.changed)
        support.presetLibraryChanged.connect(self.changed)

    @Property(str, notify=paneChanged)
    def pane(self):
        return self._pane

    @Slot(str)
    def setPane(self, pane):
        if pane not in _PANES:
            return
        if pane != self._pane:
            self._pane = pane
            self.paneChanged.emit()
        if self.parent().route == 'tuning_workshop':
            self.enter()

    @Slot()
    def enter(self):
        if self._pane == 'style' and self._method == 'reference' and self._reference is None and not self.worker.kind:
            self.useCurrentReference()
        if self._pane == 'presets':
            self.refreshLibrary()

    @Slot()
    def refreshLibrary(self):
        if self._pane == 'presets':
            self.support.refreshPresetLibrary()

    def _analysis_stamp(self):
        fields = self._fields
        dry = fields['mode'] == 'audio'
        comparison = (self._comparison['path'], self._comparison['sha256'], self._comparison_track
                      ) if fields['mode'] == 'compare' and self._comparison else None
        return (self._reference and self._reference['sha256'], fields['track'], fields['start'], fields['end'],
                fields['measurePitch'], fields['mode'], fields['audio'] if dry else '',
                fields['offset'] if dry else 0., comparison)

    def _ready(self):
        return bool(self._result and self._analysis_key == self._analysis_stamp())

    def _trial_is_current(self):
        return bool(self._trial and self._trial['plan']['target'] == self.tuning.style_trial_identity()
                    and (self._method == 'design' or self._ready()))

    def _notice(self, text):
        self._message = tr(str(text)[:1400])
        self.changed.emit()

    @Property('QVariantMap', notify=changed)
    def view(self):
        fields = dict(self._fields)
        try:
            start, end = (float(fields[key]) for key in ('start', 'end'))
            valid_range = math.isfinite(start) and math.isfinite(end) and start < end
        except ValueError:
            valid_range = False
        try:
            valid_offset = fields['mode'] != 'audio' or math.isfinite(float(fields['offset']))
        except ValueError:
            valid_offset = False
        measured = self._result or {}
        ready = self._ready()
        valid_recipe = ready and bool(fields['name'].strip()) and (
            (fields['useGain'] and measured['points']) or (fields['usePitch'] and measured['gestures']))
        design_view = self._design_view() if self._method == 'design' else {}
        if design_view:
            draft = design_view['draft']
            fields.update(name=draft['name'], strength=draft['strength'])
            ready = True
            valid_recipe = bool(draft['name'].strip() and design_view['hasChanges'])
        trial = self._trial if self._trial_is_current() else {}
        review = self._reference_review
        audio = self._audio_binding or {}
        return {'busy': bool(self.worker.kind), 'running': self.worker.kind.startswith('workshop-'),
                'message': self._message, 'pane': self._pane, 'fields': fields,
                'method': self._method, 'design': design_view,
                'reference': self._reference or {}, 'points': measured.get('points', []),
                'comparison': self._comparison or {}, 'comparisonTrack': self._comparison_track,
                'referenceReview': review['view'] if review and review['key'] == self._analysis_stamp() else {},
                'preparedVocal': {'title': audio['title'], 'role': audio['role']} if audio else {},
                'gestures': measured.get('gestures', []), 'report': measured.get('report', {}),
                'referencePanel': self._reference_panel,
                'positions': measured.get('positions', []) if self._ready() else [],
                'chosenPositions': sorted(self._chosen_positions) if self._ready() else [],
                'ready': ready, 'hasAnalysis': bool(measured), 'selectedPoint': self._point,
                'canUseCurrent': bool(self.tuning.style_reference_seed()),
                'canAnalyze': bool(self._reference and fields['track'] >= 0 and valid_range
                                   and (fields['mode'] != 'audio' or (valid_offset and fields['audio']))
                                   and (fields['mode'] != 'compare' or self._comparison)),
                'validRecipe': bool(valid_recipe), 'trial': trial.get('summary', ''),
                'canApplyTrial': bool(trial.get('hasEffect')),
                'target': self.tuning.style_trial_target(), 'selectedPreset': self._selection,
                'associatedFolder': self._associated}

    def _design_view(self):
        from style_authoring import structure_contents, structure_preview, structure_templates

        draft = self._designs[self._template]
        rows = [{'id': 'none', 'label': '新风格草稿', 'description': '先决定单音和整句的变化，再选择在哪些位置加唱腔动作。', 'color': ''},
                *structure_templates()]
        if 'library' in self._designs:
            rows.append({'id': 'library', 'label': '从预设库复制', 'description': '编辑预设的独立副本。',
                         'color': self._designs['library']['color'] or ''})
        controls, actions = structure_contents(draft)
        return {'template': self._template, 'templates': rows, 'draft': draft, 'channel': self._design_channel,
                'panel': self._design_panel, 'connectionAction': self._connection_action,
                'activeActions': list(actions), 'hasChanges': bool(controls or actions),
                'preview': structure_preview(draft, self._design_channel) if self._design_panel == 'expression' else {}}

    @Property('QVariantMap', constant=True)
    def designBounds(self):
        return {section: {key: list(bounds) for key, bounds in fields.items()}
                for section, fields in {'timing': STYLE_TIMING_BOUNDS, **STYLE_ACTION_BOUNDS}.items()}

    @Slot(str)
    def setAuthorMethod(self, method):
        if self.worker.kind or method not in ('reference', 'design') or self._method == method:
            return
        if method == 'design':
            self.selectTemplate(self._template)
            return
        self._method, self._trial = method, None
        self._notice('')
        self.enter()

    @Slot(str)
    def selectTemplate(self, template):
        if self.worker.kind:
            return
        from style_authoring import structure_draft, structure_templates

        if template not in self._designs:
            row = next((row for row in structure_templates() if row['id'] == template), None)
            if template != 'none' and row is None:
                return
            draft = structure_draft(template)
            draft['name'] = tr('{name} · 自定义').format(name=tr(row['label'])) if row else ''
            self._designs[template] = draft
        if self._method != 'design' or self._template != template:
            self._method, self._template, self._trial = 'design', template, None
            self._notice('')

    @Slot(str)
    def selectDesignPanel(self, panel):
        if panel in ('expression', 'pitch') and self._design_panel != panel:
            self._design_panel = panel
            self.changed.emit()

    @Slot(str)
    def selectConnectionAction(self, action):
        if action in _CONNECTION_ACTIONS and self._connection_action != action:
            self._connection_action = action
            self.changed.emit()

    @Slot(str)
    def selectDesignChannel(self, channel):
        if channel in STYLE_CONTROL_NAMES and self._design_channel != channel:
            self._design_channel = channel
            self.changed.emit()

    @Slot(str, str, 'QVariant')
    def editDesign(self, section, key, value):
        if self.worker.kind or self._method != 'design':
            return
        draft = self._designs[self._template]
        if section == 'timing':
            fields, bounds = draft['timing'], STYLE_TIMING_BOUNDS
        elif section in draft['pitch']:
            fields, bounds = draft['pitch'][section], STYLE_ACTION_BOUNDS[section]
        else:
            channel, _, layer = section.partition('.')
            fields = draft['controls'].get(channel, {}).get(layer, {})
            limit = 12 if channel == 'loudness' else 1
            bounds = {field: (-limit, limit) for field in fields}
        if key not in fields:
            return
        if key in bounds:
            if type(value) not in (int, float) or not math.isfinite(value):
                return
            low, high = bounds[key]
            value = round(max(low, min(high, value)), 4)
        elif key == 'direction' and value not in ('settle', 'follow'):
            return
        elif key == 'anticipate_descent' and type(value) is not bool:
            return
        if fields[key] != value:
            fields[key] = value
            self._trial = None
            self.changed.emit()

    def _command(self, kind, label, work, finish):
        if self.worker.kind:
            return False
        outcome = ['succeeded', '']

        def complete(value, error):
            if error is None:
                try:
                    finish(value)
                except (TypeError, KeyError, OSError, ValueError) as failure:
                    error = failure
            if error is not None:
                outcome[:] = ['cancelled' if isinstance(error, InterruptedError) else 'failed', error]
                self._notice(error)
            self.changed.emit()

        self._message = tr(label)
        return self.worker.start('workshop-' + kind, work, complete, self._notice, label=label, route='tuning_workshop',
                                 outcome=lambda _value: outcome)

    @Slot()
    def cancel(self):
        if self.worker.kind.startswith('workshop-'):
            self.worker.cancel()
            self._notice('正在停止…')

    def _range(self, track, span=None):
        self._fields.update(track=track['index'],
                            start=f"{(span[0] if span else track['start']):.3f}",
                            end=f"{(span[1] if span else track['end']):.3f}")

    @Slot()
    def useCurrentReference(self):
        if seed := self.tuning.style_reference_seed():
            self._load_reference(seed[0], seed)

    @Slot(QUrl)
    def loadReference(self, url):
        if url.isLocalFile():
            self._load_reference(url.toLocalFile())

    @Slot(QUrl)
    def loadComparison(self, url):
        if url.isLocalFile():
            self._load_reference(url.toLocalFile(), comparison=True)

    @Slot(int)
    def selectComparisonTrack(self, index):
        if not self.worker.kind and self._comparison and self._comparison_track != index:
            if any(row['index'] == index for row in self._comparison['tracks']):
                self._comparison_track = index
                self._trial = None
                self.changed.emit()

    def _load_reference(self, path, seed=None, *, comparison=False):
        from style_authoring import inspect_reference

        def work(stop, _send):
            _cancel(stop)
            value = inspect_reference(path)
            _cancel(stop)
            return value

        def finish(value):
            track = value['tracks'][0]
            if comparison:
                self._comparison = value
                self._comparison_track = next((row['index'] for row in value['tracks']
                                               if row['index'] == self._fields['track']), track['index'])
                self._trial = None
                self._notice('已读取对照底稿。提取时会核对两份工程的乐谱和时间轴。')
                return
            span = None
            if seed and value['sha256'] == seed[1]:
                match = next((row for row in value['tracks'] if row['index'] == seed[2]), None)
                if match:
                    track = match
                    if seed[3][1] > seed[3][0]:
                        span = seed[3]
            self._reference, self._result, self._analysis_key, self._trial, self._recipe = value, None, None, None, None
            self._point = 0
            self._range(track, span)
            self._notice('已读取参考工程的演唱轨。')

        self._command('reference', '正在读取对照底稿…' if comparison else '正在读取参考工程…', work, finish)

    @Slot(int)
    def selectReferenceTrack(self, index):
        if not self.worker.kind and self._reference:
            track = next((row for row in self._reference['tracks'] if row['index'] == index), None)
            if track and self._fields['track'] != index:
                self._range(track)
                self._trial = None
                self.changed.emit()

    @Slot(QUrl)
    def chooseDryVocal(self, url):
        if not self.worker.kind and (url.isLocalFile() or url.isEmpty()):
            self._fields['audio'] = url.toLocalFile()
            self._audio_binding = self._reference_review = self._analysis_key = None
            self._trial = None
            self.changed.emit()

    def acceptPreparedVocal(self, job, title, artifact, role):
        same_file = self._fields['audio'] and Path(self._fields['audio']) == Path(artifact['path'])
        self._audio_binding = {'job': job, 'title': title or Path(job).name, 'artifact': dict(artifact), 'role': role}
        if not same_file:
            self._fields['offset'] = '0'
        self._fields.update(mode='audio', audio=artifact['path'])
        self._method = 'reference'
        self._reference_review = self._analysis_key = self._trial = None
        self._notice('已承接准备任务的人声。请选择对应乐谱并核对干声时间。')

    @Slot(str, 'QVariant')
    def edit(self, name, value):
        if self.worker.kind or name not in ('mode', 'start', 'end', 'offset', 'measurePitch',
                                            'name', 'strength', 'useGain', 'usePitch'):
            return
        if name == 'mode' and value not in ('project', 'audio', 'compare'):
            return
        if name == 'strength':
            value = round(float(value), 3)
            if not 0 <= value <= 1:
                return
        if self._method == 'design' and name in ('name', 'strength'):
            draft = self._designs[self._template]
            if draft[name] != value:
                draft[name], self._trial = value, None
                self.changed.emit()
            return
        if value != self._fields[name]:
            self._fields[name] = value
            self._trial = None
            self.changed.emit()

    @Slot()
    def analyze(self):
        self._run_reference(review_only=False)

    @Slot()
    def checkReference(self):
        self._run_reference(review_only=True)

    def _run_reference(self, *, review_only):
        if self.worker.kind or self._method != 'reference' or not self.view['canAnalyze']:
            return
        from style_authoring import analyze_reference

        path = self._reference['path']
        stamp = self._analysis_stamp()
        sha, index, start, end, pitch, _mode, wave, offset, comparison = stamp
        before, before_sha, before_track = comparison or (None, None, None)
        binding = self._audio_binding if wave else None
        review = self._reference_review or {}
        expected_audio = review.get('audio_sha256') if review.get('key') == stamp else None
        self._reference_review = None
        if not review_only:
            self._result = self._trial = self._recipe = None
            self._chosen_positions.clear()
            self._point = 0

        def work(stop, send):
            try:
                digest = expected_audio
                if binding:
                    verified, _role = verify_preparation_vocal(binding['job'], binding['artifact'], check=stop.is_set)
                    digest = verified['sha256']
                return analyze_reference(path, index, (float(start), float(end)), audio=wave or None,
                                         audio_zero=float(offset), pitch=pitch, expected_sha256=sha,
                                         before=before, before_track=before_track, expected_before_sha256=before_sha,
                                         expected_audio_sha256=digest, review_only=review_only,
                                         cancel=lambda: _cancel(stop), progress=send)
            except InterruptedError:
                raise
            except (OSError, ValueError, RuntimeError) as error:
                if not review_only:
                    raise
                return {'error': str(error)}

        def finish(result):
            if 'error' in result:
                self._analysis_key = self._trial = None
                self._reference_review = {'key': stamp, 'view': {'error': tr(result['error'])}}
                self._notice('参考暂不可用，请查看核对结果。')
                return
            report = result['report']
            self._reference_review = {'key': stamp, 'audio_sha256': result['audio_sha256'],
                                      'view': {**report, 'review_only': review_only}}
            if review_only:
                if not report['usable']:
                    self._analysis_key = self._trial = None
                self._notice('参考已核对，可继续提取。' if report['usable'] else '参考暂不可用，请查看核对结果。')
                return
            self._result = result
            self._analysis_key = stamp
            self._notice('已提取参考风格，可调整后试套或保存。' if report['usable']
                         else '已识别唱段位置，参考中暂无可用的表达变化。')

        self._command('review' if review_only else 'analysis',
                      '正在核对参考唱段…' if review_only else '正在分析参考唱段…', work, finish)

    @Slot(str)
    def adoptReference(self, part):
        if self.worker.kind or self._method != 'reference' or not self._ready() or part not in ('loudness', 'pitch'):
            return
        from style_authoring import adopt_reference_part

        try:
            adopt_reference_part(self._designs['none'], self._result, part)
        except ValueError as error:
            self._notice(error)
            return
        self.selectTemplate('none')
        self.selectDesignPanel('expression' if part == 'loudness' else 'pitch')
        self.selectDesignChannel('loudness')
        self._notice('参考力度已带入草稿的单音包络。' if part == 'loudness' else '参考起收音高已带入句首和句尾动作。')

    @Slot(str)
    def selectReferencePanel(self, panel):
        if panel in ('positions', 'envelope') and self._reference_panel != panel:
            self._reference_panel = panel
            self.changed.emit()

    @Slot(str, bool)
    def chooseReferencePosition(self, position, checked):
        if self.worker.kind or self._method != 'reference' or not self._ready():
            return
        if not any(row['id'] == position and row['adoptable'] for row in self._result['positions']):
            return
        if checked:
            self._chosen_positions.add(position)
        else:
            self._chosen_positions.discard(position)
        self.changed.emit()

    @Slot()
    def adoptReferencePositions(self):
        if self.worker.kind or self._method != 'reference' or not self._ready() or not self._chosen_positions:
            return
        from style_authoring import adopt_reference_position

        positions = self._chosen_positions.copy()
        for position in positions:
            adopt_reference_position(self._designs['none'], self._result, position)
        self._chosen_positions.clear()
        self.selectTemplate('none')
        self.selectDesignPanel('expression' if positions == {'sustain'} else 'pitch')
        action = next((kind for kind in _CONNECTION_ACTIONS if kind in positions), None)
        if action:
            self.selectConnectionAction(action)
        self.selectDesignChannel('loudness')
        self._notice('所选唱段位置的变化已带入草稿。')

    @Slot(int)
    def selectPoint(self, index):
        if self._ready() and 0 <= index < len(self._result['points']) and self._point != index:
            self._point = index
            self.changed.emit()

    @Slot(int, float)
    def setPoint(self, index, value):
        if self._ready() and not self.worker.kind and math.isfinite(value) and 0 <= index < len(self._result['points']):
            self._point = index
            self._result['points'][index][1] = round(max(-12, min(12, value)), 2)
            self._trial = None
            self.changed.emit()

    @Slot(str, float)
    def setPitch(self, kind, cents):
        if not self._ready() or self.worker.kind or kind not in ('onset', 'release') or not math.isfinite(cents):
            return
        from style_authoring import EDGE_FRACTION
        from preset_documents import STYLE_PITCH_MAX_CENTS

        cents = round(max(-STYLE_PITCH_MAX_CENTS, min(STYLE_PITCH_MAX_CENTS, cents)), 1)
        gestures = [row for row in self._result['gestures'] if row['kind'] != kind]
        if cents:
            gestures.append({'kind': kind, 'cents': cents, 'fraction': EDGE_FRACTION})
        self._result['gestures'] = sorted(gestures, key=lambda row: row['kind'])
        self._trial = None
        self.changed.emit()

    def _current_recipe(self):
        from preset_documents import encode_document
        from style_authoring import make_recipe, make_structure_recipe

        if self._method == 'design':
            draft = self._designs[self._template]
            key = ('structure', draft)
            if self._recipe is None or key != self._recipe[0]:
                document = make_structure_recipe(draft['name'], draft)
                self._recipe = deepcopy(key), freeze_recipe(encode_document(document))
            return self._recipe[1]

        if not self._ready():
            raise ValueError('请先提取参考风格。')
        fields = self._fields
        points = self._result['points'] if fields['useGain'] else []
        gestures = self._result['gestures'] if fields['usePitch'] else []
        parameters = (fields['name'].strip(), fields['strength'], tuple(map(tuple, points)),
                      tuple(tuple(sorted(row.items())) for row in gestures))
        if self._recipe is None or parameters != self._recipe[0]:
            document = make_recipe(parameters[0], points, gestures, strength=fields['strength'])
            self._recipe = parameters, freeze_recipe(encode_document(document))
        return self._recipe[1]

    @Slot()
    def trialStyle(self):
        if self.worker.kind:
            return
        try:
            recipe = self._current_recipe()
            plan = self.tuning.plan_style_trial(recipe)
        except (ValueError, OSError) as error:
            self._notice(error)
            return
        self._trial = None

        def finish(result):
            self._trial = {'plan': plan, **result}
            self._notice('已试套。确认后应用到选区。' if result['hasEffect'] else result['summary'])

        self._command('trial', '正在试算目标选区…',
                      lambda stop, send: self.tuning.run_style_trial(plan, stop, send), finish)

    @Slot()
    def applyStyle(self):
        if self.worker.kind:
            return
        try:
            if not self._trial_is_current():
                raise ValueError('风格或目标选区已变化，请重新试套。')
            if not self._trial['hasEffect']:
                raise ValueError('当前配方在该选区未产生可写变化。')
            if self.tuning.apply_style_trial(self._trial['plan']):
                self._trial = None
                self.parent().navigate('tuning')
        except (ValueError, OSError) as error:
            self._notice(error)

    @Slot()
    def saveStyle(self):
        if self.worker.kind:
            return
        try:
            document = self._current_recipe()['document']
        except (ValueError, OSError) as error:
            self._notice(error)
            return

        def work(stop, _send):
            snapshot = self.library.snapshot()
            _cancel(stop)
            return self.library.put_document(document, expected_revision=snapshot['revision'])['entry']

        def finish(entry):
            self._select(entry)
            self.refreshLibrary()
            self._notice('风格配方已添加到本机库，可从预设导出。')

        self._command('save-style', '正在处理本机预设库…', work, finish)

    def _select(self, entry):
        self._selection = dict(entry)
        self._associated = (self.bindings.get(entry['sha256']).get('folder', '')
                            if entry['type'] == CALIBRATION_TYPE else '')
        self.changed.emit()

    @Slot(str, str)
    def selectPreset(self, identifier, version):
        row = next((entry for entry in self.support.presetLibrary['entries']
                    if (entry['id'], entry['version']) == (identifier, version)), None)
        if row:
            try:
                self._select(row)
                self.support.inspectPreset(identifier, version)
            except (OSError, ValueError) as error:
                self._notice(error)

    def calibrationSaved(self, entry):
        self._select(entry)
        self.refreshLibrary()

    def _selected_document(self, row):
        snapshot = self.library.get_snapshot(row['id'], row['version'])
        if snapshot['entry']['sha256'] != row['sha256']:
            raise ValueError('预设已变化，请重新选择。')
        return snapshot

    @Slot()
    def editSelectedStructure(self):
        row = self._selection
        if self.worker.kind or row.get('type') != 'style_recipe':
            return

        def work(stop, _send):
            _cancel(stop)
            document = self._selected_document(row)['document']
            if document['requires']['engine_contract'] != STYLE_STRUCTURE_CONTRACT:
                raise ValueError('此配方使用参考提取的相位包络。')
            return document

        def finish(document):
            from style_authoring import structure_draft

            if self._edited_preset_sha != row['sha256']:
                draft = structure_draft(document=document)
                draft['name'] = tr('{name} · 自定义').format(name=row['name'][:60])
                self._designs['library'] = draft
                self._edited_preset_sha = row['sha256']
            self.selectTemplate('library')
            self.setPane('style')

        self._command('open-recipe', '正在读取风格配方…', work, finish)

    @Slot(QUrl)
    def attachTrialFolder(self, url):
        row = self._selection
        if not url.isLocalFile() or not row or row['type'] != CALIBRATION_TYPE:
            return
        folder = url.toLocalFile()

        def work(stop, send):
            snapshot = self._selected_document(row)
            resolve_trials(snapshot['document'], folder, cancel=lambda: _cancel(stop), progress=send)
            _cancel(stop)
            previous = self.bindings.get(row['sha256'])
            return self.bindings.put(row['sha256'], folder, previous.get('reference', ''))

        def finish(value):
            if self._selection.get('sha256') == row['sha256']:
                self._associated = value['folder']
            self._notice('已核对并关联七份试音。')

        self._command('associate', '正在核对声库试音…', work, finish)

    @Slot()
    def useSelectedPreset(self):
        row = self._selection
        if self.worker.kind or not row:
            return
        try:
            if row['type'] == CALIBRATION_TYPE:
                context = self.tuning.calibration_preset_context()
            elif not self.tuning.project or not self.tuning._can_edit_regions():
                raise ValueError('请先载入可编辑的调音工程。')
            else:
                context = None
        except ValueError as error:
            self._notice(error)
            return

        def work(stop, send):
            snapshot = self._selected_document(row)
            if context is None:
                return freeze_recipe(snapshot['raw'])
            source = self.tuning.calibration_project_snapshot(context, stop)
            seed = context[-1]
            document = snapshot['document']
            profile, local_reference = self.bindings.profile(
                document, row['sha256'], source.data, seed['index'], cancel=lambda: _cancel(stop), progress=send)
            payload = document['payload']
            return (seed['index'], payload['voice'], profile, payload['strength'],
                    seed['reference'] or local_reference)

        def finish(value):
            if context is None:
                self.tuning.use_workshop_style(value)
            else:
                self.tuning.use_calibration_profile(*value)
            self.parent().navigate('tuning')

        self._command('bind', '正在核验并应用预设…', work, finish)
