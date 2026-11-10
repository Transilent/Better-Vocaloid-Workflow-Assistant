"""Offscreen contract for the startup update notice."""

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QWidget

from support_info import QQ_GROUP
from update_notice_ui import UpdateNoticeDialog


app = QApplication.instance() or QApplication([])
html_notes = (
    '<h1 onclick="alert(1)">Update notes</h1>'
    '<script>window.location="https://example.invalid"</script>'
    '<img src=x onerror="alert(2)">'
    + '\n' + '\n'.join('Long release note line %03d' % index for index in range(500))
)


def process_events():
    app.processEvents()
    QTest.qWait(5)
    app.processEvents()


def assert_inside(dialog, widget):
    top_left = widget.mapTo(dialog, QPoint(0, 0))
    rect = QRect(top_left, widget.size())
    assert dialog.rect().contains(rect), (widget.objectName(), rect, dialog.rect())


parent = QWidget()
parent.resize(560, 430)
parent.show()
process_events()

release = {
    'version': '9.9.9',
    'notes': html_notes,
    'available': True,
    'update': {'bytes': 123456, 'download_bytes': 234567},
    'url': 'https://example.invalid/releases/v9.9.9',
}
dialog = UpdateNoticeDialog(release, '1.2.3', parent)
assert not dialog.isVisible(), 'Construction must not open or block'
assert dialog.windowModality() == Qt.WindowModal
assert dialog.delay_combo.currentIndex() == 0
assert dialog.defer_seconds == 0
assert dialog.immediate_button.text() == '立即更新'
assert dialog.later_button.text() == '下次再说'
assert dialog.later_button.isDefault()
assert not dialog.immediate_button.isDefault()
expected_names = {
    dialog: 'updateNoticeDialog',
    dialog.notes_view: 'notesView',
    dialog.delay_combo: 'delayCombo',
    dialog.group_label: 'groupLabel',
    dialog.later_button: 'laterButton',
    dialog.immediate_button: 'immediateButton',
}
for widget, name in expected_names.items():
    assert widget.objectName() == name, (widget, widget.objectName(), name)
assert '1.2.3' in dialog.current_version_label.text()
assert '9.9.9' in dialog.new_version_label.text()
assert '一键更新' in dialog.compatibility_label.text()
assert dialog.notes_view.toPlainText() == html_notes
rendered_notes = dialog.notes_view.toHtml().lower()
assert '<script' not in rendered_notes and '<img' not in rendered_notes
assert dialog.notes_view.isReadOnly()
assert not dialog.notes_view.acceptRichText()
assert QQ_GROUP in dialog.group_label.text()
assert dialog.group_label.textInteractionFlags() & Qt.TextSelectableByMouse
assert dialog.group_label.textInteractionFlags() & Qt.TextSelectableByKeyboard

expected_delays = [
    ('下次启动', 0),
    ('一小时后', 3600),
    ('一天后', 86400),
    ('七天后', 604800),
    ('三十天后', 2592000),
    ('忽略此版本', -1),
]
assert dialog.delay_combo.count() == len(expected_delays)
for index, (title, seconds) in enumerate(expected_delays):
    assert dialog.delay_combo.itemText(index) == title
    assert dialog.delay_combo.itemData(index) == seconds
    dialog.delay_combo.setCurrentIndex(index)
    assert type(dialog.defer_seconds) is int
    assert dialog.defer_seconds == seconds
dialog.delay_combo.setCurrentIndex(0)

finished = []
dialog.finished.connect(finished.append)
dialog.open()
process_events()
assert dialog.focusWidget() is dialog.later_button
assert dialog.later_button.hasFocus()
assert dialog.notes_view.verticalScrollBar().maximum() > 0, 'Long notes must scroll'

dialog.resize(430, 340)
process_events()
assert dialog.notes_view.isVisible() and dialog.notes_view.height() >= 90
for widget in (dialog.notes_view, dialog.group_label, dialog.delay_combo,
               dialog.later_button, dialog.immediate_button):
    assert widget.isVisible()
    assert_inside(dialog, widget)
later_rect = QRect(dialog.later_button.mapTo(dialog, QPoint(0, 0)), dialog.later_button.size())
immediate_rect = QRect(dialog.immediate_button.mapTo(dialog, QPoint(0, 0)), dialog.immediate_button.size())
assert not later_rect.intersects(immediate_rect)

pixmap = dialog.grab()
assert not pixmap.isNull()
assert pixmap.width() > 0 and pixmap.height() > 0

QTest.keyClick(dialog, Qt.Key_Escape)
process_events()
assert dialog.result() == QDialog.Rejected
assert finished[-1] == QDialog.Rejected

close_dialog = UpdateNoticeDialog(release, '1.2.3', parent)
close_finished = []
close_dialog.finished.connect(close_finished.append)
close_dialog.open()
process_events()
close_dialog.close()
process_events()
assert close_dialog.result() == QDialog.Rejected
assert close_finished[-1] == QDialog.Rejected

enter_dialog = UpdateNoticeDialog(release, '1.2.3', parent)
enter_dialog.open()
process_events()
assert enter_dialog.focusWidget() is enter_dialog.later_button
QTest.keyClick(enter_dialog.later_button, Qt.Key_Return)
process_events()
assert enter_dialog.result() == QDialog.Rejected

later_dialog = UpdateNoticeDialog(release, '1.2.3', parent)
later_dialog.open()
process_events()
QTest.mouseClick(later_dialog.later_button, Qt.LeftButton)
process_events()
assert later_dialog.result() == QDialog.Rejected

accept_dialog = UpdateNoticeDialog(release, '1.2.3', parent)
accepted = []
accept_dialog.finished.connect(accepted.append)
accept_dialog.open()
process_events()
QTest.mouseClick(accept_dialog.immediate_button, Qt.LeftButton)
process_events()
assert accept_dialog.result() == QDialog.Accepted
assert accepted[-1] == QDialog.Accepted
assert dialog.release['url'] == release['url']

downloaded = UpdateNoticeDialog(release, '1.2.3', parent, downloaded=True)
status = downloaded.findChild(QLabel, 'compatibilityLabel').text()
assert '已下载' in status and '已校验' in status
assert not downloaded.release_url_label.isVisible()

fallback_release = {
    'version': '10.0.0',
    'notes': 'Manual download required',
    'available': True,
    'update': None,
    'url': 'https://example.invalid/releases/v10.0.0',
}
fallback = UpdateNoticeDialog(fallback_release, '1.2.3', parent)
fallback.text = fallback.compatibility_label.text()
assert '不支持一键更新' in fallback.text and '发布页面' in fallback.text
assert fallback.release_url_label.text() == fallback_release['url']
assert fallback.release_url_label.isVisibleTo(fallback)

old_font = app.font()
larger_font = QFont(old_font)
larger_font.setPointSizeF(max(12.0, old_font.pointSizeF() + 2.0))
app.setFont(larger_font)
try:
    large_font_dialog = UpdateNoticeDialog(release, '1.2.3', parent)
    large_font_dialog.resize(430, 340)
    large_font_dialog.open()
    process_events()
    assert large_font_dialog.later_button.isVisible()
    assert large_font_dialog.immediate_button.isVisible()
    assert_inside(large_font_dialog, large_font_dialog.later_button)
    assert_inside(large_font_dialog, large_font_dialog.immediate_button)
    large_font_dialog.close()
finally:
    app.setFont(old_font)

parent.close()
process_events()
print('PASS: offscreen update notice text safety, deferral, focus, keyboard and dialog outcomes')
