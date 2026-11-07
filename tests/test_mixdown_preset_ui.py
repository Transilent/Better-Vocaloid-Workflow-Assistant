"""Offscreen contracts for accessible mixdown preset save and apply dialogs."""

from __future__ import annotations

import copy
import gc
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPlainTextEdit, QPushButton

from common import native_path
from desktop_theme import apply_theme
import i18n
from mixdown_preset_documents import make_document
from mixdown_preset_ui import MixPresetLibraryDialog, MixPresetSaveDialog
from preset_builtin_documents import example_documents
from preset_documents import document_digest, encode_document
from preset_library import PresetLibrary


application = QApplication.instance() or QApplication([])
WORK_ROOT = APP / 'work' / 'preset-mixing-20261108'
SCREENSHOT_ROOT = APP / 'work/test-results'
FULL_ID = 'user.fixture.full_scope'
BASIC_ID = 'user.fixture.processing_only'
UNSUPPORTED_ID = 'user.fixture.unsupported'


def processing_payload():
    return {
        'eq_enabled': True,
        'eq_bands': [
            {'type': 'highpass', 'freq_hz': 90.0, 'gain_db': 0.0, 'q': 0.707, 'enabled': True},
            {'type': 'lowshelf', 'freq_hz': 220.0, 'gain_db': 1.2, 'q': 0.8, 'enabled': True},
            {'type': 'bell', 'freq_hz': 3200.0, 'gain_db': 1.8, 'q': 0.9, 'enabled': True},
            {'type': 'highshelf', 'freq_hz': 11000.0, 'gain_db': 0.7, 'q': 0.8, 'enabled': True},
        ],
        'compression_enabled': True,
        'compression_strength': 0.34,
        'reverb_enabled': True,
        'reverb_profile': 'natural',
        'reverb_mix': 0.19,
        'reverb_predelay_ms': 26.0,
        'width': 1.24,
        'track_template': {
            'pan_mode': 'lead_harmony',
            'pan': 0.0,
            'harmony_pan': 0.24,
            'reverb_send': 0.62,
        },
    }


def before_snapshot():
    processing = processing_payload()
    return {
        'processing': processing,
        'tracks': [
            {'pan': 0.0, 'reverb_send': 0.25},
            {'pan': 0.0, 'reverb_send': 0.25},
            {'pan': 0.0, 'reverb_send': 0.25},
        ],
    }


def fixture_documents():
    full = make_document(processing_payload(), '完整混音 <Fixture>', include_width=True,
                         identifier=FULL_ID)
    basic_payload = processing_payload()
    basic_payload.pop('width')
    basic_payload.pop('track_template')
    basic = make_document(basic_payload, '仅人声处理', include_width=False,
                          identifier=BASIC_ID)
    unsupported = make_document(processing_payload(), '未知能力', include_width=True,
                                identifier=UNSUPPORTED_ID)
    unsupported['requires']['capabilities'].append('mix.unknown')
    return full, basic, unsupported


def remove_tree_with_retry(path):
    path = Path(path)
    for attempt in range(6):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            gc.collect()
            time.sleep(0.03 * (attempt + 1))
    # Test evidence remains inspectable if the host holds a transient handle.
    if path.exists():
        raise AssertionError('could not clean test directory: ' + str(path))


def widget_rect(widget, ancestor):
    point = widget.mapTo(ancestor, QPoint(0, 0))
    return QRect(point, widget.size())


def assert_no_control_overlap(test, dialog):
    controls = [
        dialog.preset_list,
        dialog.include_width,
        dialog.include_tracks,
        dialog.status,
        dialog.difference_table,
        dialog.refresh_button,
        dialog.manage_button,
        dialog.cancel_button,
        dialog.apply_button,
    ]
    bounds = dialog.rect()
    rects = []
    for control in controls:
        test.assertTrue(control.isVisible(), control.objectName())
        rect = widget_rect(control, dialog)
        test.assertGreater(rect.width(), 0, control.objectName())
        test.assertGreater(rect.height(), 0, control.objectName())
        test.assertTrue(bounds.contains(rect), (control.objectName(), rect, bounds))
        rects.append((control.objectName(), rect))
    for index, (name_a, rect_a) in enumerate(rects):
        for name_b, rect_b in rects[index + 1:]:
            test.assertFalse(rect_a.intersects(rect_b), (name_a, name_b, rect_a, rect_b))


