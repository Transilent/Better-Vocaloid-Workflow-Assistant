"""Slider adapters for exact 0..1 strength values.

The combo or spin box remains the real value owner.  The slider only exposes a
one-percent position, so legacy fractions such as .625 survive an untouched
load instead of being rounded to .62 or .63.
"""
from __future__ import annotations
from i18n import tr as _tr

import math

from PyQt5.QtCore import QEvent, QSignalBlocker, Qt
from PyQt5.QtWidgets import QDoubleSpinBox,QHBoxLayout,QSlider,QWidget


SLIDER_MINIMUM=0
SLIDER_MAXIMUM=100


def normalized_strength(value):
    """Return a finite 0..1 float, or None when the value is not valid."""
    if type(value) not in (int,float) or isinstance(value,bool):
        return None
    result=float(value)
    return result if math.isfinite(result) and 0<=result<=1 else None


def percent_number(value):
    """Format a 0..1 value as a percentage without losing useful decimals."""
    value=normalized_strength(value)
    if value is None:return ''
    percent=value*100
    if percent.is_integer():return str(int(percent))
    return f'{percent:.6f}'.rstrip('0').rstrip('.')


def percent_text(value):
    number=percent_number(value)
    return number+'%' if number else ''


def slider_position(value):
    """Map the owning value to the nearest integer-percent slider position."""
    value=normalized_strength(value)
    if value is None:return SLIDER_MINIMUM
    return max(SLIDER_MINIMUM,min(SLIDER_MAXIMUM,int(round(value*100))))


def exact_percent(value):
    """Round only an explicit percentage edit to four decimal places."""
    return round(float(value),4)


def strength_choices(values=(.3,.6,.7,1.)):
    """Keep historical choices only as hidden model values."""
    return [(percent_text(value),value) for value in values]


def set_combo_strength(combo,value):
    """Select an exact value in the combo without rounding its legacy data.

    One reusable custom row avoids accumulating an item for every slider drag.
    If that row is already current, its value is replaced in place and the combo
    emits currentIndexChanged so persistence sees the actual user update.
    """
    value=normalized_strength(value)
    if value is None:return False
    index=combo.findData(value)
    if index<0:
        custom=getattr(combo,'_vwa_strength_custom_index',None)
        if type(custom) is int and 0<=custom<combo.count():
            index=custom
            same_current=combo.currentIndex()==index
            combo.setItemText(index,_tr('自定义 · ') + percent_text(value))
            combo.setItemData(index,value)
            if same_current:combo.currentIndexChanged.emit(index)
        else:
            combo.addItem(_tr('自定义 · ') + percent_text(value),value)
            index=combo.count()-1
            combo._vwa_strength_custom_index=index
    combo.setCurrentIndex(index)
    return True


class NoWheelSlider(QSlider):
    """Keep wheel gestures available to the page, never to the strength value."""

    def wheelEvent(self,event):
        event.ignore()


class ExactStrengthSpinBox(QDoubleSpinBox):
    """Precise percentage input with disabled wheel changes."""

    def __init__(self,parent=None,minimum=SLIDER_MINIMUM):
        super().__init__(parent)
        self.setRange(max(SLIDER_MINIMUM,min(SLIDER_MAXIMUM,int(minimum))),SLIDER_MAXIMUM)
        self.setDecimals(4)
        self.setSingleStep(1)
        self.setSuffix(' %')
        self.setKeyboardTracking(False)
        self.setAccelerated(False)
        self.setAlignment(Qt.AlignRight)

    def wheelEvent(self,event):
        event.ignore()

    def textFromValue(self, value):
        # Keep 60% compact while still showing restored fractions such as 62.5%.
        return self.locale().toString(value, 'f', 4).rstrip('0').rstrip(self.locale().decimalPoint())


