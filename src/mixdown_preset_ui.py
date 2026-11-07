"""Accessible save and apply dialogs for portable mixdown presets.

The dialogs are presentation and planning surfaces. They do not render audio,
touch the network, import files, or commit a mix. The host owns saving a user
document and rechecking the live mixer before applying a returned plan.
"""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Callable

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from desktop_theme import button
from i18n import tr as _tr
from mixdown_preset_documents import make_document, plan_application


def _plain_label(text: str = '', *, role: str | None = None) -> QLabel:
    """Return a label that never interprets bundled or imported text as HTML."""
    value = QLabel()
    value.setTextFormat(Qt.PlainText)
    value.setWordWrap(True)
    value.setText(str(text))
    if role:
        value.setProperty('role', role)
    return value


def _set_role(widget: QWidget, role: str) -> None:
    widget.setProperty('statusRole', role)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _short_identity(identifier: str, limit: int = 52) -> str:
    value = str(identifier)
    if len(value) <= limit:
        return value
    return value[:limit - 15] + '...' + value[-12:]



class _PresetListWidget(QListWidget):
    """Use normal text over the theme's selected surface for readable contrast."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._syncing_palette_style = False
        self._sync_palette_style()

    def _sync_palette_style(self) -> None:
        if self._syncing_palette_style:
            return
        color = self.palette().color(QPalette.Text).name()
        self._syncing_palette_style = True
        try:
            self.setStyleSheet(
                'QListWidget::item { color: ' + color + '; } '
                'QListWidget::item:selected { color: ' + color + '; }'
            )
        finally:
            self._syncing_palette_style = False

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.StyleChange):
            self._sync_palette_style()


class MixPresetSaveDialog(QDialog):
    """Collect a name and return a validated, media-free mixdown document."""

    def __init__(self, payload, parent=None):
        super().__init__(parent)
        self.setObjectName('mixPresetSaveDialog')
        self.setWindowTitle(_tr('保存混音预设'))
        self.setMinimumSize(720, 520)
        self.resize(820, 600)
        self.setSizeGripEnabled(True)

        self._payload = copy.deepcopy(payload)
        self._identifier = 'user.' + str(uuid.uuid4())
        self.document: dict[str, Any] | None = None

        body = QVBoxLayout(self)
        body.setContentsMargins(24, 14, 24, 14)
        body.setSpacing(8)

        title = _plain_label(_tr('保存当前混音处理'), role='heading')
        title.setObjectName('mixPresetSaveTitle')
        body.addWidget(title)
        body.addWidget(_plain_label(
            _tr('保存人声处理参数；每次新增预设，源文件和当前工程不变。'),
            role='muted',
        ))

        name_row = QHBoxLayout()
        name_label = _plain_label(_tr('预设名称'))
        name_label.setObjectName('mixPresetSaveNameLabel')
        self.name_edit = QLineEdit(_tr('我的混音预设'))
        self.name_edit.setObjectName('mixPresetSaveName')
        self.name_edit.setMaxLength(80)
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.setAccessibleName(_tr('混音预设名称，最多 80 个字符'))
        name_label.setBuddy(self.name_edit)
        name_row.addWidget(name_label)
        name_row.addWidget(self.name_edit, 1)
        body.addLayout(name_row)

        self.include_width = QCheckBox(_tr('同时保存总输出宽度'))
        self.include_width.setObjectName('mixPresetSaveIncludeWidth')
        self.include_width.setChecked(False)
        self.include_width.setAccessibleName(_tr('同时保存总输出宽度'))
        body.addWidget(self.include_width)

        width_warning = _plain_label(
            _tr('宽度会改变伴奏与总输出宽度；不勾选时只保存人声处理参数。'),
            role='warning',
        )
        width_warning.setObjectName('mixPresetSaveWidthWarning')
        body.addWidget(width_warning)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName('mixPresetSaveJsonPreview')
        self.preview.setReadOnly(True)
        self.preview.setUndoRedoEnabled(False)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.preview.setMinimumHeight(112)
        self.preview.setAccessibleName(_tr('只读预设 JSON 预览'))
        body.addWidget(self.preview, 1)

        identity_note = _tr(
            '每次保存都会新增一项预设，不会覆盖已有预设。新预设编号：{0}'
        ).format(self._identifier)
        self.identity_note = _plain_label(identity_note, role='muted')
        self.identity_note.setObjectName('mixPresetSaveIdentityNote')
        self.identity_note.setAccessibleName(_tr('新预设编号说明'))
        self.identity_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.identity_note)

        self.status = QPlainTextEdit()
        self.status.setObjectName('mixPresetSaveStatus')
        self.status.setReadOnly(True)
        self.status.setUndoRedoEnabled(False)
        self.status.setFocusPolicy(Qt.NoFocus)
        self.status.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.status.setMinimumHeight(40)
        self.status.setMaximumHeight(54)
        self.status.setAccessibleName(_tr('保存状态'))
        body.addWidget(self.status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = button(_tr('取消'), self.reject)
        self.cancel_button.setObjectName('mixPresetSaveCancel')
        self.cancel_button.setAccessibleName(_tr('取消保存混音预设'))
        self.save_button = button(_tr('保存'), self.accept, 'primary')
        self.save_button.setObjectName('mixPresetSaveAccept')
        self.save_button.setAccessibleName(_tr('保存混音预设文档'))
        self.save_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        body.addLayout(actions)

        self.name_edit.textChanged.connect(self._update_preview)
        self.include_width.toggled.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self) -> None:
        self.document = None
        self.save_button.setEnabled(False)
        name = self.name_edit.text().strip()
        if not name:
            self.preview.clear()
            self.status.setPlainText(_tr('请输入 1 到 80 个字符的预设名称。'))
            _set_role(self.status, 'error')
            return
        try:
            document = make_document(
                self._payload,
                name,
                include_width=self.include_width.isChecked(),
                identifier=self._identifier,
            )
        except Exception as error:
            self.preview.clear()
            self.status.setPlainText(_tr('无法生成预设：') + str(error))
            _set_role(self.status, 'error')
            return
        self.document = document
        self.preview.setPlainText(_json_text(document))
        self.preview.verticalScrollBar().setValue(0)
        self.status.setPlainText(_tr('预设内容有效；将新增一项本机预设，当前混音保持不变。'))
        _set_role(self.status, 'success')
        self.save_button.setEnabled(True)

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.document = None
            self.status.setPlainText(_tr('请输入 1 到 80 个字符的预设名称。'))
            _set_role(self.status, 'error')
            return
        try:
            document = make_document(
                self._payload,
                name,
                include_width=self.include_width.isChecked(),
                identifier=self._identifier,
            )
        except Exception as error:
            self.document = None
            self.status.setPlainText(_tr('无法保存预设：') + str(error))
            _set_role(self.status, 'error')
            return
        self.document = document
        super().accept()


class MixPresetLibraryDialog(QDialog):
    """Preview a stored mixdown preset and return an explicit apply plan."""

    def __init__(self, library, before, *, sample_rate=44100, parent=None,
                 manage: Callable[..., Any] | None = None):
        super().__init__(parent)
        self.setObjectName('mixPresetLibraryDialog')
        self.setWindowTitle(_tr('应用混音预设'))
        self.setMinimumSize(720, 520)
        self.resize(880, 640)
        self.setSizeGripEnabled(True)

        self.library = library
        self.sample_rate = sample_rate
        self.manage = manage
        self._before = copy.deepcopy(before)
        self._entries: list[dict[str, Any]] = []
        self._syncing = False
        self._preview_identity: tuple[str, str, str] | None = None

        self.plan: dict[str, Any] | None = None
        self.selected_entry: dict[str, Any] | None = None
        self.selected_document: dict[str, Any] | None = None

        body = QVBoxLayout(self)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)

        title = _plain_label(_tr('选择混音预设'), role='heading')
        title.setObjectName('mixPresetLibraryTitle')
        body.addWidget(title)
        body.addWidget(_plain_label(
            _tr('选择只会查看差异；点击“确认应用”后才会按所选范围应用。应用前会再次核对当前混音。'),
            role='muted',
        ))

        self.preset_list = _PresetListWidget()
        self.preset_list.setObjectName('mixPresetLibraryList')
        self.preset_list.setAccessibleName(_tr('混音预设列表'))
        self.preset_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.preset_list.setWordWrap(True)
        self.preset_list.setMinimumHeight(100)
        body.addWidget(self.preset_list, 1)

        options = QHBoxLayout()
        options.setSpacing(18)
        self.include_width = QCheckBox(_tr('同时应用输出宽度'))
        self.include_width.setObjectName('mixPresetLibraryIncludeWidth')
        self.include_width.setChecked(False)
        self.include_width.setEnabled(False)
        self.include_width.setAccessibleName(_tr('同时应用预设声明的输出宽度'))
        self.include_tracks = QCheckBox(_tr('同时应用轨道模板'))
        self.include_tracks.setObjectName('mixPresetLibraryIncludeTracks')
        self.include_tracks.setChecked(False)
        self.include_tracks.setEnabled(False)
        self.include_tracks.setAccessibleName(_tr('同时应用预设声明的声像与混响发送模板'))
        options.addWidget(self.include_width)
        options.addWidget(self.include_tracks)
        options.addStretch(1)
        body.addLayout(options)

        self.status = QPlainTextEdit()
        self.status.setObjectName('mixPresetLibraryStatus')
        self.status.setReadOnly(True)
        self.status.setUndoRedoEnabled(False)
        self.status.setFocusPolicy(Qt.NoFocus)
        self.status.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.status.setMinimumHeight(42)
        self.status.setMaximumHeight(60)
        self.status.setAccessibleName(_tr('预设预览状态'))
        body.addWidget(self.status)

        self.difference_table = QTableWidget(0, 3)
        self.difference_table.setObjectName('mixPresetLibraryDifferences')
        self.difference_table.setAccessibleName(_tr('应用差异，只读'))
        self.difference_table.setHorizontalHeaderLabels([
            _tr('字段'), _tr('当前'), _tr('应用后'),
        ])
        self.difference_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.difference_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.difference_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.difference_table.setAlternatingRowColors(False)
        self.difference_table.verticalHeader().setVisible(False)
        header = self.difference_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.difference_table.setMinimumHeight(110)
        body.addWidget(self.difference_table, 1)

        actions = QHBoxLayout()
        self.refresh_button = button(_tr('刷新'), self.refresh)
        self.refresh_button.setObjectName('mixPresetLibraryRefresh')
        self.refresh_button.setAccessibleName(_tr('刷新本机混音预设列表'))
        self.manage_button = button(_tr('管理预设'), self._open_manager)
        self.manage_button.setObjectName('mixPresetLibraryManage')
        self.manage_button.setAccessibleName(_tr('打开本机预设管理器'))
        self.manage_button.setEnabled(manage is not None)
        self.cancel_button = button(_tr('取消'), self.reject)
        self.cancel_button.setObjectName('mixPresetLibraryCancel')
        self.cancel_button.setAccessibleName(_tr('取消应用混音预设'))
        self.apply_button = button(_tr('确认应用'), self.accept, 'primary')
        self.apply_button.setObjectName('mixPresetLibraryApply')
        self.apply_button.setAccessibleName(_tr('确认应用当前预览的混音预设'))
        self.apply_button.setEnabled(False)
        self.apply_button.setDefault(True)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.manage_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.apply_button)
        body.addLayout(actions)

        self.preset_list.currentItemChanged.connect(self._preview_selected)
        self.include_width.toggled.connect(self._controls_changed)
        self.include_tracks.toggled.connect(self._controls_changed)
        self.refresh()

    def _set_status(self, text: str, role: str) -> None:
        self.status.setPlainText(str(text))
        _set_role(self.status, role)

    def _clear_preview(self, *, preserve_status: bool = False) -> None:
        self.plan = None
        self.selected_entry = None
        self.selected_document = None
        self._preview_identity = None
        self._syncing = True
        try:
            self.include_width.setChecked(False)
            self.include_tracks.setChecked(False)
            self.include_width.setEnabled(False)
            self.include_tracks.setEnabled(False)
        finally:
            self._syncing = False
        self.difference_table.setRowCount(0)
        self.apply_button.setEnabled(False)
        if not preserve_status:
            self._set_status(_tr('请选择一项混音预设进行预览。'), 'muted')

    def _filtered_entries(self, snapshot: Any) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('entries'), list):
            raise ValueError(_tr('本机预设库返回了无效快照。'))
        entries = []
        for raw in snapshot['entries']:
            if not isinstance(raw, dict) or raw.get('type') != 'mixdown':
                continue
            identifier = raw.get('id')
            builtin = raw.get('builtin')
            if not isinstance(identifier, str):
                continue
            if builtin is True or builtin is False:
                entries.append(copy.deepcopy(raw))
        return entries

    def refresh(self) -> None:
        self._reload(announce=True)

    def _reload(self, *, announce: bool) -> bool:
        self._clear_preview(preserve_status=True)
        self._syncing = True
        try:
            self.preset_list.clear()
        finally:
            self._syncing = False
        self._entries = []
        if self.library is None:
            self._set_status(_tr('本机预设库不可用。'), 'error')
            return False
        try:
            snapshot = self.library.snapshot()
            entries = self._filtered_entries(snapshot)
        except Exception as error:
            self._set_status(_tr('无法刷新混音预设：') + str(error), 'error')
            return False

        self._entries = entries
        self._syncing = True
        try:
            for entry in entries:
                source = _tr('内置（只读）') if entry.get('builtin') is True else _tr('本机用户')
                name = str(entry.get('name', ''))
                version = str(entry.get('version', ''))
                identifier = str(entry.get('id', ''))
                text = (
                    name + '\n' +
                    _tr('版本') + ' ' + version + '  ·  ' + source + '\n' +
                    _tr('编号') + ' ' + _short_identity(identifier)
                )
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, copy.deepcopy(entry))
                item.setForeground(self.palette().color(QPalette.Text))
                self.preset_list.addItem(item)
        finally:
            self._syncing = False
        if announce:
            self._set_status(
                _tr('已刷新：{0} 项混音预设；请选择一项生成预览。').format(len(entries)),
                'success' if entries else 'muted',
            )
        return True

    def _open_manager(self) -> None:
        if self.manage is None:
            self._set_status(_tr('尚未连接本机预设管理器。'), 'muted')
            return
        failure = None
        try:
            self.manage(self)
        except Exception as error:
            failure = error
        refreshed = self._reload(announce=False)
        if failure is not None:
            self._set_status(_tr('预设管理器未完成：') + str(failure), 'error')
        elif refreshed:
            self._set_status(_tr('预设列表已随管理器更新刷新；选择只会查看差异。'), 'success')
        else:
            self._set_status(_tr('预设列表刷新失败，请手动重试。'), 'error')

    def _preview_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._syncing:
            return
        self.plan = None
        self.selected_document = None
        self.selected_entry = None
        self.apply_button.setEnabled(False)
        if current is None:
            self._clear_preview()
            return
        entry = current.data(Qt.UserRole)
        if not isinstance(entry, dict):
            self._clear_preview()
            self._set_status(_tr('所选条目的身份无效。'), 'error')
            return
        self.selected_entry = copy.deepcopy(entry)
        identity = (
            str(entry.get('id', '')),
            str(entry.get('version', '')),
            str(entry.get('sha256', '')),
        )
        new_identity = identity != self._preview_identity
        self._preview_identity = identity
        self._syncing = True
        try:
            if new_identity:
                self.include_width.setChecked(False)
                self.include_tracks.setChecked(False)
        finally:
            self._syncing = False
        try:
            document = self.library.get_document(identity[0], identity[1])
            if not isinstance(document, dict):
                raise ValueError(_tr('预设库返回了无效文档。'))
            payload = document.get('payload')
            declared_width = isinstance(payload, dict) and 'width' in payload
            declared_tracks = isinstance(payload, dict) and 'track_template' in payload
            self.include_width.setEnabled(declared_width)
            self.include_tracks.setEnabled(declared_tracks)
            if not declared_width:
                self.include_width.setChecked(False)
            if not declared_tracks:
                self.include_tracks.setChecked(False)
            plan = plan_application(
                document,
                self._before,
                sample_rate=self.sample_rate,
                include_width=False,
                include_tracks=False,
            )
        except Exception as error:
            self.include_width.setEnabled(False)
            self.include_tracks.setEnabled(False)
            self.selected_document = None
            self.plan = None
            self.difference_table.setRowCount(0)
            self._set_status(_tr('无法预览所选预设：') + str(error), 'error')
            return
        self.selected_document = copy.deepcopy(document)
        self.plan = plan
        self._render_plan(plan)
        if plan['adjusted']:
            detail_adjustment = _tr('预览数值已按当前采样率和控件精度调整。')
        else:
            detail_adjustment = ''
        detail = _tr(
            '预览有效，尚未应用。默认保留当前宽度与轨道声像/混响发送；请明确选择附加范围。'
        )
        if declared_width or declared_tracks:
            detail += ' ' + _tr('此预设可选应用宽度或轨道模板。')
        detail += ' ' + detail_adjustment
        self._set_status(detail, 'success')
        self.apply_button.setEnabled(True)

    def _controls_changed(self, _checked: bool) -> None:
        if self._syncing or self.selected_document is None:
            return
        try:
            plan = plan_application(
                self.selected_document,
                self._before,
                sample_rate=self.sample_rate,
                include_width=self.include_width.isChecked(),
                include_tracks=self.include_tracks.isChecked(),
            )
        except Exception as error:
            self.plan = None
            self.apply_button.setEnabled(False)
            self.difference_table.setRowCount(0)
            self._set_status(_tr('无法更新应用范围：') + str(error), 'error')
            return
        self.plan = plan
        self._render_plan(plan)
        self.apply_button.setEnabled(True)
        extras = []
        if plan['include_width']:
            extras.append(_tr('输出宽度'))
        if plan['include_tracks']:
            extras.append(_tr('轨道模板'))
        scope = _tr('、').join(extras) if extras else _tr('仅人声处理')
        self._set_status(_tr('预览已更新：{0}；尚未应用。').format(scope), 'success')

    def _render_plan(self, plan: dict[str, Any]) -> None:
        labels = {
            'eq_enabled': _tr('启用 EQ'), 'compression_enabled': _tr('启用压缩'),
            'compression_strength': _tr('压缩强度'), 'reverb_enabled': _tr('启用混响'),
            'reverb_profile': _tr('混响空间'), 'reverb_mix': _tr('混响混合'),
            'reverb_predelay_ms': _tr('混响预延迟'), 'width': _tr('输出宽度'),
        }
        def display(value, field=''):
            if type(value) is bool:
                return _tr('启用') if value else _tr('关闭')
            if field in ('compression_strength', 'reverb_mix', 'reverb_send'):
                return f'{value * 100:g}%'
            if field == 'reverb_profile':
                return {'small': _tr('短空间'), 'natural': _tr('自然空间'),
                        'wide': _tr('宽阔空间')}[value]
            if type(value) in (int, float):
                unit = {'freq_hz': ' Hz', 'gain_db': ' dB',
                        'reverb_predelay_ms': ' ms'}.get(field, '')
                return f'{value:g}' + unit
            return str(value)
        rows = []
        for change in plan['changes']:
            field = change['field'].removeprefix('processing.')
            old, new = change['before'], change['after']
            if field == 'eq_bands':
                band_labels = {'highpass': _tr('高通'), 'lowshelf': _tr('低架'),
                               'bell': _tr('钟形'), 'highshelf': _tr('高架')}
                for index, (left, right) in enumerate(zip(old, new, strict=True)):
                    for key, caption in [('enabled', _tr('启用')), ('freq_hz', _tr('频率')),
                                         ('gain_db', _tr('增益')), ('q', 'Q')]:
                        if left[key] != right[key]:
                            label = f'EQ {index + 1} ' + band_labels[right['type']] + ' · ' + caption
                            rows.append((label, display(left[key], key), display(right[key], key)))
            elif field == 'tracks':
                for index, (left, right) in enumerate(zip(old, new, strict=True)):
                    for key, caption in [('pan', _tr('声像')), ('reverb_send', _tr('混响发送'))]:
                        if left[key] != right[key]:
                            rows.append((f'V{index + 1} · ' + caption,
                                         display(left[key], key), display(right[key], key)))
            else:
                rows.append((labels.get(field, field), display(old, field), display(new, field)))
        if not rows:
            rows = [(_tr('无变化'), _tr('当前状态'), _tr('应用后状态'))]
        self.difference_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.difference_table.setItem(row, column, item)

    def _find_current_entry(self, expected: dict[str, Any]) -> dict[str, Any] | None:
        snapshot = self.library.snapshot()
        entries = self._filtered_entries(snapshot)
        identifier = str(expected.get('id', ''))
        version = str(expected.get('version', ''))
        for entry in entries:
            if entry.get('id') == identifier and entry.get('version') == version:
                return entry
        return None

    def accept(self) -> None:
        expected = self.selected_entry
        if expected is None or self.plan is None:
            self.apply_button.setEnabled(False)
            self._set_status(_tr('请先选择并成功预览一项混音预设。'), 'error')
            return
        try:
            current = self._find_current_entry(expected)
        except Exception as error:
            self.apply_button.setEnabled(False)
            self._set_status(_tr('无法重新核对预设库：') + str(error), 'error')
            return
        if current is None:
            self.plan = None
            self.selected_document = None
            self.apply_button.setEnabled(False)
            self._set_status(_tr('预览后该预设已被删除或身份已变化；未应用，请刷新后重新选择。'), 'error')
            return
        expected_sha = str(expected.get('sha256', ''))
        current_sha = str(current.get('sha256', ''))
        if current.get('id') != expected.get('id') or current.get('version') != expected.get('version') or current_sha != expected_sha:
            self.plan = None
            self.selected_document = None
            self.apply_button.setEnabled(False)
            self._set_status(_tr('预览后预设版本或摘要已变化；未应用，请刷新后重新选择。'), 'error')
            return
        try:
            document = self.library.get_document(str(expected['id']), str(expected['version']))
            if not isinstance(document, dict):
                raise ValueError(_tr('预设库返回了无效文档。'))
            if self.selected_document is None or document != self.selected_document:
                raise ValueError(_tr('重新读取的预设内容与预览不一致。'))
            plan = plan_application(
                document,
                self._before,
                sample_rate=self.sample_rate,
                include_width=self.include_width.isChecked(),
                include_tracks=self.include_tracks.isChecked(),
            )
        except Exception as error:
            self.plan = None
            self.selected_document = None
            self.apply_button.setEnabled(False)
            self._set_status(_tr('应用前复核失败；未应用：') + str(error), 'error')
            return
        self.selected_document = copy.deepcopy(document)
        self.plan = plan
        self._render_plan(plan)
        self._set_status(_tr('预设已确认；应用时仍会核对当前混音。'), 'success')
        super().accept()
