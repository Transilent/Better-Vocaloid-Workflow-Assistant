"""Expression-source capabilities shared by the editor and the transaction."""

from copy import deepcopy
from pathlib import Path

from svp_tuning import GROUP_STYLE_ONLY
from tuning_native import at_path, native_group_path


REFERENCE_CONTROLS = ('dynamics', 'tension', 'breathiness', 'voicing')
NATIVE_REFERENCE_FORMATS = frozenset(('vpr', 'vsqx', 'ustx', 'acep'))
RETAINED_FORMATS = NATIVE_REFERENCE_FORMATS | {'ufdata', 'svp'}
NO_CALIBRATION = '声库校准使用 SVP 专用的实测试音配置。'
NO_REFERENCE = 'UFData 仅保存音高曲线，无法写入参考力度或音色。'
NO_REFERENCE_CONTROLS = '所选工程格式无法写入此参考参数。请选择可用的参考通道。'
NATIVE_DYNAMICS = {'vpr': 'dynamics', 'vsqx': 'dynamics', 'ustx': 'dyn', 'acep': 'energy'}
NATIVE_TIMBRE = {'vpr': ('breathiness',), 'vsqx': ('breathiness',),
                 'acep': ('tension', 'breathiness')}
RETAINED_CHANNELS = {
    'vpr': ('dynamics',), 'vsqx': ('dynamics', 'breathiness'), 'ustx': ('dyn',),
    'acep': ('energy', 'tension', 'breathiness', 'falsetto'), 'ufdata': ('pitch',),
}


def all_standard_vpr(tracks, indices):
    return bool(indices and all(tracks[i].get('type') == 0 for i in indices))


def reference_controls(format, *, grouped=False, vpr_standard=False):
    if format == 'svp' and not grouped:
        return REFERENCE_CONTROLS
    if format not in NATIVE_REFERENCE_FORMATS:
        return ()
    timbre = () if format == 'vpr' and not vpr_standard else NATIVE_TIMBRE.get(format, ())
    return ('dynamics', *timbre)


def source_reason(format, mode, *, grouped=False):
    if mode == 'preserve':
        return ''
    if format == 'svp' and grouped:
        return GROUP_STYLE_ONLY
    if mode == 'calibrated' and format != 'svp':
        return NO_CALIBRATION
    if mode == 'reference' and not reference_controls(format, grouped=grouped):
        return NO_REFERENCE
    if mode == 'retained' and format not in RETAINED_FORMATS:
        return '当前工程无法沿用原参数。'
    return ''


def validate_source(format, options, *, grouped=False, vpr_standard=False):
    mode = options['expression_mode']
    if reason := source_reason(format, mode, grouped=grouped):
        raise ValueError(reason)
    if mode == 'reference':
        requested = {name for name in REFERENCE_CONTROLS if options.get('reference_' + name)}
        if requested - set(reference_controls(format, grouped=grouped, vpr_standard=vpr_standard)):
            raise ValueError(NO_REFERENCE_CONTROLS)


def retained_rows(options, indices):
    rows = options['retained_sources'] or [
        {'track_index': index, 'project': options['retained_project'],
         'source_track_index': options['retained_track_index']} for index in indices]
    if (not all(isinstance(row, dict) and type(row.get('track_index')) is int for row in rows)
            or {row['track_index'] for row in rows} != set(indices) or len(rows) != len(indices)):
        raise ValueError('沿用参数必须明确覆盖每条所选演唱轨，不能重复或漏配。')
    if any(not isinstance(row.get('project'), str) or not row['project'].strip()
           or type(row.get('source_track_index', 0)) is not int or row.get('source_track_index', 0) < 0
           for row in rows):
        raise ValueError('沿用工程需要有效的文件路径和源轨索引。')
    return rows


def _part_data(data, format, part):
    return at_path(data, native_group_path(format, part.track_index, part.index))


