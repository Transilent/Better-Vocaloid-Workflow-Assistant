"""Offscreen acceptance for the standalone onboarding overlay."""

import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src'
sys.path.insert(0, str(SRC))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from onboarding_ui import OnboardingTour
from support_info import QQ_GROUP


class StubNavigation:
    def __init__(self, owner):
        self.owner = owner
        self._current = None
        self._widgets = {}
        self._buttons = {}

    def add(self, page_id, widget, button):
        self._widgets[page_id] = widget
        self._buttons[page_id] = button

    def current_page_id(self):
        return self._current

    def widget(self, page_id):
        return self._widgets[page_id]

    def button(self, page_id):
        return self._buttons.get(page_id)


class StubWindow(QWidget):
    PAGE_IDS = (
        'processing', 'components', 'tuning', 'audio_mixdown',
        'video_mixdown', 'publishing', 'updates',
    )

    def __init__(self):
        super().__init__()
        self.resize(1200, 800)
        self.setObjectName('stubWorkspace')
        self._vwa_appearance = {'theme': 'light', 'material': 'solid', 'ui_scale': 100}
        self.navigation = StubNavigation(self)
        self.calls = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        sidebar = QFrame(self)
        sidebar.setObjectName('stubSidebar')
        sidebar.setFixedWidth(210)
        side_layout = QVBoxLayout(sidebar)
        outer.addWidget(sidebar)
        self.pages = QStackedWidget(self)
        outer.addWidget(self.pages, 1)
        for page_id in self.PAGE_IDS:
            page = QFrame(self.pages)
            page.setObjectName('page-' + page_id)
            layout = QVBoxLayout(page)
            layout.addWidget(QLabel(page_id, page))
            setattr(self, page_id, page)
            self.pages.addWidget(page)
            button = QPushButton(page_id, sidebar)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, target=page_id: self.navigate(target))
            side_layout.addWidget(button)
            self.navigation.add(page_id, page, button)
        side_layout.addStretch(1)

        processing_layout = self.processing.layout()
        self.lower_edit = QLineEdit(self.processing)
        self.lower_edit.setObjectName('stubLowerEdit')
        processing_layout.addWidget(self.lower_edit)
        self.lower_button = QPushButton('lower action', self.processing)
        self.lower_button.setObjectName('stubLowerButton')
        self.lower_clicks = []
        self.lower_button.clicked.connect(lambda: self.lower_clicks.append(True))
        processing_layout.addWidget(self.lower_button)
        self.processing_hits = []
        self.processing.mousePressEvent = lambda event: self.processing_hits.append(True)
        self.navigate('processing')

    def navigate(self, page_id):
        if page_id not in self.navigation._widgets:
            raise KeyError(page_id)
        self.calls.append(page_id)
        self.pages.setCurrentWidget(self.navigation.widget(page_id))
        self.navigation._current = page_id


class BrokenWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(900, 620)
        self._vwa_appearance = {'theme': 'light', 'material': 'solid', 'ui_scale': 100}
        self.navigation = StubNavigation(self)

    def current_page_id(self):
        return 'processing'

    def navigate(self, page_id):
        raise KeyError(page_id)


def child(root, name):
    found = root.findChild(QWidget, name)
    assert found is not None, name
    return found


def assert_inside_card(tour, card):
    assert tour.isVisible()
    assert card.isVisible()
    assert tour.rect().contains(card.geometry()), (tour.rect(), card.geometry())
    assert card.width() > 250 and card.height() > 220


app = QApplication.instance() or QApplication([])
app.setStyle('Fusion')
app.setFont(QFont('Microsoft YaHei', 10))
window = StubWindow()
window.show()
app.processEvents()

finished = []
tour = OnboardingTour(window)
tour.finished.connect(finished.append)
window.onboarding = tour
QTest.mouseClick(window.lower_edit, Qt.LeftButton)
assert window.lower_edit.hasFocus()

window.navigate('processing')
tour.start()
app.processEvents()
assert tour.active and tour.isVisible()
assert window.calls[-1] == 'processing'
assert child(tour, 'onboardingSkip').isVisible()
assert not child(tour, 'onboardingPrevious').isEnabled()

lower_point = window.lower_button.mapTo(window, window.lower_button.rect().center())
hit = window.childAt(lower_point)
assert hit is not None
assert hit is tour or tour.isAncestorOf(hit), hit
assert not window.lower_edit.hasFocus()
for _ in range(8):
    QTest.keyClick(tour, Qt.Key_Tab)
    app.processEvents()
    assert tour.isAncestorOf(app.focusWidget()) or app.focusWidget() is tour
QTest.mouseClick(hit, Qt.LeftButton, pos=hit.mapFrom(window, lower_point))
QTest.keyClicks(hit, 'blocked')
app.processEvents()
assert window.lower_clicks == []
assert window.lower_edit.text() == ''
assert not window.processing_hits
assert not window.lower_edit.hasFocus()

QTest.keyClick(tour, Qt.Key_Right)
app.processEvents()
assert window.navigation.current_page_id() == 'components'
tour.previous_step()
assert window.navigation.current_page_id() == 'processing'
window.calls.clear()
expected_pages = list(window.PAGE_IDS)
while window.navigation.current_page_id() != 'updates':
    QTest.keyClick(tour, Qt.Key_Right)
    app.processEvents()
