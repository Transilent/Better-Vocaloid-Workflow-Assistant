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

from chorus_project import Recording, SYLLABLE_GAIN, VARIANTS, sha256 as source_digest


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
    engine = project.pitch_engine if variant in ('pitch', 'processed') else ''
    return (project.first_frame, project.frames, project.rate, rendered, variant, edits, engine)


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


PITCH_CONTEXT_S = .09
PITCH_MAX_GAP_S = .095
PITCH_SMOOTH_S = .028
PITCH_FADE_S = .020


def _pitch_active(region):
    return any(y for _, y in region.points) if region.points else bool(region.value)


def _voiced_curve_gap(voiced_curve, start, end, rate):
    midi, step = voiced_curve
    a = max(0, round((start / rate) / step))
    b = min(len(midi), max(a + 1, round((end / rate) / step)))
    if b <= a:
        return False
    return float(np.mean(np.isfinite(midi[a:b]))) >= .65


def compile_pitch_spans(regions, rate, size, samples=None, *, voiced_curve=None):
    """Compile active pitch edits into independent continuous voiced spans.

    Auto, manual, split and hand-drawn edits share one compilation rule so that
    equal curves render identically, splits preserve PCM bit-for-bit, and editing
    one span never perturbs any non-continuous neighbor.
    """
    from chorus_pitch_dsp import voiced_short_gap

    context = round(PITCH_CONTEXT_S * rate)
    max_gap = round(PITCH_MAX_GAP_S * rate)
    untouched = tuple(
        (max(0, round(edit.start * rate)), min(size, round(edit.end * rate)))
        for edit in regions if edit.manual and not _pitch_active(edit)
    )
    active = []
    for region in sorted((row for row in regions if _pitch_active(row)),
                         key=lambda edit: (edit.start, edit.end)):
        start = max(0, round(region.start * rate))
        end = min(size, round(region.end * rate))
        if end > start:
            active.append((start, end, region))
    if not active:
        return ()

    # Partition strictly by acoustic continuity and explicit keep-original locks.
    grouped = [[active[0]]]
    for item in active[1:]:
        start, end, _ = item
        prev_end = grouped[-1][-1][1]
        blocked = any(a < start and prev_end < b for a, b in untouched)
        if not blocked and start <= prev_end + 1:
            grouped[-1].append(item)
        elif not blocked and 0 < start - prev_end <= max_gap:
            bridged = (voiced_short_gap(samples, prev_end, start, rate, PITCH_MAX_GAP_S)
                       if samples is not None else
                       _voiced_curve_gap(voiced_curve, prev_end, start, rate)
                       if voiced_curve is not None else False)
            if bridged:
                grouped[-1].append(item)
            else:
                grouped.append([item])
        else:
            grouped.append([item])

    window = max(1, round(PITCH_SMOOTH_S * rate))
    compiled = []
    for members in grouped:
        span_start = members[0][0]
        span_end = max(end for _, end, _ in members)
        left = max(0, span_start - context)
        right = min(size, span_end + context)
        shift = np.zeros(right - left, dtype=np.float64)
        cursor = None
        for start, end, region in members:
            cents = region.value
            if region.points:
                x, y = zip(*region.points)
                cents = np.interp(np.arange(start, end) / rate, x, y)
            shift[start - left:end - left] = np.asarray(cents, dtype=np.float64) / 100.
            if cursor is not None and start > cursor:
                shift[cursor - left:start - left] = np.linspace(
                    shift[cursor - left - 1], shift[start - left], start - cursor + 2)[1:-1]
            cursor = max(cursor, end) if cursor is not None else end
        edges = np.pad(shift, (window // 2, (window - 1) // 2), mode='edge')
        prefix = np.r_[0., np.cumsum(edges)]
        shift = (prefix[window:] - prefix[:-window]) / window
        compiled.append((span_start, span_end, left, right, shift))
    return tuple(compiled)


def compiled_pitch_semitones(regions, times, *, duration, voiced_curve=None, sample_rate=1000):
    """Evaluate the exact compiled continuous pitch shift (in semitones) at take times."""
    times = np.asarray(times, dtype=np.float64)
    result = np.zeros(times.shape, dtype=np.float64)
    if not regions or duration <= 0.:
        return result
    size = max(1, round(duration * sample_rate))
    spans = compile_pitch_spans(regions, sample_rate, size, voiced_curve=voiced_curve)
    if not spans:
        return result
    indices = np.rint(times * sample_rate).astype(np.int64)
    for span_start, span_end, left, _right, shift in spans:
        inside = (indices >= span_start) & (indices < span_end)
        if np.any(inside):
            result[inside] = shift[indices[inside] - left]
    return result


def _pitch_groups(regions, rate, size, samples=None):
    """Compatibility helper returning compiled span bounds for diagnostics."""
    return [(span_start, span_end, left, right)
            for span_start, span_end, left, right, _ in compile_pitch_spans(regions, rate, size, samples)]


def _shift_pitch(samples, rate, regions, check, *, engine='auto'):
    from chorus_pitch_dsp import blend_pitch, preserve_level, render_continuous_pitch

    spans = compile_pitch_spans(regions, rate, len(samples), samples)
    if not spans:
        return
    # Snapshot dry input so each independent voiced span reads immutable audio
    # and never perturbs a non-continuous neighbor.
    dry_source = samples.copy()
    for span_start, span_end, left, right, shift in spans:
        check()
        dry = dry_source[left:right]
        clip = np.ascontiguousarray(dry.T)
        edited = render_continuous_pitch(
            clip, rate, shift, check, engine=engine, pedalboard_loader=_pedalboard).T
        if edited.shape != dry.shape or not np.isfinite(edited).all():
            raise ValueError('修音组件返回的长度、声道或采样无效。')
        mixed = dry.copy()
        interval = slice(span_start - left, span_end - left)
        fade = min(round(PITCH_FADE_S * rate), (span_end - span_start) // 2)
        blend_pitch(mixed[interval], edited[interval], fade)
        level = preserve_level(dry, mixed, rate)
        _crossfade(mixed[interval], level[interval], min(round(.006 * rate), (span_end - span_start) // 2))
        samples[span_start:span_end] = mixed[interval]


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


def _timed_slice(placed, edit, origin, rate, start, end, check):
    if not edit.points:
        return _window(placed, start - round(edit.value * rate), end - start)
    warped = _warp_audio(placed, edit, origin, rate, check)
    return _window(warped, start + origin - round(edit.start * rate), end - start)


def render_track(project, track, variant, check=lambda: None):
    """Render the take into the project's common half-open sample interval."""
    if variant not in VARIANTS:
        raise ValueError('试听版本无效。')
    rate = project.rate
    original = _comped_audio(project, track, rate, check)
    origin = project.first_frame
    offset = origin - round(track.offset * rate)
    timing = project.edits_for(track.id, 'timing') if variant in ('timing', 'processed') else ()
    syllables = project.edits_for(track.id, SYLLABLE_GAIN) if variant == 'processed' else ()
    dry_reference = None
    if variant in ('pitch', 'processed'):
        regions = project.edits_for(track.id, 'pitch')
        active = tuple(row for row in regions if _pitch_active(row))
        if active:
            if timing:
                dry_reference = original.copy() if syllables else _window(original, offset, project.frames)
            _shift_pitch(original, rate, regions, check, engine=project.pitch_engine)
    if syllables:
        from chorus_gain import render_syllable_gains

        render_syllable_gains(original, rate, syllables, check, dry_reference)
        if dry_reference is not None:
            dry_reference = _window(dry_reference, offset, project.frames)
    placed = _window(original, offset, project.frames)
    rendered = placed.copy() if timing else placed
    for edit in timing:
        check()
        start = max(0, round(edit.start * rate) - project.first_frame)
        end = min(project.frames, round(edit.end * rate) - project.first_frame)
        shift = round(edit.value * rate)
        if start >= end or (not edit.points and not shift):
            continue
        source = _timed_slice(placed, edit, origin, rate, start, end, check)
        fade = min(round(.018 * rate), (end - start) // 2)
        _crossfade(rendered[start:end], source, fade)
        if dry_reference is not None and any(
                row.start + track.offset < edit.source_time(edit.end) and
                row.end + track.offset > edit.source_time(edit.start) for row in active):
            from chorus_pitch_dsp import preserve_low_transients

            dry = dry_reference[start:end].copy()
            _crossfade(dry, _timed_slice(dry_reference, edit, origin, rate, start, end, check), fade)
            preserve_low_transients(dry, rendered[start:end], rate)
    if variant == 'processed':
        for edit in project.edits_for(track.id, 'gain'):
            start = max(0, round(edit.start * rate) - project.first_frame)
            end = min(project.frames, round(edit.end * rate) - project.first_frame)
            if start < end and edit.value:
                view = rendered[start:end]
                _crossfade(view, view * 10 ** (edit.value / 20.), min(round(.018 * rate), (end - start) // 2))
    return rendered


def _stereo_channels(data):
    return data if data.shape[1] <= 2 else data.mean(axis=1, keepdims=True)


def render_mix(project, variant, solo, check, progress):
    rows = monitor_tracks(project, solo)
    stereo = np.zeros((project.frames, 2), dtype=np.float32)
    gain = 1. / max(1, len(rows))
    for index, track in enumerate(rows):
        check()
        progress(index, len(rows), track.name)
        audio = render_track(project, track, variant, check)
        stereo += _stereo_channels(audio) * gain
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

    spec = _render_spec(project, variant, solo=solo)
    labels = [track.name for track in rows]
    with model_batch(__file__, spec, cache, check, lambda i, name: progress(i, len(rows), name),
                     labels, '修音试听渲染失败') as folder:
        audio = np.load(folder / 'monitor.npy', allow_pickle=False)
    if audio.dtype != np.float32 or audio.shape != (project.frames, 2) or not np.isfinite(audio).all():
        raise RuntimeError('修音试听返回的采样或时间范围无效。')
    audio.setflags(write=False)
    return audio


def _render_spec(project, variant, **options):
    return {'title': project.title, 'id': project.id, 'rate': project.rate, 'variant': variant,
            'pitch_engine': project.pitch_engine,
            'tracks': [asdict(track) for track in project.tracks], 'edits': [asdict(edit) for edit in project.edits],
            **options}


def _render_process(request):
    from chorus_project import ChorusProject, Edit

    spec = json.loads(request.read_text(encoding='utf-8'))
    tracks = tuple(Recording(**(track | {'stamp': tuple(track['stamp'])})) for track in spec['tracks'])
    edits = tuple(Edit(**(edit | {'points': tuple(map(tuple, edit['points']))})) for edit in spec['edits'])
    project = ChorusProject(spec['title'], spec['id'], spec['rate'], tracks, edits=edits,
                            pitch_engine=spec.get('pitch_engine', 'auto'))

    def progress(index, _total, _name):
        (request.parent / 'progress.txt').write_text(str(index), encoding='utf-8')

    if spec.get('export'):
        destination, receipt = export_stems(project, spec['folder'], spec['variant'], spec['selection'],
                                           lambda: None, progress, include_mix=spec['include_mix'])
        (request.parent / 'export.json').write_text(json.dumps({'folder': destination.name, 'receipt': receipt},
                                                                ensure_ascii=False), encoding='utf-8')
    else:
        mix = render_mix(project, spec['variant'], spec['solo'], lambda: None, progress)
        np.save(request.parent / 'monitor.npy', mix, allow_pickle=False)


def _stem_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)[:80].rstrip(' .') or '音轨'


def _publish_stems(stage, folder, title):
    base = folder / (_stem_name(title) + ' - 分轨')
    number = 1
    while True:
        destination = base if number == 1 else base.with_name(f'{base.name} {number}')
        if not destination.exists():
            try:
                stage.rename(destination)
                return destination
            except FileExistsError:
                pass
        number += 1


def export_delivery(project, folder, variant, selection, cache, check, progress, *, include_mix=False):
    """Render delivery in a child process so the desktop stays responsive."""
    from chorus_model_worker import model_batch

    folder = Path(folder).expanduser().resolve(strict=True)
    rows = [track.name for track in project.tracks if track.role == 'vocal']
    if include_mix:
        rows.append('合唱干声')
    if variant == 'original':
        return export_stems(project, folder, variant, selection, check, progress, include_mix=include_mix)
    # The parent owns this directory. Cancellation removes everything here
    # even if the render process was terminated while writing a stem.
    with TemporaryDirectory(dir=folder, prefix='.vwa-delivery-') as directory:
        stage = Path(directory).resolve()
        spec = _render_spec(project, variant, export=True, folder=str(stage), selection=selection,
                            include_mix=include_mix)
        with model_batch(__file__, spec, cache, check, lambda i, text: progress(i, len(rows), text), rows,
                         '分轨渲染失败') as worker:
            result = json.loads((worker / 'export.json').read_text(encoding='utf-8'))
        source = (stage / result['folder']).resolve(strict=True)
        if source.parent != stage or not source.is_dir():
            raise RuntimeError('分轨渲染返回的目录无效。')
        check()
        return _publish_stems(source, folder, project.title), result['receipt']


def export_stems(project, folder, variant, selection, check, progress, *, include_mix=False):
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
    total = len(rows) + int(include_mix)
    receipt = {'schema': 'vwa-chorus-stems/1', 'project': project.id, 'title': project.title,
               'variant': variant, 'sample_rate': project.rate, 'origin_frame': first,
               'origin_seconds': first / project.rate, 'frames': stop - start, 'tracks': []}
    with TemporaryDirectory(dir=folder, prefix='.vwa-chorus-') as scratch:
        stage = Path(scratch) / 'stems'
        stage.mkdir()
        vocal_mix = np.zeros((stop - start, 2), dtype=np.float32) if include_mix else None
        for index, track in enumerate(rows, 1):
            check()
            progress(index - 1, total, track.name)
            data = render_track(project, track, variant, check)[start:stop]
            filename = f'{index:02d} {_stem_name(track.name)}.wav'
            destination = stage / filename
            sf.write(destination, data, project.rate, format='WAV', subtype='FLOAT')
            if vocal_mix is not None:
                vocal_mix += _stereo_channels(data)
            takes = [{'source_sha256': source.sha, 'name': source.name, 'start': edit.start, 'end': edit.end}
                     for edit in project.edits_for(track.id, 'take')
                     if (source := project.track(edit.source)) is not None]
            receipt['tracks'].append({'id': track.id, 'name': track.name, 'part': track.part,
                                      'source_sha256': track.sha, 'file': filename,
                                      'channels': track.channels, 'sha256': source_digest(destination),
                                      'timing_edits': len(project.edits_for(track.id, 'timing')),
                                      'pitch_edits': len(project.edits_for(track.id, 'pitch')),
                                      'gain_edits': len(project.edits_for(track.id, 'gain')),
                                      'syllable_gain_edits': len(project.edits_for(track.id, SYLLABLE_GAIN)),
                                      'take_selections': takes})
        if vocal_mix is not None:
            check()
            progress(len(rows), total, '合唱干声')
            peak = float(np.max(np.abs(vocal_mix), initial=0.))
            gain = .98 / peak if peak > .98 else 1.
            vocal_mix *= gain
            destination = stage / '合唱干声.wav'
            sf.write(destination, vocal_mix, project.rate, format='WAV', subtype='FLOAT')
            receipt['vocal_mix'] = {'file': destination.name, 'channels': 2, 'gain': gain,
                                    'sha256': source_digest(destination)}
        view = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False)
        (stage / '分轨说明.json').write_text(view + '\n', encoding='utf-8')
        check()
        return _publish_stems(stage, folder, project.title), receipt


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
    _render_process(Path(sys.argv[1]))
