"""Bounded min/max waveforms in source time, independent of the audio callback."""
from __future__ import annotations

from array import array
import base64
from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any

import audio_mixdown as audio


@dataclass(frozen=True)
class Waveform:
    duration_ms: float
    step_ms: float
    levels: tuple[array, ...]  # Interleaved signed 16-bit min/max pairs.

    @classmethod
    def unpack(cls, payload):
        levels = []
        for encoded in payload['levels']:
            peaks = array('h')
            peaks.frombytes(base64.b64decode(encoded))
            if sys.byteorder != 'little':
                peaks.byteswap()
            levels.append(peaks)
        return cls(payload['duration_ms'], payload['step_ms'], tuple(levels))

    def window(self, start_ms: float, end_ms: float, offset_ms: float,
               *, buckets: int = 768, guard_ms: float = 0.0) -> dict[str, Any]:
        step = self.step_ms
        level = self.levels[0]
        for coarser in self.levels[1:]:
            if step * buckets >= end_ms - start_ms:
                break
            step *= 4
            level = coarser
        first = max(0, math.floor((start_ms - offset_ms - guard_ms) / step))
        last = min(len(level) // 2, math.ceil((end_ms - offset_ms + guard_ms) / step))
        if first >= last:
            return {}
        return {'start': first * step + offset_ms, 'step': step,
                'peaks': level[2 * first:2 * last].tolist()}


def _peaks(path: Path, cancel):
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(str(path)) as handle:
        rate, channels, frames = handle.samplerate, handle.channels, handle.frames
        audio._valid_audio_info(audio._AudioInfo(path, rate, channels, frames))
        step = max(round(rate * .003), (frames + 262143) // 262144)
        count = (frames + step - 1) // step
        batch = max(1, 131072 // step)
        base = np.empty((count, 2), dtype='<i2')
        for bucket in range(0, count, batch):
            audio._check_cancel(cancel)
            first = bucket * step
            take = min(frames - first, batch * step)
            block = audio._read_frames(handle, first, take, channels, np)
            full, tail = divmod(take, step)
            if full:
                # Both channels must contribute; an average would erase
                # transients on stereo sources with opposing phase.
                groups = block[:full * step].reshape(full, step * channels)
                pairs = np.stack((groups.min(axis=1), groups.max(axis=1)), axis=1)
                base[bucket:bucket + full] = np.rint(np.clip(pairs, -1, 1) * 32767)
            if tail:
                rest = block[full * step:]
                base[bucket + full] = np.rint(np.clip([rest.min(), rest.max()], -1, 1) * 32767)

    levels = []
    while True:
        levels.append(base64.b64encode(base.tobytes()).decode('ascii'))
        if len(base) <= 320:
            break
        audio._check_cancel(cancel)
        starts = np.arange(0, len(base), 4)
        base = np.column_stack((np.minimum.reduceat(base[:, 0], starts),
                                np.maximum.reduceat(base[:, 1], starts))).astype('<i2', copy=False)
    return {'duration_ms': frames * 1000 / rate, 'step_ms': step * 1000 / rate, 'levels': levels}


def measure_tracks(rows, *, work_dir: Path, cancel):
    waves, errors = {}, {}
    for index, row in enumerate(rows):
        audio._check_cancel(cancel)
        identifier, path = row['id'], Path(row['path'])
        decoded = work_dir / f'{index}.wav'
        log = work_dir / f'{index}.log'
        try:
            identity = audio._source_fingerprint(path)
            if identity[-2:] != (row['bytes'], row['mtime_ns']):
                raise ValueError('读取期间源音频已变化，请重读波形。')
            result = None
            if path.suffix.lower() in ('.wav', '.flac'):
                try:
                    result = _peaks(path, cancel)
                except audio.MixdownCancelled:
                    raise
                except (RuntimeError, OSError):
                    # Some WAV encodings also need the FFmpeg decoder.
                    pass
            if result is None:
                # Use the same decoder as audition/export for compressed media,
                # including encoder delay and gapless trimming.
                ffmpeg = audio._ffmpeg_executable()
                info = audio._probe_audio(path, ffmpeg, log, cancel)
                audio._decode_to_float_wav(path, decoded, info.sample_rate,
                                           info.channels, ffmpeg, log, cancel)
                result = _peaks(decoded, cancel)
            if audio._source_fingerprint(path) != identity:
                raise ValueError('读取期间源音频已变化，请重读波形。')
            waves[identifier] = result
        except audio.MixdownCancelled:
            raise
        except (ValueError, RuntimeError, OSError) as error:
            errors[identifier] = str(error)
        finally:
            decoded.unlink(missing_ok=True)
    return {'waves': waves, 'errors': errors}
