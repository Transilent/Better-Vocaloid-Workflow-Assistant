"""One audio render plan for native audition and sample-aligned stem delivery."""
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf
import soxr

if __name__ == '__main__':
    extra = os.environ.get('VWA_MIXDOWN_EXTRA_PATHS', '').split(os.pathsep)
    sys.path[:0] = [str(Path(__file__).resolve().parent), *(path for path in extra if path)]

from chorus_project import Recording, VARIANTS, sha256 as source_digest


def recording_key(project, track):
    selections = project.edits_for(track.id, 'take')
    if not selections:
        return track.sha
    sources = tuple((edit, take.sha, take.offset) for edit in selections
                    if (take := project.track(edit.source)) is not None)
    return track.sha, track.offset, sources


def monitor_tracks(project, solo):
    return tuple(track for track in project.tracks
                 if (track.id == solo if solo else not track.muted and track.role != 'take'))


def monitor_sources(project, solo):
    rows = monitor_tracks(project, solo)
    visible = {track.id for track in rows}
    takes = {edit.source for edit in project.edits if edit.track in visible and edit.kind == 'take'}
    return (*rows, *(track for track in project.tracks if track.id in takes))


def render_key(project, variant, solo=''):
    rendered = tuple((track.id, recording_key(project, track), track.offset)
                     for track in monitor_tracks(project, solo))
    ids = {row[0] for row in rendered}
    edits = tuple(edit for edit in project.edits
                  if edit.track in ids and edit.kind != 'take'
                  and (variant == 'processed' or edit.kind == variant))
    return (project.first_frame, project.frames, project.rate, rendered, variant, edits)


def _read_take(track, sample_rate, check):
    track.check_source()
    data, rate = sf.read(track.path, dtype='float32', always_2d=True)
    if not np.isfinite(data).all():
        raise ValueError(f'音频包含无效采样：{track.name}')
    check()
    if rate != sample_rate:
        data = soxr.resample(data, rate, sample_rate)
    track.check_source()
    return np.ascontiguousarray(data, dtype=np.float32)


def _window(samples, start, length):
    result = np.zeros((length, samples.shape[1]), dtype=np.float32)
    left, right = max(0, start), min(len(samples), start + length)
    if left < right:
        result[left - start:right - start] = samples[left:right]
    return result


def _crossfade(destination, source, frames):
    if frames <= 0:
        destination[:] = source
        return
    weight = np.ones((len(destination), 1), dtype=np.float32)
    ramp = np.linspace(0., 1., frames, endpoint=False, dtype=np.float32)
    weight[:frames, 0], weight[-frames:, 0] = ramp, ramp[::-1]
    destination[:] = destination * (1 - weight) + source * weight