class PercentStrengthControl(QWidget):
    """A 1% slider paired with an exact percentage spin box.

    ``spin`` owns the value.  The slider is synchronised without signals, so an
    exact 62.5% legacy value stays 62.5% until the user actually moves the
    slider or commits another numeric input.
    """

    def __init__(self,value=60,on_change=None,parent=None,minimum=SLIDER_MINIMUM):
        super().__init__(parent)
        self.minimum_percent=max(SLIDER_MINIMUM,min(SLIDER_MAXIMUM,int(minimum)))
        self.slider=NoWheelSlider(Qt.Horizontal,self)
        self.slider.setRange(self.minimum_percent,SLIDER_MAXIMUM)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTracking(True)
        self.slider.setToolTip(_tr('拖动滑条按 1% 调整；滚轮用于滚动页面。'))
        self.slider.setAccessibleName(_tr('强度百分比'))
        self.spin=ExactStrengthSpinBox(self,self.minimum_percent)
        self.spin.setMinimumWidth(94)
        self.spin.setToolTip(_tr('可输入精确百分比；上下按钮按 1% 调整。'))
        layout=QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(10)
        layout.addWidget(self.slider,1)
        layout.addWidget(self.spin)
        self._syncing=False
        self._on_change=on_change
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.set_percent(float(value))

    def value(self):
        return self.spin.value()

    def set_percent(self,value,notify=False):
        value=max(self.minimum_percent,min(SLIDER_MAXIMUM,float(value)))
        self._syncing=True
        try:
            blockers=(QSignalBlocker(self.spin),QSignalBlocker(self.slider))
            self.spin.setValue(exact_percent(value))
            self.slider.setValue(slider_position(value/100.))
            del blockers
        finally:self._syncing=False
        if notify and self._on_change is not None:self._on_change(self.spin.value())

    def _from_slider(self,position):
        if self._syncing:return
        self._syncing=True
        try:self.spin.setValue(float(position))
        finally:self._syncing=False
        if self._on_change is not None:self._on_change(self.spin.value())

    def _from_spin(self,value):
        if self._syncing:return
        blocker=QSignalBlocker(self.slider)
        self.slider.setValue(slider_position(float(value)/100.))
        del blocker
        if self._on_change is not None:self._on_change(float(value))


class StrengthControl(QWidget):
    """Visual slider and exact input for a legacy float-owning combo.

    The hidden combo remains the sole model/value owner.  It retains the
    original ``currentData``, ``findData`` and ``currentIndexChanged`` API.
    The adapter also exposes empty ``buttons`` plus ``sync``/``reflow`` for the
    existing ``_choice_views`` compatibility loop.
    """

    buttons=()

    def __init__(self,combo,parent=None):
        super().__init__(parent)
        self.combo=combo
        combo.setParent(self)
        combo.hide()
        combo.setFocusPolicy(Qt.NoFocus)
        self.editor=PercentStrengthControl(60,self._from_editor,self)
        self.slider=self.editor.slider
        self.spin=self.editor.spin
        layout=QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.editor)
        combo.currentIndexChanged.connect(self.sync)
        combo.installEventFilter(self)
        self.sync()

    def _from_editor(self,value):
        if not self.combo.isEnabled():
            self.sync()
            return
        set_combo_strength(self.combo,exact_percent(value)/100.)

    def sync(self,*args):
        value=normalized_strength(self.combo.currentData())
        if value is None:
            self.editor.setEnabled(False)
            return
        self.editor.setEnabled(self.combo.isEnabled())
        self.editor.set_percent(value*100.)
        self.editor.setToolTip(_tr('当前精确值：') + percent_text(value) + _tr('。滑条按 1% 调整。'))

    def reflow(self):
        pass

    def eventFilter(self,watched,event):
        if watched is self.combo and event.type() in (QEvent.EnabledChange,QEvent.Show):self.sync()
        return False

    def currentData(self):
        return self.combo.currentData()

    def findData(self,value):
        return self.combo.findData(value)

    def currentIndex(self):
        return self.combo.currentIndex()

    def setCurrentIndex(self,index):
        return self.combo.setCurrentIndex(index)

    @property
    def currentIndexChanged(self):
        return self.combo.currentIndexChanged
