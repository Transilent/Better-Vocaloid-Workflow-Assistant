"""Native continuous output worker: null determinism and optional silent WASAPI."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
from mixdown_native_output import NativeOutput, NativeOutputError


def _maintenance_root() -> Path:
    for candidate in (APP, *APP.parents):
        if (candidate / 'workspace' / 'paths.json').is_file():
            return candidate
    return APP.parent.parent


MAINTENANCE_ROOT = _maintenance_root()
BUILD_DIR = APP / 'work' / 'native-audio' / 'build'
DLL_PATH = BUILD_DIR / 'vwa_native_output.dll'
BUILD_SCRIPT = APP / 'tools' / 'Build-MixdownNative.ps1'
GCC_PATH = Path('D:/mingw64/bin/gcc.exe')
HEADER_CANDIDATES = (
    Path(os.environ.get('VWA_MINIaudio_HEADER', '')) if os.environ.get('VWA_MINIaudio_HEADER') else None,
    APP / 'work' / 'native-audio' / 'third_party' / 'miniaudio' / 'miniaudio.h',
    MAINTENANCE_ROOT / 'work' / 'audition-native-device-20261108' / 'native' / 'third_party' / 'miniaudio' / 'miniaudio.h',
    MAINTENANCE_ROOT / 'work' / 'audition-implementation-20261108' / 'native-prototype' / 'third_party' / 'miniaudio' / 'miniaudio.h',
)


def _header_path() -> Path | None:
    return next((path for path in HEADER_CANDIDATES if path is not None and path.is_file()), None)


def _build() -> None:
    header = _header_path()
    if os.name != 'nt':
        print('SKIP: native output test requires Windows/WASAPI')
        raise SystemExit(0)
    if not GCC_PATH.is_file() or header is None:
        print(f'SKIP: existing GCC={GCC_PATH} or pinned miniaudio header is unavailable')
        raise SystemExit(0)
    shell = Path(os.environ['WINDIR']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    result = subprocess.run(
        [
            str(shell),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            str(BUILD_SCRIPT),
            '-GccPath',
            str(GCC_PATH),
            '-MiniaudioHeaderPath',
            str(header),
            '-OutputDirectory',
            str(BUILD_DIR),
        ],
        cwd=APP,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError((result.stdout + result.stderr)[-6000:])


def _expected(start: int, frames: int, channels: int = 2) -> np.ndarray:
    frame_numbers = np.arange(start, start + frames, dtype=np.uint64)[:, None]
    channel_numbers = np.arange(channels, dtype=np.uint64)[None, :]
    values = (frame_numbers * np.uint64(17) + channel_numbers * np.uint64(29)) % np.uint64(1009)
    return (values.astype(np.float32) / np.float32(1008.0)) - np.float32(0.5)


def _write_all(output: NativeOutput, samples: np.ndarray, epoch: int, start_frame: int) -> None:
    accepted_total = 0
    deadline = time.monotonic() + 4.0
    while accepted_total < len(samples):
        accepted = output.write(samples[accepted_total:], epoch, start_frame + accepted_total)
        accepted_total += accepted
        if accepted == 0:
            if time.monotonic() >= deadline:
                raise AssertionError('null writer made no progress')
            time.sleep(0.001)
    assert accepted_total == len(samples)


def test_null_wrapper() -> None:
    with NativeOutput(DLL_PATH, sample_rate=48000, channels=2, capacity_frames=256, period_frames=64, mode='null') as output:
        initial = output.snapshot()
        required = {
            'epoch', 'queued_frames', 'media_frame', 'capacity_frames', 'sample_rate',
            'channels', 'period_frames', 'backend', 'clock_quality', 'underrun_frames',
            'device_error',
        }
        assert required <= initial.keys()
        assert initial['epoch'] == 0
        assert initial['backend'] == 'null'
        assert initial['clock_quality'] == 'deterministic-null'
        assert initial['capacity_frames'] == 256
        assert initial['sample_rate'] == 48000
        assert initial['channels'] == 2
        assert initial['period_frames'] == 64
        assert initial['queued_frames'] == 0
        assert initial['media_frame'] == 0

        output.seek(0, epoch=1)
        output.set_playing(True)
        first = _expected(0, 100)
        assert output.write(first, epoch=1, start_frame=0) == 100
        assert np.array_equal(output.pull(64), first[:64])

        more = _expected(100, 500)
        assert output.write(more, epoch=1, start_frame=100) == 220
        rendered = output.pull(300)
        assert np.array_equal(rendered[:256], _expected(64, 256))
        assert np.all(rendered[256:] == 0)
        after_underrun = output.snapshot()
        assert after_underrun['queued_frames'] == 0
        assert after_underrun['media_frame'] == 320
        assert after_underrun['accepted_end_frame'] == 320
        assert after_underrun['underrun_frames'] == 44
        assert after_underrun['xrun'] is True

        assert output.write(_expected(600, 1), epoch=0, start_frame=320) == 0
        assert output.write(_expected(321, 1), epoch=1, start_frame=321) == 0
        rejected = output.snapshot()
        assert rejected['stale_epoch_writes'] == 1
        assert rejected['continuity_rejections'] == 1

        media_before_pause = rejected['media_frame']
        output.set_playing(False)
        assert np.all(output.pull(64) == 0)
        paused = output.snapshot()
        assert paused['media_frame'] == media_before_pause
        assert paused['pause_frames'] == 64

        output.seek(48000, epoch=2)
        output.set_playing(True)
        _write_all(output, _expected(48000, 64), 2, 48000)
        variable = np.concatenate([output.pull(3), output.pull(5), output.pull(17), output.pull(39)])
        assert np.array_equal(variable, _expected(48000, 64))
        final = output.snapshot()
        assert final['epoch'] == 2
        assert final['media_frame'] == 48064
        assert final['ring_owner_violations'] == 0
        assert final['device_error'] == ''

def test_null_pull_thread_is_serialized_with_write_and_seek() -> None:
    output = NativeOutput(DLL_PATH, sample_rate=48000, channels=2, capacity_frames=512, period_frames=64, mode='null')
    errors: list[BaseException] = []
    pull_sizes: list[int] = []
    stop = threading.Event()

    def consume() -> None:
        sizes = (1, 2, 7, 13, 31, 5, 17)
        index = 0
        try:
            while not stop.is_set():
                frames = sizes[index % len(sizes)]
                block = output.pull(frames)
                assert block.shape == (frames, 2)
                assert block.dtype == np.float32
                pull_sizes.append(frames)
                index += 1
                time.sleep(0.0002)
        except BaseException as error:
            errors.append(error)

    try:
        output.seek(48000, epoch=1)
        output.set_playing(True)
        consumer = threading.Thread(target=consume, name='vwa-null-consumer', daemon=True)
        consumer.start()
        for block_index in range(4):
            start = 48000 + block_index * 64
            _write_all(output, _expected(start, 64), 1, start)
        output.seek(96000, epoch=2)
        for block_index in range(4):
            start = 96000 + block_index * 64
            _write_all(output, _expected(start, 64), 2, start)
        time.sleep(0.04)
        stop.set()
        consumer.join(timeout=3.0)
        assert not consumer.is_alive()
        output.set_playing(False)
        snapshot = output.snapshot()
        assert not errors, errors
        assert pull_sizes
        assert snapshot['epoch'] == 2
        assert snapshot['media_frame'] >= 96000
        assert snapshot['ring_owner_violations'] == 0
    finally:
        stop.set()
        output.close()
        output.close()
    try:
        output.pull(1)
    except NativeOutputError:
        pass
    else:
        raise AssertionError('closed null output accepted pull()')


def test_clean_path_dll_load() -> None:
    runtime = Path(sys.executable).parent
    clean_path = os.pathsep.join([str(runtime), str(runtime / 'DLLs'), str(Path(os.environ['WINDIR']) / 'System32')])
    code = (
        'import ctypes, pathlib, sys; '
        f'p=pathlib.Path({str(DLL_PATH)!r}); '
        'lib=ctypes.CDLL(str(p)); '
        "lib.vwa_output_abi_string.restype=ctypes.c_char_p; "
        "print(lib.vwa_output_abi_string().decode('ascii'))"
    )
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=BUILD_DIR,
        env=dict(os.environ, PATH=clean_path),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'miniaudio-0.11.26' in result.stdout
    assert sorted(path.suffix.lower() for path in BUILD_DIR.glob('*.dll')) == ['.dll']


def test_optional_silent_wasapi() -> None:
    if os.environ.get('VWA_TEST_WASAPI') != '1':
        print('SKIP: silent WASAPI wrapper probe; set VWA_TEST_WASAPI=1 to run')
        return
    with NativeOutput(DLL_PATH, sample_rate=48000, channels=2, capacity_frames=4096, period_frames=256, mode='wasapi') as output:
        deadline = time.monotonic() + 3.0
        snapshot = output.snapshot()
        while snapshot['callback_count'] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
            snapshot = output.snapshot()
        assert snapshot['backend'] == 'wasapi-shared'
        assert snapshot['share_mode'] == 'shared'
        assert snapshot['callback_count'] > 0
        assert snapshot['actual_device_sample_rate'] > 0
        assert snapshot['actual_device_period_frames'] > 0
        assert snapshot['device_buffer_frames'] > 0
        assert snapshot['playing'] is False
        assert snapshot['pause_frames'] > 0
        assert snapshot['device_error'] == ''
        output.seek(0, epoch=1)
        after_seek = output.snapshot()
        assert after_seek['seek_count'] == 1
        assert after_seek['media_frame'] == 0
        assert after_seek['queued_frames'] == 0
        if after_seek['seek_clock_evidence']['after_stop']:
            assert after_seek['seek_clock_evidence']['reset_zero_ok'] is True


if __name__ == '__main__':
    _build()
    test_null_wrapper()
    test_null_pull_thread_is_serialized_with_write_and_seek()
    test_clean_path_dll_load()
    test_optional_silent_wasapi()
    print('PASS: native null output, concurrent control serialization and clean-path DLL load')
