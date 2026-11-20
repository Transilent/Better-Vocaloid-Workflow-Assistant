"""Small, explicitly bound native note parameters for the tuning inspector."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Detail:
    label: str
    unit: str
    minimum: int
    maximum: int
    decimals: int = 0


DETAILS = {
    'fine_tune': Detail('微调音高', '¢', -100, 100, 1),
    'velocity': Detail('辅音力度', 'VEL', 0, 127),
    'vibrato_length': Detail('颤音范围', '%', 0, 100, 1),
    'vibrato_depth': Detail('颤音深度', '¢', 5, 200, 1),
    'vibrato_period': Detail('颤音周期', 'ms', 5, 500, 1),
    'vibrato_in': Detail('颤音渐入', '%', 0, 100, 1),
    'vibrato_out': Detail('颤音渐出', '%', 0, 100, 1),
    'vibrato_shift': Detail('颤音相位', '%', 0, 100, 1),
    'vibrato_drift': Detail('颤音中心', '%', -100, 100, 1),
}
VIBRATO_PATHS = {
    'vibrato_' + key: ('vibrato', key)
    for key in ('length', 'depth', 'period', 'in', 'out', 'shift', 'drift')
}
FIELDS = {
    'svp': {'fine_tune': ('detune',)},
    'ustx': {'fine_tune': ('tuning',), **VIBRATO_PATHS},
    'vpr': {'velocity': ('velocity',)},
    'vsqx': {'velocity': ('velocity',)},
}


def valid_value(field, value):
    spec = DETAILS[field]
    return (type(value) in (int, float) and spec.minimum <= value <= spec.maximum
            and (spec.decimals or int(value) == value))


def integer_field(field, path):
    return DETAILS[field].decimals == 0 or path == ('tuning',)


def read_fields(format, note, *, pitch_protected, consonant_protected):
    fields, values = {}, {}
    for field, path in FIELDS.get(format, {}).items():
        if (field == 'fine_tune' and pitch_protected) or (field == 'velocity' and consonant_protected):
            continue
        parent = note if len(path) == 1 else note.get(path[0])
        value = parent.get(path[-1]) if isinstance(parent, dict) else None
        if valid_value(field, value) and (not integer_field(field, path) or int(value) == value):
            fields[field] = path
            values[field] = value
    fades = ('vibrato_in', 'vibrato_out')
    if not all(field in values for field in fades) or sum(values[field] for field in fades) > 100:
        for field in fades:
            fields.pop(field, None)
            values.pop(field, None)
    return fields, values


def field_views(fields, values):
    result = []
    for field, spec in DETAILS.items():
        if field not in fields:
            continue
        other = 'vibrato_out' if field == 'vibrato_in' else 'vibrato_in'
        maximum = (100 - values.get(other, 0) if field in ('vibrato_in', 'vibrato_out')
                   else spec.maximum)
        result.append({'key': field, 'label': spec.label, 'unit': spec.unit, 'value': values[field],
                       'minimum': spec.minimum, 'maximum': maximum,
                       'decimals': 0 if integer_field(field, fields[field]) else spec.decimals})
    return result
