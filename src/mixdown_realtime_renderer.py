"""Persistent PRE-LIMITER block renderer for the interactive mix console.

This module owns the DSP session between the native audio output and the
existing mixdown implementation.  Source audio is decoded once through the
audition cache; each later ``render`` call consumes a small block while the EQ,
shared-bus compressor, reverb, and pre-delay retain their processor state.

The emitted bus is explicitly PRE-LIMITER.  It never invokes the FFmpeg
``alimiter`` stage and it does not hard-clip samples.  Consequently it is not a
substitute for, or a parity claim about, the existing final export path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
import threading
from typing import Any, Mapping

import audio_mixdown as backend
import audio_mixdown_audition as audition


OUTPUT_STAGE = "pre_limiter"
OUTPUT_SCHEMA = "vwa.audio_mixdown.pre-limiter-session/v1"


@dataclass(frozen=True)
class _PreparedParameters:
    owner: object
    settings: Any
    options: Any

MIN_STATEFUL_BLOCK_FRAMES = 4
MONO_UPNMIX_DBFS = -3.010299956639812
MONO_UPNMIX_GAIN = 10.0 ** (MONO_UPNMIX_DBFS / 20.0)
MONO_ROUTING_NOTE = (
    "Mono sources are decoded once into equal stereo channels. FFmpeg's "
    "energy-preserving mono-to-stereo upmix applies -3.0103 dB per channel; "
    "the stereo playback bus therefore preserves the mono source's total "
    "energy before pan, width, and effects."
)


def _process_exact(plugin: Any, block: Any, sample_rate: int, first: bool,
                   numpy: Any, *, allow_terminal_padding: bool = False) -> Any:
    """Process a continuous block without advancing hidden zero padding.

    Pedalboard 0.9.26 uses its smaller array dimension for channel inference.
    Shapes ``(2, 1)`` and ``(2, 2)`` are therefore ambiguous.  Continuous
    stateful blocks use an explicit minimum of four frames, where the channel
    dimension is unambiguously smaller.  A final 1–3 frame block that reaches
    ``duration_frames`` may use the shared padding helper because no later
    processor state exists.  Any hidden padding in a continuous block is
    rejected instead of silently advancing FX history.
    """
    frames = len(block)
    if frames < MIN_STATEFUL_BLOCK_FRAMES:
        if not allow_terminal_padding:
            raise ValueError(
                "连续 PRE-LIMITER 块必须至少 "
                + str(MIN_STATEFUL_BLOCK_FRAMES) + " 帧。"
            )
        return backend._pedalboard_process(
            plugin, block, sample_rate, first, numpy,
        )
    if frames >= 32:
        return backend._pedalboard_process(
            plugin, block, sample_rate, first, numpy,
        )
    channels = block.shape[1]
    source = numpy.ascontiguousarray(block.T, dtype=numpy.float32)
    output = plugin.process(
        source,
        sample_rate,
        buffer_size=backend.PEDALBOARD_BUFFER_SIZE,
        reset=first,
    )
    output = numpy.asarray(output, dtype=numpy.float32)
    if output.ndim != 2 or output.shape[0] != channels or output.shape[1] < frames:
        raise backend.MixdownError("Pedalboard 返回的音频形状异常，已停止输出。")
    output = output[:, :frames].T
    if not numpy.isfinite(output).all():
        raise ValueError("人声处理产生了 NaN 或无穷大采样，已停止输出。")
    return numpy.ascontiguousarray(output, dtype=numpy.float32)


class _ExactDelayedProcessor(audition._DelayedProcessor):
    """Reuse audition delay state while avoiding sub-32-frame padding."""

    def process(self, block: Any, sample_rate: int, first: bool,
                numpy: Any, *, allow_terminal_padding: bool = False) -> Any:
        if self.delay_frames <= 0:
            return _process_exact(
                self.plugin, block, sample_rate, first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
        combined = numpy.concatenate((self.carry, block), axis=0)
        current = combined[:len(block)]
        self.carry = numpy.ascontiguousarray(
            combined[len(block):], dtype=numpy.float32,
        )
        return _process_exact(
            self.plugin, current, sample_rate, first, numpy,
            allow_terminal_padding=allow_terminal_padding,
        )


class PreLimiterMixSession:
    """A stateful stereo vocal/instrumental bus rendered in caller-sized blocks.

    The public block contract is intentionally small:

    ``render(frames)`` returns exactly ``(frames, 2)`` float32 samples and
    advances ``position_frame`` by the same count.  Samples after the complete
    source/reverb budget are exact zeros.  ``seek`` performs source seeking and
    optional graph warm-up synchronously; callers must invoke it on a control
    path rather than from a device callback.  ``set_parameters`` accepts the
    existing mixdown request shape while keeping the source set and rate fixed.

    ``channels`` is always two because it is the stereo device bus.  Mono
    sources are decoded once to equal channels by the FFmpeg conversion inside
    :func:`audio_mixdown_audition._prepare_sources`; its energy-preserving
    mono-to-stereo upmix is -3.0103 dB per channel.  Pan and width therefore
    operate on an already stable stereo layout and never trigger re-decoding.
    """

    OUTPUT_STAGE = OUTPUT_STAGE

    def __init__(self, request: Mapping[str, Any], cache_dir: str | Path,
                 numpy: Any = None, soundfile: Any = None,
                 pedalboard: Any = None, ffmpeg: str | Path | None = None):
        self._lock = threading.RLock()
        self._parameter_owner = object()
        if numpy is None:
            try:
                import numpy as numpy_module
            except ImportError as error:
                raise backend.MixdownError(
                    "实时 PRE-LIMITER 渲染缺少 NumPy。"
                ) from error
            numpy = numpy_module
        if soundfile is None:
            try:
                import soundfile as soundfile_module
            except ImportError as error:
                raise backend.MixdownError(
                    "实时 PRE-LIMITER 渲染缺少 SoundFile。"
                ) from error
            soundfile = soundfile_module
        if pedalboard is None:
            pedalboard = backend._load_pedalboard()
        required = ("HighpassFilter", "PeakFilter", "Compressor", "Reverb")
        missing = [name for name in required if not hasattr(pedalboard, name)]
        if missing:
            raise backend.MixdownError(
                "Pedalboard 缺少 PRE-LIMITER 所需组件：" + "、".join(missing) + "。"
            )
        version = str(getattr(pedalboard, "__version__", ""))
        if version != backend.PEDALBOARD_VERSION:
            raise backend.MixdownError(
                "PRE-LIMITER 实时渲染固定使用 Pedalboard "
                + backend.PEDALBOARD_VERSION + "，当前版本为 "
                + (version or "未知") + "。"
            )

        self._numpy = numpy
        self._soundfile = soundfile
        self._pedalboard = pedalboard
        settings = backend._settings(request)
        options = audition._v3_options(request, settings)
        if ffmpeg is None:
            ffmpeg = backend._ffmpeg_executable()
        else:
            ffmpeg = Path(ffmpeg).expanduser().resolve()
            if not ffmpeg.is_file():
                raise backend.MixdownError(
                    "指定的 FFmpeg 不存在或不是文件：" + str(ffmpeg)
                )
        cache_root = Path(cache_dir).expanduser().resolve()
        # Device playback always receives a stereo bus.  Forcing this only in
        # the cache preparation settings makes mono sources explicit,
        # energy-preserving stereo without changing caller routing parameters.
        preparation_settings = replace(
            settings, width=0.0, uses_v3=True,
        )
        prepared = audition._prepare_sources(
            preparation_settings, cache_root, ffmpeg, soundfile, None,
        )

        decoder_identity = audition._decoder_identity(ffmpeg)
        source_rows = audition._source_rows(settings)
        post_token = audition._cache_token(
            source_rows, 2, decoder_identity,
        )
        if post_token != prepared.token:
            raise backend.MixdownError(
                "源文件在解码准备期间发生变化；未建立实时会话。"
            )
        self._request_settings = settings
        self._options = options
        self._prepared = prepared
        self._ffmpeg = ffmpeg
        self.source_token = str(prepared.token)
        self.sample_rate = int(prepared.sample_rate)
        self.channels = 2
        self.source_end_frame = self._source_end(settings)
        self.duration_frames = self._duration(settings)
        self.position_frame = 0
        self._source_paths = self._source_identity(settings)
        self._source_fingerprints = tuple(
            (
                int(row["device"]), int(row["inode"]),
                int(row["bytes"]), int(row["mtime_ns"]),
            )
            for row in source_rows
        )
        self._handles: list[Any] = []
        self._closed = False
        self._first = True
        self._effects_signature = self._effect_structure(options, settings)
        self._eq_chain = None
        self._compressor = None
        self._reverb = None
        self._reverb_stage = None
        self._warnings: list[str] = []
        self.effect_rebuild_count = 0
        self.last_warmup: dict[str, Any] = {
            "reason": "initial",
            "requested_frame": 0,
            "warmup_start_frame": 0,
            "warmup_frames": 0,
            "bounded": False,
            "strategy": "fresh_graph_at_source_start",
            "skipped_below_safe_quantum": False,
        }
        try:
            self._handles = [
                soundfile.SoundFile(str(path), "r")
                for path in (*prepared.vocal_paths, prepared.backing_path)
            ]
            self._rebuild_effects()
        except BaseException:
            self._close_handles()
            raise

    @property
    def output_stage(self) -> str:
        return OUTPUT_STAGE

    def _source_end(self, settings: backend._Settings) -> int:
        offsets = [
            int(round(track.offset_seconds * self.sample_rate))
            for track in settings.vocals
        ]
        ends = [
            max(0, frames + offset)
            for frames, offset in zip(
                self._prepared.vocal_frames, offsets,
            )
        ]
        return max([int(self._prepared.backing_frames), *ends])

    def _reverb_enabled(self, settings: backend._Settings) -> bool:
        return float(settings.reverb_mix) > 0.0

    def _duration(self, settings: backend._Settings) -> int:
        end = self._source_end(settings)
        if self._reverb_enabled(settings):
            end += backend._reverb_tail_budget_frames(
                self._options.reverb_predelay_ms, self.sample_rate,
            )
        return int(end)

    @staticmethod
    def _source_identity(settings: backend._Settings) -> tuple[str, ...]:
        return tuple(
            backend._normal_path(path)
            for path in (
                *(track.path for track in settings.vocals),
                settings.instrumental_path,
            )
        )

    def _effect_structure(self, options: Any,
                          settings: backend._Settings) -> tuple[Any, ...]:
        eq_identity = (
            tuple(options.eq_bands)
            if options.eq_bands is not None else None
        )
        return (
            eq_identity,
            float(settings.eq_low_cut_hz),
            float(settings.eq_presence_db),
            float(settings.compression_strength),
            bool(float(settings.reverb_mix) > 0.0),
            str(settings.reverb_profile),
            int(backend._predelay_frames(
                options.reverb_predelay_ms, self.sample_rate)),
        )

    def _rebuild_effects(self) -> None:
        warnings: list[str] = []
        try:
            eq_chain = audition._make_eq_chain(
                self._pedalboard, self._request_settings, self._options,
                self.sample_rate, warnings,
            )
            compressor = audition._make_compressor(
                self._pedalboard, self._request_settings, warnings,
            )
            reverb = audition._make_reverb(
                self._pedalboard, self._request_settings,
            )
        except Exception as error:
            raise backend.MixdownError(
                "无法创建 PRE-LIMITER 人声处理链。"
            ) from error
        reverb_stage = None
        if reverb is not None:
            delay_frames = backend._predelay_frames(
                self._options.reverb_predelay_ms, self.sample_rate,
            )
            reverb_stage = _ExactDelayedProcessor(
                reverb, delay_frames, self.channels, self._numpy,
            )
        self._eq_chain = eq_chain
        self._compressor = compressor
        self._reverb = reverb
        self._reverb_stage = reverb_stage
        self._warnings = warnings
        self._first = True
        self.effect_rebuild_count += 1

    def _process_block(self, start: int, frames: int, *,
                       allow_terminal_padding: bool) -> Any:
        numpy = self._numpy
        settings = self._request_settings
        channels = self.channels
        vocal_audible, backing_audible = backend._audibility(settings)
        offsets = [
            int(round(track.offset_seconds * self.sample_rate))
            for track in settings.vocals
        ]
        vocal_gains = [
            10.0 ** (track.gain_db / 20.0) for track in settings.vocals
        ]
        vocal_pans = [audition._pan_gains(track.pan) for track in settings.vocals]
        backing_gain = 10.0 ** (settings.instrumental_db / 20.0)
        backing_pans = audition._pan_gains(settings.instrumental_pan)

        vocal_block = numpy.zeros((frames, channels), dtype=numpy.float32)
        send_block = numpy.zeros((frames, channels), dtype=numpy.float32)
        backing_block = numpy.zeros((frames, channels), dtype=numpy.float32)

        for handle, source_frames, offset, gain, pan, audible, track in zip(
                self._handles[:-1], self._prepared.vocal_frames, offsets,
                vocal_gains, vocal_pans, vocal_audible, settings.vocals,
        ):
            if not audible:
                continue
            source_start = start - offset
            destination_start = max(0, -source_start)
            source_index = max(0, source_start)
            count = min(frames - destination_start, source_frames - source_index)
            if count <= 0:
                continue
            track_block = numpy.zeros((frames, channels), dtype=numpy.float32)
            track_block[destination_start:destination_start + count] = backend._read_frames(
                handle, source_index, count, channels, numpy,
            )
            track_block *= numpy.float32(gain)
            track_block[:, 0] *= numpy.float32(pan[0])
            track_block[:, 1] *= numpy.float32(pan[1])
            vocal_block = numpy.add(vocal_block, track_block, dtype=numpy.float32)
            send = float(track.reverb_send)
            if send > 0.0:
                send_block = numpy.add(
                    send_block, track_block * numpy.float32(send),
                    dtype=numpy.float32,
                )

        if backing_audible:
            count = min(frames, self._prepared.backing_frames - start)
            if count > 0:
                backing_block[:count] = backend._read_frames(
                    self._handles[-1], start, count, channels, numpy,
                )
                backing_block *= numpy.float32(backing_gain)
                backing_block[:, 0] *= numpy.float32(backing_pans[0])
                backing_block[:, 1] *= numpy.float32(backing_pans[1])

        vocal = vocal_block
        if self._eq_chain is not None:
            vocal = _process_exact(
                self._eq_chain, vocal, self.sample_rate, self._first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
        if self._compressor is not None:
            vocal = _process_exact(
                self._compressor, vocal, self.sample_rate, self._first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
        if self._reverb_stage is not None:
            wet = self._reverb_stage.process(
                send_block, self.sample_rate, self._first, numpy,
                allow_terminal_padding=allow_terminal_padding,
            )
            mix_value = float(settings.reverb_mix)
            vocal = (
                vocal * (1.0 - mix_value) + wet * mix_value
            ).astype(numpy.float32, copy=False)
        self._first = False

        mixed = numpy.add(vocal, backing_block, dtype=numpy.float32)
        mixed = backend._apply_width(mixed, float(settings.width), numpy)
        if not numpy.isfinite(mixed).all():
            raise ValueError("PRE-LIMITER 混音产生了 NaN 或无穷大采样。")
        return numpy.ascontiguousarray(mixed, dtype=numpy.float32)

    def _render_locked(self, frames: int) -> Any:
        output = self._numpy.zeros(
            (frames, self.channels), dtype=self._numpy.float32,
        )
        start = int(self.position_frame)
        process_frames = min(frames, max(0, self.duration_frames - start))
        if process_frames > 0:
            terminal = start + process_frames >= self.duration_frames
            output[:process_frames] = self._process_block(
                start, process_frames,
                allow_terminal_padding=terminal,
            )
        self.position_frame = start + frames
        return self._numpy.ascontiguousarray(output, dtype=self._numpy.float32)

    def render(self, frames: int) -> Any:
        """Render exactly ``frames`` contiguous stereo float32 samples.

        Continuous stateful blocks require at least four frames so Pedalboard
        cannot misinfer ``(2, 1)`` or ``(2, 2)`` as mono.  A 1–3 frame final
        remainder is accepted only when the block reaches ``duration_frames``;
        later calls return zeros and advance no processor state.  The samples
        remain pre-limiter and can exceed the final export ceiling; the pump
        owns safe-ceiling rejection.
        """
        if isinstance(frames, bool) or not isinstance(frames, int):
            raise TypeError("frames 必须是整数。")
        if frames < 1:
            raise ValueError("frames 必须至少为 1。")
        with self._lock:
            self._ensure_open()
            start = int(self.position_frame)
            remaining = max(0, int(self.duration_frames) - start)
            if (0 < frames < MIN_STATEFUL_BLOCK_FRAMES
                    and frames < remaining):
                raise ValueError(
                    "连续状态渲染块必须至少 "
                    + str(MIN_STATEFUL_BLOCK_FRAMES)
                    + " 帧；1–3 帧只允许用于触及 duration_frames 的终块。"
                )
            return self._render_locked(frames)

    def _default_preroll_frames(self, target: int) -> int:
        has_reverb_send = any(
            audible and track.reverb_send > 0.0
            for track, audible in zip(
                self._request_settings.vocals,
                backend._audibility(self._request_settings)[0],
            )
        )
        seconds = audition._audition_preroll_seconds(
            self._request_settings, self.sample_rate, has_reverb_send,
        )
        return min(target, max(0, int(math.ceil(seconds * self.sample_rate))))

    def _warm_to(self, target: int, requested_preroll: int | None,
                 reason: str) -> None:
        if requested_preroll is None:
            warmup_frames = self._default_preroll_frames(target)
            bounded = warmup_frames < target
            strategy = "bounded_eq_compressor_predelay_reverb_history"
        else:
            if isinstance(requested_preroll, bool) or not isinstance(requested_preroll, int):
                raise TypeError("preroll_frames 必须是整数或 None。")
            if requested_preroll < 0:
                raise ValueError("preroll_frames 不能为负数。")
            warmup_frames = min(target, requested_preroll)
            bounded = warmup_frames < target
            strategy = "explicit_preroll"
        skipped_short = False
        if (0 < warmup_frames < MIN_STATEFUL_BLOCK_FRAMES
                and target < self.duration_frames):
            # A non-terminal history shorter than four frames cannot identify
            # two channels safely.  Start with a fresh graph instead of feeding
            # hidden padding into FX state.
            warmup_frames = 0
            bounded = True
            skipped_short = True
            strategy = "skipped_history_below_safe_stateful_quantum"
        warmup_start = max(0, target - warmup_frames)
        self.position_frame = warmup_start
        remaining = target - warmup_start
        while remaining > 0:
            count = min(backend.CHUNK_FRAMES, remaining)
            tail = remaining - count
            if 0 < tail < MIN_STATEFUL_BLOCK_FRAMES:
                count -= MIN_STATEFUL_BLOCK_FRAMES - tail
            self._render_locked(count)
            remaining -= count
        self.position_frame = target
        self.last_warmup = {
            "reason": reason,
            "requested_frame": int(target),
            "warmup_start_frame": int(warmup_start),
            "warmup_frames": int(warmup_frames),
            "bounded": bool(bounded),
            "strategy": strategy,
            "skipped_below_safe_quantum": bool(skipped_short),
        }

    def seek(self, frame: int, preroll_frames: int | None = None) -> None:
        """Reset effects, seek sources, and synchronously warm bounded history."""
        if isinstance(frame, bool) or not isinstance(frame, int):
            raise TypeError("frame 必须是整数。")
        if frame < 0:
            raise ValueError("frame 不能为负数。")
        with self._lock:
            self._ensure_open()
            self._rebuild_effects()
            self._warm_to(frame, preroll_frames, "seek")

    def set_parameters(self, request: Mapping[str, Any]) -> None:
        """Apply validated parameters without rendering an intermediate file.

        Gain, pan, reverb send, mute/solo and width change only future block
        scalars.  A structural EQ/compressor/reverb/pre-delay change rebuilds
        that graph and warms bounded source history at the current position.
        The source paths, order, content fingerprint, sample rate, and stereo
        channel layout are immutable for the lifetime of the session.
        """
        self.apply_parameters(self.prepare_parameters(request))

    def prepare_parameters(self, request: Mapping[str, Any]) -> _PreparedParameters:
        """Validate on a control thread while the DSP worker keeps supplying audio.

        Source cache identity is immutable. The same canonical request parser
        and source/decoder fingerprint checks are used by the synchronous API;
        no path normalization, stat or decoder query is repeated at apply time.
        """
        with self._lock:
            self._ensure_open()
            source_paths, token = self._source_paths, self.source_token
            channels, ffmpeg = self.channels, self._ffmpeg
            owner = self._parameter_owner
        settings = backend._settings(request)
        options = audition._v3_options(request, settings)
        identity = self._source_identity(settings)
        if identity != source_paths:
            raise ValueError(
                "PreLimiterMixSession 的源路径或轨道顺序不可在会话中改变。"
            )
        rows = audition._source_rows(settings)
        source_token = audition._cache_token(
            rows, channels, audition._decoder_identity(ffmpeg))
        if source_token != token:
            raise ValueError(
                "PreLimiterMixSession 只接受同一源文件状态、顺序、"
                "解码器、采样率与双声道布局。"
            )
        return _PreparedParameters(owner, settings, options)

    def apply_parameters(self, prepared: _PreparedParameters) -> None:
        """Apply an already validated, session-owned plan at a block boundary."""
        with self._lock:
            self._ensure_open()
            if not isinstance(prepared, _PreparedParameters) or prepared.owner is not self._parameter_owner:
                raise ValueError('参数计划不属于当前 PRE-LIMITER 会话。')
            settings, options = prepared.settings, prepared.options
            previous_position = int(self.position_frame)
            self._request_settings = settings
            self._options = options
            self.source_end_frame = self._source_end(settings)
            self.duration_frames = self._duration(settings)
            structure = self._effect_structure(options, settings)
            if structure != self._effects_signature:
                self._effects_signature = structure
                self._rebuild_effects()
                self._warm_to(
                    previous_position, None,
                    "structural_parameter_change",
                )

    def snapshot(self) -> dict[str, Any]:
        """Return control-path state for orchestration and diagnostics."""
        with self._lock:
            self._ensure_open()
            return {
                "schema": OUTPUT_SCHEMA,
                "stage": OUTPUT_STAGE,
                "sample_rate": int(self.sample_rate),
                "channels": int(self.channels),
                "mono_upmix_dbfs": MONO_UPNMIX_DBFS,
                "mono_upmix_gain": MONO_UPNMIX_GAIN,
                "mono_routing": MONO_ROUTING_NOTE,
                "source_duration_frames": int(self.source_end_frame),
                "duration_frames": int(self.duration_frames),
                "tail_frames": int(
                    self.duration_frames - self.source_end_frame
                ),
                "tail_policy": (
                    "source_end_plus_quantized_predelay_and_reverb_budget"
                    if self.duration_frames > self.source_end_frame
                    else "source_end_without_reverb_tail"
                ),
                "position_frame": int(self.position_frame),
                "min_continuous_block_frames": MIN_STATEFUL_BLOCK_FRAMES,
                "terminal_short_policy": (
                    "accept_1_to_3_frames_only_when_reaching_duration"
                ),
                "effect_rebuild_count": int(self.effect_rebuild_count),
                "last_warmup": dict(self.last_warmup),
                "warnings": list(self._warnings),
                "cache_hit": bool(self._prepared.cache_hit),
                "cache_source": str(self._prepared.cache_source),
                "source_token": self.source_token,
                "ffmpeg_path": backend._normal_path(self._ffmpeg),
                "source_fingerprints": [
                    {
                        "device": int(value[0]),
                        "inode": int(value[1]),
                        "bytes": int(value[2]),
                        "mtime_ns": int(value[3]),
                    }
                    for value in self._source_fingerprints
                ],
            }

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("PreLimiterMixSession 已关闭。")

    def _close_handles(self) -> None:
        for handle in self._handles:
            try:
                handle.close()
            except Exception:
                pass
        self._handles = []

    def close(self) -> None:
        """Release decoded handles; safe to call repeatedly."""
        with self._lock:
            if self._closed:
                return
            self._close_handles()
            self._eq_chain = None
            self._compressor = None
            self._reverb = None
            self._reverb_stage = None
            self._closed = True


def pre_limiter_renderer(request: Mapping[str, Any], cache_dir: str | Path,
                         numpy: Any = None, soundfile: Any = None,
                         pedalboard: Any = None,
                         ffmpeg: str | Path | None = None
                         ) -> PreLimiterMixSession:
    """Convenience factory with the same explicit injection contract."""
    return PreLimiterMixSession(
        request, cache_dir, numpy=numpy, soundfile=soundfile,
        pedalboard=pedalboard, ffmpeg=ffmpeg,
    )
