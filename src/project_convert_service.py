"""Qt-free project format conversion domain service, preflight inspection, and subprocess runner.

Never imports PySide6 or LibreSVIP at module level so cold startup stays fast and
process-wide YAML behavior stays isolated in the converter worker subprocess.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

from common import ROOT, native_path


@dataclass(frozen=True)
class FormatSpec:
    id: str
    label: str
    short_label: str
    extensions: tuple[str, ...]
    group: str  # 'core' | 'extended' | 'lyrics'
    can_read: bool
    can_write: bool
    supports_pitch: bool
    supports_instrumental: bool
    supports_multitrack: bool
    high_fidelity_reader: bool = False
    note: str = ''


FORMAT_SPECS: tuple[FormatSpec, ...] = (
    # Core bidirectional + high-fidelity formats
    FormatSpec(
        id='svp', label='Synthesizer V (.svp)', short_label='SVP',
        extensions=('.svp',), group='core',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True, high_fidelity_reader=True,
        note='支持读取 SV1 与 SV2（v182–196）；导出为通用 SVP，SV1/SV2 均可打开。',
    ),
    FormatSpec(
        id='ustx', label='OpenUtau (.ustx)', short_label='USTX',
        extensions=('.ustx',), group='core',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True, high_fidelity_reader=True,
        note='支持多轨、音高曲线与中/日/英续唱符自动适配。',
    ),
    FormatSpec(
        id='vpr', label='VOCALOID 5/6 (.vpr)', short_label='VPR',
        extensions=('.vpr',), group='core',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True, high_fidelity_reader=True,
        note='支持 VOCALOID 5/6 工程读写与多轨结构。',
    ),
    FormatSpec(
        id='vsqx', label='VOCALOID 3/4 (.vsqx)', short_label='VSQX',
        extensions=('.vsqx',), group='core',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True, high_fidelity_reader=True,
        note='支持 VSQX3/4，英文工程自动补写标准发音音素。',
    ),
    FormatSpec(
        id='ufdata', label='UtaFormatix / ACE 交换 (.ufdata)', short_label='UFData',
        extensions=('.ufdata',), group='core',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=False,
        supports_multitrack=True, high_fidelity_reader=True,
        note='ACE Studio 官方推荐的通用交换格式（不含伴奏轨与歌手配置）。',
    ),
    FormatSpec(
        id='mid', label='标准 MIDI (.mid)', short_label='MIDI',
        extensions=('.mid', '.midi'), group='core',
        can_read=True, can_write=True, supports_pitch=False, supports_instrumental=False,
        supports_multitrack=True, high_fidelity_reader=True,
        note='通用乐谱与歌词 MIDI，适合 DAW、扒谱或参考谱轨。',
    ),
    FormatSpec(
        id='acep', label='ACE Studio (.acep)', short_label='ACEP',
        extensions=('.acep',), group='core',
        can_read=True, can_write=False, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True, high_fidelity_reader=True,
        note='支持高保真读取老版 JSON 与新版 ACEP2（CBOR）；若需导回 ACE Studio 请选择 UFData。',
    ),
    # Lyrics export formats (output-only)
    FormatSpec(
        id='lrc', label='LRC 滚动歌词 (.lrc)', short_label='LRC',
        extensions=('.lrc',), group='lyrics',
        can_read=False, can_write=True, supports_pitch=False, supports_instrumental=False,
        supports_multitrack=False,
        note='按工程音符时间轴自动生成 UTF-8 LRC 时间戳歌词。',
    ),
    FormatSpec(
        id='srt', label='SRT 视频字幕 (.srt)', short_label='SRT',
        extensions=('.srt',), group='lyrics',
        can_read=False, can_write=True, supports_pitch=False, supports_instrumental=False,
        supports_multitrack=False,
        note='按工程音符时间轴自动生成 UTF-8 SRT 字幕文件。',
    ),
    # Extended formats (powered by bundled LibreSVIP plugins)
    FormatSpec(
        id='ust', label='UTAU (.ust)', short_label='UST',
        extensions=('.ust',), group='extended',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=False,
        supports_multitrack=False,
        note='经典 UTAU 单轨格式；多轨导出时建议开启「按人声轨拆分」。',
    ),
    FormatSpec(
        id='svip', label='X Studio (.svip)', short_label='SVIP',
        extensions=('.svip',), group='extended',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True,
        note='支持 X Studio 二进制工程互转。',
    ),
    FormatSpec(
        id='ccs', label='CeVIO (.ccs)', short_label='CCS',
        extensions=('.ccs',), group='extended',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True,
        note='支持 CeVIO Creative Studio / AI 工程互转。',
    ),
    FormatSpec(
        id='ds', label='DiffSinger (.ds)', short_label='DS',
        extensions=('.ds',), group='extended',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=False,
        supports_multitrack=False,
        note='DiffSinger 推理脚本格式；多轨建议勾选单轨或拆分导出。',
    ),
    FormatSpec(
        id='dspx', label='OpenVPI DSPX (.dspx)', short_label='DSPX',
        extensions=('.dspx',), group='extended',
        can_read=True, can_write=True, supports_pitch=True, supports_instrumental=True,
        supports_multitrack=True,
        note='OpenVPI / DiffScope 工程格式。',
    ),
    FormatSpec(
        id='musicxml', label='MusicXML (.musicxml)', short_label='MusicXML',
        extensions=('.musicxml', '.xml'), group='extended',
        can_read=True, can_write=True, supports_pitch=False, supports_instrumental=False,
        supports_multitrack=True,
        note='通用五线谱交换格式，适合 MuseScore / Sibelius / Dorico。',
    ),
)

SPECS_BY_ID: dict[str, FormatSpec] = {spec.id: spec for spec in FORMAT_SPECS}
EXTENSION_TO_FORMAT: dict[str, str] = {
    ext: spec.id for spec in FORMAT_SPECS for ext in spec.extensions
}
READABLE_FORMATS = tuple(spec for spec in FORMAT_SPECS if spec.can_read)
WRITABLE_FORMATS = tuple(spec for spec in FORMAT_SPECS if spec.can_write)
NATIVE_INSPECT_FORMATS = frozenset(('svp', 'ustx', 'vpr', 'vsqx', 'acep', 'ufdata'))

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_CONVERTER_OPTIONS_DEFAULTS = {
    'pitch_mode': 'keep',       # 'keep' | 'strip'
    'lyric_mode': 'adapt',      # 'adapt' | 'raw'
    'include_instrumental': True,
    'split_tracks': False,
    'selected_tracks': None,    # list[int] of vocal track indices (0-based among vocal tracks), or None = all
}


def spec_to_dict(spec: FormatSpec) -> dict[str, Any]:
    return {
        'id': spec.id,
        'label': spec.label,
        'shortLabel': spec.short_label,
        'extensions': list(spec.extensions),
        'primaryExt': spec.extensions[0],
        'group': spec.group,
        'canRead': spec.can_read,
        'canWrite': spec.can_write,
        'supportsPitch': spec.supports_pitch,
        'supportsInstrumental': spec.supports_instrumental,
        'supportsMultitrack': spec.supports_multitrack,
        'highFidelityReader': spec.high_fidelity_reader,
        'note': spec.note,
    }


def catalog() -> dict[str, Any]:
    """Return UI-ready format catalog grouped into Core, Lyrics, and Extended."""
    all_exts = sorted({ext for spec in READABLE_FORMATS for ext in spec.extensions})
    filter_globs = ' '.join(f'*{ext}' for ext in all_exts)
    return {
        'inputFormats': [spec_to_dict(s) for s in READABLE_FORMATS],
        'outputCoreFormats': [spec_to_dict(s) for s in WRITABLE_FORMATS if s.group == 'core'],
        'outputLyricsFormats': [spec_to_dict(s) for s in WRITABLE_FORMATS if s.group == 'lyrics'],
        'outputExtendedFormats': [spec_to_dict(s) for s in WRITABLE_FORMATS if s.group == 'extended'],
        'openFileFilter': f'支持的人声工程 ({filter_globs})',
        'runtimeAvailable': export_runtime_available(),
    }


def detect_format(path: str | Path) -> str:
    suffix = Path(path).suffix.casefold()
    fmt = EXTENSION_TO_FORMAT.get(suffix)
    if not fmt:
        raise ValueError(f'暂不支持该工程文件后缀：{suffix or "(无后缀)"}')
    return fmt


def export_runtime_available(root: Path | None = None) -> bool:
    explicit = os.environ.get('VWA_EXPORT_RUNTIME', '').strip()
    if explicit and (Path(explicit) / 'libresvip/__init__.py').is_file():
        return True
    base = Path(root) if root is not None else ROOT
    for candidate in (base / 'dependencies/export/libresvip/__init__.py',
                      base.parent / 'dependencies/export/libresvip/__init__.py'):
        if candidate.is_file():
            return True
    return False


def _sanitize_filename_part(text: str, fallback: str = 'track') -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub('_', str(text or '').strip()).strip(' .')
    return cleaned[:48] if cleaned else fallback


def _unique_non_clobbering_path(directory: Path, stem: str, ext: str, *, source_resolved: Path) -> Path:
    candidate = (directory / f'{stem}{ext}').resolve()
    if candidate != source_resolved and not candidate.exists():
        return candidate
    for idx in range(2, 1000):
        candidate = (directory / f'{stem}_{idx}{ext}').resolve()
        if candidate != source_resolved and not candidate.exists():
            return candidate
    raise ValueError('目标目录已有过多同名文件，请更换输出目录。')


def plan_output_paths(
    source_path: str | Path,
    target_format: str,
    output_dir: str | Path | None = None,
    *,
    vocal_track_names: list[str] | tuple[str, ...] = (),
    split_tracks: bool = False,
) -> list[dict[str, Any]]:
    """Compute safe, non-clobbering output paths for single or per-track split exports."""
    if target_format not in SPECS_BY_ID or not SPECS_BY_ID[target_format].can_write:
        raise ValueError(f'目标格式 {target_format!r} 不支持导出。')
    source = Path(source_path).expanduser().resolve()
    directory = Path(output_dir).expanduser().resolve() if output_dir else source.parent
    ext = SPECS_BY_ID[target_format].extensions[0]
    base_stem = _sanitize_filename_part(source.stem, fallback='converted')

    if split_tracks and len(vocal_track_names) > 1:
        planned = []
        used: set[Path] = set()
        for index, name in enumerate(vocal_track_names):
            tag = _sanitize_filename_part(name, fallback=f'track_{index + 1}')
            stem = f'{base_stem} - {tag}'
            path = _unique_non_clobbering_path(directory, stem, ext, source_resolved=source)
            n = 2
            while path in used or path == source or path.exists():
                path = (directory / f'{stem}_{n}{ext}').resolve()
                n += 1
            used.add(path)
            planned.append({'path': str(path), 'fileName': path.name, 'trackName': name or f'轨道 {index + 1}'})
        return planned

    path = _unique_non_clobbering_path(directory, base_stem, ext, source_resolved=source)
    return [{'path': str(path), 'fileName': path.name, 'trackName': ''}]


def normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(_CONVERTER_OPTIONS_DEFAULTS)
    if isinstance(options, dict):
        raw.update(options)
    pitch_mode = raw.get('pitch_mode', 'keep')
    if pitch_mode not in ('keep', 'strip'):
        raise ValueError('音高策略必须是 keep 或 strip。')
    lyric_mode = raw.get('lyric_mode', 'adapt')
    if lyric_mode not in ('adapt', 'raw'):
        raise ValueError('歌词策略必须是 adapt 或 raw。')
    selected = raw.get('selected_tracks')
    if selected is not None:
        if not isinstance(selected, (list, tuple)) or any(type(i) is not int or i < 0 for i in selected):
            raise ValueError('所选轨道索引无效。')
        selected = sorted(set(selected))
    return {
        'pitch_mode': pitch_mode,
        'lyric_mode': lyric_mode,
        'include_instrumental': bool(raw.get('include_instrumental', True)),
        'split_tracks': bool(raw.get('split_tracks', False)),
        'selected_tracks': selected,
    }


def _format_tempo_summary(tempos: list[dict[str, Any]]) -> str:
    if not tempos:
        return '120 BPM'
    bpms = [float(t['bpm']) for t in tempos if 'bpm' in t]
    if not bpms:
        return '120 BPM'
    lo, hi = min(bpms), max(bpms)
    if abs(hi - lo) < 0.05:
        val = f'{lo:.1f}'.rstrip('0').rstrip('.')
        return f'{val} BPM'
    return f'{lo:.1f}–{hi:.1f} BPM（{len(bpms)} 处变速）'


def _format_meter_summary(meters: list[dict[str, Any]]) -> str:
    if not meters:
        return '4/4'
    first = meters[0]
    base = f"{int(first.get('numerator', 4))}/{int(first.get('denominator', 4))}"
    return base if len(meters) == 1 else f'{base}（{len(meters)} 处拍号）'


def _inspect_native_project(path: Path, fmt: str) -> dict[str, Any]:
    """Zero-subprocess inspection using VWA's own verified parsers for SVP/USTX/VPR/VSQX/ACEP/UFData."""
    from project_io import read_source, score_layout
    from tuning_native_pitch import _collect

    snapshot = read_source(path)
    score, layout = score_layout(snapshot.data, fmt)
    spec = SPECS_BY_ID[fmt]
    clock = layout.clock

    tracks: list[dict[str, Any]] = []
    vocal_index = 0
    max_end_sec = 0.0
    total_notes = 0
    overlap_tracks = 0

    for row, raw_track in zip(layout.tracks, score['tracks']):
        is_inst = bool(row.instrumental)
        note_count = row.note_count
        total_notes += note_count
        has_lyrics = False
        has_pitch = False
        if not is_inst:
            for placement in row.placements:
                part_ref = layout.native.parts.get((row.index, placement.key))
                if part_ref is not None:
                    try:
                        if _collect(snapshot.data, fmt, part_ref.raw):
                            has_pitch = True
                    except (KeyError, TypeError, ValueError, OverflowError):
                        has_pitch = has_pitch
                for note in placement.notes:
                    if not has_lyrics:
                        lyr = str(note.data.get('lyrics') or '').strip()
                        if lyr and lyr not in ('-', '+', 'sil', 'sp', 'pau', 'R', 'r'):
                            has_lyrics = True
                    try:
                        end_s = float(clock.to_seconds(note.end))
                        if end_s > max_end_sec:
                            max_end_sec = end_s
                    except (TypeError, ValueError, OverflowError):
                        max_end_sec = max_end_sec
            if any('overlap' in k for k in row.issues) or any(getattr(p, 'overlap_spans', None) for p in row.placements):
                overlap_tracks += 1

        media_ref = next((m.reference for m in layout.native.media if m.track == row.index and m.reference), '')
        tracks.append({
            'index': row.index,
            'vocalIndex': None if is_inst else vocal_index,
            'name': str(raw_track.get('name') or (f'伴奏轨 {row.index + 1}' if is_inst else f'演唱轨 {vocal_index + 1}')),
            'kind': 'instrumental' if is_inst else 'vocal',
            'noteCount': note_count,
            'hasPitch': has_pitch,
            'hasLyrics': has_lyrics,
            'audioPath': str(media_ref or ''),
            'selected': not is_inst and note_count > 0,
        })
        if not is_inst:
            vocal_index += 1

    # Include VSQX standalone wavParts (not bound to a vsTrack index)
    if fmt == 'vsqx':
        for idx, media in enumerate(layout.native.media):
            if media.track is None and media.reference:
                tracks.append({
                    'index': len(tracks),
                    'vocalIndex': None,
                    'name': Path(media.reference).name or f'伴奏音频 {idx + 1}',
                    'kind': 'instrumental',
                    'noteCount': 0,
                    'hasPitch': False,
                    'hasLyrics': False,
                    'audioPath': str(media.reference),
                    'selected': False,
                })

    sv2 = bool(getattr(layout, 'sv2', False))
    acep2 = fmt == 'acep' and snapshot.data.get('container') == 'acep2'
    has_group_refs = fmt == 'svp' and any(t.native_refs for t in layout.tracks)
    version_text = str(layout.version or '')
    if sv2:
        version_text = f'SV2 ({layout.version})'
    elif acep2:
        version_text = f'ACEP2 ({layout.version})'

    source_notes: list[str] = []
    if sv2:
        source_notes.append('检测到 Synthesizer V 2 工程：将走 VWA 高保真自研读取通道，音符组与音高曲线会自动展平转换。')
    elif has_group_refs:
        source_notes.append('检测到 SVP 素材库音符组引用：转换时将展平为独立音符。')
    if acep2:
        source_notes.append('检测到新版 ACEP2（CBOR）工程：已通过 VWA 高保真解码通道完整解析。')
    elif fmt == 'acep':
        source_notes.append('已通过 VWA 高保真解码通道解析 ACEP 工程。')
    if overlap_tracks:
        source_notes.append(f'检测到 {overlap_tracks} 条人声轨含重叠音符，转换至单声部格式时请注意核对。')

    return {
        'path': str(snapshot.path),
        'fileName': snapshot.path.name,
        'sizeBytes': snapshot.size,
        'sha256': snapshot.sha256,
        'format': fmt,
        'formatLabel': spec.label,
        'formatShort': spec.short_label,
        'formatVersion': version_text,
        'sv2': sv2,
        'acep2': acep2,
        'engineSource': 'native',
        'tempoSummary': _format_tempo_summary(score['time'].get('tempo', [])),
        'meterSummary': _format_meter_summary(score['time'].get('meter', [])),
        'durationSeconds': round(max_end_sec, 2),
        'totalNotes': total_notes,
        'vocalTrackCount': vocal_index,
        'instrumentalTrackCount': sum(1 for t in tracks if t['kind'] == 'instrumental'),
        'tracks': tracks,
        'sourceNotes': source_notes,
        'defaultOutputDir': str(snapshot.path.parent),
    }


