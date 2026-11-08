"""Full four-track real-audio acceptance for the simple mixdown workflow."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

DEPENDENCY_ENV = 'VWA_MIX_TEST_PEDALBOARD_PATH'
if not os.environ.get(DEPENDENCY_ENV):
    installed = APP / 'dependencies/optional/pedalboard/current/packages'
    if installed.is_dir():
        os.environ[DEPENDENCY_ENV] = str(installed)
    else:
        print('SKIP: ' + DEPENDENCY_ENV + ' is not set and Pedalboard is not installed.')
        raise SystemExit(0)

try:
    import numpy as np
    import soundfile as sf
except Exception as error:
    print('ERROR: cannot import mixdown acceptance runtime: ' + str(error))
    raise SystemExit(1)


sys.path.insert(0, os.environ[DEPENDENCY_ENV])
import copy
import audio_mixdown as backend
from mixdown_realtime_graph import LimitedMixSession


def _envelope(seconds, rate, spans):
    samples = np.arange(int(rate * seconds), dtype=np.float32) / rate
    gate = np.zeros_like(samples)
    for start, end, value in spans:
        gate[max(0, int(start * rate)):min(len(gate), int(end * rate))] = value
    return samples, gate


class FullSampleAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mixdown-full-', dir=APP / 'work')
        self.root = Path(self.temp.name)
        self.rate = 44100
        self.seconds = 12.0
        t, gate1 = _envelope(self.seconds, self.rate, ((0, 12, 1), (2.7, 4.1, .45), (8, 9, .35)))
        _, gate2 = _envelope(self.seconds, self.rate, ((0, 1.6, .4), (3, 7.8, 1), (9.2, 12, .7)))
        _, gate3 = _envelope(self.seconds, self.rate, ((0, .6, .2), (4.5, 10.5, 1)))
        vocal_signals = (
            (0.14 + 0.055 * np.sin(2 * np.pi * .17 * t)) * np.sin(
                2 * np.pi * 220 * t + .18 * np.sin(2 * np.pi * 5.2 * t)) * gate1,
            (0.13 + 0.045 * np.sin(2 * np.pi * .23 * t + .8)) * np.sin(
                2 * np.pi * 330 * t + .12 * np.sin(2 * np.pi * 4.4 * t)) * gate2,
            (0.12 + 0.04 * np.sin(2 * np.pi * .31 * t + 1.7)) * np.sin(
                2 * np.pi * 440 * t + .22 * np.sin(2 * np.pi * 6.1 * t)) * gate3,
        )
        self.vocals = []
        for index, signal in enumerate(vocal_signals, 1):
            path = self.root / ('vocal-%d.wav' % index)
            sf.write(path, np.stack((signal, signal * .93), axis=1).astype(np.float32),
                     self.rate, subtype='PCM_24')
            self.vocals.append(path)
        left = (.075 * np.sin(2 * np.pi * 110 * t) +
                .035 * np.sin(2 * np.pi * 220 * t) * (.5 + .5 * np.sin(2 * np.pi * .7 * t)) +
                .025 * np.sin(2 * np.pi * 880 * t))
        right = (.07 * np.sin(2 * np.pi * 113 * t + .4) +
                 .032 * np.sin(2 * np.pi * 330 * t) * (.5 + .5 * np.sin(2 * np.pi * .9 * t)) +
                 .022 * np.sin(2 * np.pi * 1320 * t))
        for hit in (1.4, 2.9, 4.6, 6.2, 8.1, 10.0):
            position = int(hit * self.rate)
            length = int(.06 * self.rate)
            decay = np.linspace(0, 1, length, dtype=np.float32)
            pulse = .13 * np.exp(-12 * decay) * np.sin(2 * np.pi * 72 * decay)
            left[position:position + length] += pulse
            right[position:position + length] += pulse * .8
        self.backing = self.root / 'backing.flac'
        sf.write(self.backing, np.stack((left, right), axis=1).astype(np.float32),
                 self.rate, format='FLAC', subtype='PCM_24')
        self.output = self.root / 'full-mix.wav'
        self.source_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (*self.vocals, self.backing)
        }


    def tearDown(self):
        self.temp.cleanup()


    def request(self):
        return dict(vocals=[dict(path=str(path),gain_db=-2.0*index,offset_seconds=.02*index,
                                mute=False,solo=False,pan=.2*(index-1),reverb_send=.8)
                            for index,path in enumerate(self.vocals)],
                    instrumental_path=str(self.backing),output_path=str(self.output),instrumental_db=-6.,
                    eq_low_cut_hz=70.,eq_presence_db=3.,compression_strength=.6,reverb_mix=.24,
                    reverb_profile='natural',reverb_predelay_ms=25.,stereo_width=1.1)

    def test_full_song_true_pcm_measurements_routes_and_export(self):
        request=self.request()
        dll=APP/'components/native-audio/vwa_native_output.dll'
        self.assertTrue(dll.with_name('vwa_limiter.dll').is_file(),'native component must be provisioned for this acceptance')
        ffmpeg=Path(os.environ.get('VWA_MIX_TEST_FFMPEG_PATH',APP/'dependencies/ffmpeg/ffmpeg.exe'))
        graph=LimitedMixSession(request,self.root/'cache',dll,ffmpeg=ffmpeg)
        blocks=[]; measured=[]
        try:
            while graph.position_frame<graph.duration_frames:
                start=graph.position_frame
                block=graph.render(min(512,graph.duration_frames-start))
                self.assertTrue(np.isfinite(block).all())
                self.assertLessEqual(float(np.max(np.abs(block))),.891251+1e-5)
                blocks.append(block)
                frame=graph.telemetry_for_range(start,graph.position_frame)
                if frame and frame.get('spectrum'):measured.append(frame)
            self.assertTrue(measured,'actual FFT must exist after 100ms')
            self.assertEqual(len(measured[len(measured)//3]['tracks']),4)
            meter=measured[len(measured)//3]
            self.assertEqual(meter['source'],'realtime_dsp')
            self.assertTrue(all(np.isfinite(row['rms_dbfs']) for row in meter['tracks']))
            self.assertGreater(meter['dynamics']['compressor']['gr_db'],0.05)
            self.assertGreater(float(np.max(np.abs(np.asarray(meter['spectrum']['before_dbfs'])-
                                                            np.asarray(meter['spectrum']['after_dbfs'])))),.2)
            output=np.concatenate(blocks)
            self.assertEqual(output.shape,(graph.duration_frames,2))
            old_frames=graph.duration_frames
            changed=copy.deepcopy(request);changed['vocals'][1]['mute']=True
            graph.seek(self.rate*3)
            graph.apply_parameters(graph.prepare_parameters(changed))
            start=graph.position_frame;graph.render(512)
            meter=graph.telemetry_for_range(start,graph.position_frame)
            self.assertFalse(meter['tracks'][1]['audible'])
            self.assertLessEqual(meter['tracks'][1]['peak_dbfs'],-100)
            self.assertFalse(self.output.exists(),'continuous render must never write audition WAVs')
        finally:graph.close()
        report=backend.render(request)
        self.assertTrue(self.output.is_file())
        self.assertTrue(report)
        self.assertEqual(sf.info(str(self.output)).samplerate,self.rate)
        self.assertEqual(sf.info(str(self.output)).channels,2)
        for path in (*self.vocals,self.backing):
            self.assertEqual(self.source_hashes[path.name],hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main(verbosity=2)
