"""Latency-compensated graph, exact frame count, safe limiting and seek history."""
import os
from pathlib import Path
import sys
import subprocess
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import numpy as np
from mixdown_limiter import StreamingLimiter
from mixdown_realtime_graph import LimitedMixSession

APP = Path(__file__).resolve().parents[1]
DLL = Path(os.environ.get('VWA_MIXDOWN_NATIVE_DLL', APP / 'components/native-audio/vwa_native_output.dll'))


class Source:
    sample_rate = 48000
    channels = 2
    def __init__(self, request, cache_dir, **_kwargs):
        self.data = request['samples']
        self.duration_frames = len(self.data)
        self.position_frame = 0
        self.closed = 0
        self.gain = 1
    def render(self, frames):
        start = self.position_frame
        self.position_frame += frames
        return self.data[start:start+frames] * self.gain
    def seek(self, frame, **_kwargs): self.position_frame = frame
    def prepare_parameters(self, request): return request
    def apply_parameters(self, request): self.gain = request['gain']
    def close(self): self.closed += 1


class GraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global DLL
        if not DLL.with_name('vwa_limiter.dll').is_file():
            gcc = Path(os.environ.get('VWA_LIMITER_GCC', 'D:/mingw64/bin/gcc.exe'))
            if not gcc.is_file():
                raise unittest.SkipTest('native limiter and existing C compiler are unavailable')
            build = APP / 'work/native-graph-test'
            build.mkdir(parents=True, exist_ok=True)
            DLL = build / 'vwa_native_output.dll'
            result = subprocess.run([str(gcc), '-std=c11', '-O2', '-shared',
                '-DVWA_LIMITER_BUILD_DLL', str(APP / 'src/native/vwa_limiter.c'),
                '-o', str(DLL.with_name('vwa_limiter.dll')), '-static-libgcc'],
                capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)

    def fixture(self, frames=6407):
        data = np.random.default_rng(72).normal(0, .7, (frames, 2)).astype('float32')
        data[0] = [3, -2]
        data[-1] = [4, -1]
        graph = LimitedMixSession({'samples': data}, 'unused', DLL, renderer_factory=Source)
        self.addCleanup(graph.close)
        return data, graph

    def reference(self, data):
        with StreamingLimiter(DLL.with_name('vwa_limiter.dll'), 48000, 2) as limiter:
            return np.concatenate((limiter.process(data), limiter.finish()))

    def test_partition_and_terminal_drain_are_exact(self):
        for total in (1, 2, 238, 239, 240, 6407):
            data, graph = self.fixture(total)
            parts = []
            remaining = total
            sizes = iter([1, 3, 81, 512, 17, 1200, 4096, 8192])
            while remaining:
                count = min(next(sizes), remaining)
                parts.append(graph.render(count))
                remaining -= count
            np.testing.assert_array_equal(np.concatenate(parts), self.reference(data))
            np.testing.assert_array_equal(graph.render(5), np.zeros((5, 2), dtype='float32'))

    def test_seek_resets_old_limiter_and_preserves_bounded_history(self):
        data, graph = self.fixture(15000)
        graph.render(1000)
        graph.seek(10000)
        start = 10000 - 4800
        expected = self.reference(data[start:])[4800:4800+400]
        np.testing.assert_array_equal(graph.render(400), expected)
        graph.seek(0)
        np.testing.assert_array_equal(graph.render(700), self.reference(data)[:700])
        graph.seek(len(data))
        np.testing.assert_array_equal(graph.render(3), np.zeros((3, 2), dtype='float32'))

    def test_limiter_telemetry_is_frame_aligned_and_measured(self):
        data, graph = self.fixture(4096)
        rendered = graph.render(512)
        telemetry = graph.telemetry_for_range(0, 512)
        self.assertEqual(rendered.shape, (512, 2))
        self.assertIsInstance(telemetry, dict)
        self.assertEqual(telemetry['schema'], 'vwa.audio_mixdown.realtime-telemetry/v1')
        self.assertEqual((telemetry['frame_start'], telemetry['frame_end']), (0, 512))
        self.assertEqual(telemetry['sample_rate'], 48000)
        self.assertIn('pre_limiter', telemetry['bus'])
        self.assertIn('post_limiter', telemetry['bus'])
        limiter = telemetry['dynamics']['limiter']
        self.assertEqual(limiter['method'], 'aligned_rms_db_difference')
        self.assertGreaterEqual(limiter['gr_db'], 0.0)
        self.assertGreater(limiter['gr_db'], 0.0)
        self.assertEqual(telemetry['bus']['peak_dbfs'], telemetry['bus']['final_output']['peak_dbfs'])
        self.assertLessEqual(telemetry['bus']['peak_dbfs'], -1.0 + 1.0e-4)

    def test_volume_does_not_change_limiter_history_or_gain_contract(self):
        data, graph = self.fixture()
        graph.set_volume(25)
        np.testing.assert_array_equal(graph.render(503), self.reference(data)[:503] * .25)
        graph.set_volume(100)
        np.testing.assert_array_equal(graph.render(300), self.reference(data)[503:803])
        for value in (101, -1, float('nan'), True):
            with self.assertRaises(ValueError): graph.set_volume(value)
        graph.close()
        graph.close()
        self.assertEqual(graph._pre.closed, 1)
        with self.assertRaises(RuntimeError): graph.render(12)

    def test_seek_warmup_publishes_no_display_telemetry(self):
        data, graph = self.fixture(15000)
        graph.render(512)
        graph.seek(10000)
        self.assertEqual(len(graph._telemetry_records), 0)
        self.assertEqual(len(graph._telemetry_index), 0)
        if hasattr(graph._pre, '_telemetry_records'):
            self.assertEqual(len(graph._pre._telemetry_records), 0)
        graph.render(512)
        telemetry = graph.telemetry_for_range(10000, 10512)
        self.assertEqual(telemetry['frame_start'], 10000)
        self.assertEqual(telemetry['frame_end'], 10512)

    def test_parameters_apply_after_prepared_read_ahead(self):
        _data, graph = self.fixture()
        graph.render(64)
        prepared_end = graph._pre.position_frame
        graph.apply_parameters(graph.prepare_parameters({'gain': .5}))
        self.assertEqual(graph.parameter_first_frame, prepared_end)
        self.assertGreaterEqual(prepared_end, graph.position_frame)
        self.assertLess(graph.snapshot()['fifo_frames'], 1024)


if __name__ == '__main__':
    unittest.main()
