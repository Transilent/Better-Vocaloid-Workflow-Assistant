"""Japanese alignment failures must not shift lyrics or break a whole MIDI job."""
import sys
import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
os.environ['NUMBA_CACHE_DIR'] = str(APP / 'work/test-numba-cache')
sys.path[:0] = [str(APP), str(APP / 'dependencies/vocal2midi')]
from inference.API.hfa_api import _repair_short_word_boundaries, run_hubert_fa
from inference.API.game_api import extract_vowel_boundaries
from inference.HubertFA.tools.align_word import Word, WordList
from inference.HubertFA.tools.infer_base import InferenceBase
from midi_merge import merge_voices
from midi_checks import inspect_midi
import mido
import numpy as np


def word(start, end, text):
    return Word(start, end, text, init_phoneme=True)


class JapaneseMidiTests(unittest.TestCase):
    def test_short_word_does_not_bridge_long_silence(self):
        words = [word(0, .8, 'a'), word(.8, .81, 'i'),
                 word(.81, 5, 'SP'), word(5, 5.8, 'u')]
        _repair_short_word_boundaries(words, max_gap_sec=.15)
        self.assertEqual(words[1].end, .81)
        self.assertEqual(len(words), 4)

    def test_short_gap_can_still_be_repaired(self):
        words = [word(0, .8, 'a'), word(.8, .81, 'i'),
                 word(.81, .9, 'SP'), word(.9, 1.7, 'u')]
        logs = _repair_short_word_boundaries(words, max_gap_sec=.15)
        self.assertTrue(logs)
        self.assertEqual(words[1].end, .9)
        self.assertEqual(len(words), 3)

    def test_single_alignment_error_preserves_other_chunks(self):
        class Model:
            vocab_folder = Path('.')
            def get_dataset(self, **kwargs):
                self.dataset = [(Path('good.wav'),), (Path('bad.wav'),), (Path('later.wav'),)]
                self.dataset_errors = {}
            def infer(self, **kwargs):
                for item in self.dataset:
                    if item[0].stem == 'bad':
                        raise ValueError('Invalid phoneme boundary')
                    self.predictions.append((item[0], 1.0, WordList([word(0, 1, 'a')])))
        model = Model()
        result = run_hubert_fa(model, Path('.'), language='ja')
        self.assertEqual(set(result), {'good', 'later'})
        self.assertIn('bad', model.alignment_errors)
        self.assertEqual(len(model.dataset), 3)

    def test_alignment_cancellation_is_not_swallowed(self):
        class Model:
            vocab_folder = Path('.')
            def get_dataset(self, **kwargs):
                self.dataset = [(Path('one.wav'),)]
            def infer(self, **kwargs):
                raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            run_hubert_fa(Model(), Path('.'), language='ja')

    def test_dictionary_keeps_supported_bare_japanese_phone(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work') as temp:
            root = Path(temp)
            (root / 'dict.txt').write_text('shi\tsh i\n', encoding='utf-8')
            (root / 'one.wav').write_bytes(b'fixture')
            (root / 'one.lab').write_text('k shi', encoding='utf-8')
            model = InferenceBase()
            model.vocab = {'language_prefix': True, 'dictionaries': {},
                           'vocab': {'SP': 0, 'ja/k': 1, 'ja/sh': 2, 'ja/i': 3}}
            model.vocab_folder = root
            model.get_dataset(root, 'ja', dictionary_path=root / 'dict.txt')
            self.assertEqual(model.dataset[0][2], ['k', 'shi'])
            self.assertFalse(model.dataset_errors)

    def test_invalid_lab_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work') as temp:
            root = Path(temp)
            (root / 'dict.txt').write_text('a\ta\n', encoding='utf-8')
            (root / 'bad.wav').write_bytes(b'fixture')
            (root / 'bad.lab').write_text('unsupported', encoding='utf-8')
            model = InferenceBase()
            model.vocab = {'language_prefix': True, 'dictionaries': {}, 'vocab': {'SP': 0, 'ja/a': 1}}
            model.vocab_folder = root
            model.get_dataset(root, 'ja', dictionary_path=root / 'dict.txt')
            self.assertEqual(model.dataset, [])
            self.assertIn('bad', model.dataset_errors)

    def test_kana_after_unvoiced_mora_stays_on_correct_word(self):
        words = [word(0, .1, 'cl'), word(.1, .5, 'a'), word(.5, .9, 'i')]
        _, voiced, lyrics = extract_vowel_boundaries(words, ['っ', 'あ', 'い'], language='ja')
        self.assertEqual([text for text, flag in zip(lyrics, voiced) if flag], ['あ', 'い'])

    def test_pipeline_distinguishes_empty_asr_and_failed_alignment(self):
        from application.config import PipelineConfig
        import inference.pipeline.auto_lyric_hybrid as hybrid
        from inference.io.note_io import NoteInfo
        with tempfile.TemporaryDirectory(dir=APP / 'work') as temp:
            root = Path(temp)
            chunks = [{'waveform': np.zeros(44100, dtype=np.float32), 'offset': float(i)} for i in range(3)]
            model = SimpleNamespace(alignment_errors={'chunk_2': 'Invalid phoneme boundary'})
            cfg = PipelineConfig(audio_path='fixture.wav', output_filename='lead.wav', output_dir=root,
                                 game_model_dir='fixture', hfa_model_dir='fixture', asr_model_path='fixture',
                                 device='cpu', language='ja', ts=[0.0], quantization_step=0,
                                 output_formats=['mid'], phoneme_asr_model_path='fixture')
            with patch.object(hybrid.tempfile, 'tempdir', str(root)), \
                 patch.object(hybrid.librosa, 'load', return_value=(np.zeros(44100), 44100)), \
                 patch.object(hybrid, 'slice_audio', return_value=chunks), \
                 patch.object(hybrid, 'run_romaji_asr', return_value=({'chunk_0': ['a'], 'chunk_2': ['i']}, [])) as asr, \
                 patch.object(hybrid, 'run_qwen_asr_and_fa', side_effect=AssertionError('Japanese weights must be retained')), \
                 patch.object(hybrid, 'load_hfa_model', return_value=model), \
                 patch.object(hybrid, 'run_hubert_fa', return_value={'chunk_0': ('fixture', 1, [])}), \
                 patch.object(hybrid, 'export_hfa_artifacts'), \
                 patch.object(hybrid, 'load_game_model', return_value=object()), \
                 patch.object(hybrid, 'extract_pitches_and_align_torch', return_value=([NoteInfo(.2, .4, 60, 'a')], {0})), \
                 patch.object(hybrid, 'extract_pitches_only_torch', return_value=[NoteInfo(1.2, 1.4, 62), NoteInfo(2.2, 2.4, 64)]) as fallback:
                hybrid.auto_lyric_hybrid_pipeline(**cfg.to_kwargs())
            self.assertEqual(asr.call_args.kwargs['language'], 'ja')
            self.assertEqual([c['offset'] for c in fallback.call_args.args[0]], [1.0, 2.0])
            report = json.loads((root / 'lead_alignment.json').read_text(encoding='utf-8'))
            self.assertEqual(report['asr_engine'], 'romaji_asr')
            self.assertEqual(report['aligned_chunks'], 1)
            self.assertEqual(report['asr_empty_chunks'], 1)
            self.assertEqual(report['alignment_failed_chunks'], 1)
            self.assertEqual(report['pitch_only_chunks'], 2)
            self.assertEqual([c['reason'] for c in report['chunks']], [None, 'asr_empty_or_failed', 'alignment_failed'])
            self.assertEqual(inspect_midi(root / 'lead.mid')['notes'], 3)

    def test_incomplete_midi_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work') as temp:
            path = Path(temp) / 'incomplete.mid'
            path.write_bytes(b'MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0'
                             b'MTrk\x00\x00\x00\x09\x00\x90\x3c\x64\x83\x60\x80\x3c\x00')
            with self.assertRaisesRegex(ValueError, '结束'):
                inspect_midi(path)

    def test_merged_midi_is_valid_and_has_full_length_tempo_track(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work') as temp:
            root = Path(temp)
            paths = []
            for voice, pitch, lyric in [('lead', 60, 'あ'), ('backing', 67, 'い')]:
                midi = mido.MidiFile(type=0, charset='utf8')
                midi.tracks.append(mido.MidiTrack([
                    mido.MetaMessage('set_tempo', tempo=500000),
                    mido.MetaMessage('lyrics', text=lyric, time=960),
                    mido.Message('note_on', note=pitch, velocity=100),
                    mido.Message('note_off', note=pitch, time=480)]))
                path = root / (voice + '.mid')
                midi.save(path)
                paths.append((voice, path))
            destination = root / 'voices.mid'
            merge_voices(paths, destination)
            midi = mido.MidiFile(destination, charset='utf8')
            self.assertEqual(len(midi.tracks), 3)
            self.assertEqual([sum(m.time for m in t) for t in midi.tracks], [1440, 1440, 1440])
            self.assertEqual(inspect_midi(destination)['notes'], 2)
            self.assertFalse(any(m.type == 'sysex' for t in midi.tracks for m in t))
            before = destination.read_bytes()
            with patch('midi_checks.inspect_midi', side_effect=ValueError('Invalid MIDI')):
                with self.assertRaises(ValueError):
                    merge_voices(paths, destination)
            self.assertEqual(destination.read_bytes(), before)
            self.assertFalse(destination.with_name(destination.name + '.bvwa-partial').exists())


if __name__ == '__main__':
    (APP / 'work').mkdir(exist_ok=True)
    unittest.main()
