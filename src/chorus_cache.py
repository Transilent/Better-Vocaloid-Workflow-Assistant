"""Content-addressed cross-session cache for Chorus RMVPE F0 and pitch spans."""
from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading

import numpy as np


_MAX_MEMORY_SPANS = 64
_MAX_DISK_SPAN_BYTES = 128 * 1024 * 1024
_LOCK = threading.Lock()
_MEMORY_SPANS: OrderedDict[str, np.ndarray] = OrderedDict()


def _cache_root() -> Path | None:
    explicit = os.environ.get('VWA_CAS_CACHE_DIR', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get('BVWA_TESTING') == '1' or os.environ.get('VWA_TESTING') == '1':
        return None
    from common import ROOT

    return (ROOT / 'cache/chorus-cas').resolve()


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
    except OSError:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


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
    target = root / 'spans' / f'{key}.npy'
    if not target.is_file():
        return None
    try:
        arr = np.load(target, allow_pickle=False)
        if arr.dtype != np.float32 or arr.shape != expected_shape or not np.isfinite(arr).all():
            return None
        with _LOCK:
            _MEMORY_SPANS[key] = arr
            _MEMORY_SPANS.move_to_end(key)
            while len(_MEMORY_SPANS) > _MAX_MEMORY_SPANS:
                _MEMORY_SPANS.popitem(last=False)
        return arr.copy()
    except (OSError, ValueError):
        return None


def save_pitch_span(key: str, audio: np.ndarray):
    root = _cache_root()
    if root is None:
        return
    arr = np.ascontiguousarray(audio, dtype=np.float32)
    with _LOCK:
        _MEMORY_SPANS[key] = arr.copy()
        _MEMORY_SPANS.move_to_end(key)
        while len(_MEMORY_SPANS) > _MAX_MEMORY_SPANS:
            _MEMORY_SPANS.popitem(last=False)
    folder = root / 'spans'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{key}.npy'
    if target.is_file():
        return
    tmp = None
    try:
        with NamedTemporaryFile(dir=folder, prefix='.span-', suffix='.npy', delete=False) as handle:
            tmp = Path(handle.name)
        np.save(tmp, arr, allow_pickle=False)
        os.replace(tmp, target)
        _prune_disk_spans(folder)
    except OSError:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _prune_disk_spans(folder: Path):
    try:
        files = [(f.stat().st_mtime_ns, f.stat().st_size, f) for f in folder.glob('*.npy')]
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
