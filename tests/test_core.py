"""Regression checks for timekeeping, content integrity and portable paths."""
import json
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / 'vendor')]
from common import app_path, clean_lyrics, extract_bv, load_config, file_hash
from publication import load_bundle, validate_content
from tests.support import ensure_fixture
import mido
import numpy as np
from mdx import Transform
from midi_merge import merge_voices
from midi_checks import inspect_midi

cfg = load_config()
assert all(Path(cfg[key]).is_relative_to(APP) for key in ('python', 'vocal2midi', 'ffmpeg', 'mdx_model', 'chorus_model', 'publish_browser_executable'))
assert extract_bv('https://www.bilibili.com/video/BV1000000000/?x=1') == 'BV1000000000'
assert clean_lyrics('歌 词，\n测试！') == '歌词测试'
try:
    app_path('@/../../outside')
except ValueError:
    pass
else:
    raise AssertionError('Escaping internal path accepted')
scratch = APP / 'work/core-tests'
scratch.mkdir(parents=True, exist_ok=True)
for voice, pitch in [('lead', 60), ('backing', 67)]:
    midi = mido.MidiFile(ticks_per_beat=480, charset='utf8')
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([mido.MetaMessage('set_tempo', tempo=500000),
                  mido.MetaMessage('lyrics', text='测', time=240),
                  mido.Message('note_on', note=pitch, velocity=80, time=0),
                  mido.Message('note_off', note=pitch, velocity=0, time=480)])
    midi.save(scratch / (voice + '.mid'))
# Use the same merging entrypoint as the pipeline.
merge_voices([('Lead Vocal', scratch / 'lead.mid'), ('Backing Vocal', scratch / 'backing.mid')], scratch / 'voices.mid')
info = inspect_midi(scratch / 'voices.mid')
assert info['notes'] == 2 and info['lyrics_events'] == 2
merged = mido.MidiFile(scratch / 'voices.mid', charset='utf8')
starts = []
for track in merged.tracks:
    tick = 0
    for message in track:
        tick += message.time
        if message.type == 'note_on' and message.velocity:
            starts.append(mido.tick2second(tick, merged.ticks_per_beat, 500000))
assert starts == [0.25, 0.25], starts
wave = np.random.default_rng(42).normal(0, .1, (2, 8192)).astype(np.float32)
stft = Transform(2048, 512)
rebuilt = stft.inverse(stft.forward(wave, 1025))
assert rebuilt.shape == wave.shape and np.max(np.abs(wave - rebuilt)) < 1e-5
bundle = ensure_fixture(APP)
data = load_bundle(bundle, 'bilibili')
assert data['final_publish'] == 'human_only' and data['platform_covers']['bilibili']['policy'] == 'contain'
assert file_hash(bundle / 'cover.jpg') == file_hash(Path(data['cover']['source']))
assert data['platform_covers']['bilibili']['4:3']['width'] == 1440
assert data['platform_covers']['bilibili']['4:3']['height'] == 1080
try:
    validate_content({'title': '字' * 21, 'description': 'STAFF'}, 'xiaohongshu')
except ValueError:
    pass
else:
    raise AssertionError('Oversized title accepted')
video = Path(data['video']['path'])
with video.open('ab') as stream:
    stream.write(b'integrity-test')
try:
    load_bundle(bundle, 'bilibili')
except ValueError:
    pass
else:
    raise AssertionError('Changed finished video accepted')
with video.open('r+b') as stream:
    stream.truncate(data['video']['bytes'])
load_bundle(bundle, 'bilibili')
print('PASS: portable paths, exact MIDI timing, spectral reconstruction, original cover and video integrity')
