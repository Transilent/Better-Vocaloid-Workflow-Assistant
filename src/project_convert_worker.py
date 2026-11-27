"""Isolated subprocess worker for project format inspection & conversion.

Runs outside the Qt GUI process so importing LibreSVIP never slows cold startup
or mutates process-wide YAML / plugin state in the desktop application.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
import traceback
from typing import Any

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(SRC_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SRC_DIR.parent))

from common import ROOT, file_hash
from project_convert_compat import (
    detect_tracks_language as _detect_language,
    detect_ust_encoding,
    friendly_project_error,
    patch_ustx_masked_curve,
    prepare_diffsinger_project,
    strip_extended_prefix,
    vsqx_english_dict_available,
)


CONVERTER_REGISTRY: dict[str, tuple[str, str]] = {
    'svp': ('libresvip.plugins.svp.synthv_studio_converter', 'SynthVStudioConverter'),
    'ustx': ('libresvip.plugins.ustx.ustx_converter', 'OpenUtauConverter'),
    'vpr': ('libresvip.plugins.vpr.vpr_converter', 'VocaloidConverter'),
    'vsqx': ('libresvip.plugins.vsqx.vsqx_converter', 'VsqxConverter'),
    'acep': ('libresvip.plugins.acep.ace_studio_converter', 'ACEStudioConverter'),
    'ufdata': ('libresvip.plugins.ufdata.ufdata_converter', 'UFDataConverter'),
    'mid': ('libresvip.plugins.mid.midi_converter', 'MidiConverter'),
    'ust': ('libresvip.plugins.ust.ust_converter', 'USTConverter'),
    'svip': ('libresvip.plugins.svip.binsvip_converter', 'SvipConverter'),
    'ccs': ('libresvip.plugins.ccs.cevio_converter', 'CeVIOConverter'),
    'ds': ('libresvip.plugins.ds.diffsinger_converter', 'DiffSingerConverter'),
    'dspx': ('libresvip.plugins.dspx.dspx_converter', 'DspxConverter'),
    'musicxml': ('libresvip.plugins.musicxml.musicxml_converter', 'MusicXMLConverter'),
    'lrc': ('libresvip.plugins.lrc.lrc_converter', 'LrcConverter'),
    'srt': ('libresvip.plugins.srt.srt_converter', 'SrtConverter'),
}

_SV_QUARTER = 705_600_000
_TICK_FACTOR = _SV_QUARTER // 480


def _bootstrap_libresvip():
    explicit = os.environ.get('VWA_EXPORT_RUNTIME', '').strip()
    candidates = (
        *([Path(explicit)] if explicit else ()),
        ROOT / 'dependencies/export',
        ROOT.parent / 'dependencies/export',
        SRC_DIR.parent / 'dependencies/export',
    )
    runtime = next((p for p in candidates if (p / 'libresvip/__init__.py').is_file()), None)
    if runtime is None:
        raise FileNotFoundError('工程互转运行库缺失（dependencies/export/libresvip）。')
    settings = ROOT / 'cache/export-app'
    settings.mkdir(parents=True, exist_ok=True)
    os.environ['APPDATA'] = os.environ['LOCALAPPDATA'] = str(settings)
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    # Work around LibreSVIP 2.9.2 upstream bugs in vsqx/vocaloid_controllers.py:
    # 1) `param_def.vsqx_name` typo (actual field is `vsq_name`, mapped via `self.param_names[...].value`)
    # 2) `mctrl_class.__dataclass_fields__["attr"]` fails on Pydantic `Vsq3MCtrl`/`Vsq4MCtrl`.
    try:
        from libresvip.model.vocaloid.controller_registry import VocaloidParameterDef, get_param_def
        from libresvip.plugins.vsqx.model import Vsq3MCtrl, Vsq3TypeParamAttr, Vsq4MCtrl, Vsq4TypeParamAttr
        from libresvip.plugins.vsqx.vocaloid_controllers import VsqxControllerAdapter

        if not hasattr(VocaloidParameterDef, 'vsqx_name'):
            VocaloidParameterDef.vsqx_name = property(lambda self: self.vsq_name)

        if not getattr(VsqxControllerAdapter, '_vwa_patched', False):
            def _patched_create_mctrl_list(self, curve, mctrl_class):
                if curve.is_empty():
                    return []
                param_def = get_param_def(curve.name)
                if param_def is not None and param_def.vsq_name and hasattr(self.param_names, param_def.vsq_name):
                    vsqx_param_id = self.param_names[param_def.vsq_name].value
                elif param_def is not None and param_def.vsq_name:
                    vsqx_param_id = param_def.vsq_name
                else:
                    vsqx_param_id = curve.name
                attr_cls = Vsq4TypeParamAttr if mctrl_class is Vsq4MCtrl else (
                    Vsq3TypeParamAttr if mctrl_class is Vsq3MCtrl else None
                )
                mctrl_list = []
                for event in curve.events:
                    attr_val = (
                        attr_cls(type_param_attr_id=vsqx_param_id, value=event.value)
                        if attr_cls is not None
                        else {'type_param_attr_id': vsqx_param_id, 'value': event.value}
                    )
                    mctrl_list.append(mctrl_class(pos_tick=event.pos, attr=attr_val))
                return mctrl_list

            VsqxControllerAdapter.create_mctrl_list = _patched_create_mctrl_list
            VsqxControllerAdapter._vwa_patched = True
    except ImportError:
        pass


def _get_converter(fmt: str):
    from importlib import import_module

    if fmt not in CONVERTER_REGISTRY:
        raise ValueError(f'未知格式插件：{fmt}')
    mod_name, cls_name = CONVERTER_REGISTRY[fmt]
    return getattr(import_module(mod_name), cls_name)


def _load_high_fidelity_project(path: Path, fmt: str, *, include_pitch: bool = True):
    """Build a standard LibreSVIP Project directly from VWA's verified SV2 / ACEP2 readers."""
    import numpy as np
    from libresvip.model.base import (
        InstrumentalTrack, Note, ParamCurve, Params, Point, Points,
        Project, SingingTrack, SongTempo, TimeSignature,
    )
    from project_io import read_source, score_layout
    from tuning_native import at_path
    from tuning_native_pitch import _collect, sample_authored_pitch

    snapshot = read_source(path)
    score, layout = score_layout(snapshot.data, fmt)
    curve_data = layout.sequence if fmt == 'vsqx' else snapshot.data

    tempos = [
        SongTempo(position=max(0, int(round(row['position'] / _TICK_FACTOR))), bpm=float(row['bpm']))
        for row in score['time'].get('tempo', [])
    ] or [SongTempo(position=0, bpm=120.0)]
    if tempos[0].position != 0:
        tempos.insert(0, SongTempo(position=0, bpm=tempos[0].bpm))

    meters = [
        TimeSignature(
            bar_index=max(0, int(row.get('index', 0))),
            numerator=int(row.get('numerator', 4)),
            denominator=int(row.get('denominator', 4)),
        )
        for row in score['time'].get('meter', [])
    ] or [TimeSignature(bar_index=0, numerator=4, denominator=4)]
    if meters[0].bar_index != 0:
        meters.insert(0, TimeSignature(bar_index=0, numerator=4, denominator=4))

    track_list = []
    for row, raw_track in zip(layout.tracks, score['tracks']):
        title = str(raw_track.get('name') or f'Track {row.index + 1}')
        if row.instrumental:
            media_ref = next((m.reference for m in layout.native.media if m.track == row.index and m.reference), '')
            if media_ref:
                audio_path = Path(media_ref)
                if not audio_path.is_absolute():
                    audio_path = (path.parent / audio_path).resolve()
                track_list.append(InstrumentalTrack(title=title, audio_file_path=str(audio_path), offset=0))
            continue

        notes_out: list[Note] = []
        pitch_points: list[Point] = []
        step_native = getattr(layout, 'step', _TICK_FACTOR) or _TICK_FACTOR

        for placement in row.placements:
            part_ref = layout.native.parts.get((row.index, placement.key))
            part_obj = at_path(curve_data, part_ref.path) if part_ref is not None else None
            curves = _collect(curve_data, fmt, part_obj) if (include_pitch and part_obj is not None) else ()

            for note in placement.notes:
                if note.pitch is None:
                    continue
                start_tick = int(round(note.start / _TICK_FACTOR))
                end_tick = int(round(note.end / _TICK_FACTOR))
                length = max(1, end_tick - start_tick)
                if start_tick < 0:
                    continue
                key = max(0, min(127, int(round(note.pitch))))
                lyr = str(note.data.get('source_lyric', note.data.get('lyrics', '')) or 'a')
                notes_out.append(Note(start_pos=start_tick, length=length, key_number=key, lyric=lyr))

                if curves:
                    local_start = note.data['onset'] / step_native
                    local_end = (note.data['onset'] + note.data['duration']) / step_native
                    if fmt == 'svp':
                        local_start = float(note.data['onset'])
                        local_end = float(note.data['onset'] + note.data['duration'])
                    sample_count = max(2, min(200, int(length // 10) + 1))
                    grid_native = np.linspace(local_start, local_end, sample_count)
                    grid_ticks = np.rint(np.linspace(start_tick, start_tick + length, sample_count)).astype(np.int64)
                    sampled = sample_authored_pitch(curves, grid_native, key)
                    if sampled is not None and np.isfinite(sampled).any():
                        abs_cents = np.rint(key * 100.0 + sampled)
                        valid = np.isfinite(abs_cents) & (np.abs(sampled) > 0.5)
                        if valid.any():
                            pitch_points.append(Point(x=int(grid_ticks[0]), y=-100))
                            for t_val, c_val, ok in zip(grid_ticks.tolist(), abs_cents.tolist(), valid.tolist()):
                                if ok and 0 <= int(c_val) <= 12700:
                                    pitch_points.append(Point(x=int(t_val), y=int(c_val)))
                            pitch_points.append(Point(x=int(grid_ticks[-1]), y=-100))

        notes_out.sort(key=lambda n: (n.start_pos, n.key_number))
        params = Params()
        if pitch_points:
            # Ensure monotonic x and start/end sentinel points per LibreSVIP convention
            pitch_points.sort(key=lambda p: p.x)
            params = Params(pitch=ParamCurve(points=Points(root=pitch_points)))
        track_list.append(SingingTrack(title=title, note_list=notes_out, edited_params=params))

    return Project(song_tempo_list=tempos, time_signature_list=meters, track_list=track_list)


def _strip_project_pitch(project):
    for track in project.track_list:
        if hasattr(track, 'edited_params') and track.edited_params is not None:
            track.edited_params.pitch.points.root.clear()
            for attr in ('volume', 'breath', 'gender', 'strength'):
                curve = getattr(track.edited_params, attr, None)
                if curve is not None and hasattr(curve, 'points'):
                    curve.points.root.clear()
        for note in getattr(track, 'note_list', ()):
            if hasattr(note, 'vibrato'):
                note.vibrato = None


def _build_output_options(fmt: str, *, language: str, lyric_mode: str, source_stem: str) -> dict[str, Any]:
    if fmt == 'svp':
        lang_map = {'zh': 'mandarin', 'ja': 'japanese', 'en': 'english'}
        opts: dict[str, Any] = {'down_sample': 0}
        if lyric_mode == 'adapt':
            opts['language_override'] = lang_map.get(language, 'mandarin')
        return opts
    if fmt == 'vsqx' and lyric_mode == 'adapt':
        from project_exports import ENGLISH_VSQX_VOICE, JAPANESE_VSQX_VOICE
        if language == 'en':
            return ENGLISH_VSQX_VOICE.copy()
        if language == 'ja':
            return JAPANESE_VSQX_VOICE.copy()
        return {}
    if fmt == 'ds':
        return {
            'dict_name': 'japanese_dict_full' if language == 'ja' else 'opencpop-extension',
        }
    if fmt == 'mid':
        return {'export_lyrics': True, 'lyric_encoding': 'utf-8'}
    if fmt == 'ust':
        return {'encoding': 'utf-8', 'version': 2.0}
    if fmt == 'lrc':
        return {'title': source_stem, 'by': 'VWA', 'ignore_slur_notes': lyric_mode == 'adapt', 'encoding': 'utf-8'}
    if fmt == 'srt':
        return {'track_index': 0, 'ignore_slur_notes': lyric_mode == 'adapt', 'encoding': 'utf-8'}
    return {}


def _build_input_options(fmt: str, path: Path | None = None) -> dict[str, Any]:
    if fmt == 'ustx':
        return {'import_pitch': True, 'import_instrumental_track': True}
    if fmt == 'mid':
        return {'import_lyrics': True, 'lyric_encoding': 'utf-8', 'multi_channel': 'split'}
    if fmt == 'ust':
        enc = detect_ust_encoding(path) if path is not None else 'utf-8'
        return {'import_pitch': True, 'encoding': enc}
    return {}


def _duration_from_project(project) -> float:
    from libresvip.model.base import SingingTrack

    tempos = sorted(project.song_tempo_list or [], key=lambda t: t.position)
    if not tempos:
        bpm = 120.0
    else:
        bpm = float(tempos[0].bpm) or 120.0
    max_tick = 0
    for track in project.track_list:
        if isinstance(track, SingingTrack):
            for note in track.note_list:
                end = int(note.start_pos) + int(note.length)
                if end > max_tick:
                    max_tick = end
    return round((max_tick / 480.0) * (60.0 / max(1.0, bpm)), 2)


_CCS_FLOAT_PARTITION_RE = re.compile(
    r'(<(?:SongEditor|TalkEditor)\b[^>]*\bPartition=")(\d+\.\d+)(")'
)


def _load_with_converter(converter, path: Path, fmt: str):
    opts = _build_input_options(fmt, path)
    try:
        return converter.load(path, opts)
    except Exception as exc:
        msg = str(exc).lower()
        # Modern OpenUtau projects write `type: MaskedCurve` on rpit/pito expressions,
        # which LibreSVIP 2.9.2's strict Pydantic union rejects.
        if fmt == 'ustx' and 'maskedcurve' in msg:
            raw_text = path.read_text(encoding='utf-8-sig')
            patched, count = patch_ustx_masked_curve(raw_text)
            if count:
                from vwa_io import resilient_tempdir
                with resilient_tempdir(prefix='vwa-ustx-compat-') as tmp:
                    temp_path = tmp / path.name
                    temp_path.write_text(patched, encoding='utf-8')
                    return converter.load(temp_path, opts)
        # Official CeVIO projects can store decimal splitter positions (e.g. Partition="76.8")
        # on SongEditor/TalkEditor UI layout tags, which LibreSVIP's Pydantic int field rejects.
        if fmt == 'ccs' and 'partition' in msg:
            raw_text = path.read_text(encoding='utf-8-sig')
            patched, count = _CCS_FLOAT_PARTITION_RE.subn(
                lambda m: f'{m.group(1)}{round(float(m.group(2)))}{m.group(3)}',
                raw_text,
            )
            if count:
                from vwa_io import resilient_tempdir
                with resilient_tempdir(prefix='vwa-ccs-compat-') as tmp:
                    temp_path = tmp / path.name
                    temp_path.write_text(patched, encoding='utf-8')
                    return converter.load(temp_path, opts)
        # UST encoding fallback: retry alternate encoding (UTF-8 <-> Shift-JIS) if decoding failed
        if fmt == 'ust' and ('codec' in msg or 'decode' in msg or 'encoding' in msg):
            alt = 'SHIFT_JIS' if opts.get('encoding') == 'utf-8' else 'utf-8'
            return converter.load(path, {**opts, 'encoding': alt})
        raise


def _handle_inspect(payload: dict[str, Any]) -> dict[str, Any]:
    _bootstrap_libresvip()
    from libresvip.model.base import InstrumentalTrack, SingingTrack
    from project_convert_service import SPECS_BY_ID, _format_meter_summary, _format_tempo_summary

    path = Path(payload['path']).resolve()
    fmt = payload['format']
    converter = _get_converter(fmt)
    project = _load_with_converter(converter, path, fmt)
    spec = SPECS_BY_ID[fmt]

    tracks = []
    vocal_idx = 0
    total_notes = 0
    for idx, track in enumerate(project.track_list):
        if isinstance(track, SingingTrack):
            note_count = len(track.note_list)
            total_notes += note_count
            pitch_pts = getattr(getattr(getattr(track, 'edited_params', None), 'pitch', None), 'points', None)
            has_pitch = bool(pitch_pts and any(p.y != -100 for p in pitch_pts.root))
            has_lyrics = any(
                str(n.lyric or '').strip() not in ('', '-', '+', 'R', 'r', 'sil')
                for n in track.note_list
            )
            tracks.append({
                'index': idx,
                'vocalIndex': vocal_idx,
                'name': str(track.title or f'演唱轨 {vocal_idx + 1}'),
                'kind': 'vocal',
                'noteCount': note_count,
                'hasPitch': has_pitch,
                'hasLyrics': has_lyrics,
                'audioPath': '',
                'selected': note_count > 0,
            })
            vocal_idx += 1
        elif isinstance(track, InstrumentalTrack):
            tracks.append({
                'index': idx,
                'vocalIndex': None,
                'name': str(track.title or f'伴奏轨 {idx + 1}'),
                'kind': 'instrumental',
                'noteCount': 0,
                'hasPitch': False,
                'hasLyrics': False,
                'audioPath': str(track.audio_file_path or ''),
                'selected': False,
            })

    tempos = [{'bpm': float(t.bpm)} for t in (project.song_tempo_list or [])]
    meters = [{'numerator': m.numerator, 'denominator': m.denominator} for m in (project.time_signature_list or [])]
    return {
        'path': strip_extended_prefix(path),
        'fileName': path.name,
        'sizeBytes': path.stat().st_size,
        'sha256': file_hash(path),
        'format': fmt,
        'formatLabel': spec.label,
        'formatShort': spec.short_label,
        'formatVersion': str(getattr(project, 'version', '') or ''),
        'sv2': False,
        'acep2': False,
        'hasGroupRefs': False,
        'engineSource': 'libresvip',
        'tempoSummary': _format_tempo_summary(tempos),
        'meterSummary': _format_meter_summary(meters),
        'durationSeconds': _duration_from_project(project),
        'totalNotes': total_notes,
        'vocalTrackCount': vocal_idx,
        'instrumentalTrackCount': sum(1 for t in tracks if t['kind'] == 'instrumental'),
        'tracks': tracks,
        'sourceNotes': [f'已通过 LibreSVIP 插件解析 {spec.short_label} 工程。'],
        'defaultOutputDir': strip_extended_prefix(path.parent),
    }


def _dump_and_verify_one(
    sub_project,
    target_path: Path,
    target_format: str,
    *,
    language: str,
    options: dict[str, Any],
    source_stem: str,
    expected_source_notes: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    from libresvip.model.base import InstrumentalTrack, SingingTrack
    from project_convert_service import SPECS_BY_ID
    from project_exports import (
        english_svp_project,
        remove_pitch_data,
        restore_english_ustx_lyrics,
        restore_vsqx_lyrics,
    )

    spec = SPECS_BY_ID[target_format]
    converter = _get_converter(target_format)
    vocal_tracks = [t for t in sub_project.track_list if isinstance(t, SingingTrack)]
    loaded_notes = sum(len(t.note_list) for t in vocal_tracks)
    source_notes = expected_source_notes if (expected_source_notes is not None and expected_source_notes > loaded_notes) else loaded_notes
    track_names = [str(t.title or f'轨道 {i + 1}') for i, t in enumerate(vocal_tracks)]
    item_warnings: list[str] = []

    # Strip instrumental tracks if disabled or unsupported by target
    if not options['include_instrumental'] or not spec.supports_instrumental:
        sub_project = sub_project.model_copy(
            update={'track_list': [t for t in sub_project.track_list if not isinstance(t, InstrumentalTrack)]}
        )

    if options['pitch_mode'] == 'strip':
        _strip_project_pitch(sub_project)

    draft = sub_project
    if options['lyric_mode'] == 'adapt' and language == 'en' and target_format == 'svp':
        draft, _ = english_svp_project(draft)
    out_opts = _build_output_options(
        target_format,
        language=language,
        lyric_mode=options['lyric_mode'],
        source_stem=source_stem,
    )

    ds_skipped_rests = 0
    if target_format == 'ds':
        draft, dict_name, ds_skipped_rests, ds_warns = prepare_diffsinger_project(draft, language=language)
        out_opts['dict_name'] = dict_name
        item_warnings.extend(ds_warns)

    pending = target_path.with_name(f'.pending-{os.getpid()}-{target_path.name}')
    try:
        try:
            converter.dump(pending, draft, out_opts)
        except Exception as exc:
            if target_format == 'ds' and 'dictionary' in str(exc).lower():
                raise ValueError(
                    f'DiffSinger 词典（{out_opts.get("dict_name", "")}）未能匹配部分音节：{exc}。'
                    '建议检查原工程是否存在空歌词、特殊符号或混合语种音符。'
                ) from exc
            raise

        # Apply VWA's proven post-dump fixes
        if options['lyric_mode'] == 'adapt' and language == 'en' and target_format == 'ustx':
            restore_english_ustx_lyrics(pending, sub_project)
        elif target_format == 'vsqx':
            vsqx_lang = language if options['lyric_mode'] == 'adapt' else 'zh'
            if vsqx_lang == 'en' and not vsqx_english_dict_available(ROOT):
                item_warnings.append(
                    '当前环境未安装可选英文音素词典（vocal2midi），已跳过英文 VSQX 自动音素补写并保留原始歌词与音符。'
                )
                restore_vsqx_lyrics(pending, sub_project, 'zh')
            else:
                restore_vsqx_lyrics(pending, sub_project, vsqx_lang)
        if options['pitch_mode'] == 'strip' and target_format in ('ustx', 'svp', 'vsqx'):
            remove_pitch_data(pending, target_format)

        # Read-back verification before atomic commit
        verified = False
        output_notes = loaded_notes
        line_count = 0
        if spec.group == 'lyrics':
            text = pending.read_text(encoding='utf-8-sig')
            lines = [ln for ln in text.splitlines() if ln.strip()]
            line_count = len(lines)
            if not lines:
                raise ValueError(f'导出的 {spec.short_label} 歌词文件为空，请确认所选轨道包含有效歌词。')
            verified = True
        elif target_format in ('svp', 'ustx', 'vpr', 'vsqx', 'ufdata'):
            # Use VWA's own lossless native layout reader so breath notes (`br`) are not
            # dropped by LibreSVIP's default SVP import filter during verification.
            from project_io import read_source, score_layout
            snap_out = read_source(pending)
            _, lay_out = score_layout(snap_out.data, target_format)
            output_notes = sum(t.note_count for t in lay_out.tracks if not t.instrumental)
            verified = (output_notes == source_notes)
        elif spec.can_read:
            reloaded = _load_with_converter(converter, pending, target_format)
            output_notes = sum(
                len(t.note_list) for t in reloaded.track_list if isinstance(t, SingingTrack)
            )
            # Single-track targets only keep the first vocal track if user didn't split
            base_expected = (
                len(vocal_tracks[0].note_list)
                if (not spec.supports_multitrack and vocal_tracks)
                else source_notes
            )
            expected_notes = max(0, base_expected - ds_skipped_rests)
            verified = (output_notes == expected_notes)
        else:
            verified = pending.is_file() and pending.stat().st_size > 0

        os.replace(pending, target_path)
    finally:
        pending.unlink(missing_ok=True)

    return {
        'path': strip_extended_prefix(target_path),
        'fileName': target_path.name,
        'trackNames': track_names,
        'sourceNotes': source_notes,
        'outputNotes': output_notes,
        'lineCount': line_count,
        'verified': verified,
        'sizeBytes': target_path.stat().st_size,
        'sha256': file_hash(target_path),
    }, item_warnings


def _handle_convert(payload: dict[str, Any]) -> dict[str, Any]:
    _bootstrap_libresvip()
    from libresvip.model.base import InstrumentalTrack, SingingTrack
    from project_convert_service import SPECS_BY_ID

    source_path = Path(payload['source_path']).resolve()
    source_format = payload['source_format']
    target_format = payload['target_format']
    options = payload['options']
    selected_vocal_indices = set(payload['selected_vocal_indices'])
    planned_outputs = payload['planned_outputs']
    use_hf = bool(payload.get('use_high_fidelity_reader'))

    source_spec = SPECS_BY_ID[source_format]
    target_spec = SPECS_BY_ID[target_format]

    if use_hf:
        project = _load_high_fidelity_project(
            source_path, source_format, include_pitch=(options['pitch_mode'] == 'keep')
        )
        reader_engine = 'vwa_high_fidelity'
    else:
        in_converter = _get_converter(source_format)
        project = _load_with_converter(in_converter, source_path, source_format)
        reader_engine = 'libresvip'

    all_vocals = [t for t in project.track_list if isinstance(t, SingingTrack)]
    all_insts = [t for t in project.track_list if isinstance(t, InstrumentalTrack)]
    chosen_vocals = [t for i, t in enumerate(all_vocals) if i in selected_vocal_indices]
    if not chosen_vocals:
        raise ValueError('源工程中未找到选中的人声轨。')

    language = _detect_language(chosen_vocals)
    inst_tracks = all_insts if (options['include_instrumental'] and target_spec.supports_instrumental) else []

    results = []
    warnings: list[str] = []
    expected_list = payload.get('expected_source_notes') or []
    if options['split_tracks'] and len(chosen_vocals) > 1:
        for idx, (vocal, plan) in enumerate(zip(chosen_vocals, planned_outputs, strict=True)):
            sub = project.model_copy(update={'track_list': [vocal, *inst_tracks]})
            exp_n = expected_list[idx] if idx < len(expected_list) else None
            row, warns = _dump_and_verify_one(
                sub,
                Path(plan['path']),
                target_format,
                language=language,
                options=options,
                source_stem=source_path.stem,
                expected_source_notes=exp_n,
            )
            results.append(row)
            for w in warns:
                if w not in warnings:
                    warnings.append(w)
    else:
        sub = project.model_copy(update={'track_list': [*chosen_vocals, *inst_tracks]})
        exp_total = sum(expected_list) if expected_list else None
        row, warns = _dump_and_verify_one(
            sub,
            Path(planned_outputs[0]['path']),
            target_format,
            language=language,
            options=options,
            source_stem=source_path.stem,
            expected_source_notes=exp_total,
        )
        results.append(row)
        for w in warns:
            if w not in warnings:
                warnings.append(w)

    # Honest preserved vs omitted summary
    preserved: list[str] = ['速度表与拍号']
    if target_spec.group == 'lyrics':
        preserved.append('按音符时间轴对齐的歌词文本')
    else:
        preserved.append(f'所选 {len(chosen_vocals)} 条人声轨音符、时长与歌词')
        if options['pitch_mode'] == 'keep' and target_spec.supports_pitch:
            preserved.append('音高曲线（按目标格式坐标系换算）')
        if inst_tracks:
            preserved.append(f'{len(inst_tracks)} 条伴奏轨引用')

    omitted: list[str] = ['原编辑器专有声库音色、AI 参数与混音插件']
    if options['pitch_mode'] == 'strip':
        omitted.append('音高曲线与颤音（按所选「剥离音高」策略主动清除）')
    elif not target_spec.supports_pitch and target_spec.group != 'lyrics':
        omitted.append(f'连续音高曲线（{target_spec.short_label} 格式不支持）')
    if all_insts and not inst_tracks:
        if not options['include_instrumental']:
            omitted.append('伴奏轨（按所选「仅导出人声轨」策略跳过）')
        else:
            omitted.append(f'伴奏轨（{target_spec.short_label} 格式不含伴奏轨）')

    if not all(r['verified'] for r in results):
        warnings.append('部分输出文件的回读音符数与源轨道存在差异（可能因目标格式合并/截断重叠音符），请在目标软件中复核。')

    return {
        'sourcePath': strip_extended_prefix(source_path),
        'sourceFormat': source_format,
        'sourceLabel': source_spec.label,
        'targetFormat': target_format,
        'targetLabel': target_spec.label,
        'targetGroup': target_spec.group,
        'readerEngine': reader_engine,
        'detectedLanguage': language,
        'outputDir': strip_extended_prefix(payload['output_dir']),
        'outputs': results,
        'preserved': preserved,
        'omitted': omitted,
        'warnings': warnings,
        'canSendToTuning': target_format in ('svp', 'ustx', 'vpr', 'vsqx', 'ufdata'),
        'canSendToChorus': target_format in ('svp', 'ustx', 'vpr', 'vsqx', 'ufdata', 'mid'),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in ('inspect', 'convert'):
        sys.stderr.write('Usage: project_convert_worker.py <inspect|convert> <request.json> <response.json>\n')
        return 2
    command, req_path, res_path = argv[1], Path(argv[2]), Path(argv[3])
    try:
        payload = json.loads(req_path.read_text(encoding='utf-8'))
        result = _handle_inspect(payload) if command == 'inspect' else _handle_convert(payload)
        res_path.write_text(json.dumps({'ok': True, 'result': result}, ensure_ascii=False), encoding='utf-8')
        return 0
    except Exception as exc:
        src_name = Path(payload.get('path') or payload.get('source_path') or '').name if 'payload' in locals() else ''
        res_path.write_text(
            json.dumps(
                {'ok': False, 'error': friendly_project_error(exc, file_name=src_name), 'traceback': traceback.format_exc()},
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
