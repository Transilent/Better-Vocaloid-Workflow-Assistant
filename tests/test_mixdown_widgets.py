"""Ownership and behavior checks for extracted simple-mixdown Qt controls."""
import ast
import math
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('VWA_TESTING', '1')

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src'
sys.path[:0] = [str(SRC), str(APP / 'tests')]

from PyQt5.QtCore import QPoint, QRectF, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QCheckBox, QLabel

import audio_mixdown_ui as ui
from mixdown_contract import MAX_VOCALS
import mixdown_widgets as widgets


app = QApplication.instance() or QApplication([])

EXTRACTED_NAMES = (
    '_set_role', '_db_to_fraction', '_fraction_to_db', '_SeekSlider',
    '_DbFader', '_PanDial', '_LevelMeter', '_ResponsiveMixerConsole',
    '_ResponsiveEQCurveView', '_ResponsiveEffectGrid', '_ElidedLabel',
)

for name in EXTRACTED_NAMES:
    assert getattr(ui, name) is getattr(widgets, name), name
    assert getattr(ui, name).__module__ == 'mixdown_widgets', name
assert ui._MAX_VOCAL_TRACKS == widgets._MAX_VOCAL_TRACKS == MAX_VOCALS

main_tree = ast.parse(Path(ui.__file__).read_bytes().decode('utf-8'))
main_definitions = {
    node.name for node in main_tree.body
    if isinstance(node, (ast.FunctionDef, ast.ClassDef))
}
assert set(EXTRACTED_NAMES).isdisjoint(main_definitions)
widgets_tree = ast.parse(Path(widgets.__file__).read_bytes().decode('utf-8'))
widgets_imports = {
    alias.name
    for node in widgets_tree.body if isinstance(node, ast.Import)
    for alias in node.names
}
widgets_imports.update(
    node.module
    for node in widgets_tree.body if isinstance(node, ast.ImportFrom)
)
assert 'audio_mixdown_ui' not in widgets_imports

assert widgets._db_to_fraction(-24.0) == 0.0
assert widgets._db_to_fraction(12.0) == 1.0
assert math.isclose(widgets._db_to_fraction(0.0), .78)
for expected in (-24.0, -12.0, 0.0, 6.0, 12.0):
    actual = widgets._fraction_to_db(widgets._db_to_fraction(expected))
    assert math.isclose(actual, expected, abs_tol=1e-9), (expected, actual)

role = QLabel('role probe')
widgets._set_role(role, 'muted')
assert role.property('role') == 'muted'
widgets._set_role(role, '')
assert role.property('role') == ''

slider = widgets._SeekSlider(Qt.Horizontal)
slider.resize(420, 30)
slider.setRange(0, 10000)
slider.show()
app.processEvents()
released = []
slider.sliderReleased.connect(lambda: released.append(slider.value()))
QTest.mouseClick(slider, Qt.LeftButton, pos=QPoint(315, 15))
assert 7000 < slider.value() < 8000, slider.value()
assert len(released) == 1
option = widgets.QStyleOptionSlider()
slider.initStyleOption(option)
handle = slider.style().subControlRect(
    widgets.QStyle.CC_Slider, option, widgets.QStyle.SC_SliderHandle, slider)
before = slider.value()
QTest.mousePress(slider, Qt.LeftButton, pos=handle.center() + QPoint(2, 0))
assert abs(slider.value() - before) < 30
QTest.mouseRelease(slider, Qt.LeftButton, pos=QPoint(419, 15))
assert slider.value() == 10000
cancelled = []
slider.seekCancelled.connect(lambda: cancelled.append(True))
QTest.mousePress(slider, Qt.LeftButton, pos=QPoint(100, 15))
QTest.keyClick(slider, Qt.Key_Escape)
assert cancelled == [True] and not slider.isSliderDown()

fader = widgets._DbFader(-240, 120, 0)
assert fader.value() == 0
assert fader.sizeHint().width() >= fader.minimumWidth()
fader.setValue(5)
assert fader.value() == 5
assert '+0.5 dB' in fader.toolTip()

pan = widgets._PanDial()
assert pan._pan_text(-25) == 'L25'
assert pan._pan_text(0) == 'C'
assert pan._pan_text(11) == 'R11'
pan.setValue(101)
assert pan.value() == 100

meter = widgets._LevelMeter(Qt.Vertical)
meter.set_levels(float('nan'), -3.0)
assert meter._level_db == -60.0
assert meter._peak_db == -3.0
meter.reset()
assert (meter._level_db, meter._peak_db, meter._clip) == (-60.0, -60.0, False)

label = widgets._ElidedLabel('a long mixdown file name')
assert label.toolTip() == 'a long mixdown file name'
label.setText('replacement name')
assert label._full_text == 'replacement name'
assert label.toolTip() == 'replacement name'

console = widgets._ResponsiveMixerConsole()
assert console.objectName() == 'mixer-console'
assert console._rack.objectName() == 'mixer-rack'
assert console.GAP == 8

effect_grid = widgets._ResponsiveEffectGrid([
    (QCheckBox('EQ'), 'EQ'), (QCheckBox('Compression'), 'Compression'),
    (QCheckBox('Reverb'), 'Reverb'),
])
effect_grid.resize(700, 200)
effect_grid.reflow(force=True)
assert effect_grid.column_count() == 3
effect_grid.resize(450, 200)
effect_grid.reflow()
assert effect_grid.column_count() == 2

eq = widgets._ResponsiveEQCurveView()
eq.resize(600, 320)
assert eq.bypass_rect().isValid()
assert isinstance(eq.bypass_rect(), QRectF)

for widget in (role, slider, fader, pan, meter, label, console, effect_grid, eq):
    widget.close()
    widget.deleteLater()
for _ in range(3):
    app.processEvents()

print('PASS: controls have one implementation, main UI re-exports it, '
      'no UI import cycle, and extraction keeps taper and widget contracts; offscreen')
