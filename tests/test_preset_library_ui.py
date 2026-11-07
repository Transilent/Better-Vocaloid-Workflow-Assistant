"""Offscreen contracts for the local preset manager Settings entry and UI."""

import copy
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import native_path
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog, QFrame, QWidget

from desktop_theme import apply_theme
from ui_preferences import load_appearance
import i18n

application = QApplication.instance() or QApplication([])

# Bounded source tests use src modules while the installed-data root stays APP.
import common
common.ROOT = APP

# Importing and constructing Settings must stay free of preset-library work.
import updates_ui

settings_owner = QWidget()
presets_path = APP / 'user-presets'
presets_existed = presets_path.exists()
settings = updates_ui.UpdatesPage(settings_owner)
try:
    assert settings.findChild(QFrame, 'presetLibraryCard') is not None
    assert settings.findChild(QWidget, 'openPresetLibraryButton') is not None
    assert 'preset_library_ui' not in sys.modules, 'Settings construction imported the manager eagerly'
    assert presets_path.exists() == presets_existed, 'Opening Settings created a preset library'

    import preset_library_ui as preset_ui

    with patch.object(preset_ui, 'open_preset_library_manager', return_value='opened') as opened:
        assert settings.open_preset_library() == 'opened'
        opened.assert_called_once_with(settings)
finally:
    settings.stop_watching()
    settings.close()
    settings_owner.close()
    application.processEvents()

from preset_builtin_documents import builtin_documents, example_documents
from preset_documents import document_digest, encode_document


class StubPresetError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


EXAMPLES = example_documents()
BUILTIN_DOCUMENT = builtin_documents()[0]
LOCAL_DOCUMENT = EXAMPLES[0]
IMPORT_DOCUMENT = EXAMPLES[2]
BUILTIN_BYTES = encode_document(BUILTIN_DOCUMENT, allow_builtin=True)
LOCAL_BYTES = encode_document(LOCAL_DOCUMENT)
IMPORT_BYTES = encode_document(IMPORT_DOCUMENT)
BUILTIN_ID = BUILTIN_DOCUMENT['id']
LOCAL_ID = LOCAL_DOCUMENT['id']
BUILTIN_VERSION = BUILTIN_DOCUMENT['version']
LOCAL_VERSION = LOCAL_DOCUMENT['version']
BUILTIN_SHA = document_digest(BUILTIN_BYTES)
LOCAL_SHA = document_digest(LOCAL_BYTES)
IMPORT_SHA = document_digest(IMPORT_BYTES)


def display_name(document):
    return document['metadata']['name']


def entry(document, sha, *, builtin=False, favorite=False, alias=''):
    return {
        'id': document['id'],
        'version': document['version'],
        'sha256': sha,
        'type': document['type'],
        'name': alias or display_name(document),
        'builtin': builtin,
        'favorite': favorite,
        'alias': alias,
    }