def _curve(data, format, part, name):
    raw = _part_data(data, format, part)
    if format in ('vpr', 'vsqx'):
        from vpr_tuning import _curve as controller
        index, value = controller(raw, name)
        return index, value, 'vpr_invalid_controller_preserved' if value is None else ''
    if format == 'ustx':
        from ustx_tuning import _native_curve
        return _native_curve(data, raw, name)
    if format == 'acep':
        from ace_tuning import _ace_curve
        value = _ace_curve(raw, name)
        return None, value, 'ace_expression_curve_preserved' if value is None else ''
    from ace_tuning import _uf_curve
    value = _uf_curve(raw, for_display=True)
    return None, value, 'ufdata_pitch_controls' if value is None else ''


def _put(data, format, part, name, index, curve):
    dest = _part_data(data, format, part)
    if format in ('vpr', 'vsqx', 'ustx'):
        key = 'curves' if format == 'ustx' else 'controllers'
        if dest.get(key) is None:
            dest[key] = []
        if index is None:
            dest[key].append(curve)
        else:
            dest[key][index] = curve
    elif format == 'acep':
        dest.setdefault('parameters', {})[name] = curve
    else:
        dest['pitch'] = curve


def _voiced_notes(part, clock):
    from tuning_pitch_layer import _singing_lyric
    rows = [(max(0., clock.to_seconds(note.start)), clock.to_seconds(note.end), note.pitch, note.data)
            for note in part.notes if isinstance(note.data.get('lyrics'), str) and _singing_lyric(note.data['lyrics'])]
    return [row for row in rows if row[0] < row[1]]


def _reference_automation(points, intervals, clock, offset):
    """Align analysis to a part and fade to its unchanged native values at note boundaries."""
    import numpy as np
    from vocal_expression import automation

    x, y = np.asarray(points, dtype=float).T
    scoped = {}
    for start, end in intervals:
        left, right = float(max(start, x[0])), float(min(end, x[-1]))
        if right <= left:
            continue
        guard = min(.02, (right - left) / 3)
        a, b = left + guard, right - guard
        lo, hi = np.searchsorted(x, [a, b])
        times = np.r_[a, x[lo:hi], b]
        scoped.update(zip(times.tolist(), np.interp(times, x, y).tolist()))
        scoped[left] = scoped[right] = 0.
    return automation(sorted(scoped.items()), clock, offset) if len(scoped) >= 2 else None


def _audio_delta(data, format, part, clock, parameter, original, rendered):
    if format in ('vpr', 'vsqx'):
        from vpr_tuning import _add_delta
        return _add_delta(part, _part_data(data, format, part), parameter, original, rendered, saturate=True)
    if format == 'ustx':
        from ustx_tuning import _add_native_delta
        replacement, reason = _add_native_delta(original, rendered, parameter, saturate=True)
        return replacement, reason, []
    from ace_tuning import _delta, _native_pitch
    sample = _delta(part, rendered, clock, ())
    if sample is None:
        return None, 'ace_expression_curve_capacity', []
    import numpy as np
    grid, amount, _ = sample
    if parameter == 'energy':
        delta, limits = 10. ** (amount * 5) - 1., (0., 2.)
    else:
        low, high = (.7, 1.5) if parameter == 'tension' else (.2, 2.5)
        delta = amount * 100 * np.where(amount >= 0, high - 1, 1 - low)
        limits = low, high
    replacement, reason, _ = _native_pitch(original, grid, delta, neutral=1., limits=limits)
    return replacement, 'ace_expression_curve_preserved' if reason else '', []


def _analyze_reference(reference, notes, options, *, cancel, cache):
    from vocal_expression import analyze

    strength, path = options['dynamics_strength'], reference['path']
    measured = {}
    if options['reference_dynamics']:
        measured['dynamics'] = analyze(path, [(a, b) for a, b, _, _ in notes], strength, cancel,
                                       preserve_samples=True)
    timbre = tuple(name for name in ('tension', 'breathiness') if options['reference_' + name])
    if timbre:
        from vocal_timbre import analyze as analyze_timbre, phonetic_context
        feature_cache = Path(cache) / (reference['cache_key'] + '.timbre.npz') if cache else None
        nuclei = phonetic_context([raw for _, _, _, raw in notes], None) if 'tension' in timbre else None
        report = analyze_timbre(path, [(a, b, pitch) for a, b, pitch, _ in notes],
                                strength, timbre, cancel, cache=feature_cache, nuclei=nuclei,
                                preserve_samples=True)
        measured.update(report['parameters'])
    return measured


