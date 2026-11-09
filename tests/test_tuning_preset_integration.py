"""Offscreen Page integration for tuning presets and frozen project bindings."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

assert os.environ.get('BVWA_TESTING') == '1' and APP.parent.name == 'test-sandboxes', (
    'Use tests/run_tests.py isolation'
)

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QSpinBox

import common
import runtime_devices

# Keep source modules ahead of generated root copies, while application data,
# config and read-only runtime payloads resolve to the disposable app root.
common.ROOT = APP
runtime_devices.ROOT = APP

from app import Window
from preset_documents import PresetError, encode_document
from preset_library import PresetLibrary
from strength_control import set_combo_strength
from tuning_form_state import TuningFormState
from tuning_preset_documents import make_document
from tuning_preset_ui import TuningPresetLibraryDialog

assert Path(__import__('tuning_ui').__file__).resolve().parent == APP / 'src'


Q = 705600000
STYLE_DETAIL = {name: 1.0 for name in ('onset', 'sustain', 'connection', 'release')}


def note(onset, duration, pitch, lyric):
    return {
        'onset': onset,
        'duration': duration,
        'pitch': pitch,
        'lyrics': lyric,
        'attributes': {'retakes': [2, 1]},
    }


def project_fixture(marker):
    database = {'name': 'Integration Bank', 'languageOverride': 'mandarin'}
    voice = {'vocalModePreset': 'Clear'}
    return {
        'version': '1.0.0',
        'uuid': marker,
        'library': [{'name': 'keep', 'private': 7}],
        'renderConfig': {'setting': 'keep'},
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120}],
        },
        'tracks': [
            {
                'name': 'Lead Vocal',
                'mainRef': {
                    'blickOffset': Q // 4,
                    'pitchOffset': 0,
                    'isInstrumental': False,
                    'database': copy.deepcopy(database),
                    'voice': copy.deepcopy(voice),
                },
                'mainGroup': {
                    'uuid': marker + '-lead-group',
                    'notes': [note(0, Q, 60, '你'), note(Q, Q, 64, '好')],
                    'parameters': {},
                },
                'retakes': {'keep': marker},
                'groups': [],
            },
            {
                'name': 'Backing Vocal',
                'mainRef': {
                    'blickOffset': 0,
                    'pitchOffset': 0,
                    'isInstrumental': False,
                    'database': copy.deepcopy(database),
                    'voice': copy.deepcopy(voice),
                },
                'mainGroup': {
                    'uuid': marker + '-backing-group',
                    'notes': [note(0, Q, 55, '伴')],
                    'parameters': {},
                },
                'groups': [],
            },
            {
                'name': 'Instrumental',
                'mainRef': {'isInstrumental': True},
                'mainGroup': {'notes': [], 'parameters': {}},
            },
        ],
    }


def write_svp(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    path.write_bytes(payload + b'\x00')
    return path


def write_wav(path, *, samples=160, rate=16000):
    import wave

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\x00\x00' * samples)
    return path


def preset_controls(mode, **updates):
    values = {
        'expression_mode': mode,
        'track_indices': [0],
        'reference_dynamics': mode == 'reference',
        'reference_tension': False,
        'reference_breathiness': False,
        'reference_voicing': False,
        'dynamics_strength': 0.55,
        'reference_calibration': None,
        'retained_project': None,
        'retained_sources': [],
        'performance_style': 'none',
        'style_strength': 0.6,
        'style_pitch': False,
        'style_scope': 'lead',
        'style_detail': copy.deepcopy(STYLE_DETAIL),
        'expression_density': 'original',
        'style_technique': 'none',
        'technique_strength': 0.6,
        'technique_pitch': True,
    }
    values.update(copy.deepcopy(updates))
    return values


def windows_native_path(path):
    value = str(Path(path).absolute())
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\' + value
    return Path(value)


def remove_owned_tree(path, parent):
    path = Path(path).resolve()
    parent = Path(parent).resolve()
    if path.parent != parent:
        raise RuntimeError(f'refusing to remove non-owned path: {path}')
    if not path.exists():
        return
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise RuntimeError(f'refusing to traverse reparse point: {path}')
    # Windows can briefly report ERROR_DIR_NOT_EMPTY after a thread or AV scan
    # releases a just-written immutable document. Normalize access, retry, and
    # still fail the test if the owned tree cannot be removed cleanly.
    native = windows_native_path(path)
    for attempt in range(8):
        make_tree_writable(path)
        try:
            shutil.rmtree(native)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            retryable = getattr(error, 'winerror', None) in (5, 32, 33, 145)
            if not retryable or attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def make_tree_writable(path):
    """Normalize temporary fixture permissions before a strict cleanup retry."""
    root = Path(path)
    if not root.exists():
        return
    for current, directories, files in os.walk(windows_native_path(root)):
        for name in directories + files:
            target = Path(current) / name
            try:
                os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
            except FileNotFoundError:
                pass


class TuningPresetPageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.window = Window()
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'window'):
            cls.window.close()
            cls.application.processEvents()

    def setUp(self):
        self.temp_parent = (APP / 'work/temp').resolve()
        self.temp_parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='vwa-tuning-preset-page-', dir=self.temp_parent)).resolve()
        self.assertNotEqual(self.root.parent, self.root)
        self.page = self.window.tuning
        self.dialogs = []
        self._stop_worker()
        self.page._flush_style_autosave()
        self.page._preset_controller.clear()
        self.page._preset_execution_binding = None
        self.page.worker = None
        self.page.result = None
        self.page.last_session = None
        self.page.session = None
        self.page.job = None
        self.page.destination.setText(str(self.root / 'sessions-out'))
        self.marker = self._testMethodName + '-' + uuid.uuid4().hex
        self.project_data = project_fixture(self.marker)
        self.source = write_svp(self.root / 'source.svp', self.project_data)
        self.source_raw = self.source.read_bytes()
        self.source_sha = hashlib.sha256(self.source_raw).hexdigest()
        self.assertTrue(self.page.load_project(self.source), self.page.status.text())
        self.library = PresetLibrary(self.root / 'preset-library')
        self.page._preset_library = self.library
        self._set_form_baseline()
        self.application.processEvents()

    def tearDown(self):
        self._stop_worker()
        for dialog in self.dialogs:
            try:
                dialog.close()
                dialog.deleteLater()
            except RuntimeError:
                pass
        self.application.processEvents()
        self.page._preset_library = None
        self.page._preset_execution_binding = None
        self.page.worker = None
        remove_owned_tree(self.root, self.temp_parent)

    def _stop_worker(self):
        page = getattr(self, 'page', None)
        if page is None:
            return
        worker = page.worker
        if page.busy():
            page.cancel()
            deadline = time.monotonic() + 20
            while page.busy() and time.monotonic() < deadline:
                self.application.processEvents()
                time.sleep(0.01)
        if worker is not None and not worker.wait(5000):
            self.fail('Tuning worker did not stop before fixture cleanup')
        for _ in range(3):
            self.application.processEvents()

    def _set_form_baseline(self):
        page = self.page
        values = {
            'dynamics_strength': 0.6,
            'expression_density': 'original',
            'performance_style': 'none',
            'style_strength': 0.6,
            'style_scope': 'lead',
            'style_technique': 'none',
            'technique_strength': 0.6,
            'technique_pitch': True,
            'style_pitch': False,
            'reference_dynamics': False,
            'reference_tension': False,
            'reference_breathiness': False,
            'reference_voicing': False,
            'style_detail': copy.deepcopy(STYLE_DETAIL),
        }
        previous = page.loading
        page.loading = True
        try:
            page.apply_preferences(values)
            page.mode.setCurrentIndex(page.mode.findData('preserve'))
            page.calibration_file.clear()
            page.retained_project.clear()
            page.confirm_preset_baseline.setChecked(False)
            for ordinal, row in enumerate(page.rows):
                row['check'].setChecked(ordinal == 0)
                row['voice'].setCurrentIndex(row['voice'].findData('lead' if ordinal == 0 else 'backing'))
                row['path'].clear()
                row['source_track'].setValue(0)
        finally:
            page.loading = previous
        page.refresh()
        self.application.processEvents()

    def _page_state(self):
        return {
            'form': TuningFormState(self.page).snapshot(),
            'confirmation': self.page.confirm_preset_baseline.isChecked(),
        }

    def _add_preset(self, mode, name, **updates):
        controls = preset_controls(mode, **updates)
        document = make_document(controls, name)
        revision = self.library.snapshot()['revision']
        self.library.put_document(document, expected_revision=revision)
        snapshot = self.library.get_snapshot(document['id'], document['version'])
        return document, snapshot

    @staticmethod
    def _select_preset(dialog, identifier):
        for index in range(dialog.preset_list.count()):
            item = dialog.preset_list.item(index)
            entry = item.data(Qt.UserRole)
            if isinstance(entry, dict) and entry.get('id') == identifier:
                dialog.preset_list.setCurrentRow(index)
                return item
        raise AssertionError(f'preset not listed: {identifier}')

    @staticmethod
    def _dialog_target(dialog, track_index):
        widget = dialog.binding_table.cellWidget(track_index, 0)
        if widget is None:
            raise AssertionError(f'missing target control for track {track_index}')
        return widget

    @staticmethod
    def _dialog_voice(dialog, track_index):
        widget = dialog.binding_table.cellWidget(track_index, 2)
        if widget is None:
            raise AssertionError(f'missing voice control for track {track_index}')
        return widget

    @staticmethod
    def _dialog_reference_edit(dialog, track_index):
        cell = dialog.binding_table.cellWidget(track_index, 3)
        editor = cell.findChild(QLineEdit) if cell is not None else None
        if editor is None:
            raise AssertionError(f'missing reference editor for track {track_index}')
        return editor

    @staticmethod
    def _dialog_source(dialog, track_index):
        widget = dialog.binding_table.cellWidget(track_index, 4)
        if not isinstance(widget, QSpinBox):
            raise AssertionError(f'missing retained source control for track {track_index}')
        return widget

    def _choose_on_page(self, identifier, *, configure=None, after_preview=None):
        page = self.page
        captured = {}
        failures = []

        def immediate_exec(dialog):
            self.dialogs.append(dialog)
            try:
                self._select_preset(dialog, identifier)
                self.application.processEvents()
                if configure is not None:
                    configure(dialog)
                    self.application.processEvents()
                if not dialog.apply_button.isEnabled():
                    raise AssertionError(
                        'preset apply is disabled before the user can apply: '
                        + dialog.status.toPlainText()
                    )
                dialog.apply_button.click()
                self.application.processEvents()
                if dialog.result() != QDialog.Accepted or dialog.selected_plan is None:
                    raise AssertionError(
                        'preset dialog did not freeze a plan: ' + dialog.status.toPlainText()
                    )
                captured['plan'] = copy.deepcopy(dialog.selected_plan)
                captured['identity'] = copy.deepcopy(dialog.selected_identity)
                captured['pending'] = dialog.selected_pending
                if after_preview is not None:
                    after_preview(dialog)
                return dialog.result()
            except BaseException as error:
                failures.append(error)
                dialog.reject()
                return QDialog.Rejected
            finally:
                dialog.close()
                dialog.deleteLater()

        with patch.object(TuningPresetLibraryDialog, 'exec_', new=immediate_exec):
            page.choose_tuning_preset()
        self.application.processEvents()
        if failures:
            raise failures[0]
        self.assertIn('plan', captured)
        return captured

    def _wait_for_result(self):
        page = self.page
        deadline = time.monotonic() + 30
        while page.busy() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.assertFalse(page.busy(), page.status.text())
        if page.worker is not None:
            self.assertTrue(page.worker.wait(5000), 'Tuning worker did not finish')
        for _ in range(5):
            self.application.processEvents()
        self.assertIsNotNone(page.result, page.status.text())
        self.application.processEvents()
        return page.result

    def _apply_and_run_preserve(self, **updates):
        document, _snapshot = self._add_preset(
            'preserve',
            'Whole-track preserve fixture',
            performance_style='gentle',
            style_strength=0.72,
            style_pitch=True,
            expression_density='dense',
            **updates,
        )
        chosen = self._choose_on_page(
            document['id'],
            configure=lambda dialog: self._dialog_target(dialog, 0).setChecked(True),
        )
        self.assertFalse(chosen['pending'])
        self.assertIn('已应用到表单', self.page.status.text())
        self.page.start_run()
        result = self._wait_for_result()
        return document, chosen, result

    def test_11_complete_source_modes_reach_the_same_worker_and_receipt(self):
        sys.path.insert(0,str(APP/'tests'))
        from test_tuning_preset_workflow import write_profile,write_wav
        reference=write_wav(self.root/'reference.wav')
        profile=write_profile(self.root/'profile',self.project_data)
        retained_data=copy.deepcopy(self.project_data)
        retained_data['tracks'][0]['mainGroup']['parameters']['loudness']={'mode':'linear','points':[0,-2,Q,-1]}
        retained=write_svp(self.root/'retained.svp',retained_data)
        for mode in ('reference','calibrated','retained'):
            with self.subTest(mode=mode):
                self._stop_worker();self.page.session=None;self.page.result=None
                self.page._preset_controller.clear();self._set_form_baseline()
                document,_=self._add_preset(mode,'Complete '+mode,dynamics_strength=0.,style_strength=0.,technique_strength=0.)
                def configure(dialog):
                    self._dialog_target(dialog,0).setChecked(True)
                    if mode in ('reference','calibrated'):
                        dialog._rows[0]['voice'].setCurrentIndex(dialog._rows[0]['voice'].findData('lead'))
                        dialog._rows[0]['path'].setText(str(reference))
                    if mode=='calibrated':dialog.profile_edit.setText(str(profile))
                    if mode=='retained':
                        dialog.retained_edit.setText(str(retained));dialog._rows[0]['source'].setValue(1)
                self._choose_on_page(document['id'],configure=configure)
                self.assertTrue(self.page._preset_controller.active,self.page.status.text())
                self.assertTrue(self.page.apply_button.isEnabled(),self.page.tuning_preset_status.text())
                self.page.start_run();result=self._wait_for_result()
                self.assertTrue(result['receipt_verified'])
                self.assertEqual(result['preset_binding']['preset']['id'],document['id'])
                self.assertEqual(result['options']['expression_mode'],mode)
                self.assertEqual(self.source.read_bytes(),self.source_raw)
                actual=json.loads(Path(result['output']).read_text(encoding='utf-8-sig').rstrip('\x00'))
                self.assertEqual(actual['tracks'][1:],self.project_data['tracks'][1:])
                if mode=='retained':
                    self.assertEqual(actual['tracks'][0]['mainGroup']['parameters']['loudness'],retained_data['tracks'][0]['mainGroup']['parameters']['loudness'])
                else:self.assertEqual(result['report']['primary']['reason'],'zero_strength')

    def test_01_preserve_page_preset_generates_verified_candidate(self):
        source_before = self.source.read_bytes()
        document, chosen, result = self._apply_and_run_preserve()

        self.assertEqual(self.page.mode.currentData(), 'preserve')
        self.assertEqual(self.page.style_mode.currentData(), 'whole')
        self.assertEqual(self.page.performance_style.currentData(), 'gentle')
        self.assertEqual(self.page.expression_density.currentData(), 'dense')
        self.assertEqual(self.page._selected_rows()[0]['track_index'], 0)
        self.assertTrue(self.page._preset_controller.active)
        self.assertEqual(self.page._preset_controller.plan['preset']['id'], document['id'])

        self.assertTrue(result['receipt_verified'])
        self.assertEqual(result['project_commit_count'], 1)
        self.assertTrue(Path(result['output']).is_file())
        self.assertNotEqual(Path(result['output']).resolve(), self.source.resolve())
        receipt_path = Path(result['receipt'])
        self.assertTrue(receipt_path.is_file())
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        self.assertTrue(receipt['receipt_verified'])
        self.assertEqual(receipt['preset_binding']['preset']['id'], document['id'])
        self.assertEqual(receipt['preset_binding']['bindings']['vocal_tracks'], [0])
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_sha)

    def test_02_missing_reference_keeps_half_filled_role_and_blocks_run(self):
        reference = write_wav(self.root / 'reference-lead.wav')
        document, _snapshot = self._add_preset(
            'reference',
            'Reference pending fixture',
            reference_dynamics=True,
            dynamics_strength=0.61,
        )

        def configure(dialog):
            self._dialog_target(dialog, 0).setChecked(True)
            self._dialog_voice(dialog, 0).setCurrentIndex(
                self._dialog_voice(dialog, 0).findData('backing')
            )
            self._dialog_reference_edit(dialog, 0).clear()

        chosen = self._choose_on_page(document['id'], configure=configure)
        binding_rows = chosen['plan']['bindings']['reference_audio']
        self.assertEqual(binding_rows, [{'track_index': 0, 'voice': 'backing', 'path': ''}])
        self.assertTrue(chosen['pending'])
        self.assertTrue(any('参考音频' in item for item in chosen['plan']['missing']))

        self.assertEqual(self.page.mode.currentData(), 'reference')
        row = self.page._selected_rows()[0]
        self.assertEqual(row['voice'].currentData(), 'backing')
        self.assertEqual(row['path'].text(), '')
        self.assertFalse(self.page.apply_button.isEnabled())
        self.assertIn('待补输入', self.page.tuning_preset_status.text())

        outputs_before = sorted((self.root / 'sessions-out').rglob('tuned-*.svp')) if (self.root / 'sessions-out').exists() else []
        self.page.start_run()
        self.application.processEvents()
        self.assertFalse(self.page.busy())
        self.assertIsNone(self.page.result)
        outputs_after = sorted((self.root / 'sessions-out').rglob('tuned-*.svp')) if (self.root / 'sessions-out').exists() else []
        self.assertEqual(outputs_after, outputs_before)
        self.assertIn('参考音频', self.page.status.text() + self.page.tuning_preset_status.text())
        self.assertTrue(reference.is_file())

    def test_03_missing_retained_keeps_source_minus_one_pending_and_blocks_run(self):
        document, _snapshot = self._add_preset(
            'retained',
            'Retained pending fixture',
            expression_density='dense',
        )

        def configure(dialog):
            self._dialog_target(dialog, 0).setChecked(True)
            dialog.retained_edit.clear()
            self._dialog_source(dialog, 0).setValue(0)

        chosen = self._choose_on_page(document['id'], configure=configure)
        retained_rows = chosen['plan']['bindings']['retained_project']
        self.assertEqual(
            retained_rows,
            [{'track_index': 0, 'project': '', 'source_track_index': -1}],
        )
        self.assertTrue(chosen['pending'])
        self.assertTrue(any('沿用工程' in item for item in chosen['plan']['missing']))

        self.assertEqual(self.page.mode.currentData(), 'retained')
        row = self.page._selected_rows()[0]
        self.assertEqual(row['source_track'].value(), 0)
        self.assertTrue(row['source_track'].specialValueText())
        self.assertEqual(self.page.retained_project.text(), '')
        self.assertFalse(self.page.apply_button.isEnabled())
        self.assertIn('待补输入', self.page.tuning_preset_status.text())

        self.page.start_run()
        self.application.processEvents()
        self.assertFalse(self.page.busy())
        self.assertIsNone(self.page.result)
        self.assertIn('沿用', self.page.status.text() + self.page.tuning_preset_status.text())

    def test_04_manual_parameter_edit_detaches_preset(self):
        document, _snapshot = self._add_preset(
            'preserve',
            'Detach fixture',
            performance_style='gentle',
            style_strength=0.72,
            style_pitch=True,
        )
        chosen = self._choose_on_page(
            document['id'],
            configure=lambda dialog: self._dialog_target(dialog, 0).setChecked(True),
        )
        self.assertFalse(chosen['pending'])
        self.assertTrue(self.page._preset_controller.active)

        self.page.performance_style.setCurrentIndex(
            self.page.performance_style.findData('sad')
        )
        set_combo_strength(self.page.style_strength, 0.34)
        self.application.processEvents()

        self.assertFalse(self.page._preset_controller.active)
        self.assertTrue(self.page._preset_controller.detached)
        self.assertIn('自定义', self.page.tuning_preset_status.text())
        self.assertTrue(self.page.apply_button.isEnabled())
        options = self.page.options()
        self.assertIsNone(self.page._preset_execution_binding)
        self.assertEqual(options['performance_style'], 'sad')
        self.assertEqual(options['track_indices'], [0])

    def test_05_form_change_after_preview_is_rejected_without_applying_plan(self):
        document, _snapshot = self._add_preset(
            'preserve',
            'Stale form fixture',
            performance_style='gentle',
            style_strength=0.72,
            style_pitch=True,
            expression_density='dense',
        )

        changed_state = {}

        def change_form(_dialog):
            self.page.performance_style.setCurrentIndex(
                self.page.performance_style.findData('sad')
            )
            set_combo_strength(self.page.style_strength, 0.27)
            self.application.processEvents()
            changed_state.update(self._page_state())

        baseline = self._page_state()
        chosen = self._choose_on_page(
            document['id'],
            configure=lambda dialog: self._dialog_target(dialog, 0).setChecked(True),
            after_preview=change_form,
        )
        self.assertFalse(chosen['pending'])
        self.assertTrue(changed_state)
        self.assertEqual(self._page_state(), changed_state)
        self.assertFalse(self.page._preset_controller.active)
        self.assertIn('当前工程或参数已变化', self.page.status.text())
        self.assertNotEqual(self.page.performance_style.currentData(), 'gentle')

    def test_06_project_change_after_preview_is_rejected_without_applying_plan(self):
        document, _snapshot = self._add_preset(
            'preserve',
            'Stale project fixture',
            performance_style='gentle',
            style_strength=0.72,
            style_pitch=True,
        )
        baseline = self._page_state()

        def change_project(_dialog):
            changed = copy.deepcopy(self.project_data)
            changed['tracks'][0]['mainGroup']['notes'][0]['pitch'] = 67
            write_svp(self.source, changed)
            self.application.processEvents()

        chosen = self._choose_on_page(
            document['id'],
            configure=lambda dialog: self._dialog_target(dialog, 0).setChecked(True),
            after_preview=change_project,
        )
        self.assertFalse(chosen['pending'])
        self.assertNotEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_sha)
        self.assertEqual(self._page_state(), baseline)
        self.assertFalse(self.page._preset_controller.active)
        self.assertIn('工程在载入后已改变', self.page.status.text())
        self.assertNotEqual(self.page.performance_style.currentData(), 'gentle')

    def test_07_library_change_after_preview_is_rejected_without_applying_plan(self):
        document, snapshot = self._add_preset(
            'preserve',
            'Stale library fixture',
            performance_style='gentle',
            style_strength=0.72,
            style_pitch=True,
        )
        baseline = self._page_state()

        def change_library(_dialog):
            document_root = windows_native_path(self.library.root / 'documents')
            matches = [
                path for path in document_root.rglob('*.json')
                if path.is_file()
            ]
            if len(matches) != 1:
                raise AssertionError(f'expected one immutable library record, found {len(matches)}')
            changed = copy.deepcopy(snapshot['document'])
            changed['metadata']['name'] = 'changed after preview'
            matches[0].write_bytes(encode_document(changed))
            self.application.processEvents()

        chosen = self._choose_on_page(
            document['id'],
            configure=lambda dialog: self._dialog_target(dialog, 0).setChecked(True),
            after_preview=change_library,
        )
        self.assertFalse(chosen['pending'])
        self.assertEqual(self._page_state(), baseline)
        self.assertFalse(self.page._preset_controller.active)
        self.assertNotIn('已应用到表单', self.page.status.text())
        self.assertNotEqual(self.page.performance_style.currentData(), 'gentle')

    def test_08_control_application_failure_restores_hidden_values_selection_and_confirmation(self):
        reference = write_wav(self.root / 'restore-reference.wav')
        retained = write_svp(self.root / 'retained-project.svp', project_fixture(self.marker + '-retained'))
        document, _snapshot = self._add_preset(
            'reference',
            'Rollback fixture',
            performance_style='sad',
            style_strength=0.66,
            style_pitch=True,
            reference_dynamics=True,
            dynamics_strength=0.58,
        )

        page = self.page
        page.retained_project.setText(str(retained))
        page.calibration_file.setText('old-profile.json')
        page.rows[0]['voice'].setCurrentIndex(page.rows[0]['voice'].findData('lead'))
        page.rows[0]['path'].setText('old-reference.wav')
        page.rows[0]['source_track'].setValue(2)
        page.rows[1]['voice'].setCurrentIndex(page.rows[1]['voice'].findData('backing'))
        page.rows[1]['path'].setText('unselected-hidden.wav')
        page.rows[1]['source_track'].setValue(3)
        page.confirm_preset_baseline.setChecked(True)
        baseline = self._page_state()

        def configure(dialog):
            self._dialog_target(dialog, 0).setChecked(True)
            self._dialog_target(dialog, 1).setChecked(True)
            self._dialog_voice(dialog, 0).setCurrentIndex(
                self._dialog_voice(dialog, 0).findData('backing')
            )
            self._dialog_reference_edit(dialog, 0).setText(str(reference))
            self._dialog_voice(dialog, 1).setCurrentIndex(
                self._dialog_voice(dialog, 1).findData('lead')
            )
            self._dialog_reference_edit(dialog, 1).setText(str(reference))

        def make_plan_unrepresentable(_dialog):
            backing = page.rows[0]['voice'].findData('backing')
            if backing < 0:
                raise AssertionError('fixture requires a backing role')
            page.rows[0]['voice'].removeItem(backing)

        chosen = self._choose_on_page(
            document['id'],
            configure=configure,
            after_preview=make_plan_unrepresentable,
        )
        self.assertFalse(chosen['pending'])
        self.assertEqual(self._page_state(), baseline)
        self.assertEqual(page.mode.currentData(), 'preserve')
        self.assertEqual(page.retained_project.text(), str(retained))
        self.assertEqual(page.calibration_file.text(), 'old-profile.json')
        self.assertEqual([row['track_index'] for row in page._selected_rows()], [0])
        self.assertEqual(page.rows[1]['voice'].currentData(), 'backing')
        self.assertEqual(page.rows[1]['path'].text(), 'unselected-hidden.wav')
        self.assertEqual(page.rows[1]['source_track'].value(), 3)
        self.assertTrue(page.confirm_preset_baseline.isChecked())
        self.assertFalse(page._preset_controller.active)
        self.assertTrue(page.status.text())

    def test_09_session_restore_uses_frozen_binding_after_library_removal(self):
        source_before = self.source.read_bytes()
        document, _chosen, first = self._apply_and_run_preserve()
        session = Path(self.page.session)
        self.assertTrue((session.parent / 'last-preset-binding.json').is_file())
        revision = self.library.snapshot()['revision']
        self.library.delete(document['id'], document['version'], expected_revision=revision)
        with self.assertRaises(PresetError):
            self.library.get_snapshot(document['id'], document['version'])

        self.page._preset_controller.clear()
        self.page._preset_execution_binding = None
        self.page.result = None
        self.assertTrue(self.page.restore_session(session), self.page.status.text())
        self.assertTrue(self.page._preset_controller.active)
        restored = self.page._preset_controller.plan
        self.assertEqual(restored['preset']['id'], document['id'])
        self.assertEqual(restored['bindings']['vocal_tracks'], [0])

        self.page.start_run()
        second = self._wait_for_result()
        self.assertTrue(second['cached'])
        self.assertTrue(second['receipt_verified'])
        self.assertEqual(second['preset_binding']['preset']['id'], document['id'])
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_10_corrupt_frozen_binding_blocks_generation(self):
        _document, _chosen, _first = self._apply_and_run_preserve()
        session = Path(self.page.session)
        binding_path = session.parent / 'last-preset-binding.json'
        binding = json.loads(binding_path.read_text(encoding='utf-8'))
        binding['bindings']['vocal_tracks'] = [1]
        binding_path.write_text(
            json.dumps(binding, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
            encoding='utf-8',
        )
        status_path = session.parent / 'status.json'
        status_before = status_path.read_bytes()
        outputs_before = sorted((session.parent / 'outputs').glob('tuned-*.svp'))
        old_worker = self.page.worker
        self.page._preset_controller.clear()
        self.page._preset_execution_binding = None
        self.page.result = None

        self.assertTrue(self.page.restore_session(session), self.page.status.text())
        self.assertFalse(self.page._preset_controller.active)
        self.assertTrue(self.page._preset_controller.error)
        self.assertFalse(self.page.apply_button.isEnabled())
        self.assertIn('预设记录需要重新确认', self.page.tuning_preset_status.text())

        self.page.start_run()
        self.application.processEvents()
        self.assertFalse(self.page.busy())
        self.assertIs(self.page.worker, old_worker)
        self.assertIsNone(self.page.result)
        self.assertEqual(status_path.read_bytes(), status_before)
        self.assertEqual(sorted((session.parent / 'outputs').glob('tuned-*.svp')), outputs_before)
        self.assertIn('调音计划', self.page.status.text())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_sha)


if __name__ == '__main__':
    unittest.main(verbosity=2)
