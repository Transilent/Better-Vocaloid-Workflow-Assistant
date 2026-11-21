"""Application-owned workbench drafts, trials and calibration preset binding."""

import math
from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from calibration_presets import TYPE as CALIBRATION_TYPE, LocalCalibrationBindings, resolve_trials
from common import ROOT
from i18n import tr
from preset_library import PresetLibrary
from style_presets import freeze_recipe


_STYLE_DEFAULTS = {'mode': 'project', 'track': -1, 'start': '', 'end': '', 'audio': '', 'offset': '0',
                   'measurePitch': False, 'useGain': True, 'usePitch': True, 'name': '', 'strength': .6}
_PANES = ('style', 'calibration', 'presets')


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
        self._point = 0
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
        if self._pane == 'style' and self._reference is None and not self.worker.kind:
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
        return (self._reference and self._reference['sha256'], fields['track'], fields['start'], fields['end'],
                fields['measurePitch'], fields['mode'], fields['audio'] if dry else '', fields['offset'] if dry else 0.)

    def _ready(self):
        return bool(self._result and self._analysis_key == self._analysis_stamp())

    def _trial_is_current(self):
        return bool(self._ready() and self._trial and self._trial['plan']['target'] == self.tuning.style_trial_identity())

    def _notice(self, text):
        self._message = tr(str(text)[:1400])
        self.changed.emit()

    @Property('QVariantMap', notify=changed)
    def view(self):
        fields = self._fields
        try:
            start, end = (float(fields[key]) for key in ('start', 'end'))
            valid_range = math.isfinite(start) and math.isfinite(end) and start < end
        except ValueError:
            valid_range = False
        try:
            valid_offset = fields['mode'] == 'project' or math.isfinite(float(fields['offset']))
        except ValueError:
            valid_offset = False
        measured = self._result or {}
        ready = self._ready()
        valid_recipe = ready and bool(fields['name'].strip()) and (
            (fields['useGain'] and measured['points']) or (fields['usePitch'] and measured['gestures']))
        return {'busy': bool(self.worker.kind), 'running': self.worker.kind.startswith('workshop-'),
                'message': self._message, 'pane': self._pane, 'fields': dict(fields),
                'reference': self._reference or {}, 'points': measured.get('points', []),
                'gestures': measured.get('gestures', []), 'report': measured.get('report', {}),
                'ready': ready, 'hasAnalysis': bool(measured), 'selectedPoint': self._point,
                'canUseCurrent': bool(self.tuning.style_reference_seed()),
                'canAnalyze': bool(self._reference and fields['track'] >= 0 and valid_range
                                   and (fields['mode'] == 'project' or (valid_offset and fields['audio']))),
                'validRecipe': bool(valid_recipe), 'trial': self._trial['summary'] if self._trial_is_current() else '',
                'target': self.tuning.style_trial_target(), 'selectedPreset': self._selection,
                'associatedFolder': self._associated}

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

    def _load_reference(self, path, seed=None):
        from style_authoring import inspect_reference

        def work(stop, _send):
            _cancel(stop)
            value = inspect_reference(path)
            _cancel(stop)
            return value

        def finish(value):
            track = value['tracks'][0]
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

        self._command('reference', '正在读取参考工程…', work, finish)

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
            self._trial = None
            self.changed.emit()

    @Slot(str, 'QVariant')
    def edit(self, name, value):
        if self.worker.kind or name not in ('mode', 'start', 'end', 'offset', 'measurePitch',
                                            'name', 'strength', 'useGain', 'usePitch'):
            return
        if name == 'mode' and value not in ('project', 'audio'):
            return
        if name == 'strength':
            value = round(float(value), 3)
            if not 0 <= value <= 1:
                return
        if value != self._fields[name]:
            self._fields[name] = value
            self._trial = None
            self.changed.emit()

    @Slot()
    def analyze(self):
        if self.worker.kind or not self.view['canAnalyze']:
            return
        from style_authoring import analyze_reference

        path = self._reference['path']
        sha, index, start, end, pitch, _mode, wave, offset = self._analysis_stamp()
        self._result = self._trial = self._recipe = None
        self._point = 0

        def work(stop, send):
            return analyze_reference(path, index, (float(start), float(end)), audio=wave or None,
                                     audio_zero=float(offset), pitch=pitch, expected_sha256=sha,
                                     cancel=lambda: _cancel(stop), progress=send)

        def finish(result):
            self._result = result
            self._analysis_key = (sha, index, start, end, pitch, _mode, wave, offset)
            self._notice('已提取参考风格，可调整后试套或保存。')

        self._command('analysis', '正在分析参考唱段…', work, finish)

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
        from style_authoring import make_recipe

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

        def finish(label):
            self._trial = {'plan': plan, 'summary': label}
            self._notice('已试套。确认后应用到选区。')

        self._command('trial', '正在试算目标选区…',
                      lambda stop, send: self.tuning.run_style_trial(plan, stop, send), finish)

    @Slot()
    def applyStyle(self):
        if self.worker.kind:
            return
        try:
            if not self._trial_is_current():
                raise ValueError('风格或目标选区已变化，请重新试套。')
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
