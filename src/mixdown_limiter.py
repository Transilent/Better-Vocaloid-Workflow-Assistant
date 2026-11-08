"""Shared final-export limiter constants and a persistent streaming wrapper.

The runtime implementation is a small adaptation of FFmpeg 7.1's
``libavfilter/af_alimiter.c``.  It is kept in a separate native component so
the application's MIT-licensed Python code can call the LGPL-2.1-or-later
limiter through ctypes.  See ``components/ffmpeg-alimiter-NOTICE.txt``.

Export parity target:

``alimiter=limit=0.891250938:attack=5:release=100:level=false:latency=true:asc=false``
"""

from __future__ import annotations

import ctypes
from pathlib import Path
import operator
from typing import Any

import numpy as np


from mixdown_limiter_spec import (
    PEAK_CEILING_DBFS, PEAK_CEILING, ATTACK_MS, RELEASE_MS, ffmpeg_filter,
)
_BOUND_TOLERANCE = 1.0e-6
_UINT32_MAX = 2**32 - 1
_ERROR_TEXT_CAPACITY = 512


class LimiterError(RuntimeError):
    """A native limiter load, configuration, or processing failure."""


class LimiterClosedError(LimiterError):
    """Raised when a closed streaming limiter is used."""


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = operator.index(value)
    except TypeError as error:
        raise ValueError(f"{name} must be an integer") from error
    if result < 0 or result > _UINT32_MAX:
        raise ValueError(f"{name} must be between 0 and {_UINT32_MAX}")
    return int(result)


def _load_library(path: Path) -> ctypes.CDLL:
    try:
        library = ctypes.CDLL(str(path))
    except OSError as error:
        raise LimiterError(f"cannot load streaming limiter DLL: {error}") from error

    try:
        library.vwa_limiter_abi_string.argtypes = []
        library.vwa_limiter_abi_string.restype = ctypes.c_char_p
        library.vwa_limiter_attack_frames.argtypes = [ctypes.c_uint32]
        library.vwa_limiter_attack_frames.restype = ctypes.c_uint32
        library.vwa_limiter_create.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        library.vwa_limiter_create.restype = ctypes.c_int
        library.vwa_limiter_destroy.argtypes = [ctypes.c_void_p]
        library.vwa_limiter_destroy.restype = None
        library.vwa_limiter_process.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        library.vwa_limiter_process.restype = ctypes.c_int
        library.vwa_limiter_drain.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        library.vwa_limiter_drain.restype = ctypes.c_int
        library.vwa_limiter_reset.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        library.vwa_limiter_reset.restype = ctypes.c_int
    except AttributeError as error:
        raise LimiterError(
            "limiter DLL does not expose the VWA limiter ABI"
        ) from error

    abi = library.vwa_limiter_abi_string()
    if not abi or not abi.decode("ascii", errors="replace").startswith("vwa-limiter/1"):
        shown = "" if not abi else abi.decode("ascii", errors="replace")
        raise LimiterError(f"unsupported streaming limiter ABI: {shown or 'unknown'}")
    return library


def _float_pointer(samples: np.ndarray) -> ctypes.POINTER(ctypes.c_float):
    return ctypes.cast(samples.ctypes.data, ctypes.POINTER(ctypes.c_float))