class StubLibrary:
    def __init__(self):
        self.revision = 'rev-0001'
        self.entries = [
            entry(BUILTIN_DOCUMENT, BUILTIN_SHA, builtin=True),
            entry(LOCAL_DOCUMENT, LOCAL_SHA),
        ]
        self.documents = {
            (BUILTIN_ID, BUILTIN_VERSION): copy.deepcopy(BUILTIN_DOCUMENT),
            (LOCAL_ID, LOCAL_VERSION): copy.deepcopy(LOCAL_DOCUMENT),
        }
        self.calls = []
        self.conflict_once = False
        self.import_sha = IMPORT_SHA

    def snapshot(self):
        self.calls.append(('snapshot', (), {}))
        return {'revision': self.revision, 'entries': copy.deepcopy(self.entries)}

    def get_document(self, preset_id, version):
        self.calls.append(('get_document', (preset_id, version), {}))
        try:
            return copy.deepcopy(self.documents[(preset_id, version)])
        except KeyError:
            raise StubPresetError('preset_not_found', 'missing preset') from None

    def _next_revision(self):
        number = int(self.revision.rsplit('-', 1)[-1]) + 1
        self.revision = 'rev-{0:04d}'.format(number)

    def _find(self, preset_id, version):
        for row in self.entries:
            if row['id'] == preset_id and row['version'] == version:
                return row
        raise StubPresetError('preset_not_found', 'missing preset')

    def import_file(self, path, *, expected_revision, expected_sha256=None):
        self.calls.append(('import_file', (str(path),), {
            'expected_revision': expected_revision, 'expected_sha256': expected_sha256}))
        if expected_sha256 != self.import_sha:
            raise StubPresetError('file_changed', 'preset file changed after preview')
        self._next_revision()
        new_entry = entry(IMPORT_DOCUMENT, IMPORT_SHA)
        self.entries.append(new_entry)
        self.documents[(IMPORT_DOCUMENT['id'], IMPORT_DOCUMENT['version'])] = copy.deepcopy(IMPORT_DOCUMENT)
        return {'revision': self.revision, 'entry': copy.deepcopy(new_entry), 'already_present': False}

    def copy(self, preset_id, version, *, name, expected_revision):
        self.calls.append(('copy', (preset_id, version), {
            'name': name, 'expected_revision': expected_revision}))
        assert self._find(preset_id, version)['builtin']
        self._next_revision()
        document = copy.deepcopy(BUILTIN_DOCUMENT)
        document['id'] = 'user.copy.{0}'.format(self.revision.rsplit('-', 1)[-1])
        document['version'] = '0.1.0'
        document['metadata']['name'] = name
        new_entry = entry(document, 'd' * 64)
        self.entries.append(new_entry)
        self.documents[(document['id'], document['version'])] = document
        return {'revision': self.revision, 'entry': copy.deepcopy(new_entry), 'already_present': False}

    def rename(self, preset_id, version, *, alias, expected_revision):
        self.calls.append(('rename', (preset_id, version), {
            'alias': alias, 'expected_revision': expected_revision}))
        self._next_revision()
        row = self._find(preset_id, version)
        row['alias'] = alias
        row['name'] = alias or display_name(self.documents[(preset_id, version)])
        return {'revision': self.revision, 'entry': copy.deepcopy(row), 'already_present': False}

    def set_favorite(self, preset_id, version, *, favorite, expected_revision):
        self.calls.append(('set_favorite', (preset_id, version), {
            'favorite': favorite, 'expected_revision': expected_revision}))
        if self.conflict_once:
            self.conflict_once = False
            raise StubPresetError('stale_revision', 'index changed')
        self._next_revision()
        row = self._find(preset_id, version)
        row['favorite'] = favorite
        return {'revision': self.revision, 'entry': copy.deepcopy(row), 'already_present': False}

    def delete(self, preset_id, version, *, expected_revision):
        self.calls.append(('delete', (preset_id, version), {'expected_revision': expected_revision}))
        self._next_revision()
        self.entries = [row for row in self.entries
                        if not (row['id'] == preset_id and row['version'] == version)]
        return {'revision': self.revision}

    def export_file(self, preset_id, version, destination):
        self.calls.append(('export_file', (preset_id, version, str(destination)), {}))
        target = Path(destination)
        target.write_bytes(BUILTIN_BYTES)
        return target

    def recover_previous(self):
        self.calls.append(('recover_previous', (), {}))
        self.revision = 'rev-recovered'
        return {'revision': self.revision, 'entries': copy.deepcopy(self.entries)}


def select_row(dialog, index):
    dialog.entries.setCurrentItem(dialog.entries.topLevelItem(index))
    dialog.selection_changed()
    application.processEvents()


def call_count(library, name):
    return sum(1 for row in library.calls if row[0] == name)


def settle():
    for _ in range(3):
        application.processEvents()


def assert_narrow_layout(dialog):
    dialog.resize(720, 520)
    dialog.show()
    settle()
    assert (dialog.width(), dialog.height()) == (720, 520)
    detail = dialog.findChild(QWidget, 'presetLibraryDetail')
    actions_panel = dialog.findChild(QWidget, 'presetLibraryActions')
    assert detail.width() >= 250, (dialog.windowTitle(), detail.size(), dialog.entries.size())
    assert dialog.entries.viewport().height() >= 80, 'Preset rows need usable space at minimum size'
    splitter = dialog.findChild(QWidget, 'presetLibrarySplitter')
    assert splitter.mapTo(dialog, splitter.rect().bottomRight()).y() < actions_panel.y()
    action_controls = (dialog.copy_button, dialog.export_button, dialog.rename_button,
                       dialog.favorite_button, dialog.delete_button)
    for control in action_controls:
        assert actions_panel.rect().contains(control.mapTo(actions_panel, control.rect().topLeft())), (dialog.windowTitle(), control.text(), 'top-left')
        assert actions_panel.rect().contains(control.mapTo(actions_panel, control.rect().bottomRight())), (dialog.windowTitle(), control.text(), 'bottom-right', actions_panel.size(), control.geometry())
    assert dialog.rect().contains(actions_panel.mapTo(dialog, actions_panel.rect().topLeft()))
    assert dialog.rect().contains(actions_panel.mapTo(dialog, actions_panel.rect().bottomRight()))
    for control in (dialog.import_button, dialog.refresh_button, dialog.recover_button,
                    dialog.close_button):
        assert dialog.rect().contains(control.mapTo(dialog, control.rect().topLeft())), (dialog.windowTitle(), control.text(), 'dialog top-left')
        assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight())), (dialog.windowTitle(), control.text(), 'dialog bottom-right', dialog.size(), control.geometry())
    assert dialog.copy_button.mapTo(actions_panel, dialog.copy_button.rect().topLeft()).y() < \
        dialog.rename_button.mapTo(actions_panel, dialog.rename_button.rect().topLeft()).y()
    return detail


