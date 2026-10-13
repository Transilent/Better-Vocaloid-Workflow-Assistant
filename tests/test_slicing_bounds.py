"""Sparse vocals must retain their timestamps without oversized model inputs."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v2m_runtime import configure
configure(ROOT / 'dependencies/vocal2midi2')
import numpy as np
from inference.API import slicer_api as slicer_v2
spec = importlib.util.spec_from_file_location(
    '_bvwa_legacy_slicer', ROOT / 'dependencies/vocal2midi/inference/API/slicer_api.py',
)
slicer_v1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slicer_v1)

sr = 100

def segment(offset, seconds, value, stereo=False):
    shape = (2, round(seconds * sr)) if stereo else (round(seconds * sr),)
    return {'offset': offset, 'waveform': np.full(shape, value, dtype=np.float32)}

def retained(source, output, limit):
    assert all(item['waveform'].shape[-1] <= round(limit * sr) for item in output)
    assert [item['offset'] for item in output] == sorted(item['offset'] for item in output)
    for original in source:
        start = round(original['offset'] * sr)
        end = start + original['waveform'].shape[-1]
        containers = [item for item in output
                      if round(item['offset'] * sr) <= start
                      and round(item['offset'] * sr) + item['waveform'].shape[-1] >= end]
        assert len(containers) == 1, 'A vocal fragment was lost or duplicated'
        item = containers[0]
        offset = start - round(item['offset'] * sr)
        np.testing.assert_array_equal(item['waveform'][..., offset:offset + end - start],
                                      original['waveform'])

# The failed job contains two tiny fragments a minute after a previous phrase.
# Both the smart and heuristic paths must keep every fragment within 10 seconds.
timing = [(12.74, .28), (23.84, .42), (29.58, 1.66), (90.94, .28),
          (93.84, .30), (125.56, 1.48), (158.4, 8.42),
          (168.26, 3.96), (172.28, 1.50), (173.90, .90)]
source = [segment(offset, duration, i + 1) for i, (offset, duration) in enumerate(timing)]
for slicer in (slicer_v1, slicer_v2):
    for method in (slicer.pitch_based_slice, slicer.heuristic_slice):
        with patch.object(slicer.Slicer, 'slice', return_value=source):
            result = method(np.zeros(200 * sr, dtype=np.float32), sr, min_len_sec=5, max_len_sec=10)
        retained(source, result, 10)
        assert len(result) == 6
        assert max(item['waveform'].shape[-1] / sr for item in result) == 8.42

for stereo in (False, True):
    # A nearby fragment still joins its neighbor, with only the original gap.
    source = [segment(2, .2, 1, stereo), segment(2.4, .4, 2, stereo)]
    result = slicer._merge_tiny_chunks(source, sr, max_len_sec=1)
    retained(source, result, 1)
    assert len(result) == 1 and result[0]['offset'] == 2
    assert np.all(result[0]['waveform'][..., 20:40] == 0)

    # Keep isolated heads, tails and all-tiny inputs rather than dropping them.
    for source in ([segment(0, .2, 1, stereo), segment(60, .2, 2, stereo)],
                   [segment(0, 9.9, 1, stereo), segment(9.9, .2, 2, stereo)],
                   [segment(0, .2, 1, stereo)]):
        result = slicer._merge_tiny_chunks(source, sr, max_len_sec=10)
        retained(source, result, 10)
        assert len(result) == len(source)

    # A rejected backward merge can still use a nearby following neighbor.
    source = [segment(0, 9.9, 1, stereo), segment(20, .2, 2, stereo),
              segment(20.3, .4, 3, stereo)]
    result = slicer._merge_tiny_chunks(source, sr, max_len_sec=10)
    retained(source, result, 10)
    assert len(result) == 2 and result[1]['offset'] == 20

assert slicer._merge_tiny_chunks([], sr, max_len_sec=10) == []
print('PASS: bounded smart/heuristic slicing, sparse fragments, mono/stereo samples and original timestamps')
