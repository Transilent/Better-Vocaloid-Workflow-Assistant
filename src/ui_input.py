"""Application-wide mouse-wheel protection for option and numeric inputs."""
import atexit

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollArea,
    QScrollBar,
    QWidget,
)


_GUARDS = []


def remove_input_wheel_guards():
    """Detach Python virtual callbacks before Qt/Python shutdown teardown."""
    for guard in tuple(_GUARDS):
        try:
            application=guard.parent()
            if application is not None:application.removeEventFilter(guard)
        except RuntimeError:
            pass
    _GUARDS.clear()


atexit.register(remove_input_wheel_guards)


def install_input_wheel_guard(application=None):
    """Install the wheel guard once and return its retained filter object."""
    application = application or QApplication.instance()
    if application is None:
        raise RuntimeError("A QApplication is required before installing the input wheel guard")

    for guard in tuple(_GUARDS):
        try:
            if guard.parent() is application:
                return guard
        except RuntimeError:
            _GUARDS.remove(guard)

    guard = _InputWheelGuard(application)
    guard.setObjectName("vwa_input_wheel_guard")
    application.installEventFilter(guard)
    application.aboutToQuit.connect(remove_input_wheel_guards)
    _GUARDS.append(guard)
    return guard


class _InputWheelGuard(QObject):
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel or not isinstance(watched, QWidget):
            return False

        popup_combo = _popup_combo_for(watched)
        if popup_combo is not None:
            _scroll_combo_popup(popup_combo, event)
            event.accept()
            return True

        control = _nearest_value_control(watched)
        if control is None:
            return False

        scroll_area = _nearest_scroll_area(control)
        if scroll_area is not None:
            QApplication.sendEvent(scroll_area.viewport(), _copy_wheel_event(event))
        event.accept()
        return True


def _nearest_value_control(widget):
    current = widget
    while isinstance(current, QWidget):
        if isinstance(current, (QComboBox, QAbstractSpinBox)):
            return current
        if isinstance(current, QAbstractSlider) and not isinstance(current, QScrollBar):
            return current
        parent = current.parent()
        current = parent if isinstance(parent, QWidget) else None
    return None


def _popup_combo_for(widget):
    current = widget
    while isinstance(current, QWidget):
        if isinstance(current, QComboBox):
            try:
                view = current.view()
                if view is not None and view.isVisible():
                    popup = view.window()
                    if popup is not None and (
                        widget is popup or popup.isAncestorOf(widget) or view.isAncestorOf(widget)
                    ):
                        return current
            except RuntimeError:
                pass
        if isinstance(current, QScrollArea):
            break
        parent = current.parent()
        current = parent if isinstance(parent, QWidget) else None
    return None


def _scroll_combo_popup(combo, event):
    view = combo.view()
    scrollbar = view.verticalScrollBar()
    pixel_delta = event.pixelDelta().y()
    if pixel_delta:
        row_height = max(1, view.sizeHintForRow(0))
        steps = int(round(-pixel_delta / row_height))
    else:
        angle_delta = event.angleDelta().y()
        steps = int(round(-angle_delta * 3 / 120.0))
        if angle_delta and not steps:
            steps = -1 if angle_delta > 0 else 1
    if steps:
        scrollbar.setValue(scrollbar.value() + steps)


def _nearest_scroll_area(control):
    current = control.parent()
    while isinstance(current, QWidget):
        if isinstance(current, QAbstractScrollArea):
            return current
        current = current.parent()
    return None


def _copy_wheel_event(event):
    return QWheelEvent(
        event.pos(),
        event.globalPos(),
        event.pixelDelta(),
        event.angleDelta(),
        event.buttons(),
        event.modifiers(),
        event.phase(),
        event.inverted(),
        event.source(),
    )