def _inspect_midi_project(path: Path) -> dict[str, Any]:
    """Fast pure-Python MIDI inspection via agent_score.read_score."""
    from agent_score import read_score

    parsed = read_score(path)
    spec = SPECS_BY_ID['mid']
    notes_by_track: dict[int, list[dict[str, Any]]] = {}
    max_end = 0.0
    for row in parsed['notes']:
        notes_by_track.setdefault(row['track_index'], []).append(row)
        end_s = row.get('end_seconds')
        if isinstance(end_s, (int, float)) and end_s > max_end:
            max_end = float(end_s)

    tracks: list[dict[str, Any]] = []
    vocal_index = 0
    for t in parsed['tracks']:
        t_idx = int(t['track_index'])
        t_notes = notes_by_track.get(t_idx, [])
        if not t_notes:
            continue
        has_lyrics = any(bool((n.get('lyric') or '').strip()) for n in t_notes)
        tracks.append({
            'index': t_idx,
            'vocalIndex': vocal_index,
            'name': str(t.get('name') or f'MIDI 轨道 {vocal_index + 1}'),
            'kind': 'vocal',
            'noteCount': len(t_notes),
            'hasPitch': False,
            'hasLyrics': has_lyrics,
            'audioPath': '',
            'selected': True,
        })
        vocal_index += 1

    tempo_rows = [{'bpm': float(row['bpm'])} for row in parsed.get('tempos', []) if 'bpm' in row]
    return {
        'path': str(path),
        'fileName': path.name,
        'sizeBytes': path.stat().st_size,
        'sha256': parsed['source_sha256'],
        'format': 'mid',
        'formatLabel': spec.label,
        'formatShort': spec.short_label,
        'formatVersion': f"SMF Type {parsed.get('midi_type', 1)} ({parsed.get('ticks_per_beat', 480)} PPQ)",
        'sv2': False,
        'acep2': False,
        'engineSource': 'native',
        'tempoSummary': _format_tempo_summary(tempo_rows),
        'meterSummary': '4/4',
        'durationSeconds': round(max_end, 2),
        'totalNotes': sum(t['noteCount'] for t in tracks),
        'vocalTrackCount': vocal_index,
        'instrumentalTrackCount': 0,
        'tracks': tracks,
        'sourceNotes': ['标准 MIDI 不含连续音高曲线与伴奏轨。'],
        'defaultOutputDir': str(path.parent),
    }


