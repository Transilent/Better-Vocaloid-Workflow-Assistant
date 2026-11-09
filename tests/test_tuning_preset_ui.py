"""Offscreen contracts for tuning preset save and binding dialogs."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit

from desktop_theme import apply_theme

import tuning_preset_ui as ui


application = QApplication.instance() or QApplication([])
UNSET = object()



def _raw(document):
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False
    ).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def tuning_document(identifier, mode, name='调音 <b>纯文本</b>', *, version='0.1.0'):
    return {
        'format': 'vwa.preset',
        'format_version': 1,
        'id': identifier,
        'type': 'tuning',
        'version': version,
        'metadata': {'name': name},
        'requires': {
            'preset_api': 1,
            'engine_contract': 'tuning-pipeline/v1',
            'capabilities': ['tuning.svp-main-group'],
        },
        'binding_slots': [{'id': 'target_vocals', 'kind': 'vocal_tracks', 'required': True}],
        'payload': {
            'expression_mode': mode,
            'expression_density': 'dense',
            'style': {
                'ref': 'builtin:gentle', 'strength': 0.5, 'pitch_enabled': False,
                'detail': dict.fromkeys(('onset', 'sustain', 'connection', 'release'), 1.0),
            },
            'technique': {'ref': 'builtin:none', 'strength': 0.6, 'pitch_enabled': True},
        },
    }


def project(candidate_origin=UNSET):
    value = {
        'path': 'C:/projects/current.svp',
        'sha256': 'a' * 64,
        'bytes': 4096,
        'tracks': [
            {'track_index': 0, 'name': 'Lead <b>Actual</b>', 'note_count': 12, 'instrumental': False},
            {'track_index': 1, 'name': 'Harmony', 'note_count': 8, 'instrumental': False},
            {'track_index': 2, 'name': 'Instrumental', 'note_count': 0, 'instrumental': True},
        ],
    }
    if candidate_origin is not UNSET:
        value['candidate_origin'] = candidate_origin
    return value


class FakeAdapter:
    make_calls = []
    plan_calls = []

    @classmethod
    def clear(cls):
        cls.make_calls.clear()
        cls.plan_calls.clear()

    @classmethod
    def make_document(cls, options, name):
        cls.make_calls.append((copy.deepcopy(options), name))
        if not isinstance(options, dict):
            raise ValueError('options must be an object')
        if options.get('style_regions'):
            raise ValueError('非空 style_regions 不能静默投影成整曲预设。')
        mode = options.get('expression_mode', 'preserve')
        if mode not in ('preserve', 'reference', 'calibrated', 'retained'):
            raise ValueError('invalid mode')
        document = tuning_document('user.generated.' + uuid.uuid4().hex, mode, name)
        document['payload']['expression_density'] = options.get('expression_density', 'dense')
        document['payload']['style']['strength'] = options.get('style_strength', 0.5)
        return document

    @classmethod
    def plan_application(cls, document, project_value, bindings, *, raw_document=None, before=None):
        cls.plan_calls.append({
            'document': copy.deepcopy(document),
            'project': copy.deepcopy(project_value),
            'bindings': copy.deepcopy(bindings),
            'raw_document': bytes(raw_document) if raw_document is not None else None,
            'before': copy.deepcopy(before),
        })
        mode = document['payload']['expression_mode']
        selected = list(bindings.get('vocal_tracks', []))
        tracks = {
            index: row for index, row in enumerate(project_value.get('tracks', []))
            if isinstance(row, dict)
        }
        missing = []
        if not selected:
            missing.append('请选择目标演唱轨。')
        for index in selected:
            row = tracks.get(index)
            if row is None or row.get('instrumental'):
                raise ValueError('目标轨不是真实演唱轨。')
        if project_value.get('candidate_origin') is not None:
            if bindings.get('confirm_candidate_baseline') is not True:
                missing.append('请确认将此候选作为新基线。')
        if mode in ('reference', 'calibrated'):
            references = {
                row.get('track_index'): row for row in bindings.get('reference_audio', [])
                if isinstance(row, dict)
            }
            for index in selected:
                row = references.get(index, {})
                if row.get('voice') not in ('lead', 'backing', 'vocals'):
                    missing.append('轨 {0} 的声部请明确选择主唱或和声。'.format(index))
                if not isinstance(row.get('path'), str) or not row['path'].strip():
                    missing.append('轨 {0} 缺少本地参考音频。'.format(index))
        if mode == 'calibrated' and not str(bindings.get('calibration_profile', '')).strip():
            missing.append('缺少本地声部校准 profile。')
        if mode == 'retained':
            retained = {
                row.get('track_index'): row for row in bindings.get('retained_project', [])
                if isinstance(row, dict)
            }
            for index in selected:
                row = retained.get(index, {})
                if not str(row.get('project', '')).strip():
                    missing.append('轨 {0} 缺少共同原参数工程。'.format(index))
                if type(row.get('source_track_index')) is not int or row['source_track_index'] < 0:
                    missing.append('轨 {0} 缺少原工程源轨号。'.format(index))
        raw = bytes(raw_document) if raw_document is not None else _raw(document)
        return {
            'schema': 'vwa.tuning-preset-plan/v1',
            'document': copy.deepcopy(document),
            'document_text': raw.decode('utf-8'),
            'preset': {
                'id': document['id'], 'version': document['version'],
                'sha256': _sha(raw), 'canonical_sha256': _sha(_raw(document)),
                'name': document['metadata']['name'],
            },
            'input_sha256': _sha((project_value['sha256'] + json.dumps(bindings, sort_keys=True)).encode()),
            'bindings': copy.deepcopy(bindings),
            'controls': {'expression_mode': mode, 'track_indices': selected},
            'options': None if missing else {'expression_mode': mode, 'track_indices': selected},
            'missing': missing,
            'changes': [{
                'field': 'style.strength',
                'before': (before or {}).get('style_strength', 0.6),
                'after': document['payload']['style']['strength'],
            }],
        }


class FakeLibrary:
    def __init__(self, documents):
        self.documents = {document['id']: copy.deepcopy(document) for document in documents}
        self.revision = '1' * 64
        self.snapshot_calls = 0
        self.raw_calls = []
        self.writes = 0

    def entry(self, identifier):
        document = self.documents[identifier]
        raw = _raw(document)
        return {
            'id': identifier, 'version': document['version'], 'sha256': _sha(raw),
            'type': document['type'], 'bytes': len(raw), 'builtin': identifier.startswith('builtin.'),
            'name': document['metadata']['name'], 'alias': '', 'favorite': False,
        }

    def snapshot(self):
        self.snapshot_calls += 1
        entries = [self.entry(identifier) for identifier in self.documents]
        entries.append({
            'id': 'user.mix.only', 'version': '0.1.0', 'sha256': 'f' * 64,
            'type': 'mixdown', 'bytes': 20, 'builtin': False,
            'name': '不应显示', 'alias': '', 'favorite': False,
        })
        return {'revision': self.revision, 'entries': entries}

    def get_snapshot(self, identifier, version):
        document = self.documents[identifier]
        if document['version'] != version:
            raise KeyError(identifier)
        raw = _raw(document)
        self.raw_calls.append((identifier, version, raw))
        return {'entry': self.entry(identifier), 'document': copy.deepcopy(document), 'raw': raw}

    def replace(self, document):
        self.documents[document['id']] = copy.deepcopy(document)
        self.revision = hashlib.sha256((self.revision + document['id']).encode()).hexdigest()
        self.writes += 1

    def delete(self, identifier):
        self.documents.pop(identifier, None)
        self.revision = hashlib.sha256((self.revision + identifier).encode()).hexdigest()
        self.writes += 1


def widget_rect(widget, ancestor):
    point = widget.mapTo(ancestor, QPoint(0, 0))
    return QRect(point, widget.size())


class TuningPresetDialogTests(unittest.TestCase):
    def setUp(self):
        FakeAdapter.clear()
        self.adapter_patch = patch.object(
            ui,
            '_adapter_api',
            return_value=(FakeAdapter.make_document, FakeAdapter.plan_application),
        )
        self.adapter_patch.start()
        self.dialogs = []
        names = (
            'user.preserve', 'user.reference', 'user.calibrated', 'user.retained',
        )
        modes = ('preserve', 'reference', 'calibrated', 'retained')
        self.library = FakeLibrary([
            tuning_document(identifier, mode, '模式 ' + mode + ' <i>纯文本</i>')
            for identifier, mode in zip(names, modes, strict=True)
        ])

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        application.processEvents()
        self.adapter_patch.stop()

    def dialog(self, *, project_value=UNSET, bindings=None, manage=None):
        value = project() if project_value is UNSET else project_value
        dialog = ui.TuningPresetLibraryDialog(
            self.library,
            value,
            {'vocal_tracks': []} if bindings is None else bindings,
            {
                'style_strength': 0.6,
                'expression_density': 'original',
            },
            manage_callback=manage,
        )
        self.dialogs.append(dialog)
        return dialog

    def select(self, dialog, identifier):
        for row in range(dialog.preset_list.count()):
            item = dialog.preset_list.item(row)
            entry = item.data(Qt.UserRole)
            if entry.get('id') == identifier:
                dialog.preset_list.setCurrentRow(row)
                application.processEvents()
                return
        self.fail('missing preset ' + identifier)

    def test_save_dialog_uses_adapter_minimal_fields_and_plain_preview(self):
        options = {
            'expression_mode': 'reference',
            'expression_density': 'dense',
            'style_strength': 0.42,
            'track_indices': [0, 1],
            'reference_sources': [{'track_index': 0, 'path': 'C:/private/ref.wav'}],
            'style_regions': [],
        }
        dialog = ui.TuningPresetSaveDialog(options)
        self.dialogs.append(dialog)
        dialog.name_edit.setText('名称 <b>不是 HTML</b>')
        application.processEvents()

        self.assertTrue(dialog.save_button.isEnabled())
        self.assertIn('<b>不是 HTML</b>', dialog.preview.toPlainText())
        self.assertEqual(FakeAdapter.make_calls[-1][0], options)
        self.assertNotIn('C:/private/ref.wav', dialog.preview.toPlainText())
        self.assertNotIn('confirm_candidate_baseline', dialog.preview.toPlainText())
        first = dialog.document()
        second = dialog.document()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        first['metadata']['name'] = 'mutated'
        self.assertNotEqual(dialog.document()['metadata']['name'], 'mutated')
        self.assertIsInstance(dialog.status, QPlainTextEdit)
        self.assertTrue(dialog.status.isReadOnly())

    def test_save_dialog_refuses_nonempty_style_regions_without_projection(self):
        options = {
            'expression_mode': 'preserve',
            'style_regions': [{'start': 0.1, 'end': 0.3}],
        }
        dialog = ui.TuningPresetSaveDialog(options)
        self.dialogs.append(dialog)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn('style_regions', dialog.status.toPlainText())
        with self.assertRaises(ValueError):
            dialog.document()

    def test_library_filters_tuning_entries_and_returns_deep_frozen_identity(self):
        dialog = self.dialog(bindings={'vocal_tracks': [0]})
        identifiers = [
            dialog.preset_list.item(row).data(Qt.UserRole)['id']
            for row in range(dialog.preset_list.count())
        ]
        self.assertEqual(len(identifiers), 4)
        self.assertNotIn('user.mix.only', identifiers)
        self.select(dialog, 'user.preserve')

        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIn('确认应用', dialog.apply_button.text())
        self.assertEqual(dialog.selected_plan, None)
        before_calls = list(FakeAdapter.plan_calls)
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertIsNotNone(dialog.selected_plan)
        self.assertEqual(
            dialog.selected_identity,
            {
                'id': 'user.preserve',
                'version': '0.1.0',
                'sha256': self.library.entry('user.preserve')['sha256'],
            },
        )
        self.assertEqual(len(FakeAdapter.plan_calls), len(before_calls) + 1)
        self.assertEqual(self.library.writes, 0)
        selected_copy = copy.deepcopy(dialog.selected_plan)
        selected_copy['bindings']['vocal_tracks'].append(1)
        self.assertEqual(dialog.selected_plan['bindings']['vocal_tracks'], [0])

    def test_preserve_reference_calibrated_and_retained_bindings(self):
        dialog = self.dialog(bindings={'vocal_tracks': [0]})
        expected = {
            'user.preserve': set(),
            'user.reference': {'参考音频', '声部'},
            'user.calibrated': {'参考音频', '声部', 'profile'},
            'user.retained': {'原参数工程', '源轨号'},
        }
        for identifier, required in expected.items():
            self.select(dialog, identifier)
            if identifier != 'user.preserve':
                self.assertTrue(dialog.selected_pending is False)
                self.assertTrue(dialog.apply_button.isEnabled(), identifier)
                self.assertIn('待补输入方案', dialog.apply_button.text())
                missing = dialog.missing_text.toPlainText()
                for token in required:
                    self.assertIn(token, missing, (identifier, token, missing))

        self.select(dialog, 'user.reference')
        dialog._rows[0]['voice'].setCurrentIndex(dialog._rows[0]['voice'].findData('lead'))
        dialog._rows[0]['path'].setText('C:/refs/lead.wav')
        application.processEvents()
        self.assertEqual(dialog.selected_pending, False)
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIn('确认应用', dialog.apply_button.text())
        self.assertEqual(dialog.bindings(), {
            'vocal_tracks': [0],
            'reference_audio': [{
                'track_index': 0,
                'voice': 'lead',
                'path': 'C:/refs/lead.wav',
            }],
        })
        self.assertNotIn('calibration_profile', dialog.bindings())
        self.assertNotIn('retained_project', dialog.bindings())

        self.select(dialog, 'user.calibrated')
        self.assertIn('profile', dialog.missing_text.toPlainText())
        self.assertTrue(dialog.profile_edit.isEnabled())
        self.assertFalse(dialog.retained_edit.isEnabled())
        dialog._rows[0]['voice'].setCurrentIndex(dialog._rows[0]['voice'].findData('lead'))
        dialog._rows[0]['path'].setText('C:/refs/lead.wav')
        dialog.profile_edit.setText('C:/profiles/voice.json')
        application.processEvents()
        self.assertEqual(dialog.selected_pending, False)
        self.assertEqual(dialog.bindings()['calibration_profile'], 'C:/profiles/voice.json')

        self.select(dialog, 'user.retained')
        self.assertTrue(dialog.retained_edit.isEnabled())
        self.assertFalse(dialog.profile_edit.isEnabled())
        self.assertGreaterEqual(dialog._rows[0]['source'].maximum(), 7)
        dialog.retained_edit.setText('C:/projects/prior.svp')
        dialog._rows[0]['source'].setValue(8)
        application.processEvents()
        self.assertEqual(dialog.selected_pending, False)
        self.assertEqual(dialog.bindings(), {
            'vocal_tracks': [0],
            'retained_project': [{
                'track_index': 0,
                'project': 'C:/projects/prior.svp',
                'source_track_index': 7,
            }],
        })

    def test_target_tracks_are_real_and_never_guessed(self):
        dialog = self.dialog(bindings={'vocal_tracks': []})
        self.select(dialog, 'user.preserve')
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn('不会按名称或顺序猜测', dialog.status.toPlainText())
        self.assertFalse(dialog._rows[2]['target'].isEnabled())
        dialog._rows[2]['target'].setChecked(True)
        self.assertEqual(dialog._selected_indices(), [])

        dialog._rows[1]['target'].setChecked(True)
        application.processEvents()
        self.assertEqual(dialog._selected_indices(), [1])
        self.assertIn('轨 1', dialog.project_identity.toPlainText())
        self.assertIn('未选为目标轨', dialog.project_identity.toPlainText())
        self.assertTrue(dialog.apply_button.isEnabled())

    def test_explicit_generic_vocal_role_is_available_without_guessing(self):
        dialog = self.dialog(bindings={'vocal_tracks': [0]})
        self.select(dialog, 'user.reference')
        combo = dialog._rows[0]['voice']
        self.assertGreaterEqual(combo.findData('vocals'), 0)
        combo.setCurrentIndex(combo.findData('vocals'))
        dialog._rows[0]['path'].setText('C:/refs/single-vocal.wav')
        application.processEvents()
        self.assertFalse(dialog.selected_pending)
        self.assertEqual(dialog.bindings()['reference_audio'], [{
            'track_index': 0,
            'voice': 'vocals',
            'path': 'C:/refs/single-vocal.wav',
        }])

    def test_candidate_baseline_is_explicit_default_unchecked_and_keeps_pending(self):
        origin = {
            'status': 'unverified',
            'candidate_sha256': 'b' * 64,
            'receipt': '<b>损坏回执仍按纯文本显示</b>',
        }
        dialog = self.dialog(
            project_value=project(origin),
            bindings={'vocal_tracks': [0], 'confirm_candidate_baseline': True},
        )
        self.assertFalse(dialog.candidate_confirm.isChecked())
        self.assertTrue(dialog.candidate_confirm.isEnabled())
        self.assertIn('<b>损坏回执仍按纯文本显示</b>', dialog.candidate_info.toPlainText())
        self.assertEqual(dialog.candidate_info.toPlainText().count('<b>'), 1)
        self.select(dialog, 'user.preserve')

        self.assertEqual(dialog.selected_pending, False)
        self.assertIn('新基线', dialog.missing_text.toPlainText())
        self.assertIn('待补输入方案', dialog.apply_button.text())
        self.assertNotIn('confirm_candidate_baseline', dialog.bindings())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertFalse(dialog.selected_plan['bindings'].get('confirm_candidate_baseline', False))

        second = self.dialog(
            project_value=project(origin),
            bindings={'vocal_tracks': [0]},
        )
        self.select(second, 'user.preserve')
        second.candidate_confirm.setChecked(True)
        application.processEvents()
        self.assertEqual(second.selected_pending, False)
        self.assertEqual(second.bindings()['confirm_candidate_baseline'], True)
        second.accept()
        self.assertTrue(second.selected_plan['bindings']['confirm_candidate_baseline'])

    def test_no_candidate_has_no_binding_flag(self):
        dialog = self.dialog(project_value=project(None), bindings={'vocal_tracks': [0]})
        self.assertFalse(dialog.candidate_confirm.isEnabled())
        self.select(dialog, 'user.preserve')
        self.assertNotIn('confirm_candidate_baseline', dialog.bindings())
        dialog.accept()
        self.assertNotIn('confirm_candidate_baseline', dialog.selected_plan['bindings'])

    def test_no_project_or_no_target_track_cannot_accept_pending_plan(self):
        no_project = self.dialog(project_value=None, bindings={'vocal_tracks': [0]})
        self.select(no_project, 'user.reference')
        self.assertFalse(no_project.apply_button.isEnabled())
        no_project.accept()
        self.assertNotEqual(no_project.result(), QDialog.Accepted)
        self.assertIsNone(no_project.selected_plan)
        self.assertIn('没有目标演唱轨', no_project.status.toPlainText())

        no_target = self.dialog(bindings={'vocal_tracks': []})
        self.select(no_target, 'user.reference')
        self.assertFalse(no_target.apply_button.isEnabled())
        no_target.accept()
        self.assertNotEqual(no_target.result(), QDialog.Accepted)
        self.assertIsNone(no_target.selected_plan)

    def test_pending_plan_can_update_form_but_keeps_missing_explicit(self):
        dialog = self.dialog(bindings={'vocal_tracks': [0]})
        self.select(dialog, 'user.calibrated')
        self.assertIn('待补输入方案', dialog.apply_button.text())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertTrue(dialog.selected_pending)
        self.assertTrue(dialog.selected_plan['missing'])
        self.assertIn('profile', dialog.missing_text.toPlainText())
        self.assertTrue(dialog.selected_plan['bindings']['vocal_tracks'] == [0])

    def test_stale_snapshot_and_deleted_entry_never_return_a_plan(self):
        dialog = self.dialog(bindings={'vocal_tracks': [0]})
        self.select(dialog, 'user.preserve')
        changed = tuning_document('user.preserve', 'preserve', 'changed', version='0.1.0')
        changed['payload']['style']['strength'] = 0.91
        self.library.replace(changed)
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertIsNone(dialog.selected_plan)
        self.assertIsNone(dialog.selected_identity)
        self.assertIn('应用前复核失败', dialog.status.toPlainText())

        second = self.dialog(bindings={'vocal_tracks': [0]})
        self.select(second, 'user.reference')
        self.library.delete('user.reference')
        second.accept()
        self.assertNotEqual(second.result(), QDialog.Accepted)
        self.assertIsNone(second.selected_plan)
        self.assertIn('应用前复核失败', second.status.toPlainText())

    def test_refresh_and_manager_callback_reload_tuning_entries_without_writes(self):
        manager_added = tuning_document('user.manager.added', 'preserve', '管理器新增')
        managed = []

        def manage(parent):
            managed.append(parent)
            self.library.replace(manager_added)

        dialog = self.dialog(bindings={'vocal_tracks': [0]}, manage=manage)
        original_calls = self.library.snapshot_calls
        dialog.refresh()
        self.assertEqual(self.library.snapshot_calls, original_calls + 1)
        dialog._open_manager()
        self.assertEqual(managed, [dialog])
        ids = [
            dialog.preset_list.item(row).data(Qt.UserRole)['id']
            for row in range(dialog.preset_list.count())
        ]
        self.assertIn('user.manager.added', ids)
        self.assertEqual(dialog.selected_plan, None)
        self.assertEqual(self.library.writes, 1)

    def test_backend_errors_and_imported_text_are_plain_widgets(self):
        class Broken:
            def snapshot(self):
                raise ValueError('<b>snapshot HTML payload</b>')

        dialog = ui.TuningPresetLibraryDialog(
            Broken(), project(), {'vocal_tracks': [0]}, {}, manage_callback=None
        )
        self.dialogs.append(dialog)
        self.assertIsInstance(dialog.status, QPlainTextEdit)
        self.assertTrue(dialog.status.isReadOnly())
        self.assertIn('<b>snapshot HTML payload</b>', dialog.status.toPlainText())
        for label in dialog.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.PlainText, label.objectName())

        self.select(dialog, 'user.preserve') if dialog.preset_list.count() else None
        good = ui.TuningPresetLibraryDialog(
            self.library, project(), {'vocal_tracks': [0]}, {}
        )
        self.dialogs.append(good)
        self.select(good, 'user.reference')
        self.assertIn('<i>纯文本</i>', good.preset_list.currentItem().text())
        self.assertIsInstance(good.status, QPlainTextEdit)

    def test_minimum_window_layout_keeps_actions_visible_and_nonoverlapping(self):
        dialog = self.dialog(bindings={'vocal_tracks': [0]})
        self.select(dialog, 'user.calibrated')
        apply_theme(dialog, 'light')
        dialog.resize(720, 540)
        dialog.show()
        application.processEvents()
        self.assertEqual(dialog.width(), 720)
        self.assertEqual(dialog.height(), 540)
        self.assertTrue(dialog.content_scroll.isVisible())
        actions = (dialog.refresh_button, dialog.manage_button, dialog.cancel_button, dialog.apply_button)
        rects = []
        for control in actions:
            rect = widget_rect(control, dialog)
            self.assertTrue(dialog.rect().contains(rect), (control.objectName(), rect))
            rects.append((control.objectName(), rect))
        for index, (name_a, rect_a) in enumerate(rects):
            for name_b, rect_b in rects[index + 1:]:
                self.assertFalse(rect_a.intersects(rect_b), (name_a, name_b))

        save = ui.TuningPresetSaveDialog({'expression_mode': 'preserve'})
        self.dialogs.append(save)
        apply_theme(save, 'light')
        save.resize(720, 520)
        save.show()
        application.processEvents()
        self.assertEqual(save.width(), 720)
        self.assertEqual(save.height(), 520)
        for control in (save.name_edit, save.preview, save.status, save.cancel_button, save.save_button):
            self.assertTrue(save.rect().contains(widget_rect(control, save)), control.objectName())


try:
    import tuning_preset_documents as real_adapter
except ImportError:
    real_adapter = None


@unittest.skipUnless(real_adapter is not None, 'tuning_preset_documents adapter is not ready')
class RealAdapterIntegrationTests(unittest.TestCase):
    def test_real_adapter_separates_local_candidate_confirmation(self):
        options = {
            'expression_mode': 'preserve',
            'expression_density': 'dense',
            'performance_style': 'gentle',
            'style_strength': 0.5,
            'style_pitch': False,
            'style_scope': 'lead',
            'style_technique': 'none',
            'technique_strength': 0.6,
            'technique_pitch': True,
        }
        document = real_adapter.make_document(options, '真实适配器基线测试')
        raw = real_adapter.encode_document(document) if hasattr(real_adapter, 'encode_document') else _raw(document)
        project_value = project({'kind': 'unverified-candidate', 'receipt': '<b>plain</b>'})
        base = real_adapter.plan_application(
            document,
            project_value,
            {'vocal_tracks': [0]},
            raw_document=raw,
            before={},
        )
        self.assertTrue(base['missing'])
        self.assertNotIn('confirm_candidate_baseline', base['bindings'])
        self.assertNotIn('confirm_candidate_baseline', document)
        self.assertNotIn('C:/', json.dumps(document, ensure_ascii=False))
        invalid_options = dict(options)
        invalid_options['confirm_candidate_baseline'] = True
        with self.assertRaises(ValueError):
            real_adapter.make_document(invalid_options, '不得写入本地确认')
        confirmed = real_adapter.plan_application(
            document,
            project_value,
            {'vocal_tracks': [0], 'confirm_candidate_baseline': True},
            raw_document=raw,
            before={},
        )
        self.assertFalse(confirmed['missing'])
        self.assertTrue(confirmed['bindings']['confirm_candidate_baseline'])


    @staticmethod
    def _real_options(mode):
        options = {
            'expression_mode': mode,
            'expression_density': 'dense',
            'performance_style': 'gentle',
            'style_strength': 0.5,
            'style_pitch': False,
            'style_scope': 'lead',
            'style_technique': 'none',
            'technique_strength': 0.6,
            'technique_pitch': True,
        }
        if mode == 'reference':
            options.update({
                'reference_dynamics': True,
                'reference_sources': [{
                    'track_index': 0, 'voice': 'lead', 'path': 'C:/private/example.wav',
                }],
            })
        return options

    def test_real_adapter_all_modes_keep_targets_but_require_explicit_inputs(self):
        project_value = project(None)
        expected = {
            'preserve': ({'vocal_tracks': [0]}, {'vocal_tracks': [0]}),
            'reference': (
                {'vocal_tracks': [0], 'reference_audio': []},
                {'vocal_tracks': [0], 'reference_audio': [
                    {'track_index': 0, 'voice': 'lead', 'path': 'C:/refs/lead.wav'},
                ]},
            ),
            'calibrated': (
                {'vocal_tracks': [0], 'reference_audio': [], 'calibration_profile': ''},
                {'vocal_tracks': [0], 'reference_audio': [
                    {'track_index': 0, 'voice': 'backing', 'path': 'C:/refs/backing.wav'},
                ], 'calibration_profile': 'C:/profiles/profile.json'},
            ),
            'retained': (
                {'vocal_tracks': [0], 'retained_project': []},
                {'vocal_tracks': [0], 'retained_project': [
                    {'track_index': 0, 'project': 'C:/projects/prior.svp', 'source_track_index': 7},
                ]},
            ),
        }
        for mode, (pending_bindings, ready_bindings) in expected.items():
            document = real_adapter.make_document(self._real_options(mode), '真实 ' + mode)
            pending = real_adapter.plan_application(document, project_value, pending_bindings, before={})
            if mode != 'preserve':
                self.assertTrue(pending['missing'], mode)
                self.assertIsNone(pending['options'])
            ready = real_adapter.plan_application(document, project_value, ready_bindings, before={})
            self.assertFalse(ready['missing'], (mode, ready['missing']))
            self.assertIsInstance(ready['options'], dict)
            self.assertEqual(ready['bindings']['vocal_tracks'], [0])
            self.assertNotIn('C:/private/example.wav', json.dumps(document, ensure_ascii=False))

    def test_real_library_snapshot_feeds_dialog_and_accepts_ready_plan(self):
        from preset_library import PresetLibrary, _Record, _State

        document = real_adapter.make_document(self._real_options('preserve'), '真实库联动')
        raw = _raw(document)
        entry = {
            'id': document['id'], 'version': document['version'], 'sha256': _sha(raw),
            'type': 'tuning', 'bytes': len(raw), 'builtin': False,
            'name': document['metadata']['name'], 'alias': '', 'favorite': False,
        }
        record = _Record(row=entry, raw=raw, document=document, entry=entry)
        state = _State(revision='1' * 64, raw=None, entries=[entry], records=(record,))
        library = PresetLibrary(APP / 'unused-test-library-root')
        with patch.object(library, '_builtin_records', return_value=[]),                 patch.object(library, '_read_current', return_value=state):
            dialog = ui.TuningPresetLibraryDialog(
                library,
                project(None),
                {'vocal_tracks': [0]},
                {},
            )
            try:
                row = next(
                    index for index in range(dialog.preset_list.count())
                    if dialog.preset_list.item(index).data(Qt.UserRole)['id'] == document['id']
                )
                dialog.preset_list.setCurrentRow(row)
                application.processEvents()
                self.assertTrue(dialog.apply_button.isEnabled())
                dialog.accept()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                self.assertEqual(dialog.selected_identity['id'], document['id'])
                self.assertEqual(dialog.selected_identity['version'], document['version'])
                self.assertEqual(dialog.selected_identity['sha256'], entry['sha256'])
                self.assertEqual(library.snapshot()['revision'], state.revision)
            finally:
                dialog.close()
                dialog.deleteLater()
                application.processEvents()


if __name__ == '__main__':
    unittest.main(verbosity=2)
