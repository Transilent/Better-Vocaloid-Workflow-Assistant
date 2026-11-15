"""Stateful, stereo-linked de-essing for decoded vocal blocks."""
from __future__ import annotations

import math

import numpy as np


MAX_REDUCTION_DB = 6.0


class _Envelope:
    """Apply a causal one-pole envelope with explicit state across audio blocks."""
    BLOCK_FRAMES = 1024

    def __init__(self, sample_rate, seconds, channels=1):
        time_constant = seconds * sample_rate
        self._decay = np.exp(-np.arange(1, self.BLOCK_FRAMES + 1) / time_constant)[:, None]
        self._weights = -math.expm1(-1.0 / time_constant) / self._decay
        self._previous = np.zeros(channels)

    def follow(self, signal):
        source = signal.reshape(len(signal), -1)
        output = np.empty(source.shape, dtype=np.float64)
        for start in range(0, len(signal), self.BLOCK_FRAMES):
            result = output[start:start + self.BLOCK_FRAMES]
            size = len(result)
            # The weighted prefix sum solves y[n] = a*y[n-1] + (1-a)*x[n].
            np.cumsum(source[start:start + size] * self._weights[:size], axis=0, out=result)
            result += self._previous
            result *= self._decay[:size]
            self._previous = result[-1].copy()
        return output.reshape(signal.shape)


class VocalDeesser:
    def __init__(self, sample_rate: int, channels: int, pedalboard):
        self._sample_rate, self._channels = sample_rate, channels
        low_hz = min(4800.0, sample_rate * .30)
        high_hz = min(10500.0, sample_rate * .45)
        self._detector = pedalboard.Pedalboard([
            pedalboard.HighpassFilter(cutoff_frequency_hz=low_hz),
            pedalboard.HighpassFilter(cutoff_frequency_hz=low_hz),
            pedalboard.LowpassFilter(cutoff_frequency_hz=high_hz),
        ])
        # The low/high outputs sum to the original with zero added latency.
        self._split = pedalboard.LowpassFilter(cutoff_frequency_hz=low_hz)
        self._power = _Envelope(sample_rate, .003, channels=2)
        self._baseline = _Envelope(sample_rate, .16)
        self._attack = _Envelope(sample_rate, .003)
        self._release = _Envelope(sample_rate, .028)

    def process(self, block: np.ndarray, strength: float) -> tuple[np.ndarray, float]:
        """Return the processed block and peak attenuation of the split high band."""
        if strength == 0.0 or not len(block):
            return block, 0.0

        # Reuse the native filters that are already loaded by the mix worker.
        # Pad a short terminal block to avoid Pedalboard's channel-axis guess.
        frames = len(block)
        source = np.ascontiguousarray(block.T)
        if frames <= self._channels:
            source = np.pad(source, ((0, 0), (0, self._channels + 1 - frames)))
        band = self._detector.process(source, self._sample_rate, reset=False)[:, :frames].T
        # Sum channel energy so the existing -3 dB mono-to-stereo upmix
        # hits the same detection gate as a mono export.
        # The detector filters have overlapping slopes. Restore sensitivity
        # in the middle of the passband before comparing with total energy.
        power = self._power.follow(np.column_stack((2.5 * np.sum(band * band, axis=1),
                                                     np.sum(block * block, axis=1))))
        high_fraction = np.minimum(1.0, power[:, 0] / np.maximum(power[:, 1], 1e-12))
        baseline = self._baseline.follow(high_fraction)
        # A relative threshold follows bright sustained sounds. The absolute
        # gate keeps near-silence from triggering on a high spectral ratio.
        target = np.clip((high_fraction - np.maximum(.18, 1.25 * baseline)) / .38, 0.0, 1.0)
        target *= np.clip((power[:, 0] - 2e-6) / 6e-5, 0.0, 1.0)
        fast = self._attack.follow(target)
        slow = self._release.follow(target)
        reduction_db = np.minimum(1.0, np.maximum(fast, slow)) * (MAX_REDUCTION_DB * strength)
        low = self._split.process(source, self._sample_rate, reset=False)[:, :frames].T
        cut = (1.0 - np.exp(reduction_db * (-math.log(10.0) / 20.0))).astype(np.float32)
        result = block - (block - low) * cut[:, None]
        return result, float(np.max(reduction_db))
