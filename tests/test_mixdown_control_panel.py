"""Offscreen contracts for the reusable mixdown P1 control cards."""

import copy
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QPushButton,
    QSlider,
    QWidget,
)

import mixdown_control_panel as panel


app = QApplication.instance() or QApplication([])


def settle(rounds=3):
    for _ in range(rounds):
        app.processEvents()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


class EchoCurve(QWidget):
    """Small EqCurveView-compatible fake used without importing the real view."""

    bandsChanged = pyqtSignal(list)

    def __init__(self, echo=True):
        super().__init__()
        self.echo = bool(echo)
        self.received = []
        self.sample_rate_value = 48000.0

    def set_sample_rate(self, sample_rate):
        self.sample_rate_value = float(sample_rate)

    def set_bands(self, bands):
        snapshot = copy.deepcopy(bands)
        self.received.append(snapshot)
        if self.echo:
            # A real curve view may notify after a programmatic set. The guard
            # inside EqBandStripControls must stop a feedback loop.
            self.bandsChanged.emit(copy.deepcopy(snapshot))


# Preset bar: explicit apply only, Lock mix captured at click time.
bar = panel.PresetBar(
    {
        "clear": {"label": "Clear"},
        "warm": {"label": "Warm"},
    },
    current_key="warm",
    lock_mix=True,
)
bar.show()
settle()
check(bar.current_key() == "warm", "initial preset key was not retained")
check(bar.is_lock_mix(), "Lock mix should default/on construct as true")
check(bar.preset_combo.findData("clear") >= 0, "preset key missing from combo")
check(bar.preset_label.buddy() is bar.preset_combo, "preset label buddy missing")
spy = QSignalSpy(bar.presetRequested)
bar.request_preset()
settle()
check(len(spy) == 1, "request_preset did not emit once")
check(list(spy[0]) == ["warm", True], "presetRequested payload mismatch")
bar.set_lock_mix(False)
bar.apply_button.setFocus()
QTest.keyClick(bar.apply_button, Qt.Key_Space)
settle()
check(len(spy) == 2, "keyboard activation did not emit presetRequested")
check(list(spy[1]) == ["warm", False], "Lock mix state was not captured at apply")
bar.close()
settle()

# Processing presets: both slider and numeric paths are continuous and source
# changes are normalized once per user action.
processing = panel.ProcessingPresetControls()
processing.show()
settle()
compression_spy = QSignalSpy(processing.compressionStrengthChanged)
reverb_spy = QSignalSpy(processing.reverbReturnMixChanged)
profile_spy = QSignalSpy(processing.reverbProfileChanged)
predelay_spy = QSignalSpy(processing.predelayChanged)

processing.compression_slider.setValue(40)
settle()
check(processing.compression_strength() == 40.0,
      "compression slider did not update the model")
check(processing.compression_value.value() == 40.0,
      "compression numeric editor did not follow slider")
check(len(compression_spy) == 1 and compression_spy[0][0] == 40.0,
      "compression slider signal payload mismatch")

processing.compression_value.setValue(22.0)
settle()
check(processing.compression_slider.value() == 22,
      "compression slider did not follow numeric editor")
check(processing.compression_strength() == 22.0,
      "compression numeric editor did not update model")
check(len(compression_spy) == 2 and compression_spy[1][0] == 22.0,
      "compression numeric signal payload mismatch")

processing.compression_slider.setFocus()
QTest.keyClick(processing.compression_slider, Qt.Key_Up)
QTest.keyClick(processing.compression_slider, Qt.Key_Up, Qt.ShiftModifier)
settle()
check(processing.compression_strength() == 28.0,
      "compression keyboard normal/fine steps are wrong")
QTest.mouseDClick(processing.compression_value, Qt.LeftButton)
settle()
check(processing.compression_strength() == 25.0,
      "compression double-click did not restore the default")

processing.reverb_mix_slider.setValue(35)
settle()
check(processing.reverb_return_mix() == 35.0 and
      processing.reverb_mix_value.value() == 35.0,
      "reverb return mix slider/numeric sync failed")
check(len(reverb_spy) == 1 and reverb_spy[0][0] == 35.0,
      "reverb return mix signal payload mismatch")
processing.reverb_mix_value.setValue(12.0)
settle()
check(processing.reverb_mix_slider.value() == 12,
      "reverb mix numeric editor did not follow through")
