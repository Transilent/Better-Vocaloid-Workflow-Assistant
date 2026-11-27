"""Content-addressed cross-session cache for Chorus RMVPE F0 and pitch spans."""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import os
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile
import threading
import zlib

import numpy as np


_MAX_MEMORY_SPANS = 64
_MAX_DISK_SPAN_BYTES = 128 * 1024 * 1024
_MAX_DISK_RMVPE_BYTES = 256 * 1024 * 1024
_PRUNE_EVERY_WRITES = 32
_ZSPAN_MAGIC = b'VWAC'
_ZSPAN_HEADER = struct.Struct('<4sII')
_LOCK = threading.Lock()
_MEMORY_SPANS: OrderedDict[str, np.ndarray] = OrderedDict()
_DISK_WRITES = 0


def _cache_root() -> Path | None:
    explicit = os.environ.get('VWA_CAS_CACHE_DIR', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get('BVWA_TESTING') == '1' or os.environ.get('VWA_TESTING') == '1':
        return None
    from common import ROOT

    # Guard against leaking cache files into src/ when imported directly from the source tree.
    base = ROOT.parent if ROOT.name == 'src' else ROOT
    return (base / 'cache/chorus-cas').resolve()


def _file_stamp_hash(path: Path) -> str:
    st = path.stat()
    return f'{st.st_size}:{st.st_mtime_ns}'


def clear_memory_cache():
    with _LOCK:
        _MEMORY_SPANS.clear()


def load_rmvpe_curve(track_sha: str, model_path: Path):
    root = _cache_root()
    if root is None or not track_sha or not model_path.is_file():
        return None
    key = sha256(f'rmvpe:v1:{track_sha}:{_file_stamp_hash(model_path)}'.encode()).hexdigest()
    target = root / 'rmvpe' / f'{key}.npz'
    if not target.is_file():
        return None
    try:
        with np.load(target, allow_pickle=False) as data:
            midi = data['midi'].copy()
            step = float(data['step'].item())
        return midi, step
    except (OSError, ValueError, KeyError):
        return None


def save_rmvpe_curve(track_sha: str, model_path: Path, midi: np.ndarray, step: float):
    root = _cache_root()
    if root is None or not track_sha or not model_path.is_file():
        return
    folder = root / 'rmvpe'
    folder.mkdir(parents=True, exist_ok=True)
    key = sha256(f'rmvpe:v1:{track_sha}:{_file_stamp_hash(model_path)}'.encode()).hexdigest()
    target = folder / f'{key}.npz'
    if target.is_file():
        return
    tmp = None
    try:
        with NamedTemporaryFile(dir=folder, prefix='.rmvpe-', suffix='.npz', delete=False) as handle:
            tmp = Path(handle.name)
        np.savez(tmp, midi=np.asarray(midi), step=np.float64(step))
        os.replace(tmp, target)
        _prune_disk_rmvpe(folder)
    except OSError:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _prune_disk_rmvpe(folder: Path):
    try:
        files = [(f.stat().st_mtime_ns, f.stat().st_size, f) for f in folder.glob('*.npz')]
    except OSError:
        return
    total = sum(sz for _, sz, _ in files)
    if total <= _MAX_DISK_RMVPE_BYTES:
        return
    files.sort(key=lambda item: item[0])
    for _, sz, path in files:
        if total <= _MAX_DISK_RMVPE_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total -= sz
        except OSError:
            pass


def span_cache_key(dry: np.ndarray, rate: int, shift: np.ndarray, engine: str) -> str:
    h = sha256()
    h.update(f'span:v1:{rate}:{dry.shape[0]}:{dry.shape[1]}:{engine}:'.encode())
    h.update(np.ascontiguousarray(dry, dtype=np.float32).tobytes())
    h.update(np.ascontiguousarray(shift, dtype=np.float64).tobytes())
    return h.hexdigest()


def load_pitch_span(key: str, expected_shape: tuple[int, int]):
    root = _cache_root()
    if root is None:
        return None
    with _LOCK:
        cached = _MEMORY_SPANS.get(key)
        if cached is not None and cached.shape == expected_shape:
            _MEMORY_SPANS.move_to_end(key)
            return cached.copy()
    ztarget = root / 'spans' / f'{key}.zspan'
    legacy = root / 'spans' / f'{key}.npy'
    arr = None
    if ztarget.is_file():
        try:
            raw = ztarget.read_bytes()
            if len(raw) >= _ZSPAN_HEADER.size:
                magic, rows, cols = _ZSPAN_HEADER.unpack_from(raw, 0)
                if magic == _ZSPAN_MAGIC and (rows, cols) == expected_shape:
                    payload = zlib.decompress(raw[_ZSPAN_HEADER.size:])
                    expected_bytes = rows * cols * 4
                    if len(payload) == expected_bytes:
                        arr = np.frombuffer(payload, dtype='<f4').reshape(expected_shape).copy()
        except (OSError, ValueError, zlib.error):
            arr = None
    elif legacy.is_file():
        try:
            loaded = np.load(legacy, allow_pickle=False)
            if loaded.dtype == np.float32 and loaded.shape == expected_shape:
                arr = loaded
        except (OSError, ValueError):
            arr = None
    if arr is None or not np.isfinite(arr).all():
        return None
    with _LOCK:
        _MEMORY_SPANS[key] = arr
        _MEMORY_SPANS.move_to_end(key)
        while len(_MEMORY_SPANS) > _MAX_MEMORY_SPANS:
            _MEMORY_SPANS.popitem(last=False)
    return arr.copy()


def save_pitch_span(key: str, audio: np.ndarray):
    root = _cache_root()
    if root is None:
        return
    arr = np.ascontiguousarray(audio, dtype='<f4')
    with _LOCK:
        _MEMORY_SPANS[key] = arr.copy()
        _MEMORY_SPANS.move_to_end(key)
        while len(_MEMORY_SPANS) > _MAX_MEMORY_SPANS:
            _MEMORY_SPANS.popitem(last=False)
    folder = root / 'spans'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{key}.zspan'
    if target.is_file():
        return
    tmp = None
    try:
        header = _ZSPAN_HEADER.pack(_ZSPAN_MAGIC, int(arr.shape[0]), int(arr.shape[1]))
        compressed = zlib.compress(arr.tobytes(), level=1)
        with NamedTemporaryFile(dir=folder, prefix='.span-', suffix='.zspan', delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(header)
            handle.write(compressed)
        os.replace(tmp, target)
        global _DISK_WRITES
        with _LOCK:
            _DISK_WRITES += 1
            should_prune = (_DISK_WRITES % _PRUNE_EVERY_WRITES == 0)
        if should_prune:
            _prune_disk_spans(folder)
    except OSError:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _prune_disk_spans(folder: Path):
    try:
        files = [
            (f.stat().st_mtime_ns, f.stat().st_size, f)
            for pattern in ('*.zspan', '*.npy')
            for f in folder.glob(pattern)
        ]
    except OSError:
        return
    total = sum(sz for _, sz, _ in files)
    if total <= _MAX_DISK_SPAN_BYTES:
        return
    files.sort()
    for _, sz, f in files:
        if total <= int(_MAX_DISK_SPAN_BYTES * .75):
            break
        try:
            f.unlink(missing_ok=True)
            total -= sz
        except OSError:
            pass
