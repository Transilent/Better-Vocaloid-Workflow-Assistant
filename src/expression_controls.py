"""Application expression boundaries shared by style, fitting and adaptation.

These are VWA's existing safety limits, not a universal synthesizer schema.
Calibration probe steps and conservative adaptation steps have distinct purposes
and remain separate even when they operate on the same expression channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ExpressionControl:
    minimum: float
    maximum: float
    neutral: float
    calibration_step: float
    adaptation_step: float | None = None
    label: str | None = None


STYLE_CONTROL_NAMES = ('loudness', 'tension', 'breathiness', 'voicing')
TIMBRE_CONTROL_NAMES = ('tension', 'breathiness', 'voicing')
CONTROL_SPECS = MappingProxyType({
    'loudness': ExpressionControl(-48., 12., 0., 3., 1.5, '力度'),
    'tension': ExpressionControl(-1., 1., 0., .3, .08, '张力'),
    'breathiness': ExpressionControl(-1., 1., 0., .3, .08, '气声'),
    'voicing': ExpressionControl(0., 1., 1., -.2, .04, '发声强度'),
    'gender': ExpressionControl(-1., 1., 0., .25),
    'toneShift': ExpressionControl(-800., 800., 0., 200.),
})


def control_limits(names=None):
    """Return an independent legacy-compatible (low, high, neutral) mapping."""
    return {name: (CONTROL_SPECS[name].minimum, CONTROL_SPECS[name].maximum,
                   CONTROL_SPECS[name].neutral)
            for name in (CONTROL_SPECS if names is None else names)}


def calibration_controls():
    """Return the existing calibration tuple shape, including signed probes."""
    return {name: (spec.minimum, spec.maximum, spec.neutral, spec.calibration_step)
            for name, spec in CONTROL_SPECS.items()}


def adaptation_controls():
    """Return the conservative adaptation profile for supported style channels."""
    return {name: {'low': spec.minimum, 'high': spec.maximum,
                   'neutral': spec.neutral, 'step': spec.adaptation_step}
            for name, spec in CONTROL_SPECS.items() if spec.adaptation_step is not None}


def neutral_values(names=None):
    return {name: CONTROL_SPECS[name].neutral
            for name in (CONTROL_SPECS if names is None else names)}


def control_labels(names=STYLE_CONTROL_NAMES):
    return {name: CONTROL_SPECS[name].label for name in names}
