"""Task -> adapter -> upstream lyric writer -> merged MIDI, in both Chinese modes."""
import json
from pathlib import Path
import sys
import tempfile
import types

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import numpy as np
import soundfile as sf
import mido
from common import midi_signature, completed_voice, load_config
from pipeline import create_job
from v2m_runtime import configure
configure(load_config()['vocal2midi'])
from inference.API.lfa_api import _select_matched_display_tokens
from inference.io.note_io import NoteInfo, _save_midi
from midi_merge import merge_voices

seen = []


def transcription_fixture(config):
    # Stub the costly recognizer, but use the real upstream display-token
    # selection and MIDI writer to exercise the exported lyric events.
    seen.append(config.lyric_output_mode)
    tokens = _select_matched_display_tokens(config.language, config.lyric_output_mode, '你 好', 'ni hao')
    notes = [NoteInfo(.2, .4, 60, tokens[0]), NoteInfo(.6, .8, 64, tokens[1])]
    stem = Path(config.output_filename).stem
    _save_midi(notes, config.output_dir / (stem + '.mid'))
    for suffix in ('csv', 'txt'):
        (config.output_dir / (stem + '.' + suffix)).write_text(' '.join(tokens), encoding='utf-8')


fake_pipeline = types.ModuleType('application.pipeline')
fake_pipeline.run_auto_lyric_job = transcription_fixture
sys.modules['application.pipeline'] = fake_pipeline
from midi_bridge import run

with tempfile.TemporaryDirectory(prefix='chinese-export-', dir=APP / 'work') as folder:
    root = Path(folder)
    audio = np.sin(np.arange(44100) * 2 * np.pi * 220 / 44100) * .03
    input_file = root / 'input.wav'
    sf.write(input_file, audio, 44100)
    events = {}
    for mode, expected in (('hanzi', ['你', '好']), ('pinyin', ['ni', 'hao'])):
        job = create_job(local_music=input_file, parent=root, remember=False, zh_lyric_mode=mode)
        request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
        assert request['zh_lyric_mode'] == mode
        (job / 'audio').mkdir()
        for voice in ('lead', 'backing'):
            sf.write(job / 'audio' / (voice + '.wav'), audio, 44100)
            run(job, voice)
            assert completed_voice(job, request, voice)
            changed = dict(request, zh_lyric_mode='pinyin' if mode == 'hanzi' else 'hanzi')
            assert not completed_voice(job, changed, voice), 'Changed format must invalidate the MIDI cache'
        merged = job / 'midi/voices.mid'
        merge_voices([('Lead Vocal', job / 'midi/lead/lead.mid'),
                      ('Backing Vocal', job / 'midi/backing/backing.mid')], merged)
        midi = mido.MidiFile(merged, charset='utf8')
        for track in midi.tracks[1:]:
            assert [m.text for m in track if m.type == 'lyrics'] == expected
        events[mode] = [(m.type, m.time, getattr(m, 'note', None)) for m in midi if not m.is_meta]
    assert seen == ['hanzi', 'hanzi', 'pinyin', 'pinyin']
    assert events['hanzi'] == events['pinyin'], 'Lyric format must not alter timing or pitches'
    ja = dict(request, language='ja')
    assert midi_signature(job, ja, 'lead') == midi_signature(job, dict(ja, zh_lyric_mode='hanzi'), 'lead')
print('PASS: Chinese characters/pinyin on both vocal tracks, unchanged notes/timing, cache invalidation, Japanese independence')
