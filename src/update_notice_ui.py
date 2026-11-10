"""Startup update notice that only returns the user's decision."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop_theme import button
from i18n import tr as _tr
from support_info import QQ_GROUP


def _plain_label(text, role=None, object_name=None):
    value = QLabel(text)
    value.setTextFormat(Qt.PlainText)
    value.setWordWrap(True)
    if role:
        value.setProperty('role', role)
    if object_name:
        value.setObjectName(object_name)
    return value


def _value(release, name, default=None):
    try:
        return release.get(name, default)
    except AttributeError:
        return getattr(release, name, default)


def _text(value, default=''):
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


class UpdateNoticeDialog(QDialog):
    """Present an update without downloading, installing, or persisting state."""

    def __init__(self, release, current_version, parent=None, *, downloaded=False):
        super().__init__(parent)
        self.release = release
        self.downloaded = bool(downloaded)
        self.setObjectName('updateNoticeDialog')
        self.setWindowTitle(_tr('发现新版本'))
        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)
        self.setAccessibleName(_tr('程序更新提示'))

        new_version = _text(_value(release, 'version'), _tr('未知版本'))
        notes = _text(_value(release, 'notes'))
        available = bool(_value(release, 'available', False))
        update = _value(release, 'update')
        release_url = _text(_value(release, 'url'))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(11)

        heading = _plain_label(_tr('发现新版本'), 'card-title', 'headingLabel')
        layout.addWidget(heading)

        self.current_version_label = _plain_label(
            _tr('当前版本：{0}', _text(current_version, _tr('未知版本'))),
            'muted',
            'currentVersionLabel',
        )
        self.new_version_label = _plain_label(
            _tr('新版本：{0}', new_version),
            None,
            'newVersionLabel',
        )
        layout.addWidget(self.current_version_label)
        layout.addWidget(self.new_version_label)

        if self.downloaded:
            status = _tr('更新包已下载并已校验。选择“立即更新”后将继续现有安装流程。')
        elif not available:
            status = _tr('当前已是最新版本，无需更新。')
        elif update:
            status = _tr('此版本支持一键更新。选择“立即更新”后将继续现有更新流程。')
        else:
            status = _tr('此版本不支持一键更新。选择“立即更新”后会转到发布页面，请手动下载安装。')
        self.compatibility_label = _plain_label(status, 'muted', 'compatibilityLabel')
        layout.addWidget(self.compatibility_label)

        self.release_url_label = _plain_label(release_url, 'muted', 'releaseUrlLabel')
        self.release_url_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.release_url_label.setVisible(bool(release_url and not update and not self.downloaded))
        layout.addWidget(self.release_url_label)

        notes_label = _plain_label(_tr('更新说明'), None, 'notesLabel')
        layout.addWidget(notes_label)
        self.notes_view = QTextEdit()
        self.notes_view.setObjectName('notesView')
        self.notes_view.setReadOnly(True)
        self.notes_view.setAcceptRichText(False)
        self.notes_view.setUndoRedoEnabled(False)
        self.notes_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.notes_view.setTabChangesFocus(True)
        self.notes_view.setPlaceholderText(_tr('此版本未提供更新说明。'))
        self.notes_view.setAccessibleName(_tr('更新说明'))
        self.notes_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.notes_view.setMinimumHeight(96)
        self.notes_view.setPlainText(notes)
        layout.addWidget(self.notes_view, 1)

        self.group_label = _plain_label(
            _tr('QQ 群：{0}（可选择复制）', QQ_GROUP),
            'muted',
            'groupLabel',
        )
        self.group_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.group_label.setAccessibleName(_tr('QQ 群号 {0}', QQ_GROUP))
        layout.addWidget(self.group_label)

        defer_row = QHBoxLayout()
        defer_row.setSpacing(10)
        defer_label = _plain_label(_tr('暂不提醒'), 'muted', 'deferLabel')
        self.delay_combo = QComboBox()
        self.delay_combo.setObjectName('delayCombo')
        self.delay_combo.setAccessibleName(_tr('延后提醒时间'))
        self.delay_combo.addItem(_tr('下次启动'), 0)
        self.delay_combo.addItem(_tr('一小时后'), 3600)
        self.delay_combo.addItem(_tr('一天后'), 86400)
        self.delay_combo.addItem(_tr('七天后'), 604800)
        self.delay_combo.addItem(_tr('三十天后'), 2592000)
        self.delay_combo.addItem(_tr('忽略此版本'), -1)
        self.delay_combo.setCurrentIndex(0)
        defer_row.addWidget(defer_label)
        defer_row.addWidget(self.delay_combo, 1)
        layout.addLayout(defer_row)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.later_button = button(_tr('下次再说'), self.reject)
        self.later_button.setObjectName('laterButton')
        self.later_button.setAccessibleName(_tr('下次再说'))
        self.later_button.setDefault(True)
        self.later_button.setAutoDefault(True)
        self.immediate_button = button(_tr('立即更新'), self.accept, 'primary')
        self.immediate_button.setObjectName('immediateButton')
        self.immediate_button.setAccessibleName(_tr('立即更新'))
        self.immediate_button.setAutoDefault(False)
        actions.addWidget(self.later_button)
        actions.addWidget(self.immediate_button)
        layout.addLayout(actions)
        self.setTabOrder(self.delay_combo, self.later_button)
        self.setTabOrder(self.later_button, self.immediate_button)

        screen = parent.screen() if isinstance(parent, QWidget) else QApplication.primaryScreen()
        available_size = screen.availableGeometry().size() if screen is not None else None
        width_limit = available_size.width() - 48 if available_size is not None else 640
        height_limit = available_size.height() - 48 if available_size is not None else 520
        if isinstance(parent, QWidget) and parent.width() > 0:
            width_limit = min(width_limit, parent.width() - 40)
        if isinstance(parent, QWidget) and parent.height() > 0:
            height_limit = min(height_limit, parent.height() - 40)
        self.resize(max(360, min(640, width_limit)), max(320, min(520, height_limit)))

    @property
    def defer_seconds(self):
        return int(self.delay_combo.currentData())

    def showEvent(self, event):
        super().showEvent(event)
        self.later_button.setFocus(Qt.OtherFocusReason)

    def closeEvent(self, event):
        if self.result() != QDialog.Accepted:
            self.reject()
        event.accept()


__all__ = ['UpdateNoticeDialog']
