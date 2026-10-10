"""Structural validation and review hints, without silently changing pitches."""
from collections import Counter
import mido


def inspect_midi(path, allow_empty=False):
    midi = mido.MidiFile(str(path), charset="utf8")
    if midi.type not in (0, 1) or not 0 < midi.ticks_per_beat < 32768:
        raise ValueError("MIDI 格式或时间分辨率不受支持。")
    for track in midi.tracks:
        if not track or track[-1].type != "end_of_track":
            raise ValueError("MIDI 轨道缺少完整的结束事件。")
        if any(not isinstance(message.time, int) or not 0 <= message.time <= 0x0fffffff for message in track):
            raise ValueError("MIDI 时间事件超出标准文件范围。")
    notes = []
    active = {}
    seconds = 0.0
    lyric_count = 0
    for message in midi:
        seconds += message.time
        if message.type == "lyrics" and message.text:
            lyric_count += 1
        if message.type == "note_on" and message.velocity > 0:
            key = (message.channel, message.note)
            if key in active:
                raise ValueError(f"MIDI 同音符重复开启而未关闭：{message.note} @ {seconds:.3f}s")
            active[key] = seconds
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            key = (message.channel, message.note)
            if key not in active:
                raise ValueError(f"MIDI 音符关闭前没有开启：{message.note}")
            start = active.pop(key)
            if seconds <= start:
                raise ValueError("MIDI 存在长度为零或倒序音符。")
            notes.append({"pitch": message.note, "start": start, "end": seconds,
                          "duration": seconds - start, "channel": message.channel})
    if active:
        raise ValueError("MIDI 存在未关闭的音符。")
    if not notes and not allow_empty:
        raise ValueError("MIDI 没有音符，请检查音频或模型参数。")
    notes.sort(key=lambda n: (n["start"], n["pitch"]))
    hints = []
    for index, note in enumerate(notes):
        reasons = []
        if note["duration"] < 0.06:
            reasons.append("极短音符，请核对装饰音或误检")
        if index and abs(note["pitch"] - notes[index-1]["pitch"]) >= 12:
            reasons.append("较前音跳进至少八度，请核对音高")
        if reasons:
            hints.append({**note, "reason": "；".join(reasons)})
    return {"notes": len(notes), "lyrics_events": lyric_count,
            "format": midi.type, "tracks": len(midi.tracks), "lyric_encoding": "utf-8",
            "first_note_seconds": notes[0]["start"] if notes else None,
            "last_note_seconds": max((n["end"] for n in notes), default=0),
            "last_event_seconds": seconds,
            "pitch_range": [min(n["pitch"] for n in notes), max(n["pitch"] for n in notes)] if notes else None,
            "pitch_counts": dict(sorted(Counter(n["pitch"] for n in notes).items())),
            "review_hints": hints}