class StreamingLimiter:
    """Persistent FFmpeg-alimiter-compatible block processor.

    ``process`` accepts time-major float data. Mono uses ``(frames,)`` or
    ``(frames, 1)``; stereo uses ``(frames, 2)``. The first
    ``latency_frames`` frames are withheld. ``finish`` (or ``drain``) emits
    exactly the frames still owed by the consumed stream.
    """

    def __init__(self, dll_path: str | Path, sample_rate: int, channels: int):
        rate = _integer(sample_rate, "sample_rate")
        channel_count = _integer(channels, "channels")
        if rate == 0:
            raise ValueError("sample_rate must be positive")
        if channel_count not in (1, 2):
            raise ValueError("channels must be 1 (mono) or 2 (stereo)")
        path = Path(dll_path).expanduser().resolve()
        if not path.is_file():
            raise LimiterError(f"streaming limiter DLL does not exist: {path}")

        self._library = _load_library(path)
        self._dll_path = path
        self._sample_rate = rate
        self._channels = channel_count
        self._attack_frames = int(self._library.vwa_limiter_attack_frames(rate))
        if self._attack_frames < 1:
            raise ValueError("sample_rate is too low for the 5 ms limiter attack")
        self._handle: ctypes.c_void_p | None = None
        self._closed = False
        self._drained = False
        self._input_frames = 0
        self._emitted_frames = 0

        handle = ctypes.c_void_p()
        error = ctypes.create_string_buffer(_ERROR_TEXT_CAPACITY)
        result = self._library.vwa_limiter_create(
            rate, channel_count, ctypes.byref(handle), error, len(error)
        )
        if result != 0 or not handle.value:
            self._closed = True
            raise LimiterError(
                error.value.decode("utf-8", errors="replace")
                or f"native limiter creation failed ({result})"
            )
        self._handle = handle

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def attack_frames(self) -> int:
        return self._attack_frames

    @property
    def latency_frames(self) -> int:
        return self._attack_frames - 1

    @property
    def frames_received(self) -> int:
        return self._input_frames

    @property
    def frames_emitted(self) -> int:
        return self._emitted_frames

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> ctypes.c_void_p:
        if self._closed or self._handle is None:
            raise LimiterClosedError("streaming limiter is closed")
        return self._handle

    def _native_error(self, result: int, error: ctypes.Array[Any]) -> None:
        message = error.value.decode("utf-8", errors="replace")
        raise LimiterError(message or f"native limiter call failed ({result})")

    def _checked_output(self, samples: np.ndarray) -> None:
        if samples.size and not np.isfinite(samples).all():
            raise LimiterError("native limiter returned a non-finite sample")
        peak = float(np.max(np.abs(samples), initial=0.0))
        if peak > PEAK_CEILING + _BOUND_TOLERANCE:
            raise LimiterError(
                f"native limiter exceeded the {PEAK_CEILING_DBFS:g} dBFS peak ceiling"
            )

    def process(self, data: Any) -> np.ndarray:
        """Consume a block and return all newly committed float32 frames."""

        handle = self._require_open()
        if self._drained:
            raise LimiterError("streaming limiter is finished; call reset()")

        try:
            samples = np.asarray(data, dtype=np.float32, order="C")
        except (TypeError, ValueError) as error:
            raise ValueError("audio data cannot be converted to float32") from error

        vector_input = samples.ndim == 1
        if vector_input:
            if self._channels != 1:
                raise ValueError("stereo streaming limiter requires shape (frames, 2)")
            samples = samples.reshape(-1, 1)
        elif samples.ndim != 2 or samples.shape[1] != self._channels:
            raise ValueError(
                f"audio data must have shape (frames, {self._channels})"
            )
        if not samples.flags.c_contiguous:
            samples = np.ascontiguousarray(samples)
        if not np.isfinite(samples).all():
            raise ValueError("audio data must contain only finite samples")

        frames = int(samples.shape[0])
        output = np.empty((frames, self._channels), dtype=np.float32)
        out_frames = ctypes.c_uint64(0)
        error = ctypes.create_string_buffer(_ERROR_TEXT_CAPACITY)
        result = self._library.vwa_limiter_process(
            handle,
            _float_pointer(samples),
            ctypes.c_uint64(frames),
            _float_pointer(output),
            ctypes.c_uint64(frames),
            ctypes.byref(out_frames),
            error,
            len(error),
        )
        if result != 0:
            self._native_error(result, error)
        emitted = int(out_frames.value)
        if emitted > frames:
            raise LimiterError("native limiter reported an invalid output frame count")
        self._input_frames += frames
        self._emitted_frames += emitted
        committed = output[:emitted]
        self._checked_output(committed)
        return committed.reshape(-1) if vector_input else committed

    def drain(self) -> np.ndarray:
        """Flush exactly the frames still owed by the consumed input."""

        handle = self._require_open()
        if self._drained:
            shape = (0,) if self._channels == 1 else (0, self._channels)
            return np.empty(shape, dtype=np.float32)
        if self._emitted_frames > self._input_frames:
            raise LimiterError("limiter frame accounting is invalid")

        remaining = self._input_frames - self._emitted_frames
        output = np.empty((remaining, self._channels), dtype=np.float32)
        out_frames = ctypes.c_uint64(0)
        error = ctypes.create_string_buffer(_ERROR_TEXT_CAPACITY)
        result = self._library.vwa_limiter_drain(
            handle,
            _float_pointer(output),
            ctypes.c_uint64(remaining),
            ctypes.byref(out_frames),
            error,
            len(error),
        )
        if result != 0:
            self._native_error(result, error)
        emitted = int(out_frames.value)
        if emitted != remaining:
            raise LimiterError(
                f"native drain emitted {emitted} frames; expected {remaining}"
            )
        self._emitted_frames += emitted
        self._drained = True
        committed = output[:emitted]
        self._checked_output(committed)
        return committed.reshape(-1) if self._channels == 1 else committed

    def finish(self) -> np.ndarray:
        """Alias for :meth:`drain` used by renderer wrappers."""

        return self.drain()

    def reset(self) -> None:
        """Discard delay, gain and finished state while keeping the handle."""

        handle = self._require_open()
        error = ctypes.create_string_buffer(_ERROR_TEXT_CAPACITY)
        result = self._library.vwa_limiter_reset(handle, error, len(error))
        if result != 0:
            self._native_error(result, error)
        self._drained = False
        self._input_frames = 0
        self._emitted_frames = 0

    def close(self) -> None:
        """Release native state. Repeated and subsequent calls stay closed."""

        if self._closed:
            return
        self._closed = True
        handle = self._handle
        self._handle = None
        if handle is not None:
            self._library.vwa_limiter_destroy(handle)

    def __enter__(self) -> "StreamingLimiter":
        self._require_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
