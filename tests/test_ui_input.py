"""Offscreen regression tests for application-wide input wheel protection."""

import gc
import os
import sys
import unittest
import weakref
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "src"))

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui_input import install_input_wheel_guard


class InputWheelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.guard = install_input_wheel_guard(cls.application)

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.application.processEvents()

    def keep(self, widget):
        self.widgets.append(widget)
        return widget

    def send_wheel(self, widget, angle_dy=-120, pixel_dy=0):
        position = widget.rect().center()
        event = QWheelEvent(
            QPointF(position),
            QPointF(widget.mapToGlobal(position)),
            QPoint(0, pixel_dy),
            QPoint(0, angle_dy),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(widget, event)
        self.application.processEvents()
        return event

    def scroll_page(self, control):
        root = self.keep(QWidget())
        layout = QVBoxLayout(root)
        page = QScrollArea(root)
        page.setWidgetResizable(False)
        content = QWidget()
        content.setMinimumSize(360, 1200)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(control)
        content_layout.addStretch(1)
        page.setWidget(content)
        page.setFixedHeight(120)
        layout.addWidget(page)
        root.resize(420, 220)
        root.show()
        self.application.processEvents()
        self.assertGreater(page.verticalScrollBar().maximum(), 0)
        return root, page

    def test_install_is_idempotent_and_retained(self):
        first = install_input_wheel_guard(self.application)
        second = install_input_wheel_guard(self.application)
        self.assertIs(first, second)
        self.assertIs(first.parent(), self.application)
        self.assertEqual(sum(child is first for child in self.application.children()), 1)
        self.assertEqual(first.objectName(), "vwa_input_wheel_guard")

        reference = weakref.ref(first)
        del first
        gc.collect()
        self.assertIsNotNone(reference())
        self.assertIs(second.parent(), self.application)

    def test_focused_combo_wheel_does_not_change_value_or_block_page_scroll(self):
        combo = self.keep(QComboBox())
        combo.addItems(["one", "two", "three"])
        combo.setCurrentIndex(1)
        _, page = self.scroll_page(combo)
        combo.setFocus()
        self.application.processEvents()

        before_value = combo.currentIndex()
        before_scroll = page.verticalScrollBar().value()
        self.send_wheel(combo)

        self.assertEqual(combo.currentIndex(), before_value)
        self.assertGreater(page.verticalScrollBar().value(), before_scroll)

    def test_combo_editor_wheel_is_blocked_and_page_still_scrolls(self):
        combo = self.keep(QComboBox())
        combo.setEditable(True)
        combo.addItems(["one", "two", "three"])
        combo.setCurrentIndex(1)
        _, page = self.scroll_page(combo)
        combo.lineEdit().setFocus()
        self.application.processEvents()

        before_text = combo.currentText()
        before_scroll = page.verticalScrollBar().value()
        self.send_wheel(combo.lineEdit())

        self.assertEqual(combo.currentText(), before_text)
        self.assertGreater(page.verticalScrollBar().value(), before_scroll)

    def test_spinboxes_and_internal_editors_ignore_wheel(self):
        cases = (
            (QSpinBox, 20),
            (QDoubleSpinBox, 20.5),
        )
        for spin_type, initial in cases:
            with self.subTest(spin_type=spin_type.__name__, target="control"):
                spin = self.keep(spin_type())
                spin.setRange(-1000, 1000)
                spin.setValue(initial)
                _, page = self.scroll_page(spin)
                spin.setFocus()
                self.application.processEvents()

                before_scroll = page.verticalScrollBar().value()
                self.send_wheel(spin)
                self.assertEqual(spin.value(), initial)
                self.assertGreater(page.verticalScrollBar().value(), before_scroll)

            with self.subTest(spin_type=spin_type.__name__, target="editor"):
                spin = self.keep(spin_type())
                spin.setRange(-1000, 1000)
                spin.setValue(initial)
                _, page = self.scroll_page(spin)
                spin.lineEdit().setFocus()
                self.application.processEvents()

                before_scroll = page.verticalScrollBar().value()
                self.send_wheel(spin.lineEdit())
                self.assertEqual(spin.value(), initial)
                self.assertGreater(page.verticalScrollBar().value(), before_scroll)

    def test_popup_wheel_browses_without_committing_selection(self):
        combo = self.keep(QComboBox())
        combo.addItems([f"item {index}" for index in range(100)])
        combo.setCurrentIndex(0)
        root = self.keep(QWidget())
        layout = QVBoxLayout(root)
        layout.addWidget(combo)
        root.resize(320, 180)
        root.show()
        self.application.processEvents()

        combo.showPopup()
        self.application.processEvents()
        QTest.qWait(20)
        view = combo.view()
        scrollbar = view.verticalScrollBar()
        self.assertTrue(view.isVisible())
        self.assertGreater(scrollbar.maximum(), 0)

        before_index = combo.currentIndex()
        before_text = combo.currentText()
        before_scroll = scrollbar.value()
        self.send_wheel(view.viewport())

        self.assertGreater(scrollbar.value(), before_scroll)
        self.assertEqual(combo.currentIndex(), before_index)
        self.assertEqual(combo.currentText(), before_text)
        self.assertTrue(view.isVisible())
        combo.hidePopup()
        self.application.processEvents()
        self.assertEqual(combo.currentIndex(), before_index)

    def test_mouse_clicks_and_keyboard_still_change_values(self):
        combo = self.keep(QComboBox())
        combo.addItems([f"item {index}" for index in range(20)])
        combo.setCurrentIndex(0)
        root = self.keep(QWidget())
        layout = QVBoxLayout(root)
        layout.addWidget(combo)
        root.resize(320, 180)
        root.show()
        self.application.processEvents()

        combo.showPopup()
        self.application.processEvents()
        view = combo.view()
        self.assertTrue(view.isVisible())
        target_index = 3
        model_index = combo.model().index(target_index, 0)
        view.scrollTo(model_index)
        self.application.processEvents()
        item_center = view.visualRect(model_index).center()
        QTest.mousePress(view.viewport(), Qt.LeftButton, pos=item_center)
        self.application.processEvents()
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=item_center)
        self.application.processEvents()
        self.assertEqual(combo.currentIndex(), target_index)
        self.assertFalse(view.isVisible())

        combo.setFocus()
        QTest.keyClick(combo, Qt.Key_Down)
        self.application.processEvents()
        self.assertEqual(combo.currentIndex(), target_index + 1)

        spin = self.keep(QSpinBox())
        spin.setRange(0, 100)
        spin.setValue(10)
        layout.addWidget(spin)
        spin.setFocus()
        QTest.keyClick(spin, Qt.Key_Up)
        self.application.processEvents()
        self.assertEqual(spin.value(), 11)

    def test_slider_wheel_does_not_change_value_and_plain_page_scroll_remains(self):
        slider = self.keep(QSlider(Qt.Horizontal))
        slider.setRange(0, 100)
        slider.setValue(25)
        _, page = self.scroll_page(slider)
        slider.setFocus()
        self.application.processEvents()

        before_scroll = page.verticalScrollBar().value()
        self.send_wheel(slider)
        self.assertEqual(slider.value(), 25)
        self.assertGreater(page.verticalScrollBar().value(), before_scroll)

        page.verticalScrollBar().setValue(0)
        self.send_wheel(page.viewport())
        self.assertGreater(page.verticalScrollBar().value(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