LANGUAGE_EXPECTATIONS = {
    'zh-CN': {
        'title': '本机预设库', 'import': '导入预设…', 'refresh': '刷新',
        'recover': '恢复上一索引…', 'copy': '复制内置预设…', 'export': '导出原始文件…',
        'rename': '修改别名…', 'favorite': '收藏', 'delete': '逻辑删除…', 'close': '关闭',
    },
    'en': {
        'title': 'Local preset library', 'import': 'Import preset…', 'refresh': 'Refresh',
        'recover': 'Recover previous index…', 'copy': 'Copy built-in…', 'export': 'Export original…',
        'rename': 'Edit alias…', 'favorite': 'Favorite', 'delete': 'Remove entry…', 'close': 'Close',
    },
    'ja': {
        'title': 'ローカルプリセットライブラリ', 'import': 'プリセットを読み込む…', 'refresh': '更新',
        'recover': '前のインデックスを復元…', 'copy': '内蔵プリセットをコピー…', 'export': '元ファイルを書き出す…',
        'rename': '別名を編集…', 'favorite': 'お気に入り', 'delete': '登録を解除…', 'close': '閉じる',
    },
}

result_images = APP / 'work/test-results'
result_images.mkdir(parents=True, exist_ok=True)
screenshots = []
for language, expected in LANGUAGE_EXPECTATIONS.items():
    i18n.set_language(language)
    localized_library = StubLibrary()
    dialog = preset_ui.PresetLibraryManagerDialog(library=localized_library)
    try:
        appearance = load_appearance()
        appearance['ui_scale'] = 100
        appearance['material'] = 'solid'
        dialog._vwa_appearance = appearance
        apply_theme(dialog, 'light')
        assert dialog.windowTitle() == expected['title']
        assert dialog.import_button.text() == expected['import']
        assert dialog.refresh_button.text() == expected['refresh']
        assert dialog.recover_button.text() == expected['recover']
        assert dialog.copy_button.text() == expected['copy']
        assert dialog.export_button.text() == expected['export']
        assert dialog.rename_button.text() == expected['rename']
        assert dialog.favorite_button.text() == expected['favorite']
        assert dialog.delete_button.text() == expected['delete']
        assert dialog.close_button.text() == expected['close']
        assert_narrow_layout(dialog)
        screenshot = result_images / ('preset-library-manager-' + language + '.png')
        assert dialog.grab().save(str(screenshot)) and screenshot.stat().st_size > 0
        screenshots.append(screenshot)
    finally:
        dialog.close()
        settle()
i18n.set_language('zh-CN')
assert len(screenshots) == 3


# Opening the manager reads without mutating, and permissions differ by source.
library = StubLibrary()
manager = preset_ui.PresetLibraryManagerDialog(library=library)
try:
    assert call_count(library, 'snapshot') == 1
    for mutation in ('import_file', 'copy', 'rename', 'set_favorite', 'delete', 'recover_previous'):
        assert call_count(library, mutation) == 0, mutation
    assert manager.windowTitle() == '本机预设库'
    assert (manager.minimumWidth(), manager.minimumHeight()) == (720, 520)
    assert manager.entries.topLevelItemCount() == 2
    assert manager.selected_entry()['builtin'] is True
    assert manager.copy_button.isEnabled() and manager.export_button.isEnabled()
    assert not manager.rename_button.isEnabled()
    assert not manager.favorite_button.isEnabled()
    assert not manager.delete_button.isEnabled()
    assert BUILTIN_ID in manager.document.toPlainText()
    scope = manager.findChild(QWidget, 'presetLibraryScopeNotice')
    assert '尚未开放' in scope.text()

    select_row(manager, 1)
    assert manager.selected_entry()['builtin'] is False
    assert not manager.copy_button.isEnabled()
    assert manager.export_button.isEnabled()
    assert manager.rename_button.isEnabled()
    assert manager.favorite_button.isEnabled()
    assert manager.delete_button.isEnabled()

    assert_narrow_layout(manager)
    manager.hide()