def sources_for(options, indices):
    rows = options['reference_sources']
    mapping = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('track_index')) is not int:
            raise ValueError('参考音频缺少明确的工程轨索引。')
        index = row['track_index']
        voice = row.get('voice')
        path = row.get('path')
        if index in mapping or not isinstance(voice, str) or voice not in ('lead', 'backing', 'vocals'):
            raise ValueError('参考声部映射重复或名称无效。')
        if not isinstance(path, str) or not path.strip() or not Path(path).is_file():
            raise ValueError('参考音频不存在。')
        mapping[index] = {'voice': voice, 'path': Path(path).resolve(), 'cache_key': 'track-' + str(index)}
    if set(mapping) != set(indices):
        raise ValueError('每条所选演唱轨都需要明确对应的参考音频；不能把主唱静默套用到和声。')
    return [mapping[index] for index in indices]


def _reference(source, output, format, layout, indices, options, *, cancel, progress, cache):
    changes, skipped, reports, incomplete = [], [], [], False
    clock = layout.clock
    for index, reference in zip(indices, sources_for(options, indices)):
        cancel()
        parts = [(part, _voiced_notes(part, clock)) for part in layout.tracks[index].style_placements]
        progress('正在分析参考音频…')
        measurements = _analyze_reference(reference, [note for _, notes in parts for note in notes],
                                          options, cancel=cancel, cache=cache)
        reports.append({'track_index': index, 'voice': reference['voice'], 'parameters': measurements})
        for control, report in measurements.items():
            points = report.pop('points')
            name = NATIVE_DYNAMICS[format] if control == 'dynamics' else control
            report.update(parameter=name, written_parts=0)
            if report['status'] != 'applied':
                incomplete |= options['dynamics_strength'] > 0
                continue
            for part, notes in parts:
                cancel()
                rendered = _reference_automation(points, [(a, b) for a, b, _, _ in notes], clock, part.offset)
                if not rendered or not rendered['points']:
                    continue
                idx, original, reason = _curve(output, format, part, name)
                evidence = {'track_index': index, 'group_placement': part.key, 'parameter': name}
                if original is not None:
                    value, reason, protections = _audio_delta(source, format, part, clock, name, original, rendered)
                    skipped.extend({**evidence, 'status': 'skipped', 'reason': why} for why in protections)
                    if value is not None:
                        _put(output, format, part, name, idx, value)
                        changes.append(evidence)
                        report['written_parts'] += 1
                if reason:
                    skipped.append({**evidence, 'status': 'skipped', 'reason': reason})
            if not report['written_parts']:
                report.update(status='skipped', reason='reference_no_native_curve')
                incomplete = True
    return changes, skipped, {'status': 'partial' if skipped or incomplete else 'applied' if changes else 'unchanged',
                              'mode': 'reference', 'operation': 'bounded_native_expression',
                              'tracks': reports, 'strength': options['dynamics_strength']}


def _part_signature(part, step):
    notes = tuple(sorted((n.start, n.end, n.pitch) for n in part.notes))
    return part.offset, getattr(part, 'length', None), getattr(part, 'step', step), notes


def _populated(format, raw):
    if format in ('vpr', 'vsqx'):
        return bool(raw['events'])
    if format == 'ustx':
        return bool(raw['xs'])
    if format == 'ufdata':
        return bool(raw['ticks'])
    return bool(raw)


def _retained_curve(format, name, current, prior):
    if format in ('vpr', 'vsqx'):
        from vpr_tuning import CONTROLLERS, MAX_EVENTS
        from vocaloid_pitch import step_values
        # Preserve destination XML/controller anchors and their metadata. Old
        # anchors become redundant steps with the exact source value.
        positions = sorted({event['pos'] for entry in (current, prior) for event in entry['events']})
        if len(positions) > MAX_EVENTS:
            raise ValueError('沿用后的控制曲线超过格式容量。')
        values = step_values(positions, prior['events'], CONTROLLERS[name][0])
        return {**current, 'events': [{'pos': tick, 'value': value} for tick, value in zip(positions, values.tolist())]}
    if format == 'ustx':
        return {**current, 'xs': prior['xs'].copy(), 'ys': prior['ys'].copy()}
    return deepcopy(prior)