def _worker_script() -> Path:
    candidates = (
        Path(__file__).with_name('project_convert_worker.py'),
        ROOT / 'project_convert_worker.py',
        ROOT / 'src/project_convert_worker.py',
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError('缺少工程互转工作器：project_convert_worker.py')


def _run_worker(
    command: str,
    payload: dict[str, Any],
    *,
    cancel: Callable[[], bool] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    if not export_runtime_available():
        raise FileNotFoundError('缺少工程互转运行库（dependencies/export），请在完整环境中安装或修复组件。')
    worker = _worker_script()
    with tempfile.TemporaryDirectory(prefix='vwa-convert-') as tmp:
        req_path = Path(tmp) / 'request.json'
        res_path = Path(tmp) / 'response.json'
        req_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.Popen(
            [sys.executable, '-B', str(worker), command, str(req_path), str(res_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
            env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if cancel is not None and cancel():
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise InterruptedError('已取消工程互转操作。')
                if time.monotonic() > deadline:
                    proc.kill()
                    raise TimeoutError('工程互转超时，已自动停止。')
                time.sleep(0.05)
        finally:
            stdout, stderr = proc.communicate()

        if res_path.is_file():
            data = json.loads(res_path.read_text(encoding='utf-8'))
            if data.get('ok'):
                return data['result']
            raise ValueError(data.get('error') or '工程互转执行失败。')

        err = (stderr or stdout or b'').decode('utf-8', errors='replace').strip()
        tail = err.splitlines()[-1] if err else f'退出码 {proc.returncode}'
        raise ValueError(f'工程互转子进程异常：{tail}')


def inspect_project_file(
    path: str | Path,
    *,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Inspect any supported project file and return a unified preflight summary."""
    resolved = Path(native_path(path)).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError('找不到所选工程文件，请确认文件路径是否存在。')
    fmt = detect_format(resolved)
    spec = SPECS_BY_ID[fmt]
    if not spec.can_read:
        raise ValueError(f'{spec.label} 仅支持作为导出目标，不能作为转换源。')
    if fmt in NATIVE_INSPECT_FORMATS:
        return _inspect_native_project(resolved, fmt)
    if fmt == 'mid':
        return _inspect_midi_project(resolved)
    return _run_worker('inspect', {'path': str(resolved), 'format': fmt}, cancel=cancel, timeout=60.0)


def build_precheck_warnings(
    inspection: dict[str, Any] | None,
    target_format: str,
    options: dict[str, Any] | None = None,
) -> list[str]:
    """Generate actionable, honest pre-conversion advisories for the user."""
    if not inspection or target_format not in SPECS_BY_ID:
        return []
    opts = normalize_options(options)
    target = SPECS_BY_ID[target_format]
    warnings: list[str] = list(inspection.get('sourceNotes') or ())

    vocals = [t for t in inspection.get('tracks', []) if t.get('kind') == 'vocal']
    selected_ids = opts['selected_tracks']
    active_vocals = (
        [t for t in vocals if t.get('vocalIndex') in selected_ids]
        if selected_ids is not None else vocals
    )
    if not active_vocals:
        warnings.append('当前未勾选任何演唱轨，请至少勾选 1 条含音符的人声轨。')

    if inspection.get('format') == target_format and not opts['split_tracks'] and opts['pitch_mode'] == 'keep':
        warnings.append('源格式与目标格式相同：转换将生成一份全新副本（可用于抽轨、剥离音高或清洗格式）。')

    if target.group == 'lyrics':
        if len(active_vocals) > 1 and not opts['split_tracks']:
            warnings.append(f'已选 {len(active_vocals)} 条人声轨：{target.short_label} 为单轨歌词格式，默认仅导出首条选中轨；如需各轨单独导出请开启「按人声轨拆分」。')
        if active_vocals and not any(t.get('hasLyrics') for t in active_vocals):
            warnings.append('所选轨道未检测到有效歌词文字，导出的歌词文件可能只有占位符或为空。')
    else:
        if not target.supports_multitrack and len(active_vocals) > 1 and not opts['split_tracks']:
            warnings.append(f'{target.short_label} 仅支持单轨工程：建议开启「按人声轨拆分」或仅勾选 1 条主唱轨。')
        if opts['pitch_mode'] == 'keep' and not target.supports_pitch and any(t.get('hasPitch') for t in active_vocals):
            warnings.append(f'{target.short_label} 不承载连续音高曲线，转换时将只保留音符、时值与歌词。')
        if opts['include_instrumental'] and inspection.get('instrumentalTrackCount', 0) > 0 and not target.supports_instrumental:
            warnings.append(f'{target.short_label} 不包含伴奏音轨，原工程中的伴奏引用将被跳过。')

    if target_format == 'ufdata':
        warnings.append('UFData 适用于 ACE Studio 导入与跨软件交换，不含声库音色与伴奏轨。')
    return warnings


def convert_project_file(
    source_path: str | Path,
    target_format: str,
    output_dir: str | Path | None = None,
    options: dict[str, Any] | None = None,
    *,
    inspection: dict[str, Any] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run an isolated conversion in a subprocess and return a verified receipt."""
    resolved = Path(native_path(source_path)).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError('找不到所选源工程文件。')
    source_format = detect_format(resolved)
    if not SPECS_BY_ID[source_format].can_read:
        raise ValueError(f'{SPECS_BY_ID[source_format].label} 不支持作为源格式。')
    if target_format not in SPECS_BY_ID or not SPECS_BY_ID[target_format].can_write:
        raise ValueError(f'目标格式 {target_format!r} 不支持导出。')

    opts = normalize_options(options)
    info = inspection if (inspection and Path(inspection.get('path', '')).resolve() == resolved) else inspect_project_file(resolved, cancel=cancel)

    vocals = [t for t in info.get('tracks', []) if t.get('kind') == 'vocal']
    if opts['selected_tracks'] is not None:
        chosen_vocals = [t for t in vocals if t.get('vocalIndex') in opts['selected_tracks']]
    else:
        chosen_vocals = [t for t in vocals if t.get('noteCount', 0) > 0] or vocals
    if not chosen_vocals:
        raise ValueError('请至少选择 1 条人声轨后再开始转换。')

    out_dir = Path(output_dir).expanduser().resolve() if output_dir else resolved.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    planned = plan_output_paths(
        resolved,
        target_format,
        out_dir,
        vocal_track_names=[t['name'] for t in chosen_vocals],
        split_tracks=opts['split_tracks'],
    )

    payload = {
        'source_path': str(resolved),
        'source_format': source_format,
        'target_format': target_format,
        'output_dir': str(out_dir),
        'planned_outputs': planned,
        'selected_vocal_indices': [int(t['vocalIndex']) for t in chosen_vocals],
        'options': opts,
        'use_high_fidelity_reader': bool(
            info.get('sv2') or source_format == 'acep'
        ),
    }
    return _run_worker('convert', payload, cancel=cancel, timeout=240.0)