check(processing.reverb_return_mix() == 12.0,
      "reverb mix numeric editor did not update model")

wide_index = processing.reverb_profile.findData("wide")
processing.reverb_profile.setCurrentIndex(wide_index)
settle()
check(processing.current_profile() == "wide", "reverb profile did not update")
check(len(profile_spy) == 1 and profile_spy[0][0] == "wide",
      "reverb profile signal payload mismatch")

processing.predelay_slider.setValue(20)
processing.predelay_value.setValue(45)
settle()
check(processing.predelay_ms() == 45 and processing.predelay_slider.value() == 45,
      "predelay slider/numeric sync failed")
check(len(predelay_spy) >= 1 and predelay_spy[-1][0] == 45,
      "predelay signal payload mismatch")
processing.set_values(
    compression_strength=0.30,
    reverb_return_mix=0.20,
    reverb_profile="small",
    predelay_ms=30,
    normalized=True,
    emit=False,
)
settle()
check(processing.as_request_fields() == {
    "compression_strength": 0.30,
    "reverb_mix": 0.20,
    "reverb_profile": "small",
    "reverb_predelay_ms": 30,
}, "normalized request mapping is wrong")
check(processing.compression_slider.minimum() == 0 and
      processing.compression_slider.maximum() == 100,
      "compression range is not 0..100")
check(processing.reverb_mix_slider.maximum() == 100,
      "reverb range is not 0..100")
check(processing.predelay_slider.minimum() == 0 and
      processing.predelay_slider.maximum() == 120,
      "predelay range is not 0..120")
processing.close()
settle()

# Width: 0..2 range, centre notch, double-click reset, keyboard fine adjust.
width = panel.WidthControl()
width.show()
settle()
width_spy = QSignalSpy(width.widthChanged)
width.slider.setValue(140)
settle()
check(width.value() == 1.40, "width slider mapping is wrong")
width.slider.setValue(103)
settle()
check(width.value() == 1.0 and width.slider.value() == 100,
      "width did not snap into the 1.0 centre")
width.slider.setFocus()
QTest.keyClick(width.slider, Qt.Key_Up, Qt.ShiftModifier)
settle()
check(width.value() == 1.01, "Shift+arrow width fine step is wrong")
QTest.keyClick(width.slider, Qt.Key_Up)
settle()
check(width.value() == 1.06, "plain arrow width step is wrong")
QTest.mouseDClick(width.slider, Qt.LeftButton)
settle()
check(width.value() == 1.0 and width.spin.value() == 1.0,
      "width double-click did not reset to 1.0")
width.spin.setValue(1.37)
settle()
check(width.value() == 1.37 and width.slider.value() == 137,
      "width numeric editor did not sync to the slider")
QTest.mouseDClick(width.spin, Qt.LeftButton)
settle()
check(width.value() == 1.0, "width numeric double-click did not reset")
width.set_value(-1.0)
check(width.value() == 0.0, "width lower clamp failed")
width.set_value(9.0)
check(width.value() == 2.0, "width upper clamp failed")
check(width.slider.minimum() == 0 and width.slider.maximum() == 200,
      "width internal slider range is not 0..2")
check(len(width_spy) > 0, "widthChanged never emitted")
width.close()
settle()

# Per-track sends: one-to-three rows, normalized input compatibility, zero-based
# signal indexes, numeric/slider sync and reset.
sends = panel.PerTrackSendControls([
    {"display_name": "Lead", "reverb_send": 0.35},
    {"name": "Harmony", "reverb_send_percent": 60},
    {"display_name": "Backing", "reverb_send": 1.0, "enabled": False},
])
sends.show()
settle()
check(sends.track_count() == 3, "three track send rows were not retained")
check(sends.send_values() == [35.0, 60.0, 100.0],
      "track send input units were not normalized: %r" % sends.send_values())
check(sends.normalized_send_values() == [0.35, 0.60, 1.0],
      "track send normalized values are wrong")
check(not sends.rows[2]["widget"].isEnabled(),
      "disabled track send row remained enabled")
check(all(row["slider"].value() == row["value"].value()
          for row in sends.rows[:3]), "initial send slider/numeric pairs differ")
send_spy = QSignalSpy(sends.sendChanged)
sends.rows[0]["slider"].setValue(47)
settle()
check(sends.rows[0]["value"].value() == 47,
      "send numeric value did not follow slider")
