"""Planning dialogs for portable tuning presets and local input bindings.

The dialogs only collect a name or freeze an explicit application proposal.
They never execute tuning, create a project, write the preset library, or
change the tuning page. Imported metadata and errors are always rendered as
plain text.
"""
from __future__ import annotations

import copy
import importlib
import json
from typing import Any, Callable

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop_theme import button
from i18n import tr as _tr


_SOURCE_MODES = ('preserve', 'reference', 'calibrated', 'retained')
_VOICES = (
    ('lead', '主唱'),
    ('backing', '和声'),
    ('vocals', '人声（单一）'),
)
_MODE_NAMES = {
    'preserve': '保留原表现',
    'reference': '参考演唱音频',
    'calibrated': '校准配置 + 参考音频',
    'retained': '沿用原工程参数',
}


def _adapter_api():
    """Load the non-Qt adapter lazily so this surface can integrate later."""

    module = importlib.import_module('tuning_preset_documents')
    make = getattr(module, 'make_document')
    plan = getattr(module, 'plan_application')
    return make, plan


def _plain_label(text: str = '', *, role: str | None = None) -> QLabel:
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


def _display_value(value: Any) -> str:
    if value is None:
        return _tr('未设置')
    if type(value) is bool:
        return _tr('是') if value else _tr('否')
    if isinstance(value, (dict, list, tuple)):
        try:
            return _json_text(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _short_identity(identifier: str, limit: int = 52) -> str:
    value = str(identifier)
    if len(value) <= limit:
        return value
    return value[:limit - 15] + '...' + value[-12:]


class _PresetListWidget(QListWidget):
    """Keep selected item text readable over the application theme."""

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


class TuningPresetSaveDialog(QDialog):
    """Collect a name and return a validated, path-free tuning document."""

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName('tuningPresetSaveDialog')
        self.setWindowTitle(_tr('保存调音预设'))
        self.setMinimumSize(720, 520)
        self.resize(820, 610)
        self.setSizeGripEnabled(True)

        self._options = copy.deepcopy(options)
        self._document: dict[str, Any] | None = None
        self._valid = False

        body = QVBoxLayout(self)
        body.setContentsMargins(24, 14, 24, 14)
        body.setSpacing(8)

        title = _plain_label(_tr('保存当前调音参数'), role='heading')
        title.setObjectName('tuningPresetSaveTitle')
        body.addWidget(title)
        body.addWidget(_plain_label(
            _tr('只保存可共享的流程意图。本机路径、工程 SHA、真实轨号和片段坐标不会写入预设。'),
            role='muted',
        ))

        name_row = QHBoxLayout()
        name_label = _plain_label(_tr('预设名称'))
        name_label.setObjectName('tuningPresetSaveNameLabel')
        self.name_edit = QLineEdit(_tr('我的调音预设'))
        self.name_edit.setObjectName('tuningPresetSaveName')
        self.name_edit.setMaxLength(80)
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.setAccessibleName(_tr('调音预设名称，最多 80 个字符'))
        name_label.setBuddy(self.name_edit)
        name_row.addWidget(name_label)
        name_row.addWidget(self.name_edit, 1)
        body.addLayout(name_row)

        self.identity_note = _plain_label(
            _tr('创建后会得到新的用户预设身份；保存动作由调音页执行，源工程与当前表单不变。'),
            role='muted',
        )
        self.identity_note.setObjectName('tuningPresetSaveIdentityNote')
        body.addWidget(self.identity_note)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName('tuningPresetSavePreview')
        self.preview.setReadOnly(True)
        self.preview.setUndoRedoEnabled(False)
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.preview.setAccessibleName(_tr('调音预设文档预览，只读纯文本'))
        body.addWidget(self.preview, 1)

        self.status = QPlainTextEdit()
        self.status.setObjectName('tuningPresetSaveStatus')
        self.status.setReadOnly(True)
        self.status.setUndoRedoEnabled(False)
        self.status.setFocusPolicy(Qt.NoFocus)
        self.status.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.status.setMinimumHeight(42)
        self.status.setMaximumHeight(62)
        self.status.setAccessibleName(_tr('调音预设保存状态'))
        body.addWidget(self.status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = button(_tr('取消'), self.reject)
        self.cancel_button.setObjectName('tuningPresetSaveCancel')
        self.cancel_button.setAccessibleName(_tr('取消保存调音预设'))
        self.save_button = button(_tr('创建预设内容'), self.accept, 'primary')
        self.save_button.setObjectName('tuningPresetSaveConfirm')
        self.save_button.setAccessibleName(_tr('验证并创建调音预设内容'))
        self.save_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        body.addLayout(actions)

        self.name_edit.textChanged.connect(self._update_preview)
        self._update_preview()

    def _set_status(self, text: str, role: str) -> None:
        self.status.setPlainText(str(text))
        _set_role(self.status, role)

    def _build_document(self, *, announce: bool = True) -> dict[str, Any] | None:
        name = self.name_edit.text().strip()
        if not 1 <= len(name) <= 80:
            self._document = None
            self.preview.clear()
            self.save_button.setEnabled(False)
            self._valid = False
            if announce:
                self._set_status(_tr('请输入 1 到 80 个字符的预设名称。'), 'error')
            return None
        try:
            make_document, _ = _adapter_api()
            document = make_document(copy.deepcopy(self._options), name)
            if not isinstance(document, dict):
                raise ValueError(_tr('调音预设适配器没有返回文档。'))
        except Exception as error:
            self._document = None
            self.preview.clear()
            self.save_button.setEnabled(False)
            self._valid = False
            if announce:
                self._set_status(_tr('无法创建调音预设：') + str(error), 'error')
            return None
        self._document = copy.deepcopy(document)
        self._valid = True
        self.preview.setPlainText(_json_text(document))
        self.save_button.setEnabled(True)
        if announce:
            self._set_status(
                _tr('文档有效；名称与共享参数已确认，尚未写入本机预设库。'),
                'success',
            )
        return self._document

    def _update_preview(self, _text: str = '') -> None:
        self._build_document(announce=True)

    def document(self) -> dict[str, Any]:
        """Return a deep copy of the validated document for the host to save."""

        document = self._document if self._valid else self._build_document(announce=True)
        if document is None:
            raise ValueError(_tr('当前调音预设内容无效。'))
        return copy.deepcopy(document)

    def accept(self) -> None:
        if self._build_document(announce=True) is None:
            return
        super().accept()


class TuningPresetLibraryDialog(QDialog):
    """Preview a library tuning preset and freeze a local application plan."""

    def __init__(self, library, project, bindings, before, parent=None, *,
                 manage_callback: Callable[..., Any] | None = None):
        super().__init__(parent)
        self.setObjectName('tuningPresetLibraryDialog')
        self.setWindowTitle(_tr('应用调音预设'))
        self.setMinimumSize(720, 540)
        self.resize(980, 760)
        self.setSizeGripEnabled(True)

        self.library = library
        self.manage_callback = manage_callback
        self.project = copy.deepcopy(project) if isinstance(project, dict) else None
        self._before = copy.deepcopy(before)
        self._initial_bindings = copy.deepcopy(bindings) if isinstance(bindings, dict) else {}

        self._entries: list[dict[str, Any]] = []
        self._rows: dict[int, dict[str, Any]] = {}
        self._syncing = False
        self._mode = 'preserve'
        self._plan: dict[str, Any] | None = None
        self._document: dict[str, Any] | None = None
        self._raw_document: bytes | None = None
        self._preview_identity: tuple[str, str, str] | None = None
        self._missing_display: list[str] = []

        self.selected_plan: dict[str, Any] | None = None
        self.selected_identity: dict[str, str] | None = None
        self.selected_pending = False

        body = QVBoxLayout(self)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)

        title = _plain_label(_tr('选择调音预设'), role='heading')
        title.setObjectName('tuningPresetLibraryTitle')
        body.addWidget(title)
        body.addWidget(_plain_label(
            _tr('选择只生成差异和绑定提案。确认后仍需在调音页补齐输入，才会允许生成。'),
            role='muted',
        ))

        self.content_scroll = QScrollArea()
        self.content_scroll.setObjectName('tuningPresetLibraryScroll')
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        content.setObjectName('tuningPresetLibraryContent')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.preset_list = _PresetListWidget()
        self.preset_list.setObjectName('tuningPresetLibraryList')
        self.preset_list.setAccessibleName(_tr('调音预设列表，只显示 type=tuning'))
        self.preset_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.preset_list.setWordWrap(True)
        self.preset_list.setMinimumHeight(92)
        self.preset_list.setMaximumHeight(130)
        layout.addWidget(self.preset_list)

        self.project_identity = QPlainTextEdit()
        self.project_identity.setObjectName('tuningPresetProjectIdentity')
        self.project_identity.setReadOnly(True)
        self.project_identity.setUndoRedoEnabled(False)
        self.project_identity.setFocusPolicy(Qt.NoFocus)
        self.project_identity.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.project_identity.setMinimumHeight(66)
        self.project_identity.setMaximumHeight(96)
        self.project_identity.setAccessibleName(_tr('工程 SHA、实际目标轨与声部映射，只读纯文本'))
        layout.addWidget(self.project_identity)

        self.candidate_confirm = QCheckBox(_tr('将此候选作为新基线'))
        self.candidate_confirm.setObjectName('tuningPresetCandidateBaseline')
        self.candidate_confirm.setAccessibleName(
            _tr('明确确认将检测到的旁置候选作为本次调音的新基线')
        )
        self.candidate_confirm.setChecked(False)
        layout.addWidget(self.candidate_confirm)

        self.candidate_info = QPlainTextEdit()
        self.candidate_info.setObjectName('tuningPresetCandidateInfo')
        self.candidate_info.setReadOnly(True)
        self.candidate_info.setUndoRedoEnabled(False)
        self.candidate_info.setFocusPolicy(Qt.NoFocus)
        self.candidate_info.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.candidate_info.setMinimumHeight(54)
        self.candidate_info.setMaximumHeight(88)
        self.candidate_info.setAccessibleName(_tr('候选来源或旁置回执摘要，只读纯文本'))
        layout.addWidget(self.candidate_info)

        self.mode_label = _plain_label('', role='muted')
        self.mode_label.setObjectName('tuningPresetSourceMode')
        layout.addWidget(self.mode_label)

        self.binding_table = QTableWidget(0, 5)
        self.binding_table.setObjectName('tuningPresetBindingTable')
        self.binding_table.setAccessibleName(_tr('目标轨与本地输入绑定表'))
        self.binding_table.setHorizontalHeaderLabels([
            _tr('目标'), _tr('工程轨'), _tr('声部'), _tr('参考音频'), _tr('原工程轨号'),
        ])
        self.binding_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.binding_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.binding_table.verticalHeader().setVisible(False)
        binding_header = self.binding_table.horizontalHeader()
        binding_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        binding_header.setSectionResizeMode(1, QHeaderView.Stretch)
        binding_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        binding_header.setSectionResizeMode(3, QHeaderView.Stretch)
        binding_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.binding_table.setMinimumHeight(134)
        self.binding_table.setMaximumHeight(190)
        layout.addWidget(self.binding_table)

        common = QFormLayout()
        common.setContentsMargins(0, 0, 0, 0)
        self.profile_edit, profile_browse = self._path_editor(
            _tr('选择声库校准 profile'),
            self._browse_profile,
            _tr('校准配置路径'),
        )
        self.profile_edit.setObjectName('tuningPresetCalibrationProfile')
        self.profile_edit.setAccessibleName(_tr('本地声库校准配置 profile 路径'))
        profile_row = QWidget()
        profile_layout = QHBoxLayout(profile_row)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(4)
        profile_layout.addWidget(self.profile_edit, 1)
        profile_layout.addWidget(profile_browse)
        self.profile_row=profile_row
        self.profile_caption=_plain_label(_tr('校准 profile'))
        common.addRow(self.profile_caption, profile_row)

        self.retained_edit, retained_browse = self._path_editor(
            _tr('选择共同的原参数 SVP 工程'),
            self._browse_retained,
            _tr('原参数工程路径'),
        )
        self.retained_edit.setObjectName('tuningPresetRetainedProject')
        self.retained_edit.setAccessibleName(_tr('沿用参数共同工程路径'))
        retained_row = QWidget()
        retained_layout = QHBoxLayout(retained_row)
        retained_layout.setContentsMargins(0, 0, 0, 0)
        retained_layout.setSpacing(4)
        retained_layout.addWidget(self.retained_edit, 1)
        retained_layout.addWidget(retained_browse)
        self.retained_row=retained_row
        self.retained_caption=_plain_label(_tr('原参数工程'))
        common.addRow(self.retained_caption, retained_row)
        layout.addLayout(common)

        self.missing_text = QPlainTextEdit()
        self.missing_text.setObjectName('tuningPresetMissingInputs')
        self.missing_text.setReadOnly(True)
        self.missing_text.setUndoRedoEnabled(False)
        self.missing_text.setFocusPolicy(Qt.NoFocus)
        self.missing_text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.missing_text.setMinimumHeight(44)
        self.missing_text.setMaximumHeight(74)
        self.missing_text.setAccessibleName(_tr('待补输入，只读纯文本'))
        layout.addWidget(self.missing_text)

        self.difference_table = QTableWidget(0, 3)
        self.difference_table.setObjectName('tuningPresetDifferences')
        self.difference_table.setAccessibleName(_tr('调音参数差异，只读'))
        self.difference_table.setHorizontalHeaderLabels([
            _tr('字段'), _tr('当前'), _tr('应用后'),
        ])
        self.difference_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.difference_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.difference_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.difference_table.verticalHeader().setVisible(False)
        difference_header = self.difference_table.horizontalHeader()
        difference_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        difference_header.setSectionResizeMode(1, QHeaderView.Stretch)
        difference_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.difference_table.setMinimumHeight(94)
        self.difference_table.setMaximumHeight(180)
        layout.addWidget(self.difference_table)

        self.status = QPlainTextEdit()
        self.status.setObjectName('tuningPresetLibraryStatus')
        self.status.setReadOnly(True)
        self.status.setUndoRedoEnabled(False)
        self.status.setFocusPolicy(Qt.NoFocus)
        self.status.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.status.setMinimumHeight(42)
        self.status.setMaximumHeight(64)
        self.status.setAccessibleName(_tr('调音预设预览状态'))
        layout.addWidget(self.status)
        self.content_scroll.setWidget(content)
        body.addWidget(self.content_scroll, 1)

        actions = QHBoxLayout()
        self.refresh_button = button(_tr('刷新'), self.refresh)
        self.refresh_button.setObjectName('tuningPresetLibraryRefresh')
        self.refresh_button.setAccessibleName(_tr('刷新本机调音预设列表'))
        self.manage_button = button(_tr('管理预设'), self._open_manager)
        self.manage_button.setObjectName('tuningPresetLibraryManage')
        self.manage_button.setAccessibleName(_tr('打开本机预设管理器'))
        self.manage_button.setEnabled(manage_callback is not None)
        self.cancel_button = button(_tr('取消'), self.reject)
        self.cancel_button.setObjectName('tuningPresetLibraryCancel')
        self.cancel_button.setAccessibleName(_tr('取消应用调音预设'))
        self.apply_button = button(_tr('请选择预设'), self.accept, 'primary')
        self.apply_button.setObjectName('tuningPresetLibraryApply')
        self.apply_button.setAccessibleName(_tr('将当前调音预设提案应用到表单'))
        self.apply_button.setEnabled(False)
        self.apply_button.setDefault(True)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.manage_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.apply_button)
        body.addLayout(actions)

        self.preset_list.currentItemChanged.connect(self._preview_selected)
        self.candidate_confirm.toggled.connect(self._binding_changed)
        self.profile_edit.textChanged.connect(self._binding_changed)
        self.retained_edit.textChanged.connect(self._binding_changed)

        self._build_binding_rows()
        self._sync_project_display()
        self._sync_candidate_display()
        self._set_mode_controls('preserve')
        self.refresh()

    def showEvent(self,event):
        super().showEvent(event)
        self._fit_binding_rows()

    def _fit_binding_rows(self):
        # Cell widgets must fit the inherited theme's actual control height.
        for number in range(self.binding_table.rowCount()):
            height=44
            for column in range(self.binding_table.columnCount()):
                widget=self.binding_table.cellWidget(number,column)
                if widget is not None and not self.binding_table.isColumnHidden(column):
                    widget.ensurePolished()
                    height=max(height,widget.sizeHint().height()+12,widget.minimumSizeHint().height()+12)
            self.binding_table.setRowHeight(number,height)

    @staticmethod
    def _path_editor(placeholder: str, callback, title: str):
        edit = QLineEdit()
        edit.setClearButtonEnabled(True)
        edit.setPlaceholderText(placeholder)
        browse = button(_tr('选择…'), callback)
        browse.setAccessibleName(title)
        return edit, browse

    def _has_project(self) -> bool:
        return (
            isinstance(self.project, dict)
            and isinstance(self.project.get('sha256'), str)
            and bool(self.project.get('sha256'))
            and isinstance(self.project.get('tracks'), list)
        )

    def _tracks(self) -> list[tuple[int, dict[str, Any]]]:
        if not self._has_project():
            return []
        result = []
        for index, raw in enumerate(self.project['tracks']):
            if isinstance(raw, dict):
                result.append((raw.get('track_index', index), raw))
        return result

    def _candidate_origin(self):
        if not self._has_project():
            return None
        return self.project.get('candidate_origin')

    def _has_candidate(self) -> bool:
        return self._candidate_origin() is not None

    def _build_binding_rows(self) -> None:
        tracks = self._tracks()
        selected = set()
        raw_selected = self._initial_bindings.get('vocal_tracks', [])
        if isinstance(raw_selected, list):
            selected = {value for value in raw_selected if type(value) is int}

        reference = {}
        raw_reference = self._initial_bindings.get('reference_audio', [])
        if isinstance(raw_reference, list):
            for row in raw_reference:
                if not isinstance(row, dict) or type(row.get('track_index')) is not int:
                    continue
                voice = row.get('voice')
                path = row.get('path')
                reference[row['track_index']] = (
                    voice if voice in ('lead', 'backing', 'vocals') else None,
                    path if isinstance(path, str) else '',
                )

        retained = {}
        raw_retained = self._initial_bindings.get('retained_project', [])
        retained_projects = set()
        if isinstance(raw_retained, list):
            for row in raw_retained:
                if not isinstance(row, dict) or type(row.get('track_index')) is not int:
                    continue
                source = row.get('source_track_index')
                project_path = row.get('project')
                if isinstance(project_path, str) and project_path.strip():
                    retained_projects.add(project_path)
                retained[row['track_index']] = (
                    source if type(source) is int and source >= 0 else -1
                )
        if len(retained_projects) == 1:
            self.retained_edit.setText(next(iter(retained_projects)))
        elif len(retained_projects) > 1:
            self.retained_edit.clear()
            self.retained_edit.setPlaceholderText(
                _tr('检测到多个原参数工程；请明确选择一个共同工程')
            )

        profile = self._initial_bindings.get('calibration_profile')
        if isinstance(profile, str):
            self.profile_edit.setText(profile)

        self._syncing = True
        try:
            self.binding_table.setRowCount(len(tracks))
            for row_index, (track_index, track) in enumerate(tracks):
                instrumental = bool(track.get('instrumental'))
                target = QCheckBox()
                target.setAccessibleName(
                    _tr('选择工程轨 {0} 作为目标轨').format(track_index + 1)
                )
                target.setEnabled(not instrumental)
                target.setChecked(not instrumental and track_index in selected)
                self.binding_table.setCellWidget(row_index, 0, target)

                name = str(track.get('name') or _tr('未命名轨'))
                label = _tr('轨 {0} · {1}').format(track_index + 1, name)
                if instrumental:
                    label += ' · ' + _tr('伴奏，不可调音')
                item = QTableWidgetItem(label)
                item.setToolTip(label)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.binding_table.setItem(row_index, 1, item)

                voice = QComboBox()
                voice.addItem(_tr('未选择声部'), None)
                for value, caption in _VOICES:
                    voice.addItem(_tr(caption), value)
                voice.setAccessibleName(
                    _tr('工程轨 {0} 的声部角色；不会自动猜测').format(track_index + 1)
                )
                voice_value = reference.get(track_index, (None, ''))[0]
                voice.setCurrentIndex(voice.findData(voice_value) if voice_value else 0)
                voice.setEnabled(False)
                self.binding_table.setCellWidget(row_index, 2, voice)

                reference_cell = QWidget()
                reference_layout = QHBoxLayout(reference_cell)
                reference_layout.setContentsMargins(0, 0, 0, 0)
                reference_layout.setSpacing(4)
                path = QLineEdit(reference.get(track_index, (None, ''))[1])
                path.setClearButtonEnabled(True)
                path.setPlaceholderText(_tr('选择本地参考音频'))
                path.setAccessibleName(
                    _tr('工程轨 {0} 的本地参考音频').format(track_index + 1)
                )
                choose = button(
                    _tr('选择…'),
                    lambda _checked=False, index=track_index: self._browse_reference(index),
                )
                choose.setAccessibleName(
                    _tr('选择工程轨 {0} 的参考音频').format(track_index + 1)
                )
                choose.setEnabled(False)
                reference_layout.addWidget(path, 1)
                reference_layout.addWidget(choose)
                self.binding_table.setCellWidget(row_index, 3, reference_cell)

                source = QSpinBox()
                source.setRange(0, 2147483647)
                source.setSpecialValueText(_tr('未选择'))
                source.setValue(retained.get(track_index, -1) + 1)
                source.setAccessibleName(
                    _tr('工程轨 {0} 对应的原工程源轨号').format(track_index + 1)
                )
                source.setEnabled(False)
                self.binding_table.setCellWidget(row_index, 4, source)

                self._rows[track_index] = {
                    'target': target,
                    'voice': voice,
                    'path': path,
                    'choose': choose,
                    'source': source,
                    'name': name,
                    'instrumental': instrumental,
                }
                target.stateChanged.connect(self._binding_changed)
                voice.currentIndexChanged.connect(self._binding_changed)
                path.textChanged.connect(self._binding_changed)
                source.valueChanged.connect(self._binding_changed)
        finally:
            self._syncing = False

    def _sync_project_display(self) -> None:
        if not self._has_project():
            self.project_identity.setPlainText(_tr('尚未载入工程；只能浏览预设，不能应用。'))
            return
        path = self.project.get('path')
        sha = str(self.project.get('sha256') or '')
        lines = []
        if isinstance(path, str) and path:
            lines.append(_tr('工程：') + path)
        lines.append(_tr('SHA-256：') + sha)
        lines.append(_tr('实际目标轨映射：'))
        selected = self._selected_indices()
        if not selected:
            lines.append(_tr('尚未选择目标演唱轨'))
        for track_index, track in self._tracks():
            name = str(track.get('name') or _tr('未命名轨'))
            if track.get('instrumental'):
                role = _tr('伴奏（不可选择）')
            elif track_index in selected:
                role = self._voice_label(self._voice_for(track_index))
            else:
                role = _tr('未选为目标轨')
            lines.append(_tr('  轨 {0} · {1} · {2}').format(track_index + 1, name, role))
        self.project_identity.setPlainText('\n'.join(lines))

    def _sync_candidate_display(self) -> None:
        origin = self._candidate_origin()
        present = origin is not None
        self.candidate_confirm.setVisible(present)
        self.candidate_confirm.setEnabled(present)
        self.candidate_info.setVisible(present)
        if not present:
            self.candidate_confirm.setChecked(False)
            self.candidate_info.clear()
            return
        if isinstance(origin, dict):
            try:
                detail = _json_text(origin)
            except (TypeError, ValueError):
                detail = str(origin)
            source_kind = str(origin.get('kind') or origin.get('status') or '')
            if origin.get('verified') is False or 'unverified' in source_kind:
                kind = _tr('未核实候选')
            elif source_kind == 'tuning':
                kind = _tr('已核实调音候选')
            elif source_kind == 'style':
                kind = _tr('已核实风格候选')
            else:
                kind = source_kind or _tr('来源未核实')
        else:
            detail = str(origin)
            kind = _tr('格式未核实')
        self.candidate_info.setPlainText(
            _tr('检测到旁置候选或处理回执（{0}）。默认不确认；只有明确勾选后才会作为新基线。').format(kind)
            + '\n' + detail
        )

    def _set_mode_controls(self, mode: str) -> None:
        self._mode = mode if mode in _SOURCE_MODES else 'preserve'
        self.mode_label.setText(
            _tr('预设参数来源：') + _tr(_MODE_NAMES[self._mode])
        )
        reference_enabled = self._mode in ('reference', 'calibrated')
        retained_enabled = self._mode == 'retained'
        self.profile_edit.setEnabled(self._mode == 'calibrated')
        self.retained_edit.setEnabled(retained_enabled)
        for widget in (self.profile_caption,self.profile_row):widget.setVisible(self._mode=='calibrated')
        for widget in (self.retained_caption,self.retained_row):widget.setVisible(retained_enabled)
        self.binding_table.setColumnHidden(2,not reference_enabled)
        self.binding_table.setColumnHidden(3,not reference_enabled)
        self.binding_table.setColumnHidden(4,not retained_enabled)
        for row in self._rows.values():
            available = not row['instrumental']
            row['target'].setEnabled(available)
            row['voice'].setEnabled(available and reference_enabled)
            row['path'].setEnabled(available and reference_enabled)
            row['choose'].setEnabled(available and reference_enabled)
            row['source'].setEnabled(available and retained_enabled)
        self._fit_binding_rows()

    def _selected_indices(self) -> list[int]:
        selected = []
        valid = {index for index, track in self._tracks() if not track.get('instrumental')}
        for index in sorted(valid):
            row = self._rows.get(index)
            if row is not None and row['target'].isChecked():
                selected.append(index)
        return selected

    @staticmethod
    def _voice_label(voice: str | None) -> str:
        caption = dict(_VOICES).get(voice)
        return _tr(caption) if caption else _tr('声部未选择')

    def _voice_for(self, track_index: int) -> str | None:
        row = self._rows.get(track_index)
        if row is None:
            return None
        value = row['voice'].currentData()
        return value if value in ('lead', 'backing', 'vocals') else None

    def bindings(self) -> dict[str, Any]:
        """Return only the local bindings required by the selected source mode."""

        selected = self._selected_indices()
        result: dict[str, Any] = {'vocal_tracks': selected}
        if self._mode in ('reference', 'calibrated'):
            rows = []
            for index in selected:
                voice = self._voice_for(index)
                path = self._rows[index]['path'].text().strip()
                rows.append({'track_index': index, 'voice': voice, 'path': path})
            result['reference_audio'] = rows
        if self._mode == 'calibrated':
            result['calibration_profile'] = self.profile_edit.text().strip()
        if self._mode == 'retained':
            project_path = self.retained_edit.text().strip()
            rows = []
            for index in selected:
                source = self._rows[index]['source'].value() - 1
                rows.append({
                    'track_index': index,
                    'project': project_path,
                    'source_track_index': source,
                })
            result['retained_project'] = rows
        if self._has_candidate() and self.candidate_confirm.isChecked():
            result['confirm_candidate_baseline'] = True
        return copy.deepcopy(result)

    def _binding_missing_hints(self) -> list[str]:
        hints = []
        for index in self._selected_indices():
            if self._mode in ('reference', 'calibrated'):
                if self._voice_for(index) not in ('lead', 'backing', 'vocals'):
                    hints.append(_tr('轨 {0} 的声部尚未选择。').format(index + 1))
                if not self._rows[index]['path'].text().strip():
                    hints.append(_tr('轨 {0} 缺少本地参考音频。').format(index + 1))
            elif self._mode == 'retained':
                if not self.retained_edit.text().strip():
                    hints.append(_tr('轨 {0} 缺少共同原参数工程。').format(index + 1))
                if self._rows[index]['source'].value() == 0:
                    hints.append(_tr('轨 {0} 缺少原工程源轨号。').format(index + 1))
        if self._mode == 'calibrated' and not self.profile_edit.text().strip():
            hints.append(_tr('缺少本地声库校准 profile。'))
        return hints

    def _set_status(self, text: str, role: str) -> None:
        self.status.setPlainText(str(text))
        _set_role(self.status, role)

    def _clear_preview(self, *, preserve_status: bool = False) -> None:
        self._plan = None
        self._document = None
        self._raw_document = None
        self._preview_identity = None
        self.selected_plan = None
        self.selected_identity = None
        self.selected_pending = False
        self.difference_table.setRowCount(0)
        self.missing_text.clear()
        self._missing_display = []
        self.apply_button.setText(_tr('请选择预设'))
        self.apply_button.setEnabled(False)
        if not preserve_status:
            self._set_status(_tr('请选择一项调音预设进行预览。'), 'muted')

    def _filtered_entries(self, snapshot: Any) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('entries'), list):
            raise ValueError(_tr('本机预设库返回了无效快照。'))
        entries = []
        for raw in snapshot['entries']:
            if not isinstance(raw, dict) or raw.get('type') != 'tuning':
                continue
            identifier = raw.get('id')
            version = raw.get('version')
            sha256 = raw.get('sha256')
            builtin = raw.get('builtin')
            if not isinstance(identifier, str) or not isinstance(version, str):
                continue
            if not isinstance(sha256, str) or not sha256:
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
            self._set_status(_tr('无法刷新调音预设：') + str(error), 'error')
            return False

        self._entries = entries
        self._syncing = True
        try:
            for entry in entries:
                source = _tr('内置（只读）') if entry.get('builtin') is True else _tr('本机用户')
                text = (
                    str(entry.get('name', '')) + '\n' +
                    _tr('版本') + ' ' + str(entry.get('version', '')) +
                    '  ·  ' + source + '\n' +
                    _tr('编号') + ' ' + _short_identity(str(entry.get('id', '')))
                )
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, copy.deepcopy(entry))
                item.setForeground(self.palette().color(QPalette.Text))
                self.preset_list.addItem(item)
        finally:
            self._syncing = False
        if announce:
            self._set_status(
                _tr('已刷新：{0} 项调音预设；选择后生成差异与绑定提案。').format(len(entries)),
                'success' if entries else 'muted',
            )
        return True

    def _open_manager(self) -> None:
        if self.manage_callback is None:
            self._set_status(_tr('尚未连接本机预设管理器。'), 'muted')
            return
        failure = None
        try:
            self.manage_callback(self)
        except Exception as error:
            failure = error
        refreshed = self._reload(announce=False)
        if failure is not None:
            self._set_status(_tr('预设管理器未完成：') + str(failure), 'error')
        elif refreshed:
            self._set_status(_tr('预设列表已随管理器更新刷新；请重新选择。'), 'success')
        else:
            self._set_status(_tr('预设列表刷新失败，请手动重试。'), 'error')

    def _snapshot_from(self, expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bytes]:
        identifier = str(expected.get('id', ''))
        version = str(expected.get('version', ''))
        expected_sha = str(expected.get('sha256', ''))
        getter = getattr(self.library, 'get_snapshot', None)
        if not callable(getter):
            raise ValueError(_tr('当前预设库不支持按快照读取；请更新预设库实现。'))
        snapshot = getter(identifier, version)
        if not isinstance(snapshot, dict):
            raise ValueError(_tr('预设库返回了无效快照。'))
        entry = snapshot.get('entry')
        document = snapshot.get('document')
        raw = snapshot.get('raw')
        if not isinstance(entry, dict) or not isinstance(document, dict):
            raise ValueError(_tr('预设快照缺少身份或文档。'))
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise ValueError(_tr('预设快照没有提供精确原始字节。'))
        raw = bytes(raw)
        if (
            str(entry.get('id', '')) != identifier
            or str(entry.get('version', '')) != version
            or str(entry.get('sha256', '')) != expected_sha
        ):
            raise ValueError(_tr('预设身份在读取期间发生变化。'))
        if entry.get('type') != 'tuning' or document.get('type') != 'tuning':
            raise ValueError(_tr('所选条目不是调音预设。'))
        payload = document.get('payload')
        if not isinstance(payload, dict) or payload.get('expression_mode') not in _SOURCE_MODES:
            raise ValueError(_tr('调音预设没有有效的参数来源。'))
        return copy.deepcopy(entry), copy.deepcopy(document), raw

    def _preview_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._syncing:
            return
        self._clear_preview(preserve_status=True)
        if current is None:
            self._set_status(_tr('请选择一项调音预设进行预览。'), 'muted')
            return
        expected = current.data(Qt.UserRole)
        if not isinstance(expected, dict):
            self._set_status(_tr('所选条目的身份无效。'), 'error')
            return
        try:
            entry, document, raw = self._snapshot_from(expected)
        except Exception as error:
            self._set_status(_tr('无法读取所选调音预设：') + str(error), 'error')
            return

        self._document = document
        self._raw_document = raw
        self._preview_identity = (
            str(entry.get('id', '')),
            str(entry.get('version', '')),
            str(entry.get('sha256', '')),
        )
        payload = document.get('payload')
        self._set_mode_controls(str(payload.get('expression_mode', 'preserve')))
        self._sync_project_display()
        self._rebuild_plan()

    def _binding_changed(self, *_args) -> None:
        if self._syncing:
            return
        self._sync_project_display()
        self._rebuild_plan()

    def _rebuild_plan(self) -> None:
        self._plan = None
        self.selected_plan = None
        self.selected_identity = None
        self.selected_pending = False
        self.apply_button.setText(_tr('请选择预设'))
        self.apply_button.setEnabled(False)
        if self._document is None or self._raw_document is None:
            return
        if not self._has_project():
            self.difference_table.setRowCount(0)
            self.missing_text.setPlainText(_tr('尚未载入工程。'))
            self._set_status(_tr('可以浏览调音预设；载入工程并选择目标轨后才能应用提案。'), 'muted')
            return
        selected = self._selected_indices()
        if not selected:
            self.difference_table.setRowCount(0)
            self.missing_text.setPlainText(_tr('尚未选择目标演唱轨。'))
            self._set_status(_tr('请至少选择一个真实演唱轨；不会按名称或顺序猜测目标。'), 'muted')
            return
        try:
            _, plan_application = _adapter_api()
            plan = plan_application(
                copy.deepcopy(self._document),
                copy.deepcopy(self.project),
                self.bindings(),
                raw_document=self._raw_document,
                before=copy.deepcopy(self._before),
            )
            if not isinstance(plan, dict):
                raise ValueError(_tr('调音预设适配器没有返回方案。'))
            if not isinstance(plan.get('changes'), list) or not isinstance(plan.get('missing'), list):
                raise ValueError(_tr('调音预设方案缺少差异或缺项列表。'))
        except Exception as error:
            self.difference_table.setRowCount(0)
            self.missing_text.setPlainText(str(error))
            self._set_status(_tr('无法生成调音预设方案：') + str(error), 'error')
            return
        self._plan = copy.deepcopy(plan)
        self._render_plan(plan)
        self._render_missing(plan)
        ready = not plan['missing']
        self.apply_button.setText(_tr('确认应用') if ready else _tr('应用待补输入方案'))
        self.apply_button.setEnabled(True)
        if ready:
            self._set_status(_tr('方案已就绪；确认只返回冻结提案，尚不生成工程。'), 'success')
        else:
            self._set_status(
                _tr('方案已载入，但仍有 {0} 项输入待补。可应用到表单；补齐前不会允许生成。').format(
                    len(self._missing_display)
                ),
                'warning',
            )

    def _render_missing(self, plan: dict[str, Any]) -> None:
        missing = []
        for value in [*self._binding_missing_hints(), *plan.get('missing', [])]:
            text_value = str(value)
            if text_value not in missing:
                missing.append(text_value)
        self._missing_display = list(missing)
        if missing:
            text = _tr('待补输入：') + '\n' + '\n'.join('• ' + value for value in missing)
        else:
            text = _tr('待补输入：无。')
        self.missing_text.setPlainText(text)

    def _render_plan(self, plan: dict[str, Any]) -> None:
        labels = {
            'expression_mode': _tr('参数来源'),
            'expression_density': _tr('表情密度'),
            'reference_controls': _tr('参考参数'),
            'reference_strength': _tr('参考强度'),
            'style': _tr('风格'),
            'style.strength': _tr('风格强度'),
            'style.pitch_enabled': _tr('风格音高'),
            'style.detail': _tr('风格细节'),
            'style.ref': _tr('风格引用'),
            'technique': _tr('唱腔'),
            'technique.strength': _tr('唱腔强度'),
            'technique.pitch_enabled': _tr('唱腔音高'),
            'technique.ref': _tr('唱腔引用'),
        }
        rows = []
        for change in plan.get('changes', []):
            if not isinstance(change, dict):
                continue
            field = str(change.get('field', ''))
            caption = labels.get(field, field)
            rows.append((
                caption,
                _display_value(change.get('before')),
                _display_value(change.get('after')),
            ))
        if not rows:
            rows = [(_tr('无变化'), _tr('当前状态'), _tr('应用后状态'))]
        self.difference_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.difference_table.setItem(row_index, column, item)

    def accept(self) -> None:
        if self._document is None or self._raw_document is None:
            self.apply_button.setEnabled(False)
            self._set_status(_tr('请先选择并成功预览一项调音预设。'), 'error')
            return
        if not self._has_project() or not self._selected_indices():
            self.apply_button.setEnabled(False)
            self._set_status(_tr('无工程或没有目标演唱轨时不能应用调音预设。'), 'error')
            return
        expected = {
            'id': self._preview_identity[0] if self._preview_identity else '',
            'version': self._preview_identity[1] if self._preview_identity else '',
            'sha256': self._preview_identity[2] if self._preview_identity else '',
        }
        try:
            entry, document, raw = self._snapshot_from(expected)
            if document != self._document or raw != self._raw_document:
                raise ValueError(_tr('预设内容在预览后发生变化。'))
            _, plan_application = _adapter_api()
            plan = plan_application(
                copy.deepcopy(document),
                copy.deepcopy(self.project),
                self.bindings(),
                raw_document=raw,
                before=copy.deepcopy(self._before),
            )
            if not isinstance(plan, dict):
                raise ValueError(_tr('调音预设适配器没有返回方案。'))
            if not isinstance(plan.get('changes'), list) or not isinstance(plan.get('missing'), list):
                raise ValueError(_tr('调音预设方案缺少差异或缺项列表。'))
        except Exception as error:
            self._clear_preview(preserve_status=True)
            self._set_status(
                _tr('应用前复核失败；未应用。请刷新并重新选择：') + str(error),
                'error',
            )
            return
        self.selected_identity = {
            'id': str(entry.get('id', '')),
            'version': str(entry.get('version', '')),
            'sha256': str(entry.get('sha256', '')),
        }
        self.selected_pending = bool(plan['missing'])
        self.selected_plan = copy.deepcopy(plan)
        self._plan = copy.deepcopy(plan)
        self._render_plan(plan)
        self._render_missing(plan)
        if self.selected_pending:
            self._set_status(
                _tr('待补输入方案已确认；只更新表单，补齐输入前仍禁止生成。'),
                'warning',
            )
        else:
            self._set_status(_tr('调音预设方案已确认；尚未生成工程。'), 'success')
        super().accept()

    def _browse_profile(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, _tr('选择声库校准 profile'), self.profile_edit.text(),
            _tr('校准配置 (*.json);;所有文件 (*)'),
        )
        if path:
            self.profile_edit.setText(path)

    def _browse_retained(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, _tr('选择原参数工程'), self.retained_edit.text(),
            _tr('SVP 工程 (*.svp);;所有文件 (*)'),
        )
        if path:
            self.retained_edit.setText(path)

    def _browse_reference(self, track_index: int) -> None:
        row = self._rows.get(track_index)
        if row is None:
            return
        path, _selected = QFileDialog.getOpenFileName(
            self, _tr('选择工程轨 {0} 的参考音频').format(track_index + 1),
            row['path'].text(),
            _tr('音频 (*.wav *.flac *.mp3 *.ogg);;所有文件 (*)'),
        )
        if path:
            row['path'].setText(path)
