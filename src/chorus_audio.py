"""One audio render plan for native audition and sample-aligned stem delivery."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np
import soundfile as sf
import soxr

from chorus_project import VARIANTS, sha256 as source_digest


def render_key(project, variant, solo=''):
    rendered = tuple(
        (track.id, track.sha, track.offset) for track in project.tracks
        if not track.muted and (not solo or track.id == solo)
    )
    edits = tuple(edit for edit in project.edits
                  if variant == 'processed' or edit.kind == variant)
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


def pitch_processor():
    """Load the already managed optional DSP component via its public API."""
    import sys

    from mixdown_runtime import cached_optional_packages, component_manifest, optional_packages

    root, spec, _ = component_manifest()
    packages = cached_optional_packages() or optional_packages(root, spec)
    if packages:
        path = str(packages)
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        from pedalboard import time_stretch
    except (ImportError, OSError) as error:
        raise RuntimeError('修音需要混音效果组件（Pedalboard）。可先试听原声或仅对齐。') from error
    return time_stretch


def _pitch_groups(regions, rate, size):
    groups = []
    context = round(.09 * rate)
    for region in sorted(regions, key=lambda edit: edit.start):
        left = max(0, round(region.start * rate))
        right = min(size, round(region.end * rate))
        if right <= left:
            continue
        if groups and groups[-1][-1][1] + 2 * context >= left:
            groups[-1].append((left, right, region.value))
        else:
            groups.append([(left, right, region.value)])
    return groups


def _shift_pitch(samples, rate, regions, check, process):
    context = round(.09 * rate)
    for group in _pitch_groups(regions, rate, len(samples)):
        check()
        left = max(0, group[0][0] - context)
        right = min(len(samples), group[-1][1] + context)
        shift = np.zeros(right - left, dtype=np.float64)
        for start, end, cents in group:
            start, end = start - left, end - left
            shift[start:end] = cents / 100.
        # Smooth pitch transitions on the voiced side. Rubber Band reads the
        # public per-sample control signal at analysis hop boundaries.
        window = max(1, round(.012 * rate))
        edges = np.pad(shift, (window // 2, (window - 1) // 2), mode='edge')
        prefix = np.r_[0., np.cumsum(edges)]
        shift = (prefix[window:] - prefix[:-window]) / window
        clip = np.ascontiguousarray(samples[left:right].T)
        edited = np.asarray(process(clip, rate, pitch_shift_in_semitones=shift,
                                    preserve_formants=True), dtype=np.float32)
        if edited.ndim == 1:
            edited = edited[None, :]
        edited = edited.T
        if edited.shape[1] != samples.shape[1] or not np.isfinite(edited).all():
            raise ValueError('修音组件返回的声道或采样无效。')
        if len(edited) != right - left:
            # With a dynamic pitch envelope Rubber Band can flush a suffix
            # beyond the input. Keep the original timeline and discard it.
            edited = _window(edited, 0, right - left)
        fade = min(round(.024 * rate), (right - left) // 2)
        _crossfade(samples[left:right], edited, fade)


def render_track(project, track, variant, check=lambda: None, *, pitch=None):
    """Render the take into the project's common half-open sample interval."""
    if variant not in VARIANTS:
        raise ValueError('试听版本无效。')
    rate = project.rate
    original = _read_take(track, rate, check)
    if variant in ('pitch', 'processed'):
        regions = project.edits_for(track.id, 'pitch')
        if regions:
            _shift_pitch(original, rate, regions, check, pitch or pitch_processor())
    placed = _window(original, project.first_frame - round(track.offset * rate), project.frames)
    regions = project.edits_for(track.id, 'timing') if variant in ('timing', 'processed') else ()
    if not regions:
        return placed
    rendered = placed.copy()
    for edit in regions:
        check()
        start = max(0, round(edit.start * rate) - project.first_frame)
        end = min(project.frames, round(edit.end * rate) - project.first_frame)
        shift = round(edit.value * rate)
        if start >= end or not shift:
            continue
        source = _window(placed, start - shift, end - start)
        _crossfade(rendered[start:end], source, min(round(.018 * rate), (end - start) // 2))
    return rendered


def render_mix(project, variant, solo, check, progress):
    rows = [track for track in project.tracks if not track.muted and (not solo or track.id == solo)]
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
            receipt['tracks'].append({'id': track.id, 'name': track.name, 'part': track.part,
                                      'source_sha256': track.sha, 'file': filename,
                                      'channels': track.channels, 'sha256': source_digest(destination),
                                      'timing_edits': len(project.edits_for(track.id, 'timing')),
                                      'pitch_edits': len(project.edits_for(track.id, 'pitch'))})
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