check(len(send_spy) == 1 and list(send_spy[0]) == [0, 47.0],
      "sendChanged slider payload/index mismatch")
sends.rows[1]["value"].setValue(65)
settle()
check(sends.rows[1]["slider"].value() == 65,
      "send slider did not follow numeric value")
check(len(send_spy) == 2 and list(send_spy[1]) == [1, 65.0],
      "sendChanged numeric payload/index mismatch")
sends.update_tracks([{"name": "Solo", "reverb_send": 0.25}])
settle()
check(sends.track_count() == 1 and sends.send_values() == [25.0],
      "update_tracks did not reduce the active track list")
check(sends.rows[1]["widget"].isHidden() and
      sends.rows[2]["widget"].isHidden(), "unused track rows were not hidden")
check(len(send_spy) == 2, "update_tracks unexpectedly emitted sendChanged")
sends.set_send(0, 0.70, normalized=True, emit=True)
settle()
check(sends.send_values() == [70.0] and list(send_spy[-1]) == [0, 70.0],
      "set_send normalized mode failed")
sends.rows[0]["slider"].setFocus()
QTest.keyClick(sends.rows[0]["slider"], Qt.Key_Up)
settle()
check(sends.send_values() == [75.0], "send keyboard normal step failed")
QTest.mouseDClick(sends.rows[0]["slider"], Qt.LeftButton)
settle()
check(sends.send_values() == [100.0], "send double-click did not restore 100%")
check(sends.rows[0]["slider"].minimum() == 0 and
      sends.rows[0]["slider"].maximum() == 100,
      "send slider range is not 0..100")
sends.close()
settle()

# EQ strips: full four-band canonical list, two-way duck-typed curve sync,
# ranges, reset and signal de-duplication.
curve = EchoCurve()
eq = panel.EqBandStripControls(curve_view=curve)
eq.show()
curve.show()
settle()
check(len(eq.bands()) == 4, "EQ strip did not expose four bands")
check(len(curve.received) >= 1, "initial EQ bands were not pushed to curve")
check(eq.bands()[0]["type"] == "highpass" and
      eq.bands()[3]["type"] == "highshelf", "EQ band types changed")
eq_spy = QSignalSpy(eq.bandsChanged)
eq.rows[2]["freq"].setValue(4000)
settle()
check(len(eq_spy) == 1, "one EQ edit should emit one bandsChanged")
check(eq.bands()[2]["freq_hz"] == 4000.0,
      "EQ frequency edit did not update canonical bands")
check(curve.received[-1][2]["freq_hz"] == 4000.0,
      "EQ frequency edit did not reach the curve view")

from_curve = copy.deepcopy(eq.bands())
from_curve[1]["gain_db"] = 1.5
from_curve[1]["q"] = 0.9
from_curve[1]["enabled"] = True
curve.bandsChanged.emit(copy.deepcopy(from_curve))
settle()
check(eq.rows[1]["gain"].value() == 1.5 and
      eq.rows[1]["q"].value() == 0.9 and
      eq.rows[1]["enabled"].isChecked(),
      "curve-to-strip synchronization failed")
check(len(eq_spy) == 2, "curve edit did not emit exactly one external band signal")
check(len(curve.received) == 2, "curve edit echoed back into the curve setter")

# Spin-box ranges are enforced, and double click restores the band defaults.
eq.rows[0]["freq"].setValue(999999)
check(eq.rows[0]["freq"].value() == 20000.0, "EQ frequency upper clamp failed")
eq.rows[1]["gain"].setValue(-999)
check(eq.rows[1]["gain"].value() == -24.0, "EQ gain lower clamp failed")
eq.rows[1]["q"].setValue(0)
check(eq.rows[1]["q"].value() == 0.1, "EQ Q lower clamp failed")
eq.rows[0]["freq"].setValue(300)
QTest.mouseDClick(eq.rows[0]["freq"], Qt.LeftButton)
settle()
check(eq.rows[0]["freq"].value() == 90.0,
      "EQ frequency double-click did not restore the default")
check(eq.rows[0]["freq"].minimum() == 10.0 and
      eq.rows[0]["freq"].maximum() == 20000.0,
      "EQ frequency range is wrong")
