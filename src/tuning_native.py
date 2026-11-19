"""Read the original part and note records behind the common tuning layout."""

from dataclasses import dataclass


def at_path(data, path):
    for field in path:
        data = data[field]
    return data


def native_group_path(format, track, index):
    if format in ('vpr', 'vsqx'):
        return ('tracks', track, 'parts', index)
    if format == 'acep':
        return ('project', 'tracks', track, 'patterns', index)
    if format == 'ufdata':
        return ('project', 'tracks', track)
    if format == 'ustx':
        return ('voice_parts', index)
    return ('tracks', track, 'mainGroup')


@dataclass(frozen=True)
class NativePart:
    placement: object
    path: tuple
    raw: object
    notes: tuple | list


@dataclass(frozen=True)
class NativeMedia:
    track: int | None
    raw: object
    reference: str | None
    embedded: bool = False


class NativeProject:
    """Keep source data addressable even where tuning writes are protected."""

    def __init__(self, data, format, layout):
        self.source = data
        self.format = format
        self.clock = layout.clock
        if format == 'vsqx':
            self.tracks = layout.native_tracks
        else:
            root = data['project'] if format in ('acep', 'ufdata') else data
            self.tracks = tuple(root['tracks'])
        self.parts = {}
        for track in layout.tracks:
            for placement in track.placements:
                path = (('library', placement.library_index)
                        if format == 'svp' and placement.library_index is not None
                        else native_group_path(format, track.index, getattr(placement, 'index', None)))
                if format == 'vsqx':
                    raw, notes = layout.native_parts[track.index, placement.index]
                else:
                    raw = at_path(data, path)
                    notes = raw['notes']
                self.parts[track.index, placement.key] = NativePart(placement, path, raw, notes)
        self.media = tuple(self._media(data, layout))

    def _media(self, data, layout):
        if self.format == 'svp':
            for i, track in enumerate(self.tracks):
                if not isinstance(track, dict):
                    continue
                ref = track.get('mainRef')
                if isinstance(ref, dict) and ref.get('isInstrumental') is True:
                    audio = ref.get('audio')
                    yield NativeMedia(i, ref, audio.get('filename') if isinstance(audio, dict) else None)
        elif self.format == 'ustx':
            for part in data.get('wave_parts', []):
                yield NativeMedia(part['track_no'], part, part.get('relative_path'))
        elif self.format == 'vpr':
            for i, track in enumerate(self.tracks):
                if track.get('type') == 1:
                    parts = track.get('parts')
                    for part in parts if isinstance(parts, list) else ():
                        if not isinstance(part, dict):
                            continue
                        wav = part.get('wav')
                        name = wav.get('name') if isinstance(wav, dict) else None
                        yield NativeMedia(i, part, name, embedded=bool(name))
        elif self.format == 'vsqx':
            for node, reference in layout.native_media:
                yield NativeMedia(None, node, reference)
        elif self.format == 'acep':
            for i, track in enumerate(self.tracks):
                if track.get('type') == 'audio':
                    parts = track.get('patterns')
                    for part in parts if isinstance(parts, list) else ():
                        if isinstance(part, dict):
                            yield NativeMedia(i, part, part.get('path'))

    def note(self, track, placement, index):
        return self.parts[track, placement].notes[index]
