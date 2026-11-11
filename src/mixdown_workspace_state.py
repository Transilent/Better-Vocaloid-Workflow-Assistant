"""Qt-independent state and measured-data helpers for the Quick mixdown page.

The Quick controller owns one instance of :class:`MixdownWorkspaceState`.
Editable controls, source identity, portable processing snapshots, analytic EQ
curves and telemetry normalization live here so no QML object is ever treated
as the source of truth.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any
import uuid

from mixdown_contract import (
    DEFAULT_SAMPLE_RATE,
    EQ_BAND_TYPES,
    EQ_HIGHPASS_Q,
    EQ_MAX_FREQUENCY_HZ,
    EQ_MAX_GAIN_DB,
    EQ_MAX_Q,
    EQ_MIN_FREQUENCY_HZ,
    EQ_MIN_GAIN_DB,
    EQ_MIN_Q,
    MAX_VOCALS,
    REVERB_PROFILES,
    eq_frequency_limit_hz,
    finite_number,
    normalize_eq_bands,
    project_eq_band,
    valid_sample_rate,
)
from mixdown_eq_math import (
    DEFAULT_BANDS,
    RESPONSE_POINTS,
    default_bands as _shared_default_bands,
    frequency_to_unit,
    normalize_band,
    response_curve,
)
from mixdown_limiter_spec import PEAK_CEILING_DBFS
from mixdown_request import build_mixdown_request, build_processing_state


TELEMETRY_SCHEMA = 'vwa.audio_mixdown.realtime-telemetry/v1'
TELEMETRY_SOURCE = 'realtime_dsp'
AUDIO_SUFFIXES = frozenset(('.wav', '.flac', '.ogg', '.mp3', '.m4a', '.aac'))
ANALYSES = frozenset(('eq', 'gr', 'reverb'))
EQ_CURVE_POINTS = RESPONSE_POINTS
MAX_SPECTRUM_POINTS = 256
MAX_REVERB_POINTS = 256

_ROOM_MAP = {
    'small': 'small', 'short': 'small', '短空间': 'small',
    'natural': 'natural', '自然空间': 'natural',
    'wide': 'wide', '宽阔空间': 'wide', 'wide chorus': 'wide',
}


def _clamp(value: Any, low: float, high: float, label: str) -> float:
    number = finite_number(value, label)
    return float(max(low, min(high, number)))


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f'{label} must be a boolean')
    return value


def _path_text(value: Any, label: str) -> str:
    try:
        text = os.fspath(value)
    except TypeError as error:
        raise ValueError(f'{label} must be a path') from error
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    text = str(text).strip()
    if not text or '\x00' in text:
        raise ValueError(f'{label} must be a non-empty path')
    return text


def default_bands() -> list[dict[str, Any]]:
    return _shared_default_bands()


def normalize_bands(value: Any, sample_rate: Any = DEFAULT_SAMPLE_RATE) -> list[dict[str, Any]]:
    bands = normalize_eq_bands(value)
    if bands is None or len(bands) != 4:
        raise ValueError('four EQ bands are required')
    rate = valid_sample_rate(sample_rate)
    return [normalize_band(band, index, sample_rate=rate)
            for index, band in enumerate(bands)]


def compression_controls(strength: Any) -> dict[str, float]:
    """Return the actual Pedalboard compressor values derived from strength."""
    value = _clamp(strength, 0.0, 1.0, 'compression_strength')
    return {
        'threshold': -8.0 - 12.0 * value,
        'ratio': 1.0 + 2.0 * value,
        'attack': 25.0 - 10.0 * value,
        'release': 120.0 + 80.0 * value,
    }


def strength_for_compressor(name: str, value: Any) -> float:
    number = finite_number(value, name)
    key = str(name).replace('_', '').replace(' ', '').casefold()
    if key in ('compressionthreshold', 'threshold', 'compressionthresholddb'):
        result = (-8.0 - number) / 12.0
    elif key in ('compressionratio', 'ratio'):
        result = (number - 1.0) / 2.0
    elif key in ('compressorattack', 'compressionattack', 'attack', 'attackms'):
        result = (25.0 - number) / 10.0
    elif key in ('compressorrelease', 'compressionrelease', 'release', 'releasems'):
        result = (number - 120.0) / 80.0
    else:
        raise ValueError('unknown compressor control')
    return float(max(0.0, min(1.0, result)))


def _normal_path(path: Any) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def fresh_output_path(directory: Any = None) -> Path:
    """Return an unused independent WAV name without creating a directory."""
    if directory is None:
        from common import output_directory
        directory = output_directory()
    root = Path(directory).expanduser().resolve()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    candidate = root / ('simple-mixdown-' + stamp + '.wav')
    while candidate.exists():
        candidate = root / ('simple-mixdown-' + stamp + '-' + uuid.uuid4().hex[:8] + '.wav')
    return candidate


def _source_stat(path: str) -> dict[str, Any]:
    try:
        info = Path(path).stat()
        return {'bytes': int(info.st_size), 'mtime_ns': int(info.st_mtime_ns)}
    except OSError:
        return {'bytes': None, 'mtime_ns': None}


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def eq_curve(bands: Sequence[Mapping[str, Any]], sample_rate: Any = DEFAULT_SAMPLE_RATE,
             points: int = EQ_CURVE_POINTS) -> list[dict[str, float]]:
    """Project the shared RBJ response into QML normalized curve points."""
    count = max(32, int(points))
    rate = valid_sample_rate(sample_rate)
    frequencies, values = response_curve(
        bands, sample_rate=rate, points=count, enabled_only=True)
    return [
        {'x': round(max(0.0, min(1.0, frequency_to_unit(frequency, rate))), 7),
         'y': round(float(value), 5)}
        for frequency, value in zip(frequencies, values, strict=True)
    ]


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def telemetry_identity(telemetry: Any, snapshot: Any) -> tuple[int, int, int, str]:
    """Return the exact player identity for one renderer telemetry payload."""
    if not isinstance(telemetry, Mapping):
        raise ValueError('telemetry must be an object')
    if telemetry.get('schema') != TELEMETRY_SCHEMA or telemetry.get('status') != 'ready':
        raise ValueError('telemetry is not a ready realtime frame')
    if not isinstance(snapshot, Mapping):
        raise ValueError('player snapshot is unavailable')
    native = snapshot.get('native') if isinstance(snapshot.get('native'), Mapping) else {}
    frame = native.get('media_frame')
    revision = snapshot.get('parameter_revision')
    if frame is None or revision is None:
        raise ValueError('player frame or parameter revision is unavailable')
    media_frame = int(telemetry.get('media_frame'))
    parameter_revision = int(telemetry.get('parameter_revision'))
    history_epoch = int(telemetry.get('history_epoch'))
    if media_frame != int(frame):
        raise ValueError('telemetry belongs to another media frame')
    if parameter_revision != int(revision):
        raise ValueError('telemetry belongs to another parameter revision')
    source = str(telemetry.get('source') or '')
    if source != TELEMETRY_SOURCE:
        raise ValueError('telemetry source is not the realtime DSP')
    return int(frame), parameter_revision, history_epoch, source


def meters_from_telemetry(telemetry: Mapping[str, Any], vocal_ids: Sequence[str],
                          instrumental_id: str = 'instrumental') -> dict[str, Any]:
    rows = telemetry.get('tracks') if isinstance(telemetry.get('tracks'), list) else []
    by_key = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get('role') or '')
        try:
            index = int(row.get('index', 0))
        except (TypeError, ValueError):
            continue
        by_key[(role, index)] = row
    lanes = []
    for index, identifier in enumerate(vocal_ids):
        row = by_key.get(('vocal', index), {})
        lanes.append({'id': str(identifier), 'rms': _finite_or_none(row.get('rms_dbfs')),
                      'peak': _finite_or_none(row.get('peak_dbfs'))})
    row = by_key.get(('backing', 0), {})
    lanes.append({'id': str(instrumental_id), 'rms': _finite_or_none(row.get('rms_dbfs')),
                  'peak': _finite_or_none(row.get('peak_dbfs'))})
    bus_for_master = telemetry.get('bus') if isinstance(telemetry.get('bus'), Mapping) else {}
    final_output = (bus_for_master.get('final_output')
                    if isinstance(bus_for_master.get('final_output'), Mapping) else {})
    lanes.append({
        'id': 'master',
        'rms': _finite_or_none(final_output.get('rms_dbfs', bus_for_master.get('rms_dbfs'))),
        'peak': _finite_or_none(final_output.get('peak_dbfs', bus_for_master.get('peak_dbfs'))),
    })
    dynamics = telemetry.get('dynamics') if isinstance(telemetry.get('dynamics'), Mapping) else {}
    compressor = dynamics.get('compressor') if isinstance(dynamics.get('compressor'), Mapping) else {}
    bus = telemetry.get('bus') if isinstance(telemetry.get('bus'), Mapping) else {}
    reverb = telemetry.get('reverb') if isinstance(telemetry.get('reverb'), Mapping) else {}
    return {
        'lanes': lanes,
        'gr': _finite_or_none(compressor.get('gr_db')),
        'limiter': _finite_or_none(bus.get('limiter_gr_db')),
        'tail': _finite_or_none(reverb.get('return_rms_dbfs')),
        'source': str(telemetry.get('source') or ''),
    }


def spectrum_from_telemetry(telemetry: Mapping[str, Any]) -> dict[str, Any]:
    source = telemetry.get('spectrum') if isinstance(telemetry.get('spectrum'), Mapping) else {}
    frequencies = source.get('freq_hz')
    if frequencies is None:
        frequencies = source.get('frequency_hz')
    before = source.get('before_dbfs')
    after = source.get('after_dbfs')
    if not all(isinstance(item, list) for item in (frequencies, before, after)):
        return {'before': [], 'after': [], 'freqs': [], 'source': ''}
    count = min(len(frequencies), len(before), len(after))
    pairs = []
    for index in range(count):
        frequency = _finite_or_none(frequencies[index])
        left = _finite_or_none(before[index])
        right = _finite_or_none(after[index])
        if frequency is None or left is None or right is None or frequency <= 0.0:
            continue
        pairs.append((float(frequency), float(left), float(right)))
    if len(pairs) > MAX_SPECTRUM_POINTS:
        step = (len(pairs) - 1) / (MAX_SPECTRUM_POINTS - 1.0)
        indexes = sorted({int(round(index * step)) for index in range(MAX_SPECTRUM_POINTS)})
        pairs = [pairs[index] for index in indexes]
    return {
        'before': [round(item[1], 4) for item in pairs],
        'after': [round(item[2], 4) for item in pairs],
        'freqs': [round(item[0], 4) for item in pairs],
        'source': str(source.get('source') or ''),
    }


def reverb_curve_from_telemetry(telemetry: Mapping[str, Any]) -> list[dict[str, float]]:
    """Return only the measured unit-impulse EDC; never synthesize a room shape."""
    reverb = telemetry.get('reverb') if isinstance(telemetry.get('reverb'), Mapping) else {}
    kernel = reverb.get('kernel_edc') if isinstance(reverb.get('kernel_edc'), Mapping) else {}
    if kernel.get('available') is not True or kernel.get('measured') is not True:
        return []
    times = kernel.get('time_seconds')
    values = kernel.get('edc_db')
    if not isinstance(times, list) or not isinstance(values, list):
        return []
    count = min(len(times), len(values))
    points = []
    for index in range(count):
        stamp = _finite_or_none(times[index])
        level = _finite_or_none(values[index])
        if stamp is not None and level is not None and stamp >= 0.0:
            points.append((float(stamp), float(level)))
    if len(points) < 2:
        return []
    if len(points) > MAX_REVERB_POINTS:
        step = (len(points) - 1) / (MAX_REVERB_POINTS - 1.0)
        indexes = sorted({int(round(index * step)) for index in range(MAX_REVERB_POINTS)})
        points = [points[index] for index in indexes]
    maximum = max(point[0] for point in points) or 1.0
    # The QML canvas uses normalized 1..0 amplitude; the values still come
    # exclusively from the measured EDC in decibels.
    return [{'x': round(point[0] / maximum, 7),
             'y': round(max(0.0, min(1.0, 10.0 ** (point[1] / 20.0))), 7)}
            for point in points]


class MixdownWorkspaceState:
    """Own editable mix state independently of a page, widget or QML object."""

    def __init__(self, output: Any = None):
        self.job = ''
        self.duration = 0
        self.position = 0
        self.playing = False
        self.loading = False
        self.busy = False
        self.state = 'idle'
        self.message = '独立 WAV · 无需先创建任务'
        self.expanded = False
        self.analysis = 'eq'
        self.selection = 'B'
        self.loop = False
        self.output = str(fresh_output_path() if output is None else Path(output).expanduser().resolve())
        self.result = ''
        self.result_stale = False
        self.master_gain = 0.0
        self.sample_rate = float(DEFAULT_SAMPLE_RATE)
        self.eq_enabled = False
        self.compression_enabled = False
        self.compression_strength = 0.25
        self.reverb_enabled = False
        self.reverb_room = 'natural'
        self.reverb_mix = 0.10
        self.reverb_predelay_ms = 0.0
        self.width = 1.0
        self.preset_name = '自定义'
        self._tracks = [
            {'id': 'vocal-1', 'label': '歌声 1', 'path': '', 'name': '', 'gain': 0.0,
             'muted': False, 'solo': False, 'send': 1.0, '_artifact': None},
            {'id': 'instrumental', 'label': '伴奏', 'path': '', 'name': '', 'gain': 0.0,
             'muted': False, 'solo': False, 'send': 0.0, '_artifact': None},
        ]
        self._bands = default_bands()

    def copy(self) -> 'MixdownWorkspaceState':
        return copy.deepcopy(self)

    def _find_track(self, identifier: Any) -> dict[str, Any]:
        wanted = str(identifier or '')
        for row in self._tracks:
            if row['id'] == wanted:
                return row
        raise ValueError('unknown mix track: ' + wanted)

    @property
    def bands(self) -> list[dict[str, Any]]:
        return [project_eq_band(band, sample_rate=self.sample_rate) for band in self._bands]

    @property
    def tracks(self) -> list[dict[str, Any]]:
        return [self._public_track(row) for row in self._tracks]

    @staticmethod
    def _public_track(row: Mapping[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(row.get(key)) for key in
                ('id', 'label', 'path', 'name', 'gain', 'muted', 'solo', 'send')}

    def set_job(self, job: Any) -> None:
        self.job = str(Path(job).resolve()) if job else ''

    def set_position(self, value: Any) -> None:
        self.position = max(0, int(round(finite_number(value, 'position'))))

    def set_duration(self, value: Any) -> None:
        duration = max(0, int(round(finite_number(value, 'duration'))))
        if duration != self.duration:
            self.duration = duration
            self.position = min(self.position, duration)

    def set_transport(self, *, playing: Any = None, loading: Any = None,
                      state: Any = None, message: Any = None) -> None:
        if playing is not None:
            self.playing = bool(playing)
        if loading is not None:
            self.loading = bool(loading)
        if state is not None:
            self.state = str(state)
        if message is not None:
            self.message = str(message)[:1600]

    def add_vocal(self) -> str:
        vocals = [row for row in self._tracks if row['id'] != 'instrumental']
        if len(vocals) >= MAX_VOCALS:
            raise ValueError(f'最多只能混入 {MAX_VOCALS} 路歌声。')
        used = {row['id'] for row in vocals}
        number = next(index for index in range(1, MAX_VOCALS + 1) if f'vocal-{index}' not in used)
        identifier = f'vocal-{number}'
        self._tracks.insert(len(vocals), {
            'id': identifier, 'label': f'歌声 {number}', 'path': '', 'name': '',
            'gain': 0.0, 'muted': False, 'solo': False, 'send': 1.0, '_artifact': None,
        })
        return identifier

    def remove_vocal(self, identifier: Any) -> None:
        row = self._find_track(identifier)
        if row['id'] == 'instrumental':
            raise ValueError('伴奏通道不能被删除。')
        vocals = [item for item in self._tracks if item['id'] != 'instrumental']
        if len(vocals) <= 1:
            raise ValueError('至少保留一路歌声通道。')
        self._tracks.remove(row)

    def set_track(self, identifier: Any, key: Any, value: Any) -> bool:
        row = self._find_track(identifier)
        name = str(key or '')
        if name == 'gain':
            row['gain'] = _clamp(value, -120.0, 24.0, 'gain')
        elif name == 'send':
            if row['id'] == 'instrumental':
                raise ValueError('混响发送仅作用于歌声轨。')
            row['send'] = _clamp(value, 0.0, 1.0, 'send')
        elif name in ('muted', 'solo'):
            row[name] = _boolean(value, name)
        elif name == 'name':
            if not isinstance(value, str):
                raise ValueError('name must be text')
            row['name'] = value[:512]
        elif name in ('id', 'label', 'path'):
            raise ValueError(f'{name} is read-only; use chooseSource or addTrack')
        else:
            raise ValueError('unknown track field: ' + name)
        return True

    def set_source(self, identifier: Any, path: Any, *, artifact: Mapping[str, Any] | None = None) -> bool:
        row = self._find_track(identifier)
        candidate = Path(_path_text(path, 'path')).expanduser().resolve()
        if not candidate.is_file():
            raise ValueError('音频文件不存在或不可读取。')
        if row['id'] != 'instrumental' and candidate.suffix.casefold() != '.wav':
            raise ValueError('歌声分轨必须使用 WAV 文件。')
        if row['id'] == 'instrumental' and candidate.suffix.casefold() not in AUDIO_SUFFIXES:
            raise ValueError('伴奏格式不受支持。')
        wanted = _normal_path(candidate)
        for other in self._tracks:
            if other is not row and other['path'] and _normal_path(other['path']) == wanted:
                raise ValueError('同一音频文件不能重复添加。')
        row['path'] = str(candidate)
        row['name'] = candidate.name
        row['_artifact'] = copy.deepcopy(dict(artifact)) if artifact else None
        return True

    def clear_source(self, identifier: Any) -> None:
        row = self._find_track(identifier)
        row['path'] = ''
        row['name'] = ''
        row['_artifact'] = None

    def load_task_sources(self, job: Any, sources: Sequence[Mapping[str, Any]]) -> None:
        """Replace the mix with verified separation stems from one explicit task."""
        job_path = Path(job).expanduser().resolve()
        rows = []
        for index, source in enumerate(sources):
            path = Path(_path_text(source.get('path'), 'source path')).expanduser().resolve()
            if not path.is_file() or not path.is_relative_to(job_path):
                raise ValueError('任务分轨必须存在且位于该任务目录内。')
            identifier = str(source.get('id') or ('instrumental' if source.get('kind') == 'instrumental'
                                                  else f'vocal-{index + 1}'))
            artifact = source.get('artifact')
            if not isinstance(artifact, Mapping):
                raise ValueError('任务分轨缺少已核验产物身份。')
            rows.append({
                'id': identifier,
                'label': str(source.get('label') or ('伴奏' if identifier == 'instrumental'
                                                     else f'歌声 {index + 1}')),
                'path': str(path), 'name': path.name, 'gain': 0.0,
                'muted': False, 'solo': False,
                'send': 0.0 if identifier == 'instrumental' else 1.0,
                '_artifact': copy.deepcopy(dict(artifact)),
            })
        vocals = [row for row in rows if row['id'] != 'instrumental']
        backing = [row for row in rows if row['id'] == 'instrumental']
        if not 1 <= len(vocals) <= MAX_VOCALS or len(backing) != 1:
            raise ValueError('任务分轨必须包含一路伴奏和一至三路歌声。')
        replacement = type(self)()
        replacement.job = str(job_path)
        replacement._tracks = vocals + backing
        replacement.message = '已载入任务原分轨；它们不是调音后或合成歌声。'
        self.__dict__.clear()
        self.__dict__.update(replacement.__dict__)

    def set_output(self, path: Any) -> None:
        candidate = Path(_path_text(path, 'output')).expanduser().resolve()
        if candidate.suffix.casefold() != '.wav':
            raise ValueError('导出文件必须使用 .wav 扩展名。')
        self.output = str(candidate)

    def set_band(self, index: int, key: str, value: Any) -> bool:
        position = int(index)
        if not 0 <= position < len(self._bands):
            raise ValueError('EQ band index is out of range')
        candidate = copy.deepcopy(self._bands[position])
        field = str(key or '')
        if field == 'type':
            band_type = str(value or '').strip().lower()
            if band_type not in EQ_BAND_TYPES:
                raise ValueError('unknown EQ band type')
            candidate['type'] = band_type
            if band_type == 'highpass':
                candidate['gain_db'] = 0.0
                candidate['q'] = EQ_HIGHPASS_Q
        elif field == 'freq_hz':
            candidate['freq_hz'] = _clamp(value, EQ_MIN_FREQUENCY_HZ, EQ_MAX_FREQUENCY_HZ, field)
        elif field == 'gain_db':
            candidate['gain_db'] = _clamp(value, EQ_MIN_GAIN_DB, EQ_MAX_GAIN_DB, field)
        elif field == 'q':
            candidate['q'] = _clamp(value, EQ_MIN_Q, EQ_MAX_Q, field)
        elif field == 'enabled':
            candidate['enabled'] = _boolean(value, field)
        else:
            raise ValueError('unknown EQ band field: ' + field)
        self._bands[position] = project_eq_band(candidate, sample_rate=self.sample_rate)
        return True

    def move_band(self, index: int, frequency_hz: float, gain_db: float) -> bool:
        position = int(index)
        if not 0 <= position < len(self._bands):
            raise ValueError('EQ band index is out of range')
        candidate = copy.deepcopy(self._bands[position])
        candidate['freq_hz'] = _clamp(frequency_hz, EQ_MIN_FREQUENCY_HZ, EQ_MAX_FREQUENCY_HZ, 'freq_hz')
        candidate['gain_db'] = _clamp(gain_db, EQ_MIN_GAIN_DB, EQ_MAX_GAIN_DB, 'gain_db')
        self._bands[position] = project_eq_band(candidate, sample_rate=self.sample_rate)
        return True

    def set_analysis(self, value: Any) -> None:
        mode = str(value or '').casefold()
        if mode not in ANALYSES:
            raise ValueError('analysis must be eq, gr, or reverb')
        self.analysis = mode

    def set_selection(self, value: Any) -> None:
        mode = str(value or '').strip().upper()
        if mode not in ('A', 'B'):
            raise ValueError('selection must be A or B')
        self.selection = mode

    def set_expanded(self, value: Any) -> None:
        self.expanded = bool(value)

    def set_loop(self, value: Any) -> None:
        self.loop = bool(value)

    def set_sample_rate(self, value: Any) -> bool:
        """Record the real stream rate for display; the renderer follows the backing."""
        rate = valid_sample_rate(value)
        if rate == self.sample_rate:
            return False
        self.sample_rate = rate
        return True

    def set_option(self, name: Any, value: Any) -> tuple[bool, str]:
        key = str(name or '')
        folded = key.replace('_', '').replace('-', '').replace(' ', '').casefold()
        if folded == 'eqenabled':
            enabled = _boolean(value, key)
            changed = enabled != self.eq_enabled
            self.eq_enabled = enabled
            return changed, 'EQ'
        if folded in ('compressionenabled', 'compressorenabled'):
            enabled = _boolean(value, key)
            changed = enabled != self.compression_enabled
            self.compression_enabled = enabled
            return changed, '压缩'
        if folded in ('reverbenabled',):
            enabled = _boolean(value, key)
            changed = enabled != self.reverb_enabled
            self.reverb_enabled = enabled
            return changed, '混响'
        if folded in ('compressionstrength', 'compressstrength'):
            strength = finite_number(value, key)
            if 1.0 < strength <= 100.0:
                strength /= 100.0
            strength = _clamp(strength, 0.0, 1.0, key)
            changed = strength != self.compression_strength
            self.compression_strength = strength
            return changed, '压缩强度'
        if folded in ('reverbroom', 'reverbprofile', 'room'):
            room = _ROOM_MAP.get(str(value or '').strip().casefold())
            if room is None:
                room = str(value or '').strip().casefold()
            if room not in REVERB_PROFILES:
                raise ValueError('未知混响空间。')
            changed = room != self.reverb_room
            self.reverb_room = room
            return changed, '混响空间'
        if folded in ('reverbmix', 'reverbwet'):
            mix_value = finite_number(value, key)
            if 1.0 < mix_value <= 100.0:
                mix_value /= 100.0
            mix_value = _clamp(mix_value, 0.0, 1.0, key)
            changed = mix_value != self.reverb_mix
            self.reverb_mix = mix_value
            return changed, '混响量'
        if folded in ('reverbpredelay', 'reverbpredelayms'):
            delay = _clamp(value, 0.0, 120.0, key)
            changed = delay != self.reverb_predelay_ms
            self.reverb_predelay_ms = delay
            return changed, '混响预延迟'
        if folded in ('width', 'outputwidth'):
            width = _clamp(value, 0.0, 2.0, key)
            changed = width != self.width
            self.width = width
            return changed, '立体声宽度'
        if folded in ('mastergain', 'volume'):
            gain = _clamp(value, -60.0, 0.0, key)
            changed = gain != self.master_gain
            self.master_gain = gain
            return changed, '试听主增益'
        if folded in ('compressionthreshold', 'threshold', 'compressionratio', 'ratio',
                      'compressorattack', 'compressionattack', 'attack', 'attackms',
                      'compressorrelease', 'compressionrelease', 'release', 'releasems'):
            strength = strength_for_compressor(key, value)
            changed = strength != self.compression_strength
            self.compression_strength = strength
            return changed, '压缩参数'
        if folded in ('limiterenabled',):
            if value is not True:
                raise ValueError('固定 -1 dBFS 安全限制器不能关闭。')
            return False, '安全限制器'
        if folded in ('limiterceiling', 'limiterceilingdbfs'):
            ceiling = finite_number(value, key)
            if abs(ceiling - float(PEAK_CEILING_DBFS)) > 1e-9:
                raise ValueError('固定安全限制器上限为 {0:g} dBFS。'.format(float(PEAK_CEILING_DBFS)))
            return False, '安全限制器'
        if folded in ('maxhz',):
            raise ValueError('最大 EQ 频率由实际采样率决定。')
        raise ValueError('unknown mix option: ' + key)

    def write_processing(self, processing: Mapping[str, Any]) -> None:
        payload = build_processing_state(
            sample_rate=DEFAULT_SAMPLE_RATE,
            eq_enabled=processing['eq_enabled'],
            eq_bands=processing['eq_bands'],
            compression_enabled=processing['compression_enabled'],
            compression_strength=processing['compression_strength'],
            reverb_enabled=processing['reverb_enabled'],
            reverb_profile=processing['reverb_profile'],
            reverb_mix=processing['reverb_mix'],
            reverb_predelay_ms=processing['reverb_predelay_ms'],
            width=processing.get('width', self.width),
        )
        self.eq_enabled = payload['eq_enabled']
        self._bands = payload['eq_bands']
        self.compression_enabled = payload['compression_enabled']
        self.compression_strength = payload['compression_strength']
        self.reverb_enabled = payload['reverb_enabled']
        self.reverb_room = payload['reverb_profile']
        self.reverb_mix = payload['reverb_mix']
        self.reverb_predelay_ms = payload['reverb_predelay_ms']
        self.width = payload['width']

    def write_track_template(self, tracks: Sequence[Mapping[str, Any]]) -> None:
        vocals = [row for row in self._tracks if row['id'] != 'instrumental']
        if len(tracks) != len(vocals):
            raise ValueError('预设声部模板与当前歌声轨数量不一致。')
        for row, values in zip(vocals, tracks, strict=True):
            row['send'] = _clamp(values.get('reverb_send', row['send']), 0.0, 1.0, 'reverb_send')

    def processing_state(self) -> dict[str, Any]:
        return build_processing_state(
            sample_rate=DEFAULT_SAMPLE_RATE,
            eq_enabled=self.eq_enabled,
            eq_bands=self._bands,
            compression_enabled=self.compression_enabled,
            compression_strength=self.compression_strength,
            reverb_enabled=self.reverb_enabled,
            reverb_profile=self.reverb_room,
            reverb_mix=self.reverb_mix,
            reverb_predelay_ms=self.reverb_predelay_ms,
            width=self.width,
        )

    def preset_state(self) -> dict[str, Any]:
        vocals = [row for row in self._tracks if row['id'] != 'instrumental' and row['path']]
        return {
            'processing': self.processing_state(),
            'tracks': [{'pan': 0.0, 'reverb_send': float(row['send'])} for row in vocals],
        }

    def write_preset_state(self, value: Mapping[str, Any]) -> None:
        processing = value.get('processing') if isinstance(value.get('processing'), Mapping) else None
        tracks = value.get('tracks')
        if processing is None or not isinstance(tracks, list):
            raise ValueError('混音预设状态无效。')
        self.write_processing(processing)
        self.write_track_template(tracks)

    def active_vocals(self) -> list[dict[str, Any]]:
        return [row for row in self._tracks if row['id'] != 'instrumental' and row['path']]

    def instrumental(self) -> dict[str, Any]:
        return self._find_track('instrumental')

    def has_media(self) -> bool:
        return bool(self.active_vocals() and self.instrumental()['path'])

    def validate_sources(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        vocals = self.active_vocals()
        backing = self.instrumental()
        if not vocals:
            raise ValueError('请至少选择一路歌声干声 WAV。')
        if len(vocals) > MAX_VOCALS:
            raise ValueError(f'最多只能混入 {MAX_VOCALS} 路歌声。')
        if not backing['path']:
            raise ValueError('请选择伴奏文件。')
        for number, row in enumerate(vocals, 1):
            path = Path(row['path'])
            if not path.is_file():
                raise ValueError(f'第 {number} 路歌声文件不存在或不可读取。')
            if path.suffix.casefold() != '.wav':
                raise ValueError(f'第 {number} 路歌声必须选择 WAV 文件。')
        backing_path = Path(backing['path'])
        if not backing_path.is_file():
            raise ValueError('伴奏文件不存在或不可读取。')
        keys = [_normal_path(row['path']) for row in vocals] + [_normal_path(backing_path)]
        if len(keys) != len(set(keys)):
            raise ValueError('同一音频文件不能同时作为歌声或伴奏输入。')
        return vocals, backing

    def validate_output(self) -> Path:
        vocals, backing = self.validate_sources()
        output = Path(self.output).expanduser().resolve()
        if output.suffix.casefold() != '.wav':
            raise ValueError('导出文件必须使用 .wav 扩展名。')
        if output.exists():
            raise FileExistsError('导出目标已存在；请换一个文件名，现有文件不会被覆盖。')
        for row in [*vocals, backing]:
            if _normal_path(output) == _normal_path(row['path']):
                raise ValueError('导出文件不能与任一源文件相同。')
        return output

    def request(self, output_path: Any, *, bypass_processing: bool = False) -> dict[str, Any]:
        vocals, backing = self.validate_sources()
        request = build_mixdown_request(
            self.processing_state(),
            vocals=[{
                'path': row['path'], 'gain_db': float(row['gain']),
                'offset_seconds': 0.0, 'mute': bool(row['muted']), 'solo': bool(row['solo']),
                'pan': 0.0, 'reverb_send': float(row['send']),
            } for row in vocals],
            instrumental_path=backing['path'],
            output_path=Path(output_path).resolve(),
            instrumental_db=float(backing['gain']),
            instrumental_mute=bool(backing['muted']),
            instrumental_solo=bool(backing['solo']),
            instrumental_pan=0.0,
            sample_rate=DEFAULT_SAMPLE_RATE,
            bypass_processing=bool(bypass_processing),
        )
        return request

    def source_rows(self) -> list[dict[str, Any]]:
        rows = []
        for row in self._tracks:
            if not row['path']:
                continue
            artifact = row.get('_artifact') or {}
            rows.append({
                'id': row['id'], 'path': _normal_path(row['path']),
                ** _source_stat(row['path']),
                'artifact_id': artifact.get('id'),
                'artifact_revision': artifact.get('revision'),
                'artifact_sha256': artifact.get('sha256'),
                'provenance': artifact.get('provenance') or 'external_local',
            })
        return rows

    def source_key(self) -> str:
        return _digest(self.source_rows())

    def render_fingerprint(self) -> str:
        try:
            request = self.request(fresh_output_path())
            request.pop('output_path', None)
        except (OSError, ValueError, TypeError):
            request = None
        return _digest({'job': self.job, 'request': request, 'sources': self.source_rows()})

    def task_artifacts(self) -> list[dict[str, Any]]:
        result = []
        for row in self._tracks:
            artifact = row.get('_artifact')
            if artifact:
                result.append(copy.deepcopy(artifact))
        return result

    def view(self, *, can_export: bool, can_register: bool, can_play: bool) -> dict[str, Any]:
        compression = compression_controls(self.compression_strength)
        try:
            max_hz = eq_frequency_limit_hz(self.sample_rate)
        except ValueError:
            max_hz = EQ_MAX_FREQUENCY_HZ
        return {
            'job': self.job,
            'duration': int(self.duration),
            'playing': bool(self.playing),
            'hasMedia': self.has_media(),
            'maxVocalTracks': MAX_VOCALS,
            'loading': bool(self.loading),
            'busy': bool(self.busy),
            'state': str(self.state),
            'message': str(self.message),
            'expanded': bool(self.expanded),
            'analysis': str(self.analysis),
            'selection': str(self.selection),
            'loop': bool(self.loop),
            'output': str(self.output),
            'result': str(self.result),
            'resultStale': bool(self.result_stale),
            'canExport': bool(can_export),
            'canRegister': bool(can_register),
            'canPlay': bool(can_play),
            'masterGain': float(self.master_gain),
            'compressionEnabled': bool(self.compression_enabled),
            'compressionThreshold': round(compression['threshold'], 3),
            'compressionRatio': round(compression['ratio'], 3),
            'compressorAttack': round(compression['attack'], 3),
            'compressorRelease': round(compression['release'], 3),
            'reverbEnabled': bool(self.reverb_enabled),
            'reverbRoom': str(self.reverb_room),
            'reverbMix': float(self.reverb_mix),
            'limiterEnabled': True,
            'limiterCeiling': float(PEAK_CEILING_DBFS),
            'maxHz': round(float(max_hz), 5),
            'sampleRate': float(self.sample_rate),
            'presetName': str(self.preset_name),
        }


__all__ = [
    'AUDIO_SUFFIXES', 'DEFAULT_BANDS', 'EQ_CURVE_POINTS', 'MAX_REVERB_POINTS',
    'MAX_SPECTRUM_POINTS', 'MixdownWorkspaceState', 'TELEMETRY_SCHEMA',
    'TELEMETRY_SOURCE', 'compression_controls', 'default_bands', 'eq_curve',
    'fresh_output_path', 'meters_from_telemetry', 'normalize_bands',
    'reverb_curve_from_telemetry', 'spectrum_from_telemetry',
    'strength_for_compressor', 'telemetry_identity',
]