finally:
    manager.close()
    application.processEvents()

# Import preview rejection performs no write; acceptance uses the snapshot revision.
library = StubLibrary()
manager = preset_ui.PresetLibraryManagerDialog(library=library)
try:
    manager._ask_import_path = lambda: str(APP / 'work' / 'preview.json')
    manager._raw_reader = lambda path: IMPORT_BYTES
    manager._document_decoder = lambda raw: copy.deepcopy(IMPORT_DOCUMENT)
    manager._document_digest = lambda raw: IMPORT_SHA
    with patch.object(preset_ui.PresetImportPreviewDialog, 'exec_', return_value=QDialog.Rejected):
        manager.import_preset()
    assert call_count(library, 'import_file') == 0
    assert '没有发生变化' in manager.status.text()

    before_entries = copy.deepcopy(library.entries)
    before_revision = library.revision

    def change_file_during_preview(*args, **kwargs):
        library.import_sha = 'e' * 64
        return QDialog.Accepted

    with patch.object(preset_ui.PresetImportPreviewDialog, 'exec_', side_effect=change_file_during_preview), \
         patch.object(preset_ui.QMessageBox, 'warning') as warning:
        manager.import_preset()
    assert warning.called
    assert library.entries == before_entries
    assert library.revision == before_revision
    assert 'file_changed' in manager.status.text()

    library.import_sha = IMPORT_SHA
    with patch.object(preset_ui.PresetImportPreviewDialog, 'exec_', return_value=QDialog.Accepted):
        manager.import_preset()
    imports = [row for row in library.calls if row[0] == 'import_file']
    assert len(imports) == 2
    assert imports[-1][2] == {
        'expected_revision': 'rev-0001', 'expected_sha256': IMPORT_SHA}
    assert manager.selected_entry()['id'] == IMPORT_DOCUMENT['id']
finally:
    manager.close()
    application.processEvents()

# Every local mutation carries the most recently observed revision.
library = StubLibrary()
manager = preset_ui.PresetLibraryManagerDialog(library=library)
try:
    select_row(manager, 0)
    with patch.object(preset_ui.QInputDialog, 'getText', return_value=('舞台明亮', True)):
        manager.copy_selected()
    copy_call = next(row for row in library.calls if row[0] == 'copy')
    assert copy_call[2] == {'name': '舞台明亮', 'expected_revision': 'rev-0001'}
    copied = manager.selected_entry()
    assert copied['builtin'] is False and copied['name'] == '舞台明亮'

    with patch.object(preset_ui.QInputDialog, 'getText', return_value=('准备别名', True)):
        manager.rename_selected()
    rename_call = next(row for row in library.calls if row[0] == 'rename')
    assert rename_call[2] == {'alias': '准备别名', 'expected_revision': 'rev-0002'}
    assert manager.selected_entry()['alias'] == '准备别名'

    manager.toggle_favorite()
    favorite_call = next(row for row in library.calls if row[0] == 'set_favorite')
    assert favorite_call[2] == {'favorite': True, 'expected_revision': 'rev-0003'}
    assert manager.selected_entry()['favorite'] is True

    with patch.object(preset_ui.QMessageBox, 'question', return_value=preset_ui.QMessageBox.Yes):
        manager.delete_selected()
    delete_call = next(row for row in library.calls if row[0] == 'delete')
    assert delete_call[2] == {'expected_revision': 'rev-0004'}
    assert all(row['id'] != copied['id'] for row in library.snapshot()['entries'])
finally:
    manager.close()
    application.processEvents()

# A stale revision refreshes the list but does not retry the failed mutation.
library = StubLibrary()
library.conflict_once = True
manager = preset_ui.PresetLibraryManagerDialog(library=library)
try:
    select_row(manager, 1)
    snapshots_before = call_count(library, 'snapshot')
    with patch.object(preset_ui.QMessageBox, 'warning') as warning:
        manager.toggle_favorite()
    assert call_count(library, 'set_favorite') == 1
    assert call_count(library, 'snapshot') == snapshots_before + 1
    assert '列表已刷新' in manager.status.text()
    warning.assert_called_once()
finally:
    manager.close()
    application.processEvents()