def _comped_audio(project, track, rate, check):
    audio = _read_take(track, rate, check)
    origin = round(track.offset * rate)
    alternatives = {}
    for choice in project.edits_for(track.id, 'take'):
        take = project.track(choice.source)
        if take is None or take.role != 'take':
            continue
        if take.id not in alternatives:
            alternatives[take.id] = _read_take(take, rate, check)
        start = max(0, round(choice.start * rate) - origin)
        end = min(len(audio), round(choice.end * rate) - origin)
        if end <= start:
            continue
        alternate = _window(alternatives[take.id], origin + start - round(take.offset * rate), end - start)
        if alternate.shape[1] != audio.shape[1]:
            alternate = alternate.mean(axis=1, keepdims=True)
        _crossfade(audio[start:end], alternate, min(round(.016 * rate), (end - start) // 2))
    return audio


def analysis_recording(project, track, folder, check):
    """Speech and F0 read the same pre-effect take selection as the monitor."""
    key = recording_key(project, track)
    if isinstance(key, str):
        return track
    track.check_source()
    for choice in project.edits_for(track.id, 'take'):
        alternate = project.track(choice.source)
        if alternate:
            alternate.check_source()
    path = folder / ('take-' + sha256(repr(key).encode()).hexdigest() + '.wav')
    if not path.is_file():
        folder.mkdir(exist_ok=True, parents=True)
        audio = _comped_audio(project, track, track.rate, check)
        with TemporaryDirectory(dir=folder) as scratch:
            staged = Path(scratch) / 'comp.wav'
            sf.write(staged, audio, track.rate, subtype='FLOAT')
            check()
            staged.replace(path)
    derived = Recording.read(path, track.role, identity=track.id)
    return replace(derived, name=track.name, part=track.part, offset=track.offset,
                   score_lane=track.score_lane, timing_ref=track.timing_ref)


def _pedalboard():
    """Load the already managed optional DSP component via its public API."""
    from mixdown_runtime import cached_optional_packages, component_manifest, optional_packages

    root, spec, _ = component_manifest()
    packages = cached_optional_packages() or optional_packages(root, spec)
    if packages:
        path = str(packages)
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        import pedalboard
    except (ImportError, OSError) as error:
        raise RuntimeError('修音需要混音效果组件（Pedalboard）。可先试听原声或仅对齐。') from error
    return pedalboard


def _pitch_groups(regions, rate, size):
    groups = []
    context = round(.09 * rate)
    for region in sorted(regions, key=lambda edit: edit.start):
        left = max(0, round(region.start * rate))
        right = min(size, round(region.end * rate))
        if right <= left:
            continue
        if groups and groups[-1][-1][1] + 2 * context >= left:
            groups[-1].append((left, right, region))
        else:
            groups.append([(left, right, region)])
    return groups


def _pitch_active(region):
    return any(y for _, y in region.points) if region.points else bool(region.value)


def _shift_pitch(samples, rate, regions, check):
    from chorus_pitch_dsp import blend_pitch, preserve_level, render_pitch, render_vocal_pitch, voiced_short_gap

    context = round(.09 * rate)
    untouched = [(round(edit.start * rate), round(edit.end * rate)) for edit in regions
                 if edit.manual and not _pitch_active(edit)]
    for group in _pitch_groups((row for row in regions if _pitch_active(row)), rate, len(samples)):
        check()
        automatic = rate <= 96000 and all(not row.manual and not row.points and abs(row.value) <= 100.
                                          for _, _, row in group)
        left = max(0, group[0][0] - context)
        right = min(len(samples), group[-1][1] + context)
        shift = np.zeros(right - left, dtype=np.float64)
        spans = []
        previous = None
        for start, end, region in group:
            cents = region.value
            if region.points:
                x, y = zip(*region.points)
                cents = np.interp(np.arange(start, end) / rate, x, y)
            shift[start - left:end - left] = np.asarray(cents) / 100.
            adjacent = spans and spans[-1][1] == start
            if spans and not adjacent and not (previous.manual or region.manual):
                gap = spans[-1][1]
                if (not any(a < start and gap < b for a, b in untouched)
                        and voiced_short_gap(samples, gap, start, rate, .10 if automatic else .058)):
                    shift[gap - left:start - left] = np.linspace(
                        shift[gap - left - 1], shift[start - left], start - gap + 2)[1:-1]
                    adjacent = True
            if adjacent:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
            previous = region
        # Smooth the requested trajectory separately from the audio splice.
        smoothing = .036 if automatic else .012 if any(row.manual or row.points for _, _, row in group) else .018
        window = max(1, round(smoothing * rate))
        edges = np.pad(shift, (window // 2, (window - 1) // 2), mode='edge')
        prefix = np.r_[0., np.cumsum(edges)]
        shift = (prefix[window:] - prefix[:-window]) / window
        dry = samples[left:right]
        clip = np.ascontiguousarray(dry.T)
        if automatic:
            edited = render_vocal_pitch(clip, rate, shift, check).T
        else:
            _pedalboard()
            edited = render_pitch(clip, rate, shift, check).T
        if edited.shape != dry.shape or not np.isfinite(edited).all():
            raise ValueError('修音组件返回的长度、声道或采样无效。')
        mixed = dry.copy()
        for start, stop in spans:
            fade = min(round((.023 if automatic else .016) * rate), (stop - start) // 2)
            interval = slice(start - left, stop - left)
            blend_pitch(mixed[interval], edited[interval], fade)
        level = preserve_level(dry, mixed, rate)
        for start, stop in spans:
            interval = slice(start - left, stop - left)
            fade = min(round(.006 * rate), (stop - start) // 2)
            _crossfade(mixed[interval], level[interval], fade)
            samples[start:stop] = mixed[interval]


def _render_knots(points, tolerance):
    """Join nearly affine runs without erasing the project's editable anchors."""
    kept = [points[0]]
    last = 0
    for i in range(2, len(points)):
        x, y = points[last]
        right, finish = points[i]
        speed = (finish - y) / (right - x)
        if any(abs(y + speed * (at - x) - value) > tolerance for at, value in points[last + 1:i]):
            kept.append(points[i - 1])
            last = i - 1
    return (*kept, points[-1])


def _warp_audio(placed, edit, origin, rate, check):
    """Fit segments between source knots with pitch-preserving time stretch."""
    output, source = zip(*_render_knots(edit.points, .00025 if edit.manual else .003))
    outs = [round(time * rate) - origin for time in output]
    ins = [round(time * rate) - origin for time in source]
    warped = np.zeros((outs[-1] - outs[0], placed.shape[1]), dtype=np.float32)
    seam = round(.005 * rate)
    processor = None
    for i, (a, b) in enumerate(zip(outs, outs[1:])):
        check()
        length = b - a
        consumed = ins[i + 1] - ins[i]
        if not length:
            continue
        tail = min(seam, outs[-1] - b)
        if consumed <= 0 or abs(consumed - length) <= round(.0005 * rate):
            result = _window(placed, ins[i], length + tail)
        else:
            factor = consumed / length
            pad = round(.08 * rate)
            audio = _window(placed, ins[i] - pad, consumed + pad * 2)
            if processor is None:
                processor = _pedalboard().time_stretch
            transformed = processor(np.ascontiguousarray(audio.T), rate, stretch_factor=factor,
                                    preserve_formants=True)
            transformed = np.asarray(transformed, dtype=np.float32).reshape(placed.shape[1], -1).T
            if not np.isfinite(transformed).all():
                raise ValueError('时间拉伸组件返回了无效采样。')
            result = _window(transformed, round(pad / factor), length + tail)
        start = a - outs[0]
        # Use the previous slice's real continuation at the seam.
        fade = min(seam, length) if start else 0
        if fade:
            blend = np.linspace(0., 1., fade, endpoint=False, dtype=np.float32)[:, None]
            result[:fade] = warped[start:start + fade] * (1. - blend) + result[:fade] * blend
        warped[start:start + length + tail] = result
    return warped


def render_track(project, track, variant, check=lambda: None):
    """Render the take into the project's common half-open sample interval."""
    if variant not in VARIANTS:
        raise ValueError('试听版本无效。')
    rate = project.rate
    original = _comped_audio(project, track, rate, check)
    if variant in ('pitch', 'processed'):
        regions = project.edits_for(track.id, 'pitch')
        if any(_pitch_active(row) for row in regions):
            _shift_pitch(original, rate, regions, check)
    placed = _window(original, project.first_frame - round(track.offset * rate), project.frames)
    regions = project.edits_for(track.id, 'timing') if variant in ('timing', 'processed') else ()
    rendered = placed.copy() if regions else placed
    for edit in regions:
        check()
        start = max(0, round(edit.start * rate) - project.first_frame)
        end = min(project.frames, round(edit.end * rate) - project.first_frame)
        shift = round(edit.value * rate)
        if start >= end or (not edit.points and not shift):
            continue
        if edit.points:
            warped = _warp_audio(placed, edit, project.first_frame, rate, check)
            source = _window(warped, start + project.first_frame - round(edit.start * rate), end - start)
        else:
            source = _window(placed, start - shift, end - start)
        _crossfade(rendered[start:end], source, min(round(.018 * rate), (end - start) // 2))
    if variant == 'processed':
        for edit in project.edits_for(track.id, 'gain'):
            start = max(0, round(edit.start * rate) - project.first_frame)
            end = min(project.frames, round(edit.end * rate) - project.first_frame)
            if start < end and edit.value:
                view = rendered[start:end]
                _crossfade(view, view * 10 ** (edit.value / 20.), min(round(.018 * rate), (end - start) // 2))
    return rendered


def render_mix(project, variant, solo, check, progress):
    rows = monitor_tracks(project, solo)
    stereo = np.zeros((project.frames, 2), dtype=np.float32)
    gain = 1. / max(1, len(rows))
    for index, track in enumerate(rows):
        check()
        progress(index, len(rows), track.name)
        audio = render_track(project, track, variant, check)
        if audio.shape[1] == 1:
            stereo += audio * gain
        elif audio.shape[1] == 2:
            stereo += audio * gain
        else:
            stereo += audio.mean(axis=1, keepdims=True) * gain
    peak = float(np.max(np.abs(stereo), initial=0.))
    if peak > .98:
        stereo *= .98 / peak
    stereo.setflags(write=False)
    return stereo


def render_monitor(project, variant, solo, cache, check, progress):
    """Generate pitch-corrected PCM away from the active output thread."""
    rows = monitor_tracks(project, solo)
    audible = {track.id for track in rows}
    pitch = variant in ('pitch', 'processed') and any(
        row.kind == 'pitch' and row.track in audible and _pitch_active(row) for row in project.edits)
    if not pitch:
        return render_mix(project, variant, solo, check, progress)

    from chorus_model_worker import model_batch

    spec = {
        'rate': project.rate, 'variant': variant, 'solo': solo,
        'tracks': [asdict(track) for track in project.tracks],
        'edits': [asdict(edit) for edit in project.edits],
    }
    labels = [track.name for track in rows]
    with model_batch(__file__, spec, cache, check, lambda i, name: progress(i, len(rows), name),
                     labels, '修音试听渲染失败') as folder:
        audio = np.load(folder / 'monitor.npy', allow_pickle=False)
    if audio.dtype != np.float32 or audio.shape != (project.frames, 2) or not np.isfinite(audio).all():
        raise RuntimeError('修音试听返回的采样或时间范围无效。')
    audio.setflags(write=False)
    return audio


def _monitor_process(request):
    from chorus_project import ChorusProject, Edit

    spec = json.loads(request.read_text(encoding='utf-8'))
    tracks = tuple(Recording(**(track | {'stamp': tuple(track['stamp'])})) for track in spec['tracks'])
    edits = tuple(Edit(**(edit | {'points': tuple(map(tuple, edit['points']))})) for edit in spec['edits'])
    project = ChorusProject(rate=spec['rate'], tracks=tracks, edits=edits)

    def progress(index, _total, _name):
        (request.parent / 'progress.txt').write_text(str(index), encoding='utf-8')

    mix = render_mix(project, spec['variant'], spec['solo'], lambda: None, progress)
    np.save(request.parent / 'monitor.npy', mix, allow_pickle=False)


def _stem_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)[:80].rstrip(' .') or '音轨'


def export_stems(project, folder, variant, selection, check, progress):
    """Publish only the completed directory, without replacing an export."""
    folder = Path(folder).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('请选择已有的分轨导出目录。')
    rows = tuple(track for track in project.tracks if track.role == 'vocal')
    if not rows:
        raise ValueError('至少需要一条待处理人声音轨。')
    first = max(project.first_frame, round(selection[0] * project.rate))
    last = min(project.first_frame + project.frames, round(selection[1] * project.rate))
    if last <= first:
        raise ValueError('导出时间范围为空。')
    start, stop = first - project.first_frame, last - project.first_frame
    receipt = {'schema': 'vwa-chorus-stems/1', 'project': project.id, 'title': project.title,
               'variant': variant, 'sample_rate': project.rate, 'origin_frame': first,
               'origin_seconds': first / project.rate, 'frames': stop - start, 'tracks': []}
    with TemporaryDirectory(dir=folder, prefix='.vwa-chorus-') as scratch:
        stage = Path(scratch) / 'stems'
        stage.mkdir()
        for index, track in enumerate(rows, 1):
            check()
            progress(index - 1, len(rows), track.name)
            data = render_track(project, track, variant, check)[start:stop]
            filename = f'{index:02d} {_stem_name(track.name)}.wav'
            destination = stage / filename
            sf.write(destination, data, project.rate, format='WAV', subtype='FLOAT')
            takes = [{'source_sha256': source.sha, 'name': source.name, 'start': edit.start, 'end': edit.end}
                     for edit in project.edits_for(track.id, 'take')
                     if (source := project.track(edit.source)) is not None]
            receipt['tracks'].append({'id': track.id, 'name': track.name, 'part': track.part,
                                      'source_sha256': track.sha, 'file': filename,
                                      'channels': track.channels, 'sha256': source_digest(destination),
                                      'timing_edits': len(project.edits_for(track.id, 'timing')),
                                      'pitch_edits': len(project.edits_for(track.id, 'pitch')),
                                      'gain_edits': len(project.edits_for(track.id, 'gain')),
                                      'take_selections': takes})
        view = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)
        (stage / '分轨说明.json').write_text(view + '\n', encoding='utf-8')
        check()
        base = folder / (_stem_name(project.title) + ' - 分轨')
        number = 1
        while True:
            destination = base if number == 1 else base.with_name(f'{base.name} {number}')
            if not destination.exists():
                try:
                    stage.rename(destination)
                    break
                except FileExistsError:
                    pass
            number += 1
        return destination, receipt


class AuditionPCM:
    """Adapter for the existing native output session and its audio clock."""

    channels = 2

    def __init__(self, rate, audio, position=0):
        self.sample_rate = rate
        self.duration_frames = len(audio)
        self.position_frame = position
        self.volume = .85
        self.audio = audio
        self.previous = None
        self.fade_remaining = 0

    def set_parameters(self, request):
        audio = request['pcm'].audio
        if audio.shape != self.audio.shape or audio.dtype != np.float32:
            raise ValueError('试听快照与工程时间轴不一致。')
        if audio is not self.audio:
            self.previous, self.audio = self.audio, audio
            self.fade_remaining = max(1, round(.012 * self.sample_rate))

    def set_volume(self, value):
        self.volume = float(value) / 100.

    def seek(self, frame):
        self.position_frame = frame

    def render(self, frames):
        start = self.position_frame
        end = start + frames
        data = self.audio[start:end] * self.volume
        if self.previous is not None and self.fade_remaining:
            length = min(frames, self.fade_remaining)
            duration = max(1, round(.012 * self.sample_rate))
            weight = ((duration - self.fade_remaining + np.arange(length)) / duration)[:, None]
            data[:length] = data[:length] * weight + self.previous[start:start + length] * self.volume * (1. - weight)
            self.fade_remaining -= length
            if not self.fade_remaining:
                self.previous = None
        self.position_frame = end
        return np.ascontiguousarray(data, dtype=np.float32)

    def close(self):
        self.previous = None


@dataclass(frozen=True)
class MonitorBuffer:
    audio: np.ndarray

    def __deepcopy__(self, _memo):
        # PCM is published read-only. Reusing the reference avoids copying
        # the entire song on the Qt thread during an A/B switch.
        return self


if __name__ == '__main__':
    _monitor_process(Path(sys.argv[1]))
