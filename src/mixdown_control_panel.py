"""Standalone PyQt5 control cards for the VWA mixdown P1 surface.

This module imports only the standard library and PyQt5. Colours are inherited
from the surrounding application; no theme module or mixer-state module is
required.

Unit conventions:
* Percentage controls expose 0..100. ``as_request_fields`` provides 0..1.
* Width is exposed as 0..2 with 1.0 as the centre.
* EQ bands use the canonical v3 shape::

      {"type": "bell", "freq_hz": 3200.0, "gain_db": 2.5,
       "q": 0.8, "enabled": True}

``EqBandStripControls`` does not import ``EqCurveView``.  Use
``bind_curve_view(view)`` with a compatible setter and list signal, or pass
explicit setter/signal adapters.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import tr as _tr
from mixdown_contract import (
    DEFAULT_SAMPLE_RATE,
    EQ_HIGHPASS_Q,
    EQ_MAX_FREQUENCY_HZ,
    EQ_MAX_GAIN_DB,
    EQ_MAX_Q,
    EQ_MIN_FREQUENCY_HZ,
    EQ_MIN_GAIN_DB,
    EQ_MIN_Q,
    eq_frequency_limit_hz,
    project_eq_band,
    valid_sample_rate,
)


__all__ = [
    "CONTROL_NAMES",
    "DEFAULT_EQ_BANDS",
    "DEFAULT_PRESETS",
    "DEFAULT_REVERB_PROFILES",
    "EQ_BAND_COUNT",
    "MAX_TRACKS",
    "EqBandStripControls",
    "PerTrackSendControls",
    "PresetBar",
    "ProcessingPresetControls",
    "WidthControl",
    "default_eq_bands",
]

MAX_TRACKS = 3
EQ_BAND_COUNT = 4
WIDTH_MIN = 0.0
WIDTH_MAX = 2.0
WIDTH_CENTER = 1.0
WIDTH_CENTER_SNAP = 0.04

CONTROL_NAMES = {
    "preset_bar": "mixdownPresetBar",
    "preset_combo": "mixdownPresetCombo",
    "preset_lock_mix": "mixdownPresetLockMix",
    "preset_apply": "mixdownPresetApply",
    "processing_controls": "mixdownProcessingPresetControls",
    "compression_slider": "mixdownProcessingCompressionSlider",
    "compression_value": "mixdownProcessingCompressionValue",
    "reverb_mix_slider": "mixdownProcessingReverbMixSlider",
    "reverb_mix_value": "mixdownProcessingReverbMixValue",
    "reverb_profile": "mixdownProcessingReverbProfile",
    "predelay_slider": "mixdownProcessingPredelaySlider",
    "predelay_value": "mixdownProcessingPredelayValue",
    "width_control": "mixdownWidthControl",
    "width_slider": "mixdownWidthSlider",
    "width_value": "mixdownWidthValue",
    "send_controls": "mixdownPerTrackSendControls",
    "eq_bands": "mixdownEqBandStripControls",
}

DEFAULT_PRESETS = (
    ("clear_lead", "清透主唱"),
    ("thick_harmony", "厚实和声"),
    ("close_mic", "贴耳近讲"),
    ("wide_chorus", "宽阔副歌"),
    ("opera_bright", "戏腔清亮"),
    ("duet_balance", "对唱平衡"),
)

DEFAULT_REVERB_PROFILES = (
    ("small", "短空间"),
    ("natural", "自然空间"),
    ("wide", "宽阔空间"),
)

DEFAULT_EQ_BANDS = (
    {"type": "highpass", "freq_hz": 90.0, "gain_db": 0.0,
     "q": 0.707, "enabled": True},
    {"type": "lowshelf", "freq_hz": 260.0, "gain_db": 0.0,
     "q": 0.707, "enabled": False},
    {"type": "bell", "freq_hz": 3200.0, "gain_db": 2.5,
     "q": 0.8, "enabled": True},
    {"type": "highshelf", "freq_hz": 10000.0, "gain_db": 1.5,
     "q": 0.707, "enabled": False},
)

_BAND_LABELS = {
    "highpass": "高通",
    "lowshelf": "低架",
    "bell": "钟形",
    "highshelf": "高架",
}
_BAND_TYPES = tuple(band["type"] for band in DEFAULT_EQ_BANDS)


def _identity(widget: QWidget, object_name: str, accessible_name: str,
              description: str = "", *, focus: bool = False) -> QWidget:
    widget.setObjectName(object_name)
    widget.setAccessibleName(accessible_name)
    if description:
        widget.setAccessibleDescription(description)
    if focus:
        widget.setFocusPolicy(Qt.StrongFocus)
    return widget


def _label(parent: QWidget, text: str, object_name: str,
           accessible_name: str, buddy: QWidget | None = None) -> QLabel:
    widget = QLabel(_tr(text), parent)
    _identity(widget, object_name, accessible_name)
    if buddy is not None:
        widget.setBuddy(buddy)
    return widget


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _finite_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _bool_value(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
    return bool(value)


def _set_value_silently(widget: QWidget, value: Any) -> None:
    blocked = widget.blockSignals(True)
    try:
        widget.setValue(value)
    finally:
        widget.blockSignals(blocked)


def _set_checked_silently(widget: QCheckBox, checked: bool) -> None:
    blocked = widget.blockSignals(True)
    try:
        widget.setChecked(bool(checked))
    finally:
        widget.blockSignals(blocked)


def _select_combo_key_silently(combo: QComboBox, key: str) -> bool:
    index = combo.findData(key)
    if index < 0:
        return False
    blocked = combo.blockSignals(True)
    try:
        combo.setCurrentIndex(index)
    finally:
        combo.blockSignals(blocked)
    return True


def _combo_items(items: Any, defaults: Any, *, field: str) -> list[tuple[str, str]]:
    """Normalize mapping, (key, label), or string choice definitions."""

    source = defaults if items is None else items
    if isinstance(source, (str, bytes)):
        raise TypeError(f"{field} must be a mapping or sequence")

    if isinstance(source, Mapping):
        normalized = []
        for key, value in source.items():
            if isinstance(value, Mapping):
                label = (value.get("label") or value.get("title")
                         or value.get("name") or str(key))
            else:
                label = value
            normalized.append((str(key), str(label)))
    else:
        try:
            raw_items = list(source)
        except TypeError as error:
            raise TypeError(f"{field} must be a mapping or sequence") from error
        normalized = []
        for item in raw_items:
            if isinstance(item, Mapping):
                key = (item.get("key") or item.get("id")
                       or item.get("value") or item.get("slug"))
                if key is None:
                    raise ValueError(f"{field} entries need a key")
                label = (item.get("label") or item.get("title")
                         or item.get("name") or str(key))
                normalized.append((str(key), str(label)))
            elif isinstance(item, (tuple, list)):
                if len(item) != 2:
                    raise ValueError(f"{field} tuples must be (key, label)")
                normalized.append((str(item[0]), str(item[1])))
            else:
                normalized.append((str(item), str(item)))

    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    seen = set()
    cleaned = []
    for key, label in normalized:
        key = key.strip()
        if not key:
            raise ValueError(f"{field} keys cannot be empty")
        if key in seen:
            raise ValueError(f"duplicate {field} key: {key}")
        seen.add(key)
        cleaned.append((key, label))
    return cleaned


def _copy_bands(bands: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": str(band["type"]),
            "freq_hz": float(band["freq_hz"]),
            "gain_db": float(band["gain_db"]),
            "q": float(band["q"]),
            "enabled": bool(band["enabled"]),
        }
        for band in bands
    ]


def default_eq_bands() -> list[dict[str, Any]]:
    return _copy_bands(DEFAULT_EQ_BANDS)


class _FineSlider(QSlider):
    """Slider with reset-on-double-click and Shift fine keyboard steps."""

    def __init__(self, orientation: Qt.Orientation, *, reset_value: int = 0,
                 normal_step: int = 5, fine_step: int = 1,
                 parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._reset_value = int(reset_value)
        self._normal_step = max(1, int(normal_step))
        self._fine_step = max(1, int(fine_step))
        self.setSingleStep(self._normal_step)
        self.setPageStep(max(self._normal_step * 2, self._normal_step + 1))
        self.setTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def setResetValue(self, value: int) -> None:
        self._reset_value = int(round(value))

    def set_reset_value(self, value: int) -> None:
        self.setResetValue(value)

    def reset(self) -> None:
        self.setValue(self._reset_value)

    def keyPressEvent(self, event) -> None:
        arrow = event.key() in (
            Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
        )
        if not arrow:
            super().keyPressEvent(event)
            return
        old_step = self.singleStep()
        fine = bool(event.modifiers() & Qt.ShiftModifier)
        self.setSingleStep(self._fine_step if fine else self._normal_step)
        try:
            super().keyPressEvent(event)
        finally:
            self.setSingleStep(old_step)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()
        event.accept()


class _ResetSpinBox(QSpinBox):
    def __init__(self, reset_value: int = 0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reset_value = int(reset_value)
        self.setKeyboardTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def setResetValue(self, value: int) -> None:
        self._reset_value = int(round(value))

    def set_reset_value(self, value: int) -> None:
        self.setResetValue(value)

    def reset(self) -> None:
        self.setValue(self._reset_value)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()
        event.accept()


class _ResetDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, reset_value: float = 0.0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reset_value = float(reset_value)
        self.setKeyboardTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def setResetValue(self, value: float) -> None:
        self._reset_value = float(value)

    def set_reset_value(self, value: float) -> None:
        self.setResetValue(value)

    def reset(self) -> None:
        self.setValue(self._reset_value)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset()
        event.accept()


class PresetBar(QWidget):
    """Preset selector, Lock mix toggle, and explicit apply action."""

    presetRequested = pyqtSignal(str, bool)

    def __init__(self, presets: Any = None, current_key: str | None = None,
                 lock_mix: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _identity(
            self, CONTROL_NAMES["preset_bar"], _tr("预设选择"),
            _tr("选择起点预设。应用前不会改变当前混音。"),
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QGridLayout(self)
        self._preset_layout = layout
        self._preset_layout_mode = None
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.preset_combo = QComboBox(self)
        _identity(
            self.preset_combo, CONTROL_NAMES["preset_combo"],
            _tr("混音预设"), _tr("使用上下方向键选择预设，然后按应用预设。"),
            focus=True,
        )
        self.preset_label = _label(
            self, "预设", "mixdownPresetLabel", _tr("预设"),
            self.preset_combo,
        )
        self.lock_mix_checkbox = QCheckBox(_tr("锁定轨道设置"), self)
        _identity(
            self.lock_mix_checkbox, CONTROL_NAMES["preset_lock_mix"],
            _tr("锁定轨道设置，应用预设时保留各轨声像和混响发送"),
            _tr("勾选时保留各轨声像和混响发送；预设仍会更改人声处理与混响总量。"),
            focus=True,
        )
        self.apply_button = QPushButton(_tr("应用预设"), self)
        _identity(
            self.apply_button, CONTROL_NAMES["preset_apply"],
            _tr("应用当前预设"),
            _tr("按 Enter 或空格应用当前预设。"),
            focus=True,
        )
        self.apply_button.setAutoDefault(False)
        self.apply_button.setDefault(False)

        self.preset_combo.setMinimumContentsLength(6)
        self.preset_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._reflow_preset_bar()

        self._items: list[tuple[str, str]] = []
        self.set_presets(presets, current_key=current_key)
        self.set_lock_mix(lock_mix)
        self.apply_button.clicked.connect(self.request_preset)
        QWidget.setTabOrder(self.preset_combo, self.lock_mix_checkbox)
        QWidget.setTabOrder(self.lock_mix_checkbox, self.apply_button)

        self.combo = self.preset_combo
        self.apply = self.apply_button

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_preset_layout'):
            self._reflow_preset_bar()

    def _reflow_preset_bar(self):
        mode = 1 if self.width() >= 560 else 2 if self.width() >= 360 else 3
        if mode == self._preset_layout_mode:
            return
        self._preset_layout_mode = mode
        layout = self._preset_layout
        while layout.count(): layout.takeAt(0)
        for column in range(4): layout.setColumnStretch(column, 0)
        layout.addWidget(self.preset_label, 0, 0)
        if mode == 1:
            layout.addWidget(self.preset_combo, 0, 1)
            layout.addWidget(self.lock_mix_checkbox, 0, 2)
            layout.addWidget(self.apply_button, 0, 3)
            layout.setColumnStretch(1, 1)
        else:
            layout.addWidget(self.preset_combo, 0, 1, 1, 3)
            layout.addWidget(self.lock_mix_checkbox, 1, 0, 1, 3 if mode == 2 else 4)
            layout.addWidget(self.apply_button, 1 if mode == 2 else 2,
                             3 if mode == 2 else 0, 1, 1 if mode == 2 else 4)
            layout.setColumnStretch(1, 1)
        layout.invalidate()
        self.updateGeometry()

    def set_presets(self, presets: Any = None,
                    current_key: str | None = None) -> None:
        items = _combo_items(presets, DEFAULT_PRESETS, field="presets")
        previous = self.current_key()
        blocked = self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for key, label in items:
                self.preset_combo.addItem(label, key)
        finally:
            self.preset_combo.blockSignals(blocked)
        self._items = items

        desired = current_key if current_key is not None else previous
        if desired is None or not _select_combo_key_silently(
                self.preset_combo, desired):
            if self.preset_combo.count():
                self.preset_combo.setCurrentIndex(0)
        self.apply_button.setEnabled(self.preset_combo.count() > 0)

    def preset_items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def current_key(self) -> str | None:
        data = self.preset_combo.currentData()
        return str(data) if data is not None else None

    def set_current_key(self, key: str) -> bool:
        return _select_combo_key_silently(self.preset_combo, str(key))

    def is_lock_mix(self) -> bool:
        return bool(self.lock_mix_checkbox.isChecked())

    def set_lock_mix(self, checked: bool) -> None:
        _set_checked_silently(self.lock_mix_checkbox, bool(checked))

    def request_preset(self) -> None:
        key = self.current_key()
        if key is None or not self.apply_button.isEnabled():
            return
        self.presetRequested.emit(key, self.is_lock_mix())


class ProcessingPresetControls(QWidget):
    """Compact compression/reverb values used by the P1 inspector."""

    compressionStrengthChanged = pyqtSignal(float)
    reverbReturnMixChanged = pyqtSignal(float)
    reverbProfileChanged = pyqtSignal(str)
    predelayChanged = pyqtSignal(int)

    def __init__(
        self,
        compression_strength: float = 25.0,
        reverb_return_mix: float = 10.0,
        reverb_profile: str = "natural",
        predelay_ms: int = 20,
        profiles: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        _identity(
            self, CONTROL_NAMES["processing_controls"],
            _tr("处理预设参数"),
            _tr("压缩、混响返回和预延迟参数；修改立即发出连续信号。"),
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        self.compression_label = _label(
            self, "压缩强度", "mixdownProcessingCompressionLabel",
            _tr("压缩强度"),
        )
        self.compression_slider = _FineSlider(
            Qt.Horizontal, reset_value=25, normal_step=5, fine_step=1,
            parent=self,
        )
        self.compression_slider.setRange(0, 100)
        _identity(
            self.compression_slider, CONTROL_NAMES["compression_slider"],
            _tr("压缩强度百分比"),
            _tr("方向键每次 5%，Shift+方向键每次 1%，双击恢复 25%。"),
            focus=True,
        )
        self.compression_value = _ResetDoubleSpinBox(25.0, self)
        self.compression_value.setRange(0.0, 100.0)
        self.compression_value.setDecimals(0)
        self.compression_value.setSingleStep(1.0)
        self.compression_value.setSuffix(" %")
        _identity(
            self.compression_value, CONTROL_NAMES["compression_value"],
            _tr("压缩强度数值百分比"),
            _tr("可直接输入 0 到 100 的百分比；双击恢复 25%。"),
            focus=True,
        )
        self.compression_label.setBuddy(self.compression_value)

        self.reverb_mix_label = _label(
            self, "混响返回湿度", "mixdownProcessingReverbMixLabel",
            _tr("混响返回湿度"),
        )
        self.reverb_mix_slider = _FineSlider(
            Qt.Horizontal, reset_value=10, normal_step=5, fine_step=1,
            parent=self,
        )
        self.reverb_mix_slider.setRange(0, 100)
        _identity(
            self.reverb_mix_slider, CONTROL_NAMES["reverb_mix_slider"],
            _tr("混响返回湿度百分比"),
            _tr("方向键每次 5%，Shift+方向键每次 1%，双击恢复 10%。"),
            focus=True,
        )
        self.reverb_mix_value = _ResetDoubleSpinBox(10.0, self)
        self.reverb_mix_value.setRange(0.0, 100.0)
        self.reverb_mix_value.setDecimals(0)
        self.reverb_mix_value.setSingleStep(1.0)
        self.reverb_mix_value.setSuffix(" %")
        _identity(
            self.reverb_mix_value, CONTROL_NAMES["reverb_mix_value"],
            _tr("混响返回湿度数值百分比"),
            _tr("可直接输入 0 到 100 的百分比；双击恢复 10%。"),
            focus=True,
        )
        self.reverb_mix_label.setBuddy(self.reverb_mix_value)

        self.reverb_profile_label = _label(
            self, "混响空间", "mixdownProcessingReverbProfileLabel",
            _tr("混响空间类型"),
        )
        self.reverb_profile = QComboBox(self)
        _identity(
            self.reverb_profile, CONTROL_NAMES["reverb_profile"],
            _tr("混响空间类型"),
            _tr("使用上下方向键切换混响空间。"),
            focus=True,
        )
        self.reverb_profile_label.setBuddy(self.reverb_profile)

        self.predelay_label = _label(
            self, "预延迟", "mixdownProcessingPredelayLabel",
            _tr("混响预延迟"),
        )
        self.predelay_slider = _FineSlider(
            Qt.Horizontal, reset_value=20, normal_step=5, fine_step=1,
            parent=self,
        )
        self.predelay_slider.setRange(0, 120)
        _identity(
            self.predelay_slider, CONTROL_NAMES["predelay_slider"],
            _tr("混响预延迟毫秒"),
            _tr("方向键每次 5 ms，Shift+方向键每次 1 ms，双击恢复 20 ms。"),
            focus=True,
        )
        self.predelay_value = _ResetSpinBox(20, self)
        self.predelay_value.setRange(0, 120)
        self.predelay_value.setSingleStep(1)
        self.predelay_value.setSuffix(" ms")
        _identity(
            self.predelay_value, CONTROL_NAMES["predelay_value"],
            _tr("混响预延迟毫秒数值"),
            _tr("可直接输入 0 到 120 ms；双击恢复 20 ms。"),
            focus=True,
        )
        self.predelay_label.setBuddy(self.predelay_value)

        grid.addWidget(self.compression_label, 0, 0)
        grid.addWidget(self.compression_slider, 0, 1)
        grid.addWidget(self.compression_value, 0, 2)
        grid.addWidget(self.reverb_mix_label, 1, 0)
        grid.addWidget(self.reverb_mix_slider, 1, 1)
        grid.addWidget(self.reverb_mix_value, 1, 2)
        grid.addWidget(self.reverb_profile_label, 2, 0)
        grid.addWidget(self.reverb_profile, 2, 1, 1, 2)
        grid.addWidget(self.predelay_label, 3, 0)
        grid.addWidget(self.predelay_slider, 3, 1)
        grid.addWidget(self.predelay_value, 3, 2)

        self._compression = 0.0
        self._reverb_mix = 0.0
        self._predelay = 0
        self._profile_key = ""
        self._profiles: list[tuple[str, str]] = []
        self.set_reverb_profiles(profiles, current_key=str(reverb_profile))
        self._apply_compression(compression_strength, emit=False)
        self._apply_reverb_mix(reverb_return_mix, emit=False)
        self._apply_predelay(predelay_ms, emit=False)

        self.compression_slider.valueChanged.connect(
            self._compression_from_slider)
        self.compression_value.valueChanged.connect(
            self._compression_from_value)
        self.reverb_mix_slider.valueChanged.connect(
            self._reverb_mix_from_slider)
        self.reverb_mix_value.valueChanged.connect(
            self._reverb_mix_from_value)
        self.reverb_profile.currentIndexChanged.connect(
            self._profile_from_index)
        self.predelay_slider.valueChanged.connect(
            self._predelay_from_slider)
        self.predelay_value.valueChanged.connect(
            self._predelay_from_value)

        QWidget.setTabOrder(self.compression_slider, self.compression_value)
        QWidget.setTabOrder(self.compression_value, self.reverb_mix_slider)
        QWidget.setTabOrder(self.reverb_mix_slider, self.reverb_mix_value)
        QWidget.setTabOrder(self.reverb_mix_value, self.reverb_profile)
        QWidget.setTabOrder(self.reverb_profile, self.predelay_slider)
        QWidget.setTabOrder(self.predelay_slider, self.predelay_value)

        self.compression_strength_slider = self.compression_slider
        self.compression_strength_value = self.compression_value

    def set_reverb_profiles(self, profiles: Any = None,
                            current_key: str | None = None) -> None:
        items = _combo_items(
            profiles, DEFAULT_REVERB_PROFILES, field="reverb profiles")
        desired = current_key or self._profile_key or items[0][0]
        blocked = self.reverb_profile.blockSignals(True)
        try:
            self.reverb_profile.clear()
            for key, label in items:
                self.reverb_profile.addItem(label, key)
        finally:
            self.reverb_profile.blockSignals(blocked)
        self._profiles = items
        keys = [key for key, _ in items]
        selected = desired if desired in keys else keys[0]
        _select_combo_key_silently(self.reverb_profile, selected)
        self._profile_key = selected

    def profile_items(self) -> list[tuple[str, str]]:
        return list(self._profiles)

    def compression_strength(self) -> float:
        return float(self._compression)

    def reverb_return_mix(self) -> float:
        return float(self._reverb_mix)

    def current_profile(self) -> str:
        return str(self._profile_key)

    def predelay_ms(self) -> int:
        return int(self._predelay)

    def values(self) -> dict[str, Any]:
        return {
            "compression_strength_percent": self.compression_strength(),
            "compression_strength": self.compression_strength() / 100.0,
            "reverb_return_mix_percent": self.reverb_return_mix(),
            "reverb_mix": self.reverb_return_mix() / 100.0,
            "reverb_profile": self.current_profile(),
            "reverb_predelay_ms": self.predelay_ms(),
        }

    def as_request_fields(self) -> dict[str, Any]:
        return {
            "compression_strength": self.compression_strength() / 100.0,
            "reverb_mix": self.reverb_return_mix() / 100.0,
            "reverb_profile": self.current_profile(),
            "reverb_predelay_ms": self.predelay_ms(),
        }

    normalized_values = as_request_fields

    def set_compression_strength(self, value: float,
                                 *, emit: bool = True) -> bool:
        return self._apply_compression(value, emit=emit)

    def set_reverb_return_mix(self, value: float,
                              *, emit: bool = True) -> bool:
        return self._apply_reverb_mix(value, emit=emit)

    def set_reverb_profile(self, key: str, *, emit: bool = True) -> bool:
        index = self.reverb_profile.findData(str(key))
        if index < 0:
            raise ValueError(f"unknown reverb profile: {key}")
        if index == self.reverb_profile.currentIndex():
            return False
        if emit:
            self.reverb_profile.setCurrentIndex(index)
        else:
            _select_combo_key_silently(self.reverb_profile, str(key))
            self._profile_key = str(key)
        return True

    def set_predelay_ms(self, value: int, *, emit: bool = True) -> bool:
        return self._apply_predelay(value, emit=emit)

    def set_values(self, *, compression_strength: float | None = None,
                   reverb_return_mix: float | None = None,
                   reverb_profile: str | None = None,
                   predelay_ms: int | None = None,
                   normalized: bool = False,
                   emit: bool = True) -> None:
        if compression_strength is not None:
            value = float(compression_strength)
            if normalized:
                value *= 100.0
            self.set_compression_strength(value, emit=emit)
        if reverb_return_mix is not None:
            value = float(reverb_return_mix)
            if normalized:
                value *= 100.0
            self.set_reverb_return_mix(value, emit=emit)
        if reverb_profile is not None:
            self.set_reverb_profile(str(reverb_profile), emit=emit)
        if predelay_ms is not None:
            self.set_predelay_ms(predelay_ms, emit=emit)

    def _apply_compression(self, value: float, *, emit: bool) -> bool:
        number = float(round(_clamp(_finite_float(value, "compression_strength"),
                                    0.0, 100.0)))
        changed = number != self._compression
        self._compression = number
        _set_value_silently(self.compression_slider, int(number))
        _set_value_silently(self.compression_value, number)
        if changed and emit:
            self.compressionStrengthChanged.emit(number)
        return changed

    def _apply_reverb_mix(self, value: float, *, emit: bool) -> bool:
        number = float(round(_clamp(_finite_float(value, "reverb_return_mix"),
                                    0.0, 100.0)))
        changed = number != self._reverb_mix
        self._reverb_mix = number
        _set_value_silently(self.reverb_mix_slider, int(number))
        _set_value_silently(self.reverb_mix_value, number)
        if changed and emit:
            self.reverbReturnMixChanged.emit(number)
        return changed

    def _apply_predelay(self, value: int, *, emit: bool) -> bool:
        number = int(round(_clamp(_finite_float(value, "predelay_ms"), 0.0, 120.0)))
        changed = number != self._predelay
        self._predelay = number
        _set_value_silently(self.predelay_slider, number)
        _set_value_silently(self.predelay_value, number)
        if changed and emit:
            self.predelayChanged.emit(number)
        return changed

    def _compression_from_slider(self, value: int) -> None:
        self._apply_compression(float(value), emit=True)

    def _compression_from_value(self, value: float) -> None:
        self._apply_compression(float(value), emit=True)

    def _reverb_mix_from_slider(self, value: int) -> None:
        self._apply_reverb_mix(float(value), emit=True)

    def _reverb_mix_from_value(self, value: float) -> None:
        self._apply_reverb_mix(float(value), emit=True)

    def _profile_from_index(self, _index: int) -> None:
        data = self.reverb_profile.currentData()
        if data is None:
            return
        key = str(data)
        changed = key != self._profile_key
        self._profile_key = key
        if changed:
            self.reverbProfileChanged.emit(key)

    def _predelay_from_slider(self, value: int) -> None:
        self._apply_predelay(int(value), emit=True)

    def _predelay_from_value(self, value: int) -> None:
        self._apply_predelay(int(value), emit=True)


class WidthControl(QWidget):
    """Stereo width 0..2 with centre snapping and fine keyboard control."""

    widthChanged = pyqtSignal(float)

    def __init__(self, value: float = WIDTH_CENTER,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _identity(
            self, CONTROL_NAMES["width_control"], _tr("立体声宽度"),
            _tr("0 到 2；中心 1.0 吸附，Shift+方向键精调，双击恢复 1.0。"),
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.label = _label(
            self, "宽度", "mixdownWidthLabel", _tr("立体声宽度"))
        self.slider = _FineSlider(
            Qt.Horizontal, reset_value=100, normal_step=5, fine_step=1,
            parent=self,
        )
        self.slider.setRange(0, 200)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(100)
        _identity(
            self.slider, CONTROL_NAMES["width_slider"],
            _tr("立体声宽度滑条"),
            _tr("0 到 2；普通方向键 0.05，Shift+方向键 0.01，双击回 1.00。"),
            focus=True,
        )
        self.spin = _ResetDoubleSpinBox(1.0, self)
        self.spin.setRange(WIDTH_MIN, WIDTH_MAX)
        self.spin.setDecimals(2)
        self.spin.setSingleStep(0.01)
        _identity(
            self.spin, CONTROL_NAMES["width_value"],
            _tr("立体声宽度数值"),
            _tr("可直接输入 0 到 2；双击恢复 1.00。"),
            focus=True,
        )
        self.label.setBuddy(self.spin)

        layout.addWidget(self.label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)

        self._units = 100
        self._snap_hold = False
        self._write_units(self._units)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.slider.sliderPressed.connect(self._slider_pressed)
        self.slider.sliderReleased.connect(self._slider_released)
        QWidget.setTabOrder(self.slider, self.spin)
        self.set_value(value, emit=False)

        self.width_slider = self.slider
        self.width_value = self.spin

    def value(self) -> float:
        return self._units / 100.0

    def set_value(self, value: float, *, emit: bool = True) -> bool:
        number = _finite_float(value, "width")
        units = int(round(_clamp(number, WIDTH_MIN, WIDTH_MAX) * 100.0))
        return self._commit(units, emit=emit)

    def reset(self, *, emit: bool = True) -> bool:
        return self.set_value(WIDTH_CENTER, emit=emit)

    def _write_units(self, units: int) -> None:
        _set_value_silently(self.slider, int(units))
        _set_value_silently(self.spin, int(units) / 100.0)

    def _commit(self, units: int, *, emit: bool) -> bool:
        units = int(round(_clamp(float(units), 0.0, 200.0)))
        changed = units != self._units
        self._units = units
        self._write_units(units)
        if changed and emit:
            self.widthChanged.emit(units / 100.0)
        return changed

    def _snap_units(self, raw: int, previous: int, *, dragging: bool) -> int:
        centre = 100
        threshold = int(round(WIDTH_CENTER_SNAP * 100.0))
        distance = abs(raw - centre)
        if dragging and self._snap_hold and distance <= threshold:
            return centre
        if distance <= threshold:
            previous_distance = abs(previous - centre)
            if distance < previous_distance or raw == centre:
                if dragging:
                    self._snap_hold = True
                return centre
        self._snap_hold = False
        return raw

    def _from_slider(self, raw: int) -> None:
        units = self._snap_units(
            int(raw), self._units, dragging=self.slider.isSliderDown())
        self._commit(units, emit=True)

    def _from_spin(self, value: float) -> None:
        raw = int(round(float(value) * 100.0))
        units = self._snap_units(raw, self._units, dragging=False)
        self._commit(units, emit=True)

    def _slider_pressed(self) -> None:
        self._snap_hold = False

    def _slider_released(self) -> None:
        self._snap_hold = False


class PerTrackSendControls(QWidget):
    """One reverb-send percentage pair per available vocal track."""

    sendChanged = pyqtSignal(int, float)

    def __init__(self, tracks: Any = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _identity(
            self, CONTROL_NAMES["send_controls"], _tr("每轨混响发送"),
            _tr("最多三轨；界面显示 0 到 100%，信号索引从 0 开始。"),
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QWidget(self)
        _identity(
            header, "mixdownReverbSendHeader", _tr("每轨混响发送标题"))
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_label = QLabel(_tr("每轨混响发送"), header)
        _identity(header_label, "mixdownReverbSendHeaderLabel",
                  _tr("每轨混响发送"))
        header_unit = QLabel("%", header)
        _identity(header_unit, "mixdownReverbSendHeaderUnit", _tr("百分比"))
        header_layout.addWidget(header_label)
        header_layout.addStretch(1)
        header_layout.addWidget(header_unit)
        layout.addWidget(header)

        self.rows: list[dict[str, Any]] = []
        self._active_count = 0
        for index in range(MAX_TRACKS):
            number = index + 1
            row_widget = QWidget(self)
            _identity(
                row_widget, f"mixdownReverbSendTrack{number}Row",
                _tr("第 {0} 轨混响发送").format(number),
            )
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            name_label = QLabel(_tr("第 {0} 轨").format(number), row_widget)
            _identity(
                name_label, f"mixdownReverbSendTrack{number}Label",
                _tr("第 {0} 轨").format(number),
            )
            slider = _FineSlider(
                Qt.Horizontal, reset_value=100, normal_step=5, fine_step=1,
                parent=row_widget,
            )
            slider.setRange(0, 100)
            _identity(
                slider, f"mixdownReverbSendTrack{number}Slider",
                _tr("第 {0} 轨混响发送百分比滑条").format(number),
                _tr("方向键每次 5%，Shift+方向键每次 1%，双击恢复 100%。"),
                focus=True,
            )
            value = _ResetSpinBox(100, row_widget)
            value.setRange(0, 100)
            value.setSingleStep(1)
            value.setSuffix(" %")
            _identity(
                value, f"mixdownReverbSendTrack{number}Value",
                _tr("第 {0} 轨混响发送数值百分比").format(number),
                _tr("可直接输入 0 到 100%；双击恢复 100%。"),
                focus=True,
            )
            name_label.setBuddy(value)
            row_layout.addWidget(name_label)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(value)
            layout.addWidget(row_widget)

            row = {
                "index": index,
                "widget": row_widget,
                "label": name_label,
                "slider": slider,
                "value": value,
                "percent": 100.0,
                "display_name": "",
            }
            self.rows.append(row)
            slider.valueChanged.connect(
                lambda new_value, i=index: self._from_slider(i, new_value))
            value.valueChanged.connect(
                lambda new_value, i=index: self._from_value(i, new_value))
            QWidget.setTabOrder(slider, value)

        self._tracks: list[dict[str, Any]] = []
        self.update_tracks(
            tracks if tracks is not None else [{}, {}, {}])

    def update_tracks(self, tracks: Any) -> None:
        if isinstance(tracks, (str, bytes, Mapping)):
            raise TypeError("tracks must be a sequence of one to three entries")
        try:
            items = list(tracks)
        except TypeError as error:
            raise TypeError("tracks must be a sequence") from error
        if not 1 <= len(items) <= MAX_TRACKS:
            raise ValueError("tracks must contain one to three entries")

        parsed = [self._parse_track(index, item)
                  for index, item in enumerate(items)]

        for index, row in enumerate(self.rows):
            active = index < len(parsed)
            row["widget"].setVisible(active)
            if not active:
                continue
            track = parsed[index]
            display_name = track["display_name"]
            row["display_name"] = display_name
            row["percent"] = track["percent"]
            row["label"].setText(display_name)
            row["label"].setAccessibleName(display_name)
            row["slider"].setAccessibleName(
                _tr("{0}混响发送百分比滑条").format(display_name))
            row["value"].setAccessibleName(
                _tr("{0}混响发送数值百分比").format(display_name))
            _set_value_silently(row["slider"], int(round(track["percent"])))
            _set_value_silently(row["value"], int(round(track["percent"])))
            row["widget"].setEnabled(track["enabled"])
            row["widget"].setProperty("trackIndex", index)
            row["widget"].setProperty("reverbSend", track["percent"])

        self._tracks = parsed
        self._active_count = len(parsed)

    def track_count(self) -> int:
        return self._active_count

    def send_values(self) -> list[float]:
        return [float(row["percent"]) for row in self.rows[:self._active_count]]

    def normalized_send_values(self) -> list[float]:
        return [value / 100.0 for value in self.send_values()]

    def set_send(self, index: int, value: float, *,
                 normalized: bool = False, emit: bool = False) -> bool:
        if not 0 <= int(index) < self._active_count:
            raise IndexError("track index out of range")
        number = _finite_float(value, "reverb_send")
        if normalized:
            number *= 100.0
        number = _clamp(number, 0.0, 100.0)
        return self._commit_send(int(index), number, emit=emit)

    def _parse_track(self, index: int, item: Any) -> dict[str, Any]:
        default_name = _tr("第 {0} 轨").format(index + 1)
        if isinstance(item, Mapping):
            display_name = (item.get("display_name") or item.get("name")
                            or item.get("label") or default_name)
            enabled = _bool_value(
                item.get("enabled", item.get("active", True)), True)
            if "reverb_send_percent" in item:
                percent = _finite_float(
                    item["reverb_send_percent"], "reverb_send_percent")
            elif "reverb_send_normalized" in item:
                percent = _finite_float(
                    item["reverb_send_normalized"],
                    "reverb_send_normalized") * 100.0
            elif "reverb_send" in item:
                raw = _finite_float(item["reverb_send"], "reverb_send")
                # The v3 request schema uses 0..1. Values above 1 are accepted
                # as already-percent for UI-side callers.
                percent = raw * 100.0 if 0.0 <= raw <= 1.0 else raw
            else:
                percent = _finite_float(item.get("send", 100.0), "send")
        else:
            display_name = default_name
            enabled = True
            percent = _finite_float(item, "reverb_send")
        return {
            "display_name": str(display_name),
            "enabled": bool(enabled),
            "percent": _clamp(percent, 0.0, 100.0),
        }

    def _commit_send(self, index: int, value: float, *, emit: bool) -> bool:
        row = self.rows[index]
        number = float(round(_clamp(value, 0.0, 100.0)))
        changed = number != row["percent"]
        row["percent"] = number
        _set_value_silently(row["slider"], int(number))
        _set_value_silently(row["value"], int(number))
        row["widget"].setProperty("reverbSend", number)
        if changed and emit:
            self.sendChanged.emit(index, number)
        return changed

    def _from_slider(self, index: int, value: int) -> None:
        self._commit_send(index, float(value), emit=True)

    def _from_value(self, index: int, value: int) -> None:
        self._commit_send(index, float(value), emit=True)


class EqBandStripControls(QWidget):
    """Four compact EQ band editors with optional curve-view synchronization."""

    bandsChanged = pyqtSignal(list)

    def __init__(self, bands: Any = None, curve_view: Any = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _identity(
            self, CONTROL_NAMES["eq_bands"], _tr("四段均衡器频段"),
            _tr("四段频率、增益、Q 与启用状态；修改立即发出 bandsChanged。"),
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QWidget(self)
        _identity(header, "mixdownEqBandsHeader", _tr("四段均衡器标题行"))
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setHorizontalSpacing(8)
        header_layout.setVerticalSpacing(2)
        header_layout.setColumnStretch(1, 1)
        headers = (
            ("频段", "mixdownEqHeaderBand", _tr("频段")),
            ("频率", "mixdownEqHeaderFrequency", _tr("频率")),
            ("增益", "mixdownEqHeaderGain", _tr("增益")),
            ("Q", "mixdownEqHeaderQ", _tr("品质因数 Q")),
            ("启用", "mixdownEqHeaderEnabled", _tr("启用")),
        )
        for column, (text, object_name, accessible) in enumerate(headers):
            item = QLabel(_tr(text), header)
            _identity(item, object_name, accessible)
            header_layout.addWidget(item, 0, column)
        layout.addWidget(header)

        self.rows: list[dict[str, Any]] = []
        self._band_types = list(_BAND_TYPES)
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._curve_view = None
        self._curve_setter = None
        self._curve_signal = None
        self._applying_to_curve = False

        for index, default in enumerate(DEFAULT_EQ_BANDS):
            number = index + 1
            row_widget = QWidget(self)
            _identity(
                row_widget, f"mixdownEqBand{number}Row",
                _tr("第 {0} 段均衡器").format(number),
            )
            row = QGridLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setHorizontalSpacing(8)
            row.setVerticalSpacing(2)
            row.setColumnStretch(1, 1)

            type_label = QLabel(
                _tr(_BAND_LABELS[default["type"]]), row_widget)
            _identity(
                type_label, f"mixdownEqBand{number}Type",
                _tr("第 {0} 段：{1}").format(
                    number, _tr(_BAND_LABELS[default["type"]])),
            )

            frequency = _ResetDoubleSpinBox(default["freq_hz"], row_widget)
            frequency.setRange(EQ_MIN_FREQUENCY_HZ, EQ_MAX_FREQUENCY_HZ)
            frequency.setDecimals(0)
            frequency.setSingleStep(10.0)
            frequency.setSuffix(" Hz")
            _identity(
                frequency, f"mixdownEqBand{number}Freq",
                _tr("第 {0} 段频率 Hz").format(number),
                _tr("范围 10 到 20000 Hz；双击恢复该段预设起点。"),
                focus=True,
            )

            gain = _ResetDoubleSpinBox(default["gain_db"], row_widget)
            gain.setRange(EQ_MIN_GAIN_DB, EQ_MAX_GAIN_DB)
            gain.setDecimals(1)
            gain.setSingleStep(0.5)
            gain.setSuffix(" dB")
            _identity(
                gain, f"mixdownEqBand{number}Gain",
                _tr("第 {0} 段增益 dB").format(number),
                _tr("范围 -24 到 24 dB；双击恢复该段预设起点。"),
                focus=True,
            )

            q_value = _ResetDoubleSpinBox(default["q"], row_widget)
            q_value.setRange(EQ_MIN_Q, EQ_MAX_Q)
            q_value.setDecimals(2)
            q_value.setSingleStep(0.1)
            _identity(
                q_value, f"mixdownEqBand{number}Q",
                _tr("第 {0} 段品质因数 Q").format(number),
                _tr("范围 0.10 到 18.00；双击恢复该段预设起点。"),
                focus=True,
            )

            enabled = QCheckBox(_tr("启用"), row_widget)
            _identity(
                enabled, f"mixdownEqBand{number}Enabled",
                _tr("启用第 {0} 段均衡器").format(number),
                _tr("按空格切换该频段启用状态。"),
                focus=True,
            )

            row.addWidget(type_label, 0, 0)
            row.addWidget(frequency, 0, 1)
            row.addWidget(gain, 0, 2)
            row.addWidget(q_value, 0, 3)
            row.addWidget(enabled, 0, 4)
            layout.addWidget(row_widget)

            item = {
                "index": index,
                "widget": row_widget,
                "type_label": type_label,
                "freq": frequency,
                "gain": gain,
                "q": q_value,
                "enabled": enabled,
            }
            self.rows.append(item)
            frequency.valueChanged.connect(
                lambda _value, i=index: self._from_controls(i))
            gain.valueChanged.connect(
                lambda _value, i=index: self._from_controls(i))
            q_value.valueChanged.connect(
                lambda _value, i=index: self._from_controls(i))
            enabled.toggled.connect(
                lambda _checked, i=index: self._from_controls(i))
            QWidget.setTabOrder(frequency, gain)
            QWidget.setTabOrder(gain, q_value)
            QWidget.setTabOrder(q_value, enabled)

        self._bands = _copy_bands(DEFAULT_EQ_BANDS)
        self.set_bands(bands if bands is not None else DEFAULT_EQ_BANDS,
                       emit=False, sync_curve=False)
        if curve_view is not None:
            self.bind_curve_view(curve_view)

        self.band_controls = self.rows

    def bands(self) -> list[dict[str, Any]]:
        return _copy_bands(self._bands)

    def sample_rate(self) -> float:
        return self._sample_rate

    def frequency_limit_hz(self) -> float:
        return eq_frequency_limit_hz(self._sample_rate)

    def set_sample_rate(self, sample_rate: float) -> None:
        """Follow the render report and project inaccessible EQ values."""
        rate = valid_sample_rate(sample_rate)
        if rate == self._sample_rate:
            return
        limit = eq_frequency_limit_hz(rate)
        self._sample_rate = rate
        for row in self.rows:
            blocked = row["freq"].blockSignals(True)
            try:
                row["freq"].setRange(EQ_MIN_FREQUENCY_HZ, limit)
                row["freq"].setValue(min(float(row["freq"].value()), limit))
            finally:
                row["freq"].blockSignals(blocked)
        self._bands = [
            project_eq_band(band, sample_rate=rate) for band in self._bands
        ]
        self._write_rows(self._bands)
        if self._curve_view is not None:
            curve_setter = getattr(self._curve_view, "set_sample_rate", None)
            if callable(curve_setter):
                curve_setter(rate)
        self._apply_to_curve(self._bands)

    def set_bands(self, bands: Any, *, emit: bool = False,
                  sync_curve: bool = True) -> None:
        normalized = self._normalize_bands(bands)
        self._bands = _copy_bands(normalized)
        self._write_rows(normalized)
        if emit:
            self.bandsChanged.emit(_copy_bands(normalized))
        if sync_curve:
            self._apply_to_curve(normalized)

    def bind_curve_view(
        self,
        view: Any,
        *,
        setter: Callable[[list[dict[str, Any]]], Any] | str | None = None,
        signal: Any | str | None = None,
    ) -> Any:
        """Bind a compatible EqCurveView without importing its module.

        Supported setter names: set_bands, setBands, update_bands,
        set_band_values. Supported list signal names: bandsChanged,
        bands_changed, eqBandsChanged, eq_bands_changed. Explicit adapters win.
        """

        if view is self:
            raise ValueError("EqBandStripControls cannot bind itself")
        self.detach_curve_view()

        if isinstance(setter, str):
            setter_fn = getattr(view, setter, None)
        elif setter is None:
            setter_fn = next(
                (getattr(view, name, None)
                 for name in ("set_bands", "setBands", "update_bands",
                              "set_band_values")
                 if callable(getattr(view, name, None))),
                None,
            )
        else:
            setter_fn = setter
        if not callable(setter_fn):
            raise TypeError("curve view needs a callable bands setter")

        if isinstance(signal, str):
            signal_obj = getattr(view, signal, None)
        elif signal is None:
            signal_obj = next(
                (getattr(view, name, None)
                 for name in ("bandsChanged", "bands_changed",
                              "eqBandsChanged", "eq_bands_changed")
                 if hasattr(getattr(view, name, None), "connect")),
                None,
            )
        else:
            signal_obj = signal
        if signal_obj is None or not hasattr(signal_obj, "connect"):
            raise TypeError("curve view needs a bands list signal")

        signal_obj.connect(self._from_curve)
        self._curve_view = view
        self._curve_setter = setter_fn
        self._curve_signal = signal_obj
        self._apply_to_curve(self._bands)
        return view

    def set_curve_view(self, view: Any, **kwargs: Any) -> Any:
        return self.bind_curve_view(view, **kwargs)

    def detach_curve_view(self) -> None:
        signal_obj = self._curve_signal
        if signal_obj is not None:
            try:
                signal_obj.disconnect(self._from_curve)
            except (RuntimeError, TypeError):
                pass
        self._curve_view = None
        self._curve_setter = None
        self._curve_signal = None
        self._applying_to_curve = False

    def reset_band(self, index: int) -> None:
        if not 0 <= index < EQ_BAND_COUNT:
            raise IndexError("EQ band index out of range")
        for field in ("freq", "gain", "q"):
            self.rows[index][field].reset()
        self.rows[index]["enabled"].setChecked(
            bool(DEFAULT_EQ_BANDS[index]["enabled"]))

    def _normalize_bands(self, bands: Any) -> list[dict[str, Any]]:
        if isinstance(bands, Mapping) and "bands" in bands:
            bands = bands["bands"]
        if isinstance(bands, (str, bytes)):
            raise TypeError("bands must be a four-item sequence")
        try:
            items = list(bands)
        except TypeError as error:
            raise TypeError("bands must be a four-item sequence") from error
        if len(items) != EQ_BAND_COUNT:
            raise ValueError("EqBandStripControls requires exactly four bands")

        normalized = []
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise TypeError(f"band {index + 1} must be a mapping")
            default = DEFAULT_EQ_BANDS[index]
            band_type = str(item.get("type", default["type"])).strip().lower()
            if band_type not in _BAND_LABELS:
                raise ValueError(f"unsupported EQ band type: {band_type}")
            frequency = _finite_float(
                item.get("freq_hz", item.get("freq", default["freq_hz"])),
                f"band {index + 1} freq_hz",
            )
            gain = _finite_float(
                item.get("gain_db", item.get("gain", default["gain_db"])),
                f"band {index + 1} gain_db",
            )
            q_value = _finite_float(
                item.get("q", item.get("Q", default["q"])),
                f"band {index + 1} q",
            )
            if not 10.0 <= frequency <= 20000.0:
                raise ValueError("EQ frequency must be between 10 and 20000 Hz")
            if not -24.0 <= gain <= 24.0:
                raise ValueError("EQ gain must be between -24 and 24 dB")
            if not 0.1 <= q_value <= 18.0:
                raise ValueError("EQ Q must be between 0.1 and 18")
            normalized.append({
                "type": band_type,
                "freq_hz": frequency,
                "gain_db": gain,
                "q": q_value,
                "enabled": _bool_value(
                    item.get("enabled", default["enabled"]),
                    bool(default["enabled"])),
            })
            normalized[-1] = project_eq_band(
                normalized[-1], sample_rate=self._sample_rate)
        return normalized

    def _apply_type_limits(self, index: int, band_type: str) -> None:
        row = self.rows[index]
        limit = self.frequency_limit_hz()
        blocked = row["freq"].blockSignals(True)
        try:
            row["freq"].setRange(EQ_MIN_FREQUENCY_HZ, limit)
            row["freq"].setValue(min(float(row["freq"].value()), limit))
        finally:
            row["freq"].blockSignals(blocked)
        highpass = band_type == "highpass"
        row["gain"].setEnabled(not highpass)
        row["q"].setEnabled(not highpass)
        if highpass:
            explanation = _tr(
                "高通频率有效；增益固定为 0 dB，Q 固定为 {0:.3f}。"
            ).format(EQ_HIGHPASS_Q)
            row["gain"].setToolTip(explanation)
            row["q"].setToolTip(explanation)
            row["gain"].setAccessibleDescription(explanation)
            row["q"].setAccessibleDescription(explanation)
        else:
            row["gain"].setToolTip("")
            row["q"].setToolTip("")
            row["gain"].setAccessibleDescription(
                _tr("范围 -24 到 24 dB；双击恢复该段预设起点。"))
            row["q"].setAccessibleDescription(
                _tr("范围 0.10 到 18.00；双击恢复该段预设起点。"))

    def _write_rows(self, bands: list[dict[str, Any]]) -> None:
        for index, (row, band) in enumerate(zip(self.rows, bands)):
            self._band_types[index] = band["type"]
            self._apply_type_limits(index, band["type"])
            label = _tr(_BAND_LABELS[band["type"]])
            row["type_label"].setText(label)
            row["type_label"].setAccessibleName(
                _tr("第 {0} 段：{1}").format(index + 1, label))
            _set_value_silently(row["freq"], band["freq_hz"])
            _set_value_silently(row["gain"], band["gain_db"])
            _set_value_silently(row["q"], band["q"])
            _set_checked_silently(row["enabled"], band["enabled"])

    def _collect_bands(self) -> list[dict[str, Any]]:
        result = []
        for index, row in enumerate(self.rows):
            result.append({
                "type": self._band_types[index],
                "freq_hz": float(row["freq"].value()),
                "gain_db": float(row["gain"].value()),
                "q": float(row["q"].value()),
                "enabled": bool(row["enabled"].isChecked()),
            })
        return result

    def _from_controls(self, _index: int) -> None:
        snapshot = self._collect_bands()
        self._bands = _copy_bands(snapshot)
        self.bandsChanged.emit(_copy_bands(snapshot))
        self._apply_to_curve(snapshot)

    def _from_curve(self, *args: Any) -> None:
        if self._applying_to_curve:
            return
        bands = args[0] if args else None
        if bands is None and self._curve_view is not None:
            possible = getattr(self._curve_view, "bands", None)
            if callable(possible):
                bands = possible()
            elif possible is not None:
                bands = possible
        if bands is None:
            return
        self.set_bands(bands, emit=True, sync_curve=False)

    def _apply_to_curve(self, bands: list[dict[str, Any]]) -> None:
        setter = self._curve_setter
        if setter is None or self._applying_to_curve:
            return
        self._applying_to_curve = True
        try:
            setter(_copy_bands(bands))
        finally:
            self._applying_to_curve = False
