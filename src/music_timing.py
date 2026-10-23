"""Constant export tempo; recognition and audio remain on a seconds timeline."""
import math

DEFAULT_BPM = 120.
MIN_BPM, MAX_BPM = 20., 400.


def validate_bpm(value):
    """None retains the legacy time reference, without claiming tempo detection."""
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not MIN_BPM <= value <= MAX_BPM:
        raise ValueError('BPM 请填写 20–400 之间的数值，或关闭手动指定。')
    return float(value)


def effective_bpm(request):
    value = validate_bpm(request.get('bpm'))
    return DEFAULT_BPM if value is None else value


def install_midi_tempo(bpm):
    """Keep fractional BPM through the upstream integer-only MIDI call site."""
    from inference.pipeline import auto_lyric_hybrid
    from inference.io.note_io import _save_midi
    value = effective_bpm({'bpm': bpm})

    def save(notes, path, tempo=DEFAULT_BPM):
        return _save_midi(notes, path, value)

    auto_lyric_hybrid._save_midi = save