# Export uses the selected identity and exact bytes, and refuses existing targets.
library = StubLibrary()
manager = preset_ui.PresetLibraryManagerDialog(library=library)
work = APP / 'work'
work.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='preset-ui-export-', dir=native_path(work)) as folder:
    destination = Path(folder) / 'exact-preset.json'
    try:
        select_row(manager, 0)
        chosen_defaults = []
        def choose_export(default):
            chosen_defaults.append(default)
            return str(destination)
        manager._ask_export_path = choose_export
        manager.export_selected()
        assert destination.read_bytes() == BUILTIN_BYTES
        assert chosen_defaults == [BUILTIN_ID + '-' + BUILTIN_VERSION + '.vwa-preset.json']
        export_call = next(row for row in library.calls if row[0] == 'export_file')
        assert export_call[1] == (BUILTIN_ID, BUILTIN_VERSION, str(destination))

        destination.write_bytes(b'KEEP')
        exports_before = call_count(library, 'export_file')
        manager._ask_export_path = lambda default: str(destination)
        with patch.object(preset_ui.QMessageBox, 'warning') as warning:
            manager.export_selected()
        assert destination.read_bytes() == b'KEEP'
        assert call_count(library, 'export_file') == exports_before
        warning.assert_called_once()
    finally:
        manager.close()
        application.processEvents()

# Previous-index recovery is explicit and does not apply a preset.
library = StubLibrary()
manager = preset_ui.PresetLibraryManagerDialog(library=library)
try:
    with patch.object(preset_ui.QMessageBox, 'question', return_value=preset_ui.QMessageBox.No):
        manager.recover_previous()
    assert call_count(library, 'recover_previous') == 0
    with patch.object(preset_ui.QMessageBox, 'question', return_value=preset_ui.QMessageBox.Yes):
        manager.recover_previous()
    assert call_count(library, 'recover_previous') == 1
    assert manager._revision == 'rev-recovered'
    assert '预设未自动应用' in manager.status.text()
finally:
    manager.close()
    application.processEvents()

# Exercise the actual persistence backend as well as the deterministic UI stub.
from preset_library import PresetLibrary
from preset_documents import decode_document
i18n.set_language('zh-CN')
with tempfile.TemporaryDirectory(prefix='preset-ui-real-', dir=native_path(work)) as folder:
    root = Path(folder)
    backend = PresetLibrary(root / 'library')
    dialog = preset_ui.PresetLibraryManagerDialog(library=backend)
    try:
        assert dialog.entries.topLevelItemCount() == 10
        assert not backend.root.exists(), 'Read-only manager must not create the real library'
        dialog.show()
        settle()
        assert dialog.grab().save(str(result_images / 'preset-library-real.png'))
        with patch.object(preset_ui.QInputDialog, 'getText', return_value=('我的混音变体', True)):
            dialog.copy_selected()
        selected = dialog.selected_entry()
        assert selected and not selected['builtin'] and selected['id'].startswith('user.')
        assert dialog.entries.topLevelItemCount() == 11
        with patch.object(preset_ui.QInputDialog, 'getText', return_value=('本机别名', True)):
            dialog.rename_selected()
        assert dialog.selected_entry()['name'] == '本机别名'
        dialog.toggle_favorite()
        assert dialog.selected_entry()['favorite']
        destination = root / 'shared.vwa-preset.json'
        dialog._ask_export_path = lambda default: str(destination)
        dialog.export_selected()
        exported = destination.read_bytes()
        assert decode_document(exported)['metadata']['name'] == '我的混音变体'
        dialog._ask_import_path = lambda: str(destination)
        with patch.object(preset_ui.PresetImportPreviewDialog, 'exec_', return_value=QDialog.Accepted):
            dialog.import_preset()
        assert dialog.entries.topLevelItemCount() == 11
        with patch.object(preset_ui.QMessageBox, 'question', return_value=preset_ui.QMessageBox.Yes):
            dialog.delete_selected()
        assert dialog.entries.topLevelItemCount() == 10
        with patch.object(preset_ui.QMessageBox, 'question', return_value=preset_ui.QMessageBox.Yes):
            dialog.recover_previous()
        assert dialog.entries.topLevelItemCount() == 11
        assert destination.read_bytes() == exported
    finally:
        dialog.close()
        settle()

# The manager module has no networking, media playback, or runtime installer dependency.
source = Path(preset_ui.__file__).read_text(encoding='utf-8')
for forbidden in ('import requests', 'import urllib', 'import webbrowser', 'QMediaPlayer',
                  'QSoundEffect', 'subprocess', 'pip install'):
    assert forbidden not in source, forbidden

print('PASS: preset manager settings entry, lazy no-write open, preview-digest rejection, two-row narrow layout, '
      'row permissions, revision-bound mutations, conflict refresh, exact export and explicit recovery; offscreen')