assert window.calls == expected_pages[1:]
assert child(tour, 'onboardingNext').text() == '完成'
assert child(tour, 'onboardingSupport').isVisible()
assert QQ_GROUP in child(tour, 'onboardingQQGroup').text()
assert child(tour, 'onboardingSkip').isVisible()
assert tour.spotlight_rect.isValid() and not tour.spotlight_rect.isEmpty()
assert tour.rect().contains(tour.spotlight_rect)
QTest.mouseClick(child(tour, 'onboardingNext'), Qt.LeftButton)
app.processEvents()
assert finished == ['completed']
assert not tour.active and not tour.isVisible()
assert window.navigation.current_page_id() == 'processing'
assert window.calls[-1] == 'processing'
QTest.mouseClick(window.lower_button, Qt.LeftButton)
assert window.lower_clicks == [True]

window.navigate('updates')
tour.start()
app.processEvents()
assert tour.active and window.navigation.current_page_id() == 'processing'
QTest.keyClick(tour, Qt.Key_Escape)
app.processEvents()
assert finished == ['completed', 'skipped']
assert not tour.active and window.navigation.current_page_id() == 'updates'

window.navigate('updates')
tour.start()
QTest.keyClick(tour, Qt.Key_Right)
QTest.keyClick(tour, Qt.Key_Right)
assert window.navigation.current_page_id() == 'tuning'
calls_before_dismiss = list(window.calls)
tour.dismiss()
app.processEvents()
assert finished == ['completed', 'skipped', 'closed']
assert not tour.active and not tour.isVisible()
assert window.navigation.current_page_id() == 'tuning'
assert window.calls == calls_before_dismiss
tour.dismiss()
assert finished == ['completed', 'skipped', 'closed']

tour.start()
assert tour.active
calls_before_refocus = list(window.calls)
tour.start()
assert tour.active and window.calls == calls_before_refocus
tour.skip()
app.processEvents()
assert finished == ['completed', 'skipped', 'closed', 'skipped']
assert window.navigation.current_page_id() == 'tuning'
tour.next_step()
tour.skip()
tour.dismiss()
assert finished == ['completed', 'skipped', 'closed', 'skipped']
tour.deleteLater()
app.processEvents()

long_body = '\n'.join(
    '第 {0} 段：这里是一段用于验证长文滚动、不同字体缩放和窗口尺寸变化的教程说明。'.format(index + 1)
    for index in range(90)
)
long_tour = OnboardingTour(window, steps=(
    {'page': 'processing', 'title': '短标题', 'body': long_body},
    {'page': 'updates', 'title': '长文与社区支持', 'body': long_body},
))
long_finished = []
long_tour.finished.connect(long_finished.append)
window.onboarding = long_tour
window._vwa_appearance = {'theme': 'light', 'material': 'solid', 'ui_scale': 100}
app.setProperty('vwa-theme', 'light')
app.setProperty('vwa-material', 'solid')
long_tour.start()
app.processEvents()
long_tour.next_step()
app.processEvents()
title = child(long_tour, 'onboardingTitle')
card = child(long_tour, 'onboardingCard')
body = child(long_tour, 'onboardingBody')
assert_inside_card(long_tour, card)
assert body.verticalScrollBar().maximum() > 0
assert child(long_tour, 'onboardingSupport').isVisible()

font_heights = {}
styles = {}
for mode, scale, size in (
    ('light', 100, (1000, 680)),
    ('dark', 115, (1280, 800)),
    ('dark', 130, (1000, 680)),
    ('light', 130, (1500, 950)),
):
    app.setProperty('vwa-theme', mode)
    app.setProperty('vwa-material', 'solid')
    window._vwa_appearance = {'theme': mode, 'material': 'solid', 'ui_scale': scale}
    window.resize(*size)
    QCoreApplication.sendEvent(window, QEvent(QEvent.StyleChange))
    QCoreApplication.sendEvent(window, QEvent(QEvent.FontChange))
    app.processEvents()
    assert long_tour.geometry() == window.rect()
    assert_inside_card(long_tour, card)
    assert long_tour.rect().contains(long_tour.spotlight_rect)
    assert body.verticalScrollBar().maximum() > 0
    font_heights[(mode, scale)] = title.fontMetrics().height()
    styles[(mode, scale)] = long_tour.styleSheet()
assert font_heights[('light', 130)] > font_heights[('light', 100)]
assert styles[('dark', 115)] != styles[('dark', 130)]
long_tour.skip()
app.processEvents()
assert long_finished == ['skipped']
long_tour.deleteLater()
app.processEvents()

broken = BrokenWindow()
broken.show()
app.processEvents()
fallback = OnboardingTour(broken, steps=(
    {'page': 'not_a_page', 'title': '缺失锚点', 'body': '这里仍应显示遮罩和整窗锚点。'},
))
fallback_finished = []
fallback.finished.connect(fallback_finished.append)
fallback.start()
app.processEvents()
assert fallback.active and fallback.isVisible()
assert fallback.spotlight_rect.isValid() and not fallback.spotlight_rect.isEmpty()
assert fallback.rect().contains(child(fallback, 'onboardingCard').geometry())
fallback.skip()
app.processEvents()
assert fallback_finished == ['skipped']
fallback.deleteLater()
broken.close()
broken.deleteLater()

window.close()
window.deleteLater()
app.processEvents()
print('PASS: onboarding spotlight, blocking hit-test, page navigation/restore, keyboard, resize/theme/font, long text, fallback and single-signal sessions; offscreen')