check(eq.rows[1]["gain"].minimum() == -24.0 and
      eq.rows[1]["gain"].maximum() == 24.0,
      "EQ gain range is wrong")
check(eq.rows[1]["q"].minimum() == 0.1 and
      eq.rows[1]["q"].maximum() == 18.0,
      "EQ Q range is wrong")
check(not eq.rows[0]["gain"].isEnabled() and not eq.rows[0]["q"].isEnabled(),
      "high-pass gain/Q must be locked in the strip")
check("固定" in eq.rows[0]["gain"].toolTip(),
      "high-pass locked fields need an explicit explanation")
highpass_bands = eq.bands()
highpass_bands[0].update({"gain_db": 12.0, "q": 3.0})
eq.set_bands(highpass_bands)
check(eq.bands()[0]["gain_db"] == 0.0 and
      abs(eq.bands()[0]["q"] - 0.707) < 1e-9,
      "high-pass request values were not projected to effective fields")
eq.set_sample_rate(32000)
check(abs(eq.frequency_limit_hz() - 15200.0) < 1e-9,
      "32 kHz EQ limit did not follow the Nyquist-safe boundary")
check(abs(curve.sample_rate_value - 32000.0) < 1e-9,
      "sample rate did not reach the bound curve")
eq.rows[0]["freq"].setValue(20000)
check(abs(eq.rows[0]["freq"].value() - 15200.0) < 1e-9,
      "frequency strip did not enforce the render-rate limit")
eq_count_before = len(eq_spy)
eq.set_bands(from_curve, emit=False)
settle()
check(len(eq_spy) == eq_count_before, "set_bands(emit=False) emitted externals")
check(curve.received[-1][1]["gain_db"] == 1.5,
      "set_bands did not synchronize an already-bound curve view")
eq.detach_curve_view()
# Change the strip after detaching, then prove the curve can no longer write it.
eq.rows[1]["gain"].setValue(-24.0)
curve.bandsChanged.emit(copy.deepcopy(from_curve))
settle()
check(eq.rows[1]["gain"].value() == -24.0,
      "detached curve view still wrote into the strip")
eq.close()
curve.close()
settle()

# Public identity, accessibility, focusability and colour-token hygiene.
components = (bar, processing, width, sends, eq)
interactive_types = (QSlider, QComboBox, QCheckBox, QPushButton, QAbstractSpinBox)
interactive = []
for component in components:
    check(bool(component.objectName()), "component objectName missing")
    check(bool(component.accessibleName()), "component accessibleName missing")
    for widget in component.findChildren(QWidget):
        if isinstance(widget, interactive_types):
            interactive.append(widget)

check(interactive, "no interactive widgets found")
for widget in interactive:
    check(bool(widget.objectName()),
          "interactive objectName missing on %s" % type(widget).__name__)
    check(bool(widget.accessibleName()),
          "interactive accessibleName missing on %s" % widget.objectName())
    check(widget.focusPolicy() != Qt.NoFocus,
          "interactive widget is not focusable: %s" % widget.objectName())

names = [widget.objectName() for widget in interactive]
check(len(names) == len(set(names)), "interactive objectNames are not unique")

source = Path(panel.__file__).read_text(encoding="utf-8")
check("setStyleSheet" not in source, "control module contains hard-coded stylesheet")
check("QColor" not in source, "control module imports/uses QColor")
check("palette" not in source.lower(), "control module contains palette colours")

# Explicit adapter names also work for a curve view that does not use the
# preferred names; this keeps the component independent of the final EQ view.
class AliasCurve(QWidget):
    eq_bands_changed = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.values = []

    def setBands(self, bands):
        self.values.append(copy.deepcopy(bands))

alias_curve = AliasCurve()
alias = panel.EqBandStripControls()
alias.bind_curve_view(
    alias_curve,
    setter="setBands",
    signal="eq_bands_changed",
)
settle()
check(len(alias_curve.values) == 1, "explicit curve adapter initial push failed")
alias_curve.eq_bands_changed.emit(panel.default_eq_bands())
settle()
check(alias.bands() == panel.default_eq_bands(), "explicit curve signal failed")
alias.close()
alias_curve.close()
settle()

print("PASS: preset/Lock mix, continuous processing signals, width snap/step/reset, "
      "one-to-three reverb sends, four-band EQ canonical sync, accessibility and "
      "theme-neutral widgets")
