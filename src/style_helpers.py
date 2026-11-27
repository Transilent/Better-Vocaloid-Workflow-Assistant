"""Shared pure geometry, curve, and option helpers for style presets and style pitch."""
from __future__ import annotations

import math

PHRASE_GAP = .25
DETAIL_DEFAULTS = {'onset': 1., 'sustain': 1., 'connection': 1., 'release': 1.}
NOTE_ENVELOPE_PRESETS = frozenset({'gentle', 'sad', 'bold', 'chinese_opera'})


def detail_settings(value=None):
    """Validate independent phase multipliers, without altering global strength."""
    if value is None:
        return dict(DETAIL_DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError('风格分段控制必须为对象。')
    unknown = set(value) - set(DETAIL_DEFAULTS)
    if unknown:
        raise ValueError('未知风格分段控制：' + ','.join(sorted(map(str, unknown))))
    result = dict(DETAIL_DEFAULTS)
    for key, amount in value.items():
        if type(amount) not in (int, float) or not math.isfinite(amount) or not 0 <= amount <= 1:
            raise ValueError('风格分段强度应为 0–100%。')
        result[key] = float(amount)
    return result


def time_range_settings(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not math.isfinite(value[1] - value[0]) or value[1] - value[0] < .025):
        raise ValueError('风格时间选区无效。')
    return tuple(float(v) for v in value)


def _phrases(track, clock):
    ref = track.get('mainRef', {})
    if (not isinstance(ref, dict) or ref.get('isInstrumental') or track.get('groups')
            or ref.get('blickAbsoluteBegin', 0) != 0 or ref.get('blickAbsoluteEnd', -1) != -1):
        raise ValueError('风格预设目前仅支持未裁剪、未链接库组的演唱 mainGroup。')
    offset = ref.get('blickOffset', 0)
    if type(offset) is not int:
        raise ValueError('风格轨道的时间偏移必须为整数。')
    group = track.get('mainGroup')
    if not isinstance(group, dict) or not isinstance(group.get('notes'), list):
        raise ValueError('风格轨道缺少 mainGroup 音符。')
    spans = []
    for note in group['notes']:
        if (not isinstance(note, dict) or type(note.get('onset')) is not int
                or type(note.get('duration')) is not int or note['duration'] <= 0):
            raise ValueError('风格轨道包含无效音符时间。')
        lyric = str(note.get('lyrics', '')).strip().lower()
        if lyric in ('sil', 'sp', 'pau') or (lyric.startswith('br') and lyric[2:].isdigit()) or lyric == 'br':
            continue
        a = clock.to_seconds(note['onset'] + offset)
        b = clock.to_seconds(note['onset'] + note['duration'] + offset)
        if not all(math.isfinite(v) for v in (a, b)) or b <= a:
            raise ValueError('风格音符时间无法换算。')
        spans.append((a, b))
    phrases = []
    for a, b in sorted(spans):
        if phrases and a - phrases[-1][1] <= PHRASE_GAP + 1e-9:
            phrases[-1] = (phrases[-1][0], max(b, phrases[-1][1]))
        else:
            phrases.append((a, b))
    return phrases, offset


def _curve(curve, *, float_positions=True, track_index=None, channel=None):
    if curve is None:
        return [], []
    from calibration_refinement import _curve_points

    parsed = _curve_points(curve, track_index=track_index, channel=channel, source='style')
    if parsed and curve.get('mode', 'cubic') != 'linear':
        raise ValueError('preserved_nonlinear')
    return [x for x, _ in parsed], [y for _, y in parsed]


def _note_spans(track, clock, offset):
    """Keep new note envelopes off short, non-singing and overlapping notes.

    Neighbours include excluded notes so an overlapping breath/rap note cannot
    accidentally receive another note's expression. _phrases validates timing.
    """
    spans = sorted((clock.to_seconds(n['onset'] + offset),
                    clock.to_seconds(n['onset'] + n['duration'] + offset), i, n)
                   for i, n in enumerate(track['mainGroup']['notes']))
    accepted, skipped = [], []
    preceding_end = -math.inf
    for j, (a, b, i, note) in enumerate(spans):
        lyric = str(note.get('lyrics', '')).strip().lower()
        attributes = note.get('attributes', {})
        reason = None
        if (lyric in ('sil', 'sp', 'pau', 'br') or (lyric.startswith('br') and lyric[2:].isdigit())
                or note.get('musicalType') == 'rap'
                or (isinstance(attributes, dict) and attributes.get('muted') is True)):
            reason = 'non_singing_or_rap'
        elif b - a < .16 - 1e-9:
            reason = 'short_note_preserved'
        elif preceding_end > a + 1e-8 or (j + 1 < len(spans) and spans[j + 1][0] < b - 1e-8):
            reason = 'overlapping_notes'
        if reason:
            skipped.append({'note_index': i, 'reason': reason, 'start': a, 'end': b})
        else:
            accepted.append((a, b))
        preceding_end = max(preceding_end, b)
    return accepted, skipped


def _range_gate(times, time_range):
    import numpy as np

    if time_range is None:
        return np.ones(len(times))
    a, b = time_range
    edge = min(.02, (b - a) * .2)
    u = np.minimum(np.clip((times - a) / edge, 0., 1.), np.clip((b - times) / edge, 0., 1.))
    return u * u * (3. - 2. * u)
