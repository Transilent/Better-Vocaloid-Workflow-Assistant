"""Merge independently transcribed voices onto a common, untrimmed timeline."""
from pathlib import Path
import mido


def merge_voices(sources, destination):
    result = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    result.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("set_tempo", tempo=500000, time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    reports = []
    for channel, (name, path) in enumerate(sources):
        source = mido.MidiFile(str(path), charset="utf8")
        track = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
        seconds = 0.0
        previous_tick = 0
        count = 0
        # MidiFile iteration converts all input tempo changes/PPQ to seconds.
        for message in source:
            seconds += message.time
            if message.type in ("set_tempo", "time_signature", "track_name", "end_of_track"):
                continue
            tick = round(mido.second2tick(seconds, result.ticks_per_beat, 500000))
            changes = {"time": tick - previous_tick}
            if not message.is_meta and hasattr(message, "channel"):
                changes["channel"] = channel
            track.append(message.copy(**changes))
            previous_tick = tick
            if message.type == "note_on" and message.velocity > 0:
                count += 1
        track.append(mido.MetaMessage("end_of_track", time=0))
        result.tracks.append(track)
        reports.append({"name": name, "notes": count, "source": str(path), "channel": channel})
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(str(destination))
    return {"format": 1, "voice_tracks": reports, "tempo": 120,
            "ticks_per_beat": 480, "timeline_offset_seconds": 0}
