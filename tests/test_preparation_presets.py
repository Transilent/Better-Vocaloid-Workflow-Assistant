"""Synthetic coverage for Quick preparation preset save/apply/remove parity."""
import gc
import json
import sys
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))

import preparation_commands as preparation
from preset_documents import PREPARATION_KEYS, PresetError
from preset_library import PresetLibrary
from workflow import DEFAULT_OPTIONS, default_graph


class PreparationPresetTests(unittest.TestCase):
    def setUp(self):
        work = APP / 'work'
        work.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='preparation-presets-', dir=work))
        self.addCleanup(self._cleanup_root)
        self.library = PresetLibrary(self.root / 'library')
        self.legacy = {
            '旧版 · 日语字幕': {
                'graph': default_graph(True),
                'options': dict(DEFAULT_OPTIONS, language='ja'),
            }
        }

    def _cleanup_root(self):
        if self.root.parent != APP / 'work' or not self.root.name.startswith('preparation-presets-'):
            raise RuntimeError('Test cleanup path escaped its owned directory.')
        self.library = None
        gc.collect()
        for attempt in range(20):
            try:
                shutil.rmtree(self.root)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(.1 * (attempt + 1))

    @staticmethod
    def draft(name=None):
        value = preparation.PreparationDraft(music_path='C:/synthetic/current.wav')
        value = value.edit('input_mode', 'local')
        value = value.edit('source', 'private-source-token')
        value = value.edit('lyrics', 'private lyric text')
        value = value.edit('output_directory', 'C:/private/output')
        value = value.edit('target', 'subtitles')
        value = value.edit('language', 'ja')
        value = value.edit('voice_mode', 'single')
        value = value.edit('midi_steps', 32)
        value = value.edit('project_formats', ['svp'])
        value = value.edit('audio_formats', ['flac'])
        return value

    def local_entries(self):
        return [row for row in self.library.snapshot()['entries'] if not row['builtin']]

    def test_append_keeps_builtins_prior_saves_and_legacy_catalog(self):
        before = self.library.snapshot()
        first = preparation.append_preset(self.library, self.draft(), '合成准备一')
        second = preparation.append_preset(self.library, self.draft(), '合成准备二')

        after = self.library.snapshot()
        self.assertEqual(after['entries'][:len(before['entries'])], before['entries'])
        local = self.local_entries()
        self.assertEqual(len(local), 2)
        self.assertEqual({row['id'] for row in local},
                         {first['entry']['id'], second['entry']['id']})
        self.assertNotEqual(first['entry']['id'], second['entry']['id'])

        entries = preparation.preset_catalog(after, self.legacy)
        self.assertEqual(sum(row['type'] == 'preparation' for row in before['entries']), 4)
        self.assertEqual(sum(row['type'] == 'preparation' for row in after['entries']), 6)
        self.assertTrue(any(row['source'] == 'legacy' and row['preset_name'] == '旧版 · 日语字幕'
                            for row in entries))
        self.assertTrue(all(not row['removable'] for row in entries if row['builtin']))
        self.assertTrue(all(row['removable'] for row in entries if not row['builtin']))

    def test_document_is_portable_and_apply_retains_current_inputs(self):
        added = preparation.append_preset(self.library, self.draft(), '合成应用')
        document = self.library.get_document(added['entry']['id'], added['entry']['version'])
        self.assertEqual(document['type'], 'preparation')
        self.assertEqual(set(document['payload']['options']), set(PREPARATION_KEYS))
        self.assertIn('subtitles', document['payload']['graph']['nodes'])
        rendered = repr(document)
        self.assertNotIn('private-source-token', rendered)
        self.assertNotIn('private lyric text', rendered)
        self.assertNotIn('C:/private/output', rendered)
        self.assertNotIn('current.wav', rendered)

        current = preparation.PreparationDraft(music_path='D:/keep/song.wav')
        current = current.edit('input_mode', 'local')
        current = current.edit('source', 'keep-source')
        current = current.edit('lyrics', 'keep lyrics')
        current = current.edit('reference_mode', 'netease')
        applied = preparation.apply_preset(current, document['payload'])
        self.assertEqual(applied.music_path, 'D:/keep/song.wav')
        self.assertEqual(applied.view['source'], 'keep-source')
        self.assertEqual(applied.view['lyrics'], 'keep lyrics')
        self.assertEqual(applied.view['reference_mode'], 'netease')
        self.assertEqual(applied.view['target'], 'subtitles')
        self.assertEqual(applied.view['language'], 'ja')
        self.assertEqual(applied.view['midi_steps'], 32)

    def test_remove_only_selected_user_row_and_reject_builtin(self):
        first = preparation.append_preset(self.library, self.draft(), '删除目标')
        second = preparation.append_preset(self.library, self.draft(), '保留目标')
        preparation.remove_preset(self.library, first['entry']['id'], first['entry']['version'])

        after = self.library.snapshot()
        local = [row for row in after['entries'] if not row['builtin']]
        self.assertEqual([row['id'] for row in local], [second['entry']['id']])
        catalog = preparation.preset_catalog(after, self.legacy)
        self.assertTrue(any(row['source'] == 'legacy' for row in catalog))
        builtin = next(row for row in catalog if row['builtin'])
        self.assertFalse(builtin['removable'])

        with self.assertRaises(PresetError) as caught:
            preparation.remove_preset(self.library, builtin['id'], builtin['version'])
        self.assertEqual(caught.exception.code, 'preset_builtin_readonly')

    def test_invalid_custom_name_is_rejected_before_write(self):
        revision = self.library.snapshot()['revision']
        with self.assertRaises(ValueError):
            preparation.append_preset(self.library, self.draft(), '内置 · 不允许')
        self.assertEqual(self.library.snapshot()['revision'], revision)

    def test_restart_settings_persist_choices_but_not_song_or_lyrics(self):
        root = self.root
        state = {}
        configured = self.draft()
        configured = configured.edit('input_mode', 'bilibili')
        configured = configured.edit('dml_adapter', 2)
        configured = configured.edit('cpu_threads', 8)
        configured = configured.edit('advanced_expanded', True)
        with (patch.object(preparation, 'ROOT', root),
              patch.object(preparation, 'workspace_state', return_value=state)):
            preparation.save_draft_settings(configured)
            saved = json.loads((root / 'cache/workspace.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['quick_preparation']['options']['dml_adapter'], 2)
            self.assertEqual(saved['quick_preparation']['options']['cpu_threads'], 8)
            serialized = json.dumps(saved, ensure_ascii=False)
            self.assertNotIn('private-source-token', serialized)
            self.assertNotIn('private lyric text', serialized)
            self.assertNotIn('current.wav', serialized)
            self.assertEqual(saved['quick_preparation']['output_directory'], 'C:/private/output')

            restored = preparation.restore_draft_settings(preparation.PreparationDraft())
        self.assertEqual(restored.view['dml_adapter'], 2)
        self.assertEqual(restored.view['cpu_threads'], 8)
        self.assertTrue(restored.view['advanced_expanded'])
        self.assertEqual(restored.view['language'], 'ja')
        self.assertEqual(restored.view['target'], 'subtitles')
        self.assertEqual(restored.view['output_directory'], 'C:/private/output')
        self.assertEqual(restored.view['input_mode'], 'bilibili')
        self.assertEqual(restored.music_path, '')
        self.assertEqual(restored.view['source'], '')
        self.assertEqual(restored.view['lyrics'], '')

    def test_intermediate_bpm_keeps_last_valid_settings(self):
        root = self.root
        state = {}
        valid = preparation.PreparationDraft().edit('cpu_threads', 4)
        with (patch.object(preparation, 'ROOT', root),
              patch.object(preparation, 'workspace_state', return_value=state)):
            preparation.save_draft_settings(valid)
            previous = json.loads(json.dumps(state))
            preparation.save_draft_settings(valid.edit('bpm', '12e'))
        self.assertEqual(state, previous)

    def test_inference_choices_are_bounded(self):
        value = preparation.PreparationDraft().edit('dml_adapter', 3).edit('cpu_threads', 16)
        self.assertEqual(value.view['dml_adapter'], 3)
        self.assertEqual(value.view['cpu_threads'], 16)
        invalid = (('dml_adapter', True), ('dml_adapter', -1), ('cpu_threads', True),
                   ('cpu_threads', -1), ('cpu_threads', 65))
        for name, item in invalid:
            with self.subTest(name=name, item=item), self.assertRaises(ValueError):
                preparation.PreparationDraft().edit(name, item)

    @staticmethod
    def write_wav(path, *, channels=1):
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((channels, 2, 16000, 0, 'NONE', 'not compressed'))
            stream.writeframes(bytes(320 * channels))
        return path

    def test_wav_set_is_validated_before_command_side_effects(self):
        root = self.root
        source = self.write_wav(root / 'source.wav')
        lead = self.write_wav(root / 'lead.wav')
        backing = self.write_wav(root / 'backing.wav')
        broken = root / 'instrumental.wav'
        broken.write_bytes(b'not a wav file')
        output = root / 'jobs'
        value = preparation.PreparationDraft(music_path=str(source))
        value = value.edit('input_mode', 'local')
        value = value.edit('output_directory', str(output))
        value = value.edit('voice_mode', 'import')
        value = value.set_stem('lead', str(lead))
        value = value.set_stem('backing', str(backing))
        value = value.set_stem('instrumental', str(broken))
        with self.assertRaisesRegex(ValueError, '完整 WAV'):
            preparation.create_from_draft(value)
        self.assertFalse(output.exists())

        not_wav = root / 'instrumental.mp3'
        not_wav.write_bytes(b'fake')
        value = value.set_stem('instrumental', str(not_wav))
        with self.assertRaisesRegex(ValueError, '须为 WAV'):
            preparation.validate_imported_stems(value)

        value = value.set_stem('instrumental', str(self.write_wav(root / 'instrumental-ok.wav')))
        validated = preparation.validate_imported_stems(value)
        self.assertEqual(set(validated), {'lead', 'backing', 'instrumental'})


if __name__ == '__main__':
    unittest.main(verbosity=2)