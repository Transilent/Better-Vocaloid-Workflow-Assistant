"""Local preset library manager for import, inspection, and library-only edits.

The backend library is imported lazily. Opening Settings must not create the
user preset directory, so even constructing this dialog only calls snapshot().
No preset is applied to a mix, tuning session, or audio processor.
"""

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QLayout,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop_theme import button, label
from i18n import tr as _tr


def _new_library():
    from preset_library import PresetLibrary
    return PresetLibrary()


def _read_raw(path):
    from preset_documents import read_regular_bytes
    return read_regular_bytes(path)


def _decode(raw):
    from preset_documents import decode_document
    return decode_document(raw)


def _digest(raw):
    from preset_documents import document_digest
    return document_digest(raw)


def _set_role(widget, role):
    value = role or ''
    if widget.property('role') == value:
        return
    widget.setProperty('role', value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _pretty(document):
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)


def _document_name(document):
    metadata = document.get('metadata') if isinstance(document, dict) else None
    name = metadata.get('name') if isinstance(metadata, dict) else None
    if isinstance(name, str) and name.strip():
        return name
    return str(document.get('id', '')) if isinstance(document, dict) else ''


def _error_code(error):
    return str(getattr(error, 'code', '') or '')


def _conflict_error(error):
    code = _error_code(error).lower()
    return 'stale' in code or 'conflict' in code


def _entry_label(entry):
    return '{0} · {1} {2}'.format(
        entry.get('name', ''), entry.get('type', ''), entry.get('version', ''))


class PresetImportPreviewDialog(QDialog):
    """Show the validated document and ask before the first filesystem write."""

    def __init__(self, document, source, parent=None):
        super().__init__(parent)
        self.document = document
        self.source = str(source)
        self.setWindowTitle(_tr('导入预设预览'))
        self.setObjectName('presetImportPreviewDialog')
        self.setModal(True)
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        layout.addWidget(label(_tr('确认导入预设'), 'card-title'))
        layout.addWidget(label(_tr('这里只验证并显示文件内容。点击“确认导入”后才会写入本机预设库。'), 'muted'))
        layout.addWidget(label(_tr('不会应用调音或混音参数，也不会处理、播放或上传音频。'), 'muted'))

        summary = label('{0}\n{1}\n{2}'.format(
            _document_name(document),
            _tr('类型') + '：' + str(document.get('type', '')) + '  ·  ' + _tr('版本') + '：' + str(document.get('version', '')),
            _tr('来源 ID') + '：' + str(document.get('id', '')),
        ))
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        summary.setAccessibleName(_tr('导入预设摘要'))
        layout.addWidget(summary)

        source_label = label(_tr('文件') + '：' + Path(self.source).name, 'muted')
        source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(source_label)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlainText(_pretty(document))
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.preview.setAccessibleName(_tr('导入预设 JSON 预览'))
        layout.addWidget(self.preview, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.import_button = buttons.addButton(_tr('确认导入'), QDialogButtonBox.AcceptRole)
        self.import_button.setProperty('role', 'primary')
        self.import_button.setObjectName('presetImportConfirmButton')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class PresetLibraryManagerDialog(QDialog):
    """Read and manage the local preset library without applying presets."""

    def __init__(self, parent=None, *, library=None, raw_reader=None,
                 document_decoder=None, document_digest=None):
        super().__init__(parent)
        self.library = library
        self._raw_reader = raw_reader or _read_raw
        self._document_decoder = document_decoder or _decode
        self._document_digest = document_digest or _digest
        self._revision = None
        self._snapshot = {'revision': None, 'entries': []}
        self._backend_error = None
        if self.library is None:
            try:
                self.library = _new_library()
            except Exception as error:
                self._backend_error = error

        self.setWindowTitle(_tr('本机预设库'))
        self.setObjectName('presetLibraryManagerDialog')
        self.setModal(True)
        self.resize(1080, 760)
        self.setMinimumSize(720, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addWidget(label(_tr('本机预设库'), 'heading'))
        notice = label(_tr('混音与调音应用功能尚未开放。'))
        notice.setObjectName('presetLibraryScopeNotice')
        notice.setAccessibleName(_tr('本机预设库功能范围'))
        root.addWidget(notice)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.import_button = button(_tr('导入预设…'), self.import_preset, 'primary')
        self.import_button.setObjectName('presetImportButton')
        self.refresh_button = button(_tr('刷新'), self.refresh)
        self.refresh_button.setObjectName('presetRefreshButton')
        self.recover_button = button(_tr('恢复上一索引…'), self.recover_previous)
        self.recover_button.setObjectName('presetRecoverButton')
        toolbar.addWidget(self.import_button)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.recover_button)
        toolbar.addStretch()
        self.revision_label = label('', 'muted')
        self.revision_label.setObjectName('presetRevisionLabel')
        toolbar.addWidget(self.revision_label)
        root.addLayout(toolbar)

        self.status = label('', 'muted')
        self.status.setObjectName('presetLibraryStatus')
        self.status.setAccessibleName(_tr('本机预设库状态'))
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName('presetLibrarySplitter')
        self.entries = QTreeWidget()
        self.entries.setObjectName('presetLibraryEntries')
        self.entries.setAccessibleName(_tr('本机预设列表'))
        self.entries.setHeaderLabels([
            _tr('名称'), _tr('类型'), _tr('版本'), _tr('来源'), _tr('来源 ID'), _tr('收藏')])
        self.entries.setRootIsDecorated(False)
        self.entries.setUniformRowHeights(True)
        self.entries.setWordWrap(False)
        self.entries.setTextElideMode(Qt.ElideMiddle)
        self.entries.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.entries.setSelectionMode(QAbstractItemView.SingleSelection)
        self.entries.header().setStretchLastSection(False)
        self.entries.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.entries.setColumnWidth(0, 180)
        self.entries.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.entries.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.entries.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.entries.header().setSectionResizeMode(4, QHeaderView.Interactive)
        self.entries.setColumnWidth(4, 220)
        self.entries.header().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.entries.itemSelectionChanged.connect(self.selection_changed)
        splitter.addWidget(self.entries)

        detail = QWidget()
        detail.setObjectName('presetLibraryDetail')
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(8)
        self.detail_summary = label(_tr('选择一项预设查看只读内容。'), 'muted')
        self.detail_summary.setObjectName('presetLibraryDetailSummary')
        self.detail_summary.setAccessibleName(_tr('所选预设摘要'))
        self.detail_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_summary)
        self.document = QPlainTextEdit()
        self.document.setObjectName('presetLibraryDocument')
        self.document.setAccessibleName(_tr('所选预设只读 JSON'))
        self.document.setReadOnly(True)
        self.document.setLineWrapMode(QPlainTextEdit.NoWrap)
        detail_layout.addWidget(self.document, 1)

        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.actions_panel = QWidget()
        self.actions_panel.setObjectName('presetLibraryActions')
        actions = QGridLayout(self.actions_panel)
        actions.setSizeConstraint(QLayout.SetMinimumSize)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        self.copy_button = button(_tr('复制内置预设…'), self.copy_selected)
        self.copy_button.setObjectName('presetCopyButton')
        self.export_button = button(_tr('导出原始文件…'), self.export_selected)
        self.export_button.setObjectName('presetExportButton')
        self.rename_button = button(_tr('修改别名…'), self.rename_selected)
        self.rename_button.setObjectName('presetRenameButton')
        self.favorite_button = button(_tr('收藏'), self.toggle_favorite)
        self.favorite_button.setObjectName('presetFavoriteButton')
        self.delete_button = button(_tr('逻辑删除…'), self.delete_selected, 'danger')
        self.delete_button.setObjectName('presetDeleteButton')
        readonly_note = _tr('内置预设只读，仅支持复制或导出。删除仅移除本机库索引，后台保留不可变文件和历史索引。')
        self.copy_button.setToolTip(readonly_note)
        self.export_button.setToolTip(readonly_note)
        self.close_button = button(_tr('关闭'), self.reject)
        self.close_button.setObjectName('presetLibraryCloseButton')
        actions.addWidget(self.copy_button, 0, 0, 1, 2)
        actions.addWidget(self.export_button, 0, 2, 1, 2)
        actions.addWidget(self.rename_button, 1, 0)
        actions.addWidget(self.favorite_button, 1, 1)
        actions.addWidget(self.delete_button, 1, 2)
        actions.addWidget(self.close_button, 1, 3)
        root.addWidget(self.actions_panel)

        self.selection_changed()
        if self._backend_error is not None:
            self._set_status_error(_tr('本机预设库尚不可用：') + str(self._backend_error))
        else:
            self.refresh()

    def _set_status_error(self, text):
        _set_role(self.status, 'error')
        self.status.setText(text)

    def _set_status_message(self, text):
        _set_role(self.status, '')
        self.status.setText(text)

    def _set_status_success(self, text):
        _set_role(self.status, 'success')
        self.status.setText(text)

    def _report_error(self, text):
        self._set_status_error(text)
        QMessageBox.warning(self, _tr('本机预设库'), text)

    def _accept_snapshot(self, snapshot, preferred=None):
        if not isinstance(snapshot, dict):
            raise ValueError('library snapshot must be an object')
        revision = snapshot.get('revision')
        if revision is not None and not isinstance(revision, str):
            raise ValueError('library revision must be text or null')
        entries = snapshot.get('entries')
        if not isinstance(entries, list):
            raise ValueError('library entries must be a list')
        normalized = []
        required = ('id', 'version', 'sha256', 'type', 'name', 'builtin', 'favorite', 'alias')
        for entry in entries:
            if not isinstance(entry, dict) or any(key not in entry for key in required):
                raise ValueError('library entry is incomplete')
            normalized.append(dict(entry))
        self._revision = revision
        self._snapshot = {'revision': revision, 'entries': normalized}
        self._render_entries(preferred)

    def _render_entries(self, preferred=None):
        previous = preferred or self.selected_entry()
        key = (str(previous.get('id')), str(previous.get('version'))) if previous else None
        self.entries.blockSignals(True)
        try:
            self.entries.clear()
            chosen = None
            for entry in self._snapshot['entries']:
                source = _tr('内置') if entry.get('builtin') else _tr('本地')
                favorite = _tr('已收藏') if entry.get('favorite') else _tr('未收藏')
                item = QTreeWidgetItem([
                    str(entry.get('name', '')),
                    str(entry.get('type', '')),
                    str(entry.get('version', '')),
                    source,
                    str(entry.get('id', '')),
                    favorite,
                ])
                item.setData(0, Qt.UserRole, dict(entry))
                item.setToolTip(0, str(entry.get('name', '')))
                item.setToolTip(3, _tr('内置预设只读') if entry.get('builtin') else _tr('本机预设'))
                item.setToolTip(4, str(entry.get('id', '')))
                item.setToolTip(5, str(entry.get('alias', '')))
                self.entries.addTopLevelItem(item)
                if key == (str(entry.get('id')), str(entry.get('version'))):
                    chosen = item
            if chosen is None and self.entries.topLevelItemCount():
                chosen = self.entries.topLevelItem(0)
            if chosen is not None:
                self.entries.setCurrentItem(chosen)
                chosen.setSelected(True)
        finally:
            self.entries.blockSignals(False)
        self.selection_changed()
        revision = self._revision[:8] if self._revision else _tr('尚无索引')
        self.revision_label.setText(revision)
        self.revision_label.setToolTip(_tr('索引') + '：' + str(self._revision or ''))

    def refresh(self, *, announce=True, preferred=None):
        if self.library is None:
            return False
        try:
            snapshot = self.library.snapshot()
            self._accept_snapshot(snapshot, preferred)
        except Exception as error:
            self._set_status_error(_tr('无法读取本机预设库：') + str(error))
            return False
        builtins = sum(1 for row in self._snapshot['entries'] if row.get('builtin'))
        local = len(self._snapshot['entries']) - builtins
        if announce:
            if self._revision is None:
                text = _tr('尚无本机索引。打开此页不会创建目录；首次导入或复制内置预设时才会建立本机库。')
            else:
                text = _tr('共 {0} 项 · 内置 {1} · 本机 {2}',
                           f'{len(self._snapshot["entries"])}', f'{builtins}', f'{local}')
            self._set_status_message(text)
        return True

    def selected_entry(self):
        item = self.entries.currentItem()
        if item is None:
            selected = self.entries.selectedItems()
            item = selected[0] if selected else None
        if item is None:
            return None
        value = item.data(0, Qt.UserRole)
        return dict(value) if isinstance(value, dict) else None

    def selection_changed(self):
        entry = self.selected_entry()
        self.document.clear()
        if entry is None:
            self.detail_summary.setText(_tr('选择一项预设查看只读内容。'))
            self._set_selection_actions(None)
            return
        builtin = bool(entry.get('builtin'))
        alias = str(entry.get('alias', '') or '')
        self.detail_summary.setText(
            _tr('名称') + '：' + str(entry.get('name', '')) +
            '\n' + _tr('类型') + '：' + str(entry.get('type', '')) +
            '  ·  ' + _tr('版本') + '：' + str(entry.get('version', '')) +
            '\n' + _tr('来源') + '：' + (_tr('内置（只读）') if builtin else _tr('本地')) +
            '  ·  ' + _tr('来源 ID') + '：' + str(entry.get('id', '')) +
            (_tr('  ·  别名：') + alias if alias else ''))
        try:
            document = self.library.get_document(entry['id'], entry['version'])
            self.document.setPlainText(_pretty(document))
        except Exception as error:
            self.document.setPlainText(_tr('无法读取此预设：') + str(error))
            self._set_status_error(_tr('所选预设无法读取：') + str(error))
        self._set_selection_actions(entry)

    def _set_selection_actions(self, entry):
        available = bool(self.library is not None and entry is not None)
        builtin = bool(entry and entry.get('builtin'))
        self.copy_button.setEnabled(available and builtin)
        self.export_button.setEnabled(available)
        self.rename_button.setEnabled(available and not builtin)
        self.favorite_button.setEnabled(available and not builtin)
        self.delete_button.setEnabled(available and not builtin)
        if entry:
            self.favorite_button.setText(_tr('取消收藏') if entry.get('favorite') else _tr('收藏'))

    def _after_mutation(self, result, success, preferred=None):
        if not isinstance(result, dict):
            self._report_error(_tr('操作未完成：本机预设库返回了无效结果。'))
            return None
        revision = result.get('revision')
        if revision is not None and not isinstance(revision, str):
            self._report_error(_tr('操作未完成：本机预设库返回了无效索引版本。'))
            return None
        self._revision = revision
        entry = result.get('entry') if isinstance(result.get('entry'), dict) else preferred
        refreshed = self.refresh(announce=False, preferred=entry)
        if refreshed:
            self._set_status_success(success)
        else:
            self._set_status_error(success + _tr(' 列表刷新失败，请手动刷新后核对。'))
        return result

    def _mutate(self, operation, success_text):
        if self.library is None:
            self._report_error(_tr('本机预设库尚不可用。'))
            return None
        expected_revision = self._revision
        try:
            result = operation(expected_revision)
        except Exception as error:
            refreshed = False
            if _conflict_error(error):
                refreshed = self.refresh(announce=False)
            code = _error_code(error)
            text = _tr('操作未完成：') + str(error)
            if code:
                text += ' · ' + code
            if refreshed:
                text += _tr(' 列表已刷新，请核对后重新操作。')
            self._report_error(text)
            return None
        return self._after_mutation(result, success_text)

    def import_preset(self):
        path = self._ask_import_path()
        if not path:
            return
        try:
            raw = self._raw_reader(path)
            document = self._document_decoder(raw)
            preview_digest = str(self._document_digest(raw)).lower()
            if not isinstance(document, dict):
                raise ValueError('document must be an object')
        except Exception as error:
            code = _error_code(error)
            text = _tr('无法预览导入文件：') + str(error)
            if code:
                text += ' · ' + code
            self._report_error(text)
            return
        preview = PresetImportPreviewDialog(document, path, self)
        if preview.exec_() != QDialog.Accepted:
            self._set_status_message(_tr('已取消导入；本机预设库没有发生变化。'))
            return
        result = self._mutate(
            lambda revision: self.library.import_file(
                path, expected_revision=revision, expected_sha256=preview_digest),
            _tr('导入检查已完成。'),
        )
        if result is None:
            return
        entry = result.get('entry') if isinstance(result, dict) else None
        if isinstance(entry, dict):
            message = (_tr('同一预设版本已存在：') if result.get('already_present') else _tr('导入已完成：')) + _entry_label(entry)
            self._set_status_success(message)

    def copy_selected(self):
        entry = self.selected_entry()
        if not entry or not entry.get('builtin'):
            return
        default = str(entry.get('name', '')) + _tr(' 副本')
        name, accepted = QInputDialog.getText(
            self, _tr('复制内置预设'), _tr('新预设的显示名称：'), QLineEdit.Normal, default)
        if not accepted or not name.strip():
            return
        self._mutate(
            lambda revision: self.library.copy(
                entry['id'], entry['version'], name=name.strip(), expected_revision=revision),
            _tr('已复制为本机预设：') + name.strip(),
        )

    def rename_selected(self):
        entry = self.selected_entry()
        if not entry or entry.get('builtin'):
            return
        current = str(entry.get('alias', '') or '')
        alias, accepted = QInputDialog.getText(
            self, _tr('修改别名'), _tr('本机显示别名（清空则恢复文件中的名称）：'),
            QLineEdit.Normal, current)
        if not accepted:
            return
        alias = alias.strip()
        self._mutate(
            lambda revision: self.library.rename(
                entry['id'], entry['version'], alias=alias, expected_revision=revision),
            _tr('别名已更新：') + (alias or _tr('已恢复原始名称')),
        )

    def toggle_favorite(self):
        entry = self.selected_entry()
        if not entry or entry.get('builtin'):
            return
        favorite = not bool(entry.get('favorite'))
        self._mutate(
            lambda revision: self.library.set_favorite(
                entry['id'], entry['version'], favorite=favorite, expected_revision=revision),
            _tr('已收藏：') + str(entry.get('name', '')) if favorite else
            _tr('已取消收藏：') + str(entry.get('name', '')),
        )

    def delete_selected(self):
        entry = self.selected_entry()
        if not entry or entry.get('builtin'):
            return
        answer = QMessageBox.question(
            self,
            _tr('逻辑删除预设'),
            _tr('从本机列表移除“{0}” {1}？\n不可变文件与上一索引会保留，不会删除用户任务或源文件。',
                str(entry.get('name', '')), str(entry.get('version', ''))),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._mutate(
            lambda revision: self.library.delete(
                entry['id'], entry['version'], expected_revision=revision),
            _tr('已从本机列表移除：') + str(entry.get('name', '')),
        )

    def export_selected(self):
        entry = self.selected_entry()
        if not entry or self.library is None:
            return
        default_name = '{0}-{1}.vwa-preset.json'.format(entry.get('id', 'preset'), entry.get('version', '1.0.0'))
        destination = self._ask_export_path(default_name)
        if not destination:
            return
        if Path(destination).exists():
            self._report_error(_tr('目标文件已存在。为避免覆盖，请选择新的文件名。'))
            return
        try:
            output = self.library.export_file(entry['id'], entry['version'], destination)
        except Exception as error:
            code = _error_code(error)
            text = _tr('导出未完成：') + str(error)
            if code:
                text += ' · ' + code
            self._report_error(text)
            return
        self._set_status_success(_tr('已导出原始文件：') + str(output))

    def recover_previous(self):
        if self.library is None:
            self._report_error(_tr('本机预设库尚不可用。'))
            return
        answer = QMessageBox.question(
            self,
            _tr('恢复上一索引'),
            _tr('恢复上一份有效索引？当前损坏索引会保留在历史中，所有引用文件会先重新校验。\n不会自动应用预设，也不会重试其他操作。'),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            snapshot = self.library.recover_previous()
            self._accept_snapshot(snapshot)
        except Exception as error:
            code = _error_code(error)
            text = _tr('恢复未完成：') + str(error)
            if code:
                text += ' · ' + code
            self._report_error(text)
            return
        self._set_status_success(_tr('已恢复上一份有效索引。列表已刷新，预设未自动应用。'))

    def _ask_import_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, _tr('选择预设文件'), '', _tr('预设文件 (*.json);;所有文件 (*)'))
        return path

    def _ask_export_path(self, default_name):
        path, _ = QFileDialog.getSaveFileName(
            self, _tr('导出原始预设文件'), default_name, _tr('预设文件 (*.json);;所有文件 (*)'))
        return path



def open_preset_library_manager(parent=None):
    dialog = PresetLibraryManagerDialog(parent)
    dialog.exec_()
    return dialog