def _retained(source, output, format, layout, indices, options, *, cancel, progress):
    from project_io import read_source, score_layout

    if options['retained_match_policy'] != 'exact':
        raise ValueError('当前格式的沿用参数需要音符、片段和时间轴完全匹配。')
    snapshots, changes, reports = {}, [], []
    for row in retained_rows(options, indices):
        cancel()
        index = row['track_index']
        progress('正在核对原参数工程…')
        path = Path(row['project']).resolve()
        if path not in snapshots:
            previous = read_source(path)
            if previous.format != format:
                raise ValueError('沿用原参数需要选择与当前工程同格式的文件。')
            snapshots[path] = previous.data, score_layout(previous.data, format)[1]
        before, old = snapshots[path]
        if (getattr(old, 'step', 1) != getattr(layout, 'step', 1)
                or old.clock.native_origin != layout.clock.native_origin or old.clock.measure_prefix != layout.clock.measure_prefix
                or old.score['time'] != layout.score['time']):
            raise ValueError('工程的速度、拍号或时间原点已改变，无法沿用原参数。')
        vocals = [track for track in old.tracks if track.instrumental is False]
        prior = row.get('source_track_index', 0)
        if prior >= len(vocals):
            raise ValueError('原参数工程的演唱轨索引无效。')
        a, b = layout.tracks[index], vocals[prior]
        target = {p.key: p for p in a.style_placements}
        earlier = {p.key: p for p in b.placements}
        if (a.raw_count != b.raw_count or set(target) != set(earlier) or not target or len(target) != len(a.placements)
                or any(_part_signature(p, layout.clock.step) != _part_signature(earlier[key], old.clock.step)
                       for key, p in target.items())):
            raise ValueError('沿用参数需要源轨和目标轨的音符及片段完整对应。')
        if format == 'vsqx':
            before = old.sequence
        transferred = 0
        channels = RETAINED_CHANNELS[format]
        if format == 'vpr' and all_standard_vpr(source['tracks'], [index]) and all_standard_vpr(before['tracks'], [b.index]):
            channels += ('breathiness',)
        for key, part in target.items():
            for name in channels:
                _, curve, reason = _curve(before, format, earlier[key], name)
                if reason:
                    raise ValueError('原参数工程包含无法安全复制的曲线。')
                if not _populated(format, curve):
                    continue
                idx, current, reason = _curve(source, format, part, name)
                if reason:
                    raise ValueError('目标轨道有无法安全替换的原生曲线。')
                transferred += 1
                replacement = _retained_curve(format, name, current, curve)
                if current != replacement:
                    _put(output, format, part, name, idx, replacement)
                    changes.append({'track_index': index, 'group_placement': key, 'parameter': name})
        if not transferred:
            raise ValueError('来源工程没有此格式可沿用的演唱曲线。')
        reports.append({'track_index': index, 'source_track_index': b.index, 'matched_notes': a.note_count,
                        'curves': transferred, 'match_policy': 'exact'})
    return changes, [], {'mode': 'retained', 'status': 'applied' if changes else 'unchanged',
                         'tracks': reports, 'new_fit': False}


def apply_native_source(data, format, layout, indices, options, *, cancel, progress, cache=None):
    """Apply a source to the native project without a conversion/export round trip."""
    original = layout.sequence if format == 'vsqx' else data
    assembled = deepcopy(original)
    if options['expression_mode'] == 'reference':
        changes, skipped, report = _reference(original, assembled, format, layout, indices, options,
                                              cancel=cancel, progress=progress, cache=cache)
    else:
        changes, skipped, report = _retained(original, assembled, format, layout, indices, options,
                                             cancel=cancel, progress=progress)
    if format == 'vsqx':
        from vsqx_tuning import merge_sequence, verify_sequence
        output = merge_sequence(data, original, assembled)
        verify_sequence(output, assembled)
    else:
        output = assembled
    return output, changes, skipped, report
