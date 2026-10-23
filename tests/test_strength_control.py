"""Pure percentage normalization and isolated slider adapter contracts."""
import os
import sys
from pathlib import Path

os.environ['QT_QPA_PLATFORM']='offscreen'
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from PyQt5.QtCore import QPoint,QPointF,Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication,QComboBox
from strength_control import (PercentStrengthControl,StrengthControl,normalized_strength,
    percent_text,set_combo_strength,slider_position,strength_choices)

application=QApplication.instance() or QApplication([])
assert normalized_strength(.625)==.625
assert normalized_strength(1.)==1.
assert normalized_strength(0.)==0.
for value in (None,True,-.1,1.01,float('nan'),float('inf'),'0.5'):
    assert normalized_strength(value) is None,value
assert percent_text(.625)=='62.5%'
assert percent_text(.6)=='60%'
assert percent_text(.5712345)=='57.12345%'
assert slider_position(.625)==62
assert slider_position(0)==0
assert slider_position(1)==100
assert strength_choices((.3,.6))==[('30%',.3),('60%',.6)]

combo=QComboBox()
for title,value in strength_choices((.3,.6,.7,1.)):combo.addItem(title,value)
control=StrengthControl(combo)
changes=[];combo.currentIndexChanged.connect(changes.append)
forwarded=[];control.currentIndexChanged.connect(forwarded.append)
assert control.combo is combo and control.buttons==()
assert control.slider.minimum()==0 and control.slider.maximum()==100 and control.slider.singleStep()==1
assert control.spin.minimum()==0 and control.spin.maximum()==100 and control.spin.singleStep()==1

# Legacy precision survives construction, combo population and repeated sync.
assert set_combo_strength(combo,.625)
assert combo.currentData()==.625 and combo.findData(.625)>=0
assert control.spin.value()==62.5 and control.slider.value()==62
count=combo.count();changes_before=len(changes)
for _ in range(5):control.sync()
assert combo.currentData()==.625 and combo.count()==count and len(changes)==changes_before

# Zero is a supported legacy bypass and remains exact through untouched syncs.
set_combo_strength(combo,0)
assert combo.currentData()==0 and control.spin.value()==0 and control.slider.value()==0
for _ in range(3):control.sync()
assert combo.currentData()==0

# Slider interaction changes only on a real 1% move and reuses one custom row.
control.slider.setValue(63)
assert combo.currentData()==.63 and combo.count()==count
assert changes[-1]==combo.currentIndex()==forwarded[-1] and control.spin.value()==63
control.slider.setValue(42)
assert combo.currentData()==.42 and combo.count()==count and control.spin.value()==42

# Exact numeric restoration remains possible; the slider merely rounds its thumb.
control.spin.setValue(62.5)
assert combo.currentData()==.625 and control.slider.value()==62
set_combo_strength(combo,.571)
assert combo.currentData()==.571 and control.spin.value()==57.1 and control.slider.value()==57

# Enabled state continues to follow the original combo owner.
combo.setEnabled(False);application.processEvents()
assert not control.editor.isEnabled() and not control.slider.isEnabled() and not control.spin.isEnabled()
# A disabled adapter cannot create a hidden value through a programmatic edit.
control.spin.setValue(33);assert combo.currentData()==.571
combo.setEnabled(True);application.processEvents()
assert control.editor.isEnabled() and control.slider.isEnabled() and control.spin.isEnabled()
assert control.spin.value()==57.1 and control.slider.value()==57

# Direct wheel delivery cannot change either slider or exact input.
def wheel(widget):
    return QWheelEvent(QPointF(5,5),QPointF(widget.mapToGlobal(QPoint(5,5))),QPoint(),QPoint(0,120),
        Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
control.slider.setValue(44);before=(combo.currentData(),control.slider.value())
QApplication.sendEvent(control.slider,wheel(control.slider));application.processEvents()
assert (combo.currentData(),control.slider.value())==before
control.spin.setValue(44.5);before=(combo.currentData(),control.spin.value())
QApplication.sendEvent(control.spin,wheel(control.spin));application.processEvents()
assert (combo.currentData(),control.spin.value())==before

# The calibration editor keeps its historical 1..100 QDoubleSpinBox range.
standalone=PercentStrengthControl(62.5,minimum=1)
emitted=[];standalone.spin.valueChanged.connect(emitted.append)
assert standalone.spin.value()==62.5 and standalone.slider.value()==62
assert standalone.spin.minimum()==1 and standalone.spin.maximum()==100
assert standalone.spin.singleStep()==1 and standalone.slider.singleStep()==1
standalone.slider.setValue(41)
assert standalone.spin.value()==41 and emitted[-1]==41
standalone.spin.setValue(63.25)
assert standalone.slider.value()==63 and standalone.value()==63.25
standalone.slider.setValue(42);before=(standalone.spin.value(),standalone.slider.value())
QApplication.sendEvent(standalone.slider,wheel(standalone.slider));application.processEvents()
assert (standalone.spin.value(),standalone.slider.value())==before

control.close();standalone.close();application.processEvents()
print('PASS: exact legacy strength retention, 1% slider edits, precise input, single-owner signals, disabled linkage and wheel rejection')
