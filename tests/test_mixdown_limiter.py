"""Native streaming limiter contract and real FFmpeg parity proof."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

try:
    import soundfile as sf
except ImportError:
    print("SKIP: soundfile is unavailable")
    raise SystemExit(0)

from mixdown_limiter import (  # noqa: E402
    ATTACK_MS,
    LimiterClosedError,
    LimiterError,
    PEAK_CEILING,
    PEAK_CEILING_DBFS,
    StreamingLimiter,
    ffmpeg_filter,
)


def _maintenance_root() -> Path:
    for candidate in (APP, *APP.parents):
        if (candidate / "workspace" / "paths.json").is_file():
            return candidate
    return APP.parent.parent


MAINTENANCE_ROOT = _maintenance_root()
ARTIFACT_DIR = Path(
    os.environ.get(
        "VWA_LIMITER_ARTIFACT_DIR",
        APP / "work/native-limiter-test",
    )
).expanduser().resolve()
DLL_PATH = ARTIFACT_DIR / "vwa_limiter.dll"
RECEIPT_PATH = Path(
    os.environ.get("VWA_LIMITER_RECEIPT", APP / "work/test-results/limiter-validation.json")
).expanduser().resolve()
SOURCE_C = APP / "src/native/vwa_limiter.c"
SOURCE_H = APP / "src/native/vwa_limiter.h"
GCC_PATH = Path(os.environ.get("VWA_LIMITER_GCC", "D:/mingw64/bin/gcc.exe"))
FFMPEG_PATH = Path(
    os.environ.get(
        "VWA_LIMITER_TEST_FFMPEG",
        os.environ.get('VWA_MIX_TEST_FFMPEG_PATH', APP / 'dependencies/ffmpeg/ffmpeg.exe'),
    )
)
TOLERANCE = 1.0e-6
IRREGULAR_PATTERN = (1, 7, 3, 211, 2, 509, 4096, 13, 97, 1024, 5, 31)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _matrix(samples: np.ndarray, channels: int) -> np.ndarray:
    array = np.asarray(samples, dtype=np.float32)
    if channels == 1 and array.ndim == 1:
        return array[:, None]
    if array.ndim != 2 or array.shape[1] != channels:
        raise AssertionError(f"expected (frames, {channels}), got {array.shape}")
    return array


def _partitions(frames: int, pattern: tuple[int, ...]) -> list[slice]:
    result = []
    start = 0
    index = 0
    while start < frames:
        width = pattern[index % len(pattern)]
        stop = min(frames, start + width)
        result.append(slice(start, stop))
        start = stop
        index += 1
    return result


def _stream_render(
    data: np.ndarray,
    sample_rate: int,
    channels: int,
    chunks: list[slice],
) -> tuple[np.ndarray, list[int], int]:
    parts = []
    process_sizes = []
    with StreamingLimiter(DLL_PATH, sample_rate, channels) as limiter:
        for chunk in chunks:
            output = limiter.process(data[chunk])
            process_sizes.append(int(_matrix(output, channels).shape[0]))
            parts.append(_matrix(output, channels))
        tail = _matrix(limiter.finish(), channels)
        parts.append(tail)
        latency = limiter.latency_frames
    return np.concatenate(parts, axis=0), process_sizes, len(tail)


def _ffmpeg_render(
    data: np.ndarray,
    sample_rate: int,
    channels: int,
    root: Path,
    name: str,
) -> np.ndarray:
    source = root / f"{name}.wav"
    target = root / f"{name}-ffmpeg.wav"
    sf.write(str(source), data, sample_rate, subtype="FLOAT")
    command = [
        str(FFMPEG_PATH),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-fflags",
        "+bitexact",
        "-i",
        str(source),
        "-af",
        ffmpeg_filter(),
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-c:a",
        "pcm_f32le",
        "-rf64",
        "auto",
        "-map_metadata",
        "-1",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise AssertionError((result.stdout + result.stderr)[-4000:])
    decoded, decoded_rate = sf.read(str(target), always_2d=True, dtype="float32")
    if decoded_rate != sample_rate:
        raise AssertionError(f"FFmpeg changed sample rate to {decoded_rate}")
    return _matrix(decoded, channels)


def _impulse_stimulus(sample_rate: int, channels: int) -> np.ndarray:
    frames = max(1200, int(round(sample_rate * 0.095)))
    data = np.zeros((frames, channels), dtype=np.float32)
    positions = (0, 1, 11, frames // 2, frames - 5, frames - 2, frames - 1)
    values = (1.9, -1.45, 0.891250938, -0.95, 1.3, -2.2, 0.72)
    for channel in range(channels):
        for index, (position, value) in enumerate(zip(positions, values)):
            column = (index + channel) % channels
            data[position, column] += np.float32(value if (index + channel) % 3 else -value)
        data[:, channel] += np.float32(
            0.0005 * np.sin(2.0 * np.pi * (173.0 + 37.0 * channel)
                            * np.arange(frames) / sample_rate)
        )
    return data


def _high_sine_stimulus(sample_rate: int, channels: int) -> np.ndarray:
    frames = int(round(sample_rate * 0.32))
    time_axis = np.arange(frames, dtype=np.float64) / sample_rate
    data = np.empty((frames, channels), dtype=np.float32)
    for channel in range(channels):
        phase = 0.19 * channel
        data[:, channel] = (
            1.24 * np.sin(2.0 * np.pi * 997.0 * time_axis + phase)
            + 0.52 * np.sin(2.0 * np.pi * (0.431 * sample_rate) * time_axis)
        ).astype(np.float32)
    return data


def _gain_change_stimulus(sample_rate: int, channels: int) -> np.ndarray:
    frames = int(round(sample_rate * 0.48))
    rng = np.random.default_rng(20261108 + channels)
    noise = rng.uniform(-1.0, 1.0, size=(frames, channels)).astype(np.float32)
    gains = (0.03, 1.85, 0.12, 2.2, 0.42, 0.0, 1.35, 0.08)
    boundaries = np.linspace(0, frames, len(gains) + 1, dtype=int)
    data = np.empty_like(noise)
    for index, gain in enumerate(gains):
        data[boundaries[index]:boundaries[index + 1]] = (
            noise[boundaries[index]:boundaries[index + 1]] * np.float32(gain)
        )
    for offset, value in ((2, 2.1), (240, -1.8), (frames - 2, 1.7)):
        if 0 <= offset < frames:
            data[offset, 0] = np.float32(value)
    return data


def _short_stimulus(frames: int, channels: int) -> np.ndarray:
    data = np.zeros((frames, channels), dtype=np.float32)
    if frames:
        data[0, 0] = np.float32(1.8)
    if frames > 1:
        data[-1, -1] = np.float32(-1.35)
    if frames > 2:
        data[frames // 2, 0] = np.float32(0.891250938)
    return data


class StreamingLimiterTests(unittest.TestCase):
    receipt: dict[str, object] = {"comparisons": []}

    @classmethod
    def setUpClass(cls) -> None:
        if os.name != "nt":
            raise unittest.SkipTest("the proof DLL and FFmpeg binary target Windows")
        if not GCC_PATH.is_file():
            raise unittest.SkipTest(f"existing GCC is unavailable: {GCC_PATH}")
        if not FFMPEG_PATH.is_file():
            raise unittest.SkipTest(f"existing FFmpeg is unavailable: {FFMPEG_PATH}")
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        command = [
            str(GCC_PATH),
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-static-libgcc",
            "-DVWA_OUTPUT_BUILD_DLL",
            "-I",
            str(SOURCE_H.parent),
            "-o",
            str(DLL_PATH),
            str(SOURCE_C),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError((result.stdout + result.stderr)[-6000:])
        version = subprocess.run(
            [str(FFMPEG_PATH), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if version.returncode != 0:
            raise AssertionError(version.stdout + version.stderr)
        cls.ffmpeg_version = version.stdout.splitlines()[0]
        cls.receipt.update(
            {
                "schema": "vwa.mixdown-limiter-validation/v1",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "python": sys.version.split()[0],
                "host_os": os.name,
                "base_commit": os.environ.get('VWA_TEST_BASE_COMMIT'),
                "source_identity_note": "Isolated source digests below; commit binding is supplied by the outer runner.",
                "sources": {
                    str(path.relative_to(APP)): _sha256(path)
                    for path in (
                        SOURCE_C,
                        SOURCE_H,
                        APP / "src/mixdown_limiter.py",
                        APP / "src/mixdown_limiter_spec.py",
                        Path(__file__).resolve(),
                    )
                },
                "proof_dll": {
                    "path": str(DLL_PATH),
                    "sha256": _sha256(DLL_PATH),
                    "size": DLL_PATH.stat().st_size,
                    "build_command": command,
                    "export_macro": "VWA_OUTPUT_BUILD_DLL",
                },
                "ffmpeg": {
                    "path": str(FFMPEG_PATH),
                    "version": cls.ffmpeg_version,
                    "sha256": _sha256(FFMPEG_PATH),
                    "size": FFMPEG_PATH.stat().st_size,
                },
                "filter": ffmpeg_filter(),
                "tolerance_max_abs": TOLERANCE,
                "upstream_alimiter": {
                    "tag": "n7.1",
                    "url": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1/"
                           "libavfilter/af_alimiter.c",
                    "sha256": "dc15973088a1e6197ae1001cfecbc0d847c479571563c37b5e2efe4073fa56eb",
                    "license_url": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/"
                                   "n7.1/COPYING.LGPLv2.1",
"license_sha256":
                        "b634ab5640e258563c536e658cad87080553df6f34f62269a21d554844e58bfe",
                },
                "runtime_contract": {
                    "process_spawns_per_block": 0,
                    "mono_channels": 1,
                    "stereo_channels": 2,
                    "output_dtype": "float32",
                    "latency_frames": "attack_frames - 1",
                    "drain_padding_frames": "attack_frames - 1",
                },
            }
        )

    @classmethod
    def tearDownClass(cls) -> None:
        RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.receipt["comparison_count"] = len(cls.receipt["comparisons"])
        RECEIPT_PATH.write_text(
            json.dumps(cls.receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"receipt: {RECEIPT_PATH}")

    def _compare_real_ffmpeg(
        self,
        data: np.ndarray,
        sample_rate: int,
        channels: int,
        name: str,
    ) -> None:
        chunks = _partitions(len(data), IRREGULAR_PATTERN)
        actual, process_sizes, tail_frames = _stream_render(
            data, sample_rate, channels, chunks
        )
        with tempfile.TemporaryDirectory(
            prefix="limiter-parity-", dir=ARTIFACT_DIR
        ) as temp:
            expected = _ffmpeg_render(
                data, sample_rate, channels, Path(temp), name
            )
        self.assertEqual(actual.shape, data.shape)
        self.assertEqual(expected.shape, data.shape)
        difference = np.abs(
            actual.astype(np.float64) - expected.astype(np.float64)
        )
        maximum = float(difference.max(initial=0.0))
        rms = float(np.sqrt(np.mean(difference * difference))) if difference.size else 0.0
        actual_peak = float(np.max(np.abs(actual), initial=0.0))
        expected_peak = float(np.max(np.abs(expected), initial=0.0))
        self.assertLessEqual(maximum, TOLERANCE)
        self.assertLessEqual(actual_peak, PEAK_CEILING + 1e-6)
        self.assertLessEqual(expected_peak, PEAK_CEILING + 1e-6)
        self.assertTrue(np.isfinite(actual).all())
        self.assertTrue(np.isfinite(expected).all())
        self.receipt["comparisons"].append(
            {
                "name": name,
                "sample_rate": sample_rate,
                "channels": channels,
                "frames": len(data),
                "irregular_chunk_count": len(chunks),
                "first_process_output_frames": process_sizes[0],
                "tail_frames": tail_frames,
                "delay_frames": max(0, int(round(sample_rate * ATTACK_MS / 1000.0)) - 1),
                "max_abs_difference": maximum,
                "rms_difference": rms,
                "stream_peak": actual_peak,
                "ffmpeg_peak": expected_peak,
            }
        )

    def test_shared_filter_contract(self) -> None:
        self.assertEqual(PEAK_CEILING_DBFS, -1.0)
        self.assertAlmostEqual(PEAK_CEILING, 0.8912509381337456, places=15)
        self.assertEqual(
            ffmpeg_filter(),
            "alimiter=limit=0.891250938:attack=5:release=100:"
            "level=false:latency=true:asc=false",
        )
        self.assertEqual(
            Path(self.receipt["proof_dll"]["path"]).name, "vwa_limiter.dll"
        )
        with StreamingLimiter(DLL_PATH, 44100, 1) as limiter:
            self.assertEqual(limiter.attack_frames, 220)
            self.assertEqual(limiter.latency_frames, 219)
        with StreamingLimiter(DLL_PATH, 48000, 2) as limiter:
            self.assertEqual(limiter.attack_frames, 240)
            self.assertEqual(limiter.latency_frames, 239)

    def test_shapes_finite_validation_reset_and_sticky_close(self) -> None:
        with StreamingLimiter(DLL_PATH, 48000, 1) as limiter:
            self.assertEqual(limiter.process(np.zeros(5, dtype=np.float32)).shape, (0,))
            with self.assertRaises(ValueError):
                limiter.process(np.zeros((5, 2), dtype=np.float32))
            with self.assertRaises(ValueError):
                limiter.process(np.array([0.0, np.nan], dtype=np.float32))
            tail = limiter.finish()
            self.assertEqual(tail.shape, (5,))
            self.assertTrue(np.all(tail == 0.0))
            self.assertEqual(limiter.finish().shape, (0,))
            with self.assertRaises(LimiterError):
                limiter.process(np.zeros(1, dtype=np.float32))
            limiter.reset()
            self.assertEqual(limiter.process(np.zeros(3, dtype=np.float32)).shape, (0,))
            self.assertEqual(limiter.finish().shape, (3,))
        limiter.close()
        limiter.close()
        with self.assertRaises(LimiterClosedError):
            limiter.process(np.zeros(1, dtype=np.float32))
        with self.assertRaises(LimiterClosedError):
            limiter.reset()
        with self.assertRaises(LimiterClosedError):
            limiter.finish()

        with StreamingLimiter(DLL_PATH, 44100, 2) as limiter:
            with self.assertRaises(ValueError):
                limiter.process(np.zeros(8, dtype=np.float32))

    def test_reset_matches_a_fresh_instance(self) -> None:
        data = _short_stimulus(1000, 2)
        chunks = _partitions(len(data), IRREGULAR_PATTERN)
        first, _, _ = _stream_render(data, 48000, 2, chunks)
        with StreamingLimiter(DLL_PATH, 48000, 2) as limiter:
            for chunk in chunks:
                limiter.process(data[chunk])
            limiter.finish()
            limiter.reset()
            parts = [_matrix(limiter.process(data[chunk]), 2) for chunk in chunks]
            parts.append(_matrix(limiter.finish(), 2))
            second = np.concatenate(parts, axis=0)
        self.assertTrue(np.array_equal(first, second))

    def test_short_inputs_match_real_ffmpeg(self) -> None:
        for sample_rate in (44100, 48000):
            attack_frames = int(round(sample_rate * ATTACK_MS / 1000.0))
            for channels in (1, 2):
                for frames in (1, attack_frames - 1, attack_frames, attack_frames + 1):
                    name = f"short-{frames}-{sample_rate}-{channels}ch"
                    data = _short_stimulus(frames, channels)
                    with self.subTest(name=name):
                        self._compare_real_ffmpeg(
                            data, sample_rate, channels, name
                        )

    def test_required_stimuli_match_real_ffmpeg_irregular_chunks(self) -> None:
        stimuli = {
            "impulses-start-end-ceiling": _impulse_stimulus,
            "high-sine": _high_sine_stimulus,
            "random-gain-changes": _gain_change_stimulus,
        }
        for sample_rate in (44100, 48000):
            for channels in (1, 2):
                for name, generator in stimuli.items():
                    case_name = f"{name}-{sample_rate}-{channels}ch"
                    data = generator(sample_rate, channels)
                    with self.subTest(name=case_name):
                        self._compare_real_ffmpeg(
                            data, sample_rate, channels, case_name
                        )

    def test_clean_path_load(self) -> None:
        runtime = Path(sys.executable).parent
        clean_path = os.pathsep.join(
            [
                str(runtime),
                str(runtime / "DLLs"),
                str(Path(os.environ["WINDIR"]) / "System32"),
            ]
        )
        code = (
            "import ctypes; "
            f"p=ctypes.CDLL({str(DLL_PATH)!r}); "
            "p.vwa_limiter_abi_string.restype=ctypes.c_char_p; "
            "print(p.vwa_limiter_abi_string().decode('ascii'))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ARTIFACT_DIR,
            env=dict(os.environ, PATH=clean_path),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("vwa-limiter/1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