class LibraryProxy:
    """Read-only proxy that can expose a deterministic stale backend state."""

    def __init__(self, library):
        self.library = library
        self.fail_snapshot = None
        self.fail_document = None
        self.shift_sha_for = None
        self.drop_id = None
        self.calls = []

    def snapshot(self):
        self.calls.append('snapshot')
        if self.fail_snapshot:
            raise RuntimeError(self.fail_snapshot)
        value = self.library.snapshot()
        if not self.shift_sha_for and not self.drop_id:
            return value
        result = copy.deepcopy(value)
        entries = []
        for entry in result['entries']:
            if entry.get('id') == self.drop_id:
                continue
            if entry.get('id') == self.shift_sha_for:
                entry['sha256'] = '0' * 64
            entries.append(entry)
        result['entries'] = entries
        return result

    def get_document(self, identifier, version):
        self.calls.append(('get_document', identifier, version))
        if self.fail_document:
            raise RuntimeError(self.fail_document)
        return self.library.get_document(identifier, version)


class MixPresetDialogTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        SCREENSHOT_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='preset-ui-test-', dir=native_path(WORK_ROOT)))
        self.library = PresetLibrary(native_path(self.root / 'library'))
        self.full, self.basic, self.unsupported = fixture_documents()
        revision = None
        for document in (self.full, self.basic, self.unsupported):
            revision = self.library.put_document(
                document, expected_revision=revision
            )['revision']
        preparation = example_documents()[0]
        self.revision = self.library.put_document(
            preparation, expected_revision=revision
        )['revision']
        self.before = before_snapshot()
        self._dialogs = []

    def tearDown(self):
        for dialog in self._dialogs:
            dialog.close()
            dialog.deleteLater()
        application.processEvents()
        self._dialogs.clear()
        remove_tree_with_retry(self.root)

    def dialog(self, library=None, *, manage=None):
        value = MixPresetLibraryDialog(
            library or self.library,
            self.before,
            sample_rate=48000,
            manage=manage,
        )
        self._dialogs.append(value)
        return value

    def select_identity(self, dialog, identifier, version='0.1.0'):
        for row in range(dialog.preset_list.count()):
            item = dialog.preset_list.item(row)
            entry = item.data(Qt.UserRole)
            if entry.get('id') == identifier and entry.get('version') == version:
                dialog.preset_list.setCurrentRow(row)
                application.processEvents()
                return entry
        self.fail('missing preset row: ' + identifier)

    def test_save_dialog_is_stable_validated_and_preview_is_plain_json(self):
        dialog = MixPresetSaveDialog(processing_payload())
        self._dialogs.append(dialog)
        self.assertEqual(dialog.name_edit.maxLength(), 80)
        self.assertFalse(dialog.include_width.isChecked())
        self.assertIsInstance(dialog.preview, QPlainTextEdit)
        self.assertTrue(dialog.preview.isReadOnly())
        self.assertIsInstance(dialog.status, QPlainTextEdit)
        self.assertTrue(dialog.status.isReadOnly())

        first = dialog.document
        self.assertIsInstance(first, dict)
        identifier = first['id']
        self.assertTrue(identifier.startswith('user.'))
        self.assertIn(identifier, dialog.identity_note.text())
        dialog.name_edit.setText('<b>Literal title</b>')
        dialog.include_width.setChecked(True)
        application.processEvents()
        self.assertEqual(dialog.document['id'], identifier)
        self.assertEqual(dialog.document['metadata']['name'], '<b>Literal title</b>')
        self.assertEqual(json.loads(dialog.preview.toPlainText()), dialog.document)
        self.assertIn('<b>Literal title</b>', dialog.preview.toPlainText())

        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.document['id'], identifier)
        self.assertEqual(dialog.document['payload']['apply_scope'],
                         'vocal_processing_and_output_width')
        self.assertIn('width', dialog.document['payload'])

        cancelled = MixPresetSaveDialog(processing_payload())
        self._dialogs.append(cancelled)
        second_identifier = cancelled.document['id']
        self.assertNotEqual(identifier, second_identifier)
        cancelled.reject()
        self.assertEqual(cancelled.result(), QDialog.Rejected)

    def test_library_filters_mixdown_entries_and_has_no_import_action(self):
        revision = self.library.snapshot()['revision']
        dialog = self.dialog()
        self.assertIsNone(dialog.plan)
        self.assertIsNone(dialog.selected_entry)
        self.assertIsNone(dialog.selected_document)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.preset_list.count(), 9)  # six built-ins plus three local fixtures

        rows = []
        for row in range(dialog.preset_list.count()):
            entry = dialog.preset_list.item(row).data(Qt.UserRole)
            rows.append(entry)
            self.assertEqual(entry['type'], 'mixdown')
            self.assertIsInstance(entry['builtin'], bool)
            text = dialog.preset_list.item(row).text()
            self.assertIn(entry['name'], text)
            self.assertIn(entry['version'], text)
            self.assertIn(entry['id'], text)
        self.assertFalse(any(entry['type'] != 'mixdown' for entry in rows))
        button_texts = [item.text() for item in dialog.findChildren(QPushButton)]
        self.assertFalse(any('导入' in text for text in button_texts))

        self.select_identity(dialog, FULL_ID)
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIsInstance(dialog.plan, dict)
        self.assertIsInstance(dialog.selected_entry, dict)
        dialog.reject()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertEqual(self.library.snapshot()['revision'], revision)

    def test_noncanonical_imported_raw_sha_previews_and_accepts(self):
        payload = processing_payload()
        payload.pop('width')
        payload.pop('track_template')
        document = make_document(
            payload,
            '非规范 JSON 导入',
            include_width=False,
            identifier='creator.fixture.noncanonical',
        )
        canonical_raw = encode_document(document)
        imported_raw = b'\xef\xbb\xbf\r\n \t' + canonical_raw + b'\r\n  '
        raw_sha = document_digest(imported_raw)
        canonical_sha = document_digest(canonical_raw)
        self.assertNotEqual(raw_sha, canonical_sha)
        source = self.root / 'noncanonical-import.json'
        source.write_bytes(imported_raw)
        result = self.library.import_file(
            native_path(source), expected_revision=self.revision
        )
        self.assertIn('entry', result)
        self.assertEqual(result['entry']['id'], document['id'])
        self.assertEqual(result['entry']['sha256'], raw_sha)

        dialog = self.dialog()
        self.select_identity(dialog, document['id'])
        self.assertIsInstance(dialog.plan, dict)
        self.assertEqual(dialog.plan['document'], document)
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.plan['document']['id'], document['id'])

    def test_scope_is_protected_by_default_and_opt_in_updates_plan(self):
        dialog = self.dialog()
        entry = self.select_identity(dialog, FULL_ID)
        self.assertFalse(dialog.include_width.isChecked())
        self.assertFalse(dialog.include_tracks.isChecked())
        self.assertTrue(dialog.include_width.isEnabled())
        self.assertTrue(dialog.include_tracks.isEnabled())
        self.assertEqual(dialog.selected_entry, entry)
        self.assertEqual(dialog.plan['include_width'], False)
        self.assertEqual(dialog.plan['include_tracks'], False)
        self.assertEqual(dialog.plan['after']['processing']['width'],
                         dialog.plan['before']['processing']['width'])
        self.assertEqual(dialog.plan['after']['tracks'], dialog.plan['before']['tracks'])
        self.assertEqual(dialog.plan['document']['binding_slots'], [])
        self.assertEqual(dialog.plan['document']['payload']['apply_scope'],
                         'vocal_processing_and_output_width')
        self.assertFalse(any(change['field'] in {'processing.width', 'tracks'}
                             for change in dialog.plan['changes']))

        dialog.include_width.setChecked(True)
        application.processEvents()
        self.assertTrue(dialog.plan['include_width'])
        self.assertFalse(dialog.plan['include_tracks'])
        self.assertEqual(dialog.plan['after']['tracks'], dialog.plan['before']['tracks'])

        dialog.include_tracks.setChecked(True)
        application.processEvents()
        self.assertTrue(dialog.plan['include_tracks'])
        self.assertNotEqual(dialog.plan['after']['tracks'], dialog.plan['before']['tracks'])
        self.assertIn('tracks', [change['field'] for change in dialog.plan['changes']])

        self.select_identity(dialog, BASIC_ID)
        self.assertFalse(dialog.include_width.isChecked())
        self.assertFalse(dialog.include_tracks.isChecked())
        self.assertFalse(dialog.include_width.isEnabled())
        self.assertFalse(dialog.include_tracks.isEnabled())
        self.assertEqual(dialog.plan['after']['processing']['width'],
                         dialog.plan['before']['processing']['width'])
        self.assertEqual(dialog.plan['after']['tracks'], dialog.plan['before']['tracks'])

    def test_unavailable_capability_and_stale_identity_never_accept(self):
        dialog = self.dialog()
        self.select_identity(dialog, UNSUPPORTED_ID)
        self.assertIsNone(dialog.plan)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn('不支持', dialog.status.toPlainText())

        proxy = LibraryProxy(self.library)
        stale = self.dialog(proxy)
        self.select_identity(stale, FULL_ID)
        self.assertTrue(stale.apply_button.isEnabled())
        proxy.shift_sha_for = FULL_ID
        stale.accept()
        self.assertEqual(stale.result(), QDialog.Rejected)
        self.assertIsNone(stale.plan)
        self.assertFalse(stale.apply_button.isEnabled())
        self.assertIn('摘要', stale.status.toPlainText())

        proxy.shift_sha_for = None
        stale.refresh()
        self.select_identity(stale, FULL_ID)
        proxy.drop_id = FULL_ID
        stale.accept()
        self.assertEqual(stale.result(), QDialog.Rejected)
        self.assertIsNone(stale.plan)
        self.assertIn('删除', stale.status.toPlainText())

    def test_accept_rechecks_real_library_and_returns_explicit_plan_without_write(self):
        revision = self.library.snapshot()['revision']
        dialog = self.dialog()
        self.select_identity(dialog, FULL_ID)
        dialog.include_width.setChecked(True)
        dialog.include_tracks.setChecked(True)
        application.processEvents()
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertTrue(dialog.plan['include_width'])
        self.assertTrue(dialog.plan['include_tracks'])
        self.assertEqual(dialog.selected_document['id'], FULL_ID)
        self.assertEqual(dialog.plan['document']['id'], FULL_ID)
        self.assertEqual(self.library.snapshot()['revision'], revision)

    def test_backend_failures_are_plain_nonmodal_status_and_manage_refreshes(self):
        proxy = LibraryProxy(self.library)
        proxy.fail_snapshot = '<b>backend snapshot failure</b>'
        with patch.object(QMessageBox, 'critical') as critical:
            dialog = self.dialog(proxy)
            critical.assert_not_called()
        self.assertIsInstance(dialog.status, QPlainTextEdit)
        self.assertTrue(dialog.status.isReadOnly())
        self.assertIn('<b>backend snapshot failure</b>', dialog.status.toPlainText())

        proxy.fail_snapshot = None
        proxy.fail_document = '<b>backend document failure</b>'
        dialog.refresh()
        self.select_identity(dialog, FULL_ID)
        self.assertIn('<b>backend document failure</b>', dialog.status.toPlainText())
        self.assertIsNone(dialog.selected_document)
        self.assertIsNone(dialog.plan)
        self.assertFalse(dialog.apply_button.isEnabled())

        proxy.fail_document = None
        managed = []
        new_document = make_document(processing_payload(), '管理器新增', include_width=False,
                                     identifier='user.fixture.from_manager')

        def manage(parent):
            managed.append(parent)
            revision = self.library.snapshot()['revision']
            self.library.put_document(new_document, expected_revision=revision)

        manager_dialog = self.dialog(proxy, manage=manage)
        manager_dialog._open_manager()
        self.assertEqual(managed, [manager_dialog])
        ids = [manager_dialog.preset_list.item(row).data(Qt.UserRole)['id']
               for row in range(manager_dialog.preset_list.count())]
        self.assertIn(new_document['id'], ids)

    def test_save_dialog_fits_minimum_size_and_remains_accessible(self):
        dialog = MixPresetSaveDialog(processing_payload())
        self._dialogs.append(dialog)
        apply_theme(dialog, 'light')
        dialog.resize(720, 520)
        dialog.show()
        application.processEvents()
        self.assertEqual(dialog.styleSheet() == '', False)  # test-owned theme application
        self.assertLessEqual(dialog.minimumSizeHint().height(), 520)
        for control in (
            dialog.name_edit,
            dialog.include_width,
            dialog.preview,
            dialog.identity_note,
            dialog.status,
            dialog.cancel_button,
            dialog.save_button,
        ):
            self.assertTrue(control.accessibleName(), control.objectName())
            self.assertTrue(control.isVisible(), control.objectName())
        controls = [
            dialog.name_edit,
            dialog.include_width,
            dialog.preview,
            dialog.identity_note,
            dialog.status,
            dialog.cancel_button,
            dialog.save_button,
        ]
        rects = []
        for control in controls:
            rect = widget_rect(control, dialog)
            self.assertTrue(dialog.rect().contains(rect), (control.objectName(), rect))
            rects.append((control.objectName(), rect))
        for index, (name_a, rect_a) in enumerate(rects):
            for name_b, rect_b in rects[index + 1:]:
                self.assertFalse(rect_a.intersects(rect_b), (name_a, name_b))

    def test_minimum_layout_nonoverlap_and_three_language_screenshots(self):
        original_language = i18n.current_language()
        produced = []
        try:
            for language in ('zh-CN', 'ja', 'en'):
                i18n.set_language(language)
                dialog = self.dialog()
                self.assertEqual(dialog.styleSheet(), '')
                self.assertGreaterEqual(dialog.minimumWidth(), 720)
                self.assertGreaterEqual(dialog.minimumHeight(), 520)
                apply_theme(dialog, 'light')
                self.select_identity(dialog, FULL_ID)
                dialog.resize(720, 520)
                dialog.show()
                application.processEvents()
                assert_no_control_overlap(self, dialog)
                target = SCREENSHOT_ROOT / ('mixdown-preset-' + language + '.png')
                self.assertTrue(dialog.grab().save(str(target), 'PNG'))
                self.assertTrue(target.is_file() and target.stat().st_size > 1000)
                produced.append(target)
        finally:
            i18n.set_language(original_language)
        self.assertEqual(len(produced), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
