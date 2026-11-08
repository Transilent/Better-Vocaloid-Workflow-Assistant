"""ctypes control surface for the VWA native continuous output worker.

The native data callback never enters Python. This wrapper serializes write,
seek, play state, snapshot and null pull calls from the control side. The null
pull helper is a deterministic consumer for tests only; actual WASAPI output is
consumed solely by miniaudio's audio thread.

Device failure is reported by native snapshots and remains visible until this
output is closed. This wrapper never opens a replacement device automatically;
the controller must close this instance and create a new one on its owner thread.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
import threading
from typing import Any, Mapping

import numpy as np


_ERROR_CAPACITY = 1024


class NativeOutputError(RuntimeError):
    """A native output operation returned an error or failed to initialize."""


class NativeDeviceError(NativeOutputError):
    """A terminal native device failure reported by a non-throwing snapshot."""

    def __init__(
        self,
        code: int,
        message: str,
        snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = int(code)
        self.device_error = str(message).strip()
        self.snapshot = dict(snapshot or {})
        detail = self.device_error or f"native device failed with code {self.code}"
        super().__init__(detail)


_DEVICE_ERROR_KINDS = {
    -100: "stopped",
    -101: "interrupted",
}


class _NativeSnapshot(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint64),
        ("backend", ctypes.c_uint64),
        ("share_mode", ctypes.c_uint64),
        ("epoch", ctypes.c_uint64),
        ("capacity_frames", ctypes.c_uint64),
        ("queued_frames", ctypes.c_uint64),
        ("media_frame", ctypes.c_uint64),
        ("accepted_end_frame", ctypes.c_uint64),
        ("submitted_frames", ctypes.c_uint64),
        ("sample_rate", ctypes.c_uint64),
        ("channels", ctypes.c_uint64),
        ("period_frames", ctypes.c_uint64),
        ("requested_period_frames", ctypes.c_uint64),
        ("actual_device_sample_rate", ctypes.c_uint64),
        ("actual_device_period_frames", ctypes.c_uint64),
        ("device_buffer_frames", ctypes.c_uint64),
        ("callback_frames_last", ctypes.c_uint64),
        ("callback_frames_min", ctypes.c_uint64),
        ("callback_frames_max", ctypes.c_uint64),
        ("callback_count", ctypes.c_uint64),
        ("underrun_frames", ctypes.c_uint64),
        ("xrun_count", ctypes.c_uint64),
        ("pause_frames", ctypes.c_uint64),
        ("stale_epoch_writes", ctypes.c_uint64),
        ("continuity_rejections", ctypes.c_uint64),
        ("partial_writes", ctypes.c_uint64),
        ("seek_count", ctypes.c_uint64),
        ("seek_padding_before", ctypes.c_uint64),
        ("seek_padding_after_stop", ctypes.c_uint64),
        ("seek_padding_after_restart", ctypes.c_uint64),
        ("seek_padding_evidence", ctypes.c_uint64),
        ("seek_clock_position_before", ctypes.c_uint64),
        ("seek_clock_frequency_before", ctypes.c_uint64),
        ("seek_clock_timestamp_before", ctypes.c_uint64),
        ("seek_clock_position_after_stop", ctypes.c_uint64),
        ("seek_clock_frequency_after_stop", ctypes.c_uint64),
        ("seek_clock_timestamp_after_stop", ctypes.c_uint64),
        ("seek_clock_evidence", ctypes.c_uint64),
        ("seek_clock_reset_zero_ok", ctypes.c_uint64),
        ("wasapi_padding_frames", ctypes.c_uint64),
        ("wasapi_padding_query_ok", ctypes.c_uint64),
        ("wasapi_clock_position", ctypes.c_uint64),
        ("wasapi_clock_frequency", ctypes.c_uint64),
        ("wasapi_clock_timestamp", ctypes.c_uint64),
        ("reserved_clock_characteristics", ctypes.c_uint64),
        ("wasapi_clock_query_ok", ctypes.c_uint64),
        ("reserved_clock_characteristics_query_ok", ctypes.c_uint64),
        ("wasapi_default_device_period_100ns", ctypes.c_uint64),
        ("wasapi_minimum_device_period_100ns", ctypes.c_uint64),
        ("wasapi_stream_latency_100ns", ctypes.c_uint64),
        ("playing", ctypes.c_uint64),
        ("closing", ctypes.c_uint64),
        ("ring_owner_violations", ctypes.c_uint64),
        ("device_error_code", ctypes.c_int64),
        ("error_code", ctypes.c_int64),
        ("device_error", ctypes.c_char * 256),
        ("clock_error", ctypes.c_char * 256),
        ("last_error", ctypes.c_char * 256),
    ]


def _decode_c_string(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def _raw_clock_seconds(position: int, frequency: int) -> float | None:
    if position < 0 or frequency <= 0:
        return None
    # IAudioClock units are opaque; the documented ratio is position/frequency.
    return float(position) / float(frequency)


def _bind_library(dll_path: Path) -> ctypes.CDLL:
    library = ctypes.CDLL(str(dll_path))
    library.vwa_output_abi_string.argtypes = []
    library.vwa_output_abi_string.restype = ctypes.c_char_p
    library.vwa_output_snapshot_size.argtypes = []
    library.vwa_output_snapshot_size.restype = ctypes.c_uint64
    native_snapshot_size = int(library.vwa_output_snapshot_size())
    if native_snapshot_size != ctypes.sizeof(_NativeSnapshot):
        raise NativeOutputError(
            f"native snapshot ABI size mismatch: C={native_snapshot_size}, Python={ctypes.sizeof(_NativeSnapshot)}"
        )
    library.vwa_output_create.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    library.vwa_output_create.restype = ctypes.c_int
    library.vwa_output_destroy.argtypes = [ctypes.c_void_p]
    library.vwa_output_destroy.restype = None
    library.vwa_output_write.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
    ]
    library.vwa_output_write.restype = ctypes.c_uint64
    library.vwa_output_seek.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    library.vwa_output_seek.restype = ctypes.c_int
    library.vwa_output_set_playing.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    library.vwa_output_set_playing.restype = ctypes.c_int
    library.vwa_output_snapshot.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_NativeSnapshot),
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    library.vwa_output_snapshot.restype = ctypes.c_int
    library.vwa_output_pull.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint64,
    ]
    library.vwa_output_pull.restype = ctypes.c_uint64
    return library


class NativeOutput:
    """Small control object around the native SPSC output worker."""

    def __init__(
        self,
        dll_path: str | Path,
        sample_rate: int = 48000,
        channels: int = 2,
        capacity_frames: int = 4096,
        period_frames: int = 256,
        mode: str = "null",
    ) -> None:
        mode_id = {"null": 0, "wasapi": 1}.get(str(mode).lower())
        if mode_id is None:
            raise ValueError("mode must be 'null' or 'wasapi'")
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("capacity_frames", capacity_frames),
            ("period_frames", period_frames),
        ):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        library_path = Path(dll_path).expanduser().resolve()
        if not library_path.is_file():
            raise FileNotFoundError(library_path)
        self._dll = _bind_library(library_path)
        self._lock = threading.RLock()
        self._handle: int | None = None
        self._closed = False
        self.dll_path = library_path
        self.sample_rate = sample_rate
        self.channels = channels
        self.capacity_frames = capacity_frames
        self.period_frames = period_frames
        self.mode = "null" if mode_id == 0 else "wasapi"
        self._owner_ident = threading.get_ident()

        create_error = ctypes.create_string_buffer(_ERROR_CAPACITY)
        handle = ctypes.c_void_p()
        result = self._dll.vwa_output_create(
            mode_id,
            sample_rate,
            channels,
            capacity_frames,
            period_frames,
            ctypes.byref(handle),
            create_error,
            len(create_error),
        )
        if result != 0 or not handle.value:
            message = _decode_c_string(create_error.value)
            raise NativeOutputError(message or f"native output create failed: {result}")
        self._handle = int(handle.value)

    def _require_open(self) -> int:
        self._require_owner()
        if self._closed or self._handle is None:
            raise NativeOutputError("native output is closed")
        return self._handle

    def _require_owner(self) -> None:
        if self.mode == 'wasapi' and threading.get_ident() != self._owner_ident:
            raise NativeOutputError('WASAPI control must use the thread that created the output')

    def _operation_error(self, operation: str, result: int, error_buffer: ctypes.Array[Any]) -> None:
        message = _decode_c_string(error_buffer.value)
        raise NativeOutputError(message or f"{operation} failed: {result}")

    def write(self, samples: np.ndarray, epoch: int = 1, start_frame: int = 0) -> int:
        """Nonblocking ring write; returns accepted frame count, possibly zero."""
        if not isinstance(samples, np.ndarray):
            raise TypeError("samples must be a numpy.ndarray")
        if samples.dtype != np.float32:
            raise TypeError("samples must have dtype float32")
        if samples.ndim != 2 or samples.shape[1] != self.channels:
            raise ValueError(f"samples must have shape (frames, {self.channels})")
        if not samples.flags.c_contiguous:
            raise ValueError("samples must be C-contiguous")
        if not isinstance(epoch, int) or epoch < 0 or not isinstance(start_frame, int) or start_frame < 0:
            raise ValueError("epoch and start_frame must be non-negative integers")
        frames = int(samples.shape[0])
        if frames == 0:
            return 0
        with self._lock:
            handle = self._require_open()
            return int(
                self._dll.vwa_output_write(
                    handle,
                    int(samples.ctypes.data),
                    frames,
                    epoch,
                    start_frame,
                )
            )

    def seek(self, frame: int = 48000, epoch: int = 2) -> None:
        """Quiesce/reset the native consumer, then restore its previous play intent."""
        if not isinstance(frame, int) or frame < 0:
            raise ValueError("frame must be a non-negative integer")
        if not isinstance(epoch, int) or epoch < 0:
            raise ValueError("epoch must be a non-negative integer")
        error = ctypes.create_string_buffer(_ERROR_CAPACITY)
        with self._lock:
            handle = self._require_open()
            result = int(self._dll.vwa_output_seek(handle, frame, epoch, error, len(error)))
            if result != 0:
                self._operation_error("seek", result, error)

    def set_playing(self, playing: bool = True) -> None:
        error = ctypes.create_string_buffer(_ERROR_CAPACITY)
        with self._lock:
            handle = self._require_open()
            result = int(self._dll.vwa_output_set_playing(handle, 1 if playing else 0, error, len(error)))
            if result != 0:
                self._operation_error("set_playing", result, error)

    def snapshot(self) -> dict[str, Any]:
        native = _NativeSnapshot()
        error = ctypes.create_string_buffer(_ERROR_CAPACITY)
        with self._lock:
            handle = self._require_open()
            result = int(self._dll.vwa_output_snapshot(handle, ctypes.byref(native), error, len(error)))
            if result != 0:
                self._operation_error("snapshot", result, error)

        backend = "null" if native.backend == 0 else "wasapi-shared"
        if native.backend == 0:
            clock_quality = "deterministic-null"
        elif native.wasapi_clock_query_ok:
            clock_quality = "raw-wasapi-IAudioClock"
        elif native.wasapi_padding_query_ok:
            clock_quality = "wasapi-padding-only"
        else:
            clock_quality = "unavailable"

        clock_seconds = _raw_clock_seconds(native.wasapi_clock_position, native.wasapi_clock_frequency)
        seek_clock_seconds_before = _raw_clock_seconds(
            native.seek_clock_position_before,
            native.seek_clock_frequency_before,
        )
        seek_clock_seconds_after_stop = _raw_clock_seconds(
            native.seek_clock_position_after_stop,
            native.seek_clock_frequency_after_stop,
        )
        padding_evidence = native.seek_padding_evidence
        clock_evidence = native.seek_clock_evidence
        device_error_code = int(native.device_error_code)
        device_error = _decode_c_string(native.device_error)
        device_error_kind = (
            _DEVICE_ERROR_KINDS.get(device_error_code, "native")
            if device_error_code else None
        )
        return {
            "abi_version": int(native.abi_version),
            "backend": backend,
            "share_mode": "shared" if native.share_mode == 1 else "none",
            "epoch": int(native.epoch),
            "capacity_frames": int(native.capacity_frames),
            "queued_frames": int(native.queued_frames),
            "media_frame": int(native.media_frame),
            "media_frame_semantics": "callback-submitted source frames; not confirmed audible",
            "accepted_end_frame": int(native.accepted_end_frame),
            "submitted_frames": int(native.submitted_frames),
            "submitted_frames_semantics": "all callback/pull frames including paused and underrun zero fill",
            "sample_rate": int(native.sample_rate),
            "channels": int(native.channels),
            "period_frames": int(native.period_frames),
            "requested_period_frames": int(native.requested_period_frames),
            "actual_device_sample_rate": int(native.actual_device_sample_rate),
            "actual_device_period_frames": int(native.actual_device_period_frames),
            "device_buffer_frames": int(native.device_buffer_frames),
            "callback_period_frames_last": int(native.callback_frames_last),
            "callback_period_frames_min": int(native.callback_frames_min),
            "callback_period_frames_max": int(native.callback_frames_max),
            "callback_count": int(native.callback_count),
            "underrun_frames": int(native.underrun_frames),
            "xrun": bool(native.xrun_count),
            "xrun_count": int(native.xrun_count),
            "pause_frames": int(native.pause_frames),
            "stale_epoch_writes": int(native.stale_epoch_writes),
            "continuity_rejections": int(native.continuity_rejections),
            "partial_writes": int(native.partial_writes),
            "seek_count": int(native.seek_count),
            "seek_padding_evidence": {
                "before": bool(padding_evidence & 1),
                "after_stop": bool(padding_evidence & 2),
                "after_restart": bool(padding_evidence & 4),
                "before_frames": int(native.seek_padding_before),
                "after_stop_frames": int(native.seek_padding_after_stop),
                "after_restart_frames": int(native.seek_padding_after_restart),
            },
            "seek_clock_evidence": {
                "before": bool(clock_evidence & 1),
                "after_stop": bool(clock_evidence & 2),
                "position_before": int(native.seek_clock_position_before),
                "frequency_before": int(native.seek_clock_frequency_before),
                "qpc_before": int(native.seek_clock_timestamp_before),
                "position_after_stop": int(native.seek_clock_position_after_stop),
                "frequency_after_stop": int(native.seek_clock_frequency_after_stop),
                "qpc_after_stop": int(native.seek_clock_timestamp_after_stop),
                "seconds_before": seek_clock_seconds_before,
                "seconds_after_stop": seek_clock_seconds_after_stop,
                "reset_zero_ok": bool(native.seek_clock_reset_zero_ok) if clock_evidence & 2 else None,
            },
            "clock_quality": clock_quality,
            "clock_mapping": (
                "Raw same-stream WASAPI clock fields only; seconds = position/frequency. "
                "They do not map one-to-one to media_frame or acoustic output."
            ),
            "wasapi_padding_frames": (
                int(native.wasapi_padding_frames) if native.wasapi_padding_query_ok else None
            ),
            "wasapi_clock": {
                "position": int(native.wasapi_clock_position),
                "frequency": int(native.wasapi_clock_frequency),
                "qpc_timestamp": int(native.wasapi_clock_timestamp),
                "characteristics": None,  # SDK reserves GetCharacteristics for future use.
                "seconds": clock_seconds,
                "query_ok": bool(native.wasapi_clock_query_ok),
            },
            "wasapi_periods": {
                "default_100ns": int(native.wasapi_default_device_period_100ns),
                "minimum_100ns": int(native.wasapi_minimum_device_period_100ns),
                "stream_latency_100ns": int(native.wasapi_stream_latency_100ns),
            },
            "playing": bool(native.playing),
            "closing": bool(native.closing),
            "ring_owner_violations": int(native.ring_owner_violations),
            "device_failed": device_error_code != 0,
            "device_error_code": device_error_code,
            "device_error_kind": device_error_kind,
            "device_error": device_error,
            "clock_error": _decode_c_string(native.clock_error),
            "last_error_code": int(native.error_code),
            "last_error": _decode_c_string(native.last_error),
        }

    def raise_if_device_failed(self, snapshot: Mapping[str, Any] | None = None) -> None:
        """Raise a typed error for a terminal native device failure.

        snapshot() remains non-throwing for diagnostics. Callers that already
        have a recent snapshot can pass it here to avoid another native status,
        clock and padding query. This method does not close or recreate a device.
        """
        self._require_owner()
        current = dict(self.snapshot() if snapshot is None else snapshot)
        code = int(current.get("device_error_code", 0) or 0)
        message = str(current.get("device_error", "") or "").strip()
        if code or message:
            raise NativeDeviceError(code, message, current)

    def pull(self, frames: int = 256) -> np.ndarray:
        """Deterministic null consumer. This method is unavailable for WASAPI."""
        if self.mode != "null":
            raise NativeOutputError("pull() is only valid for the null backend")
        if not isinstance(frames, int) or frames < 0:
            raise ValueError("frames must be a non-negative integer")
        if frames == 0:
            return np.empty((0, self.channels), dtype=np.float32)
        output = np.empty((frames, self.channels), dtype=np.float32)
        with self._lock:
            handle = self._require_open()
            pulled = int(self._dll.vwa_output_pull(handle, int(output.ctypes.data), frames))
        if pulled != frames:
            raise NativeOutputError(f"native null pull returned {pulled}, expected {frames}")
        return output

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._require_owner()
            self._closed = True
            handle = self._handle
            self._handle = None
            if handle:
                self._dll.vwa_output_destroy(handle)

    def __enter__(self) -> "NativeOutput":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


__all__ = ["NativeDeviceError", "NativeOutput", "NativeOutputError"]
