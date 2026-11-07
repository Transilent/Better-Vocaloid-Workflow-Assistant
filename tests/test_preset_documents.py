"""Portable format conformance, private-field rejection and stable bounded I/O."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import native_path
sys.path.insert(0, str(APP / 'src'))
import preset_documents as documents
from preset_documents import PresetError
from preset_builtin_documents import builtin_documents, example_documents


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.samples = {x['type']: x for x in example_documents()}

    def reject(self, doc, code=None):
        with self.assertRaises(PresetError) as caught:
            documents.validate_document(doc)
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_windows_path_and_handle_timestamps_keep_independent_change_checks(self):
        def info(ctime, size=3, inode=1):
            return SimpleNamespace(st_dev=1, st_ino=inode, st_size=size,
                                   st_mtime_ns=10, st_ctime_ns=ctime)
        with patch.object(documents.os, 'name', 'nt'):
            self.assertTrue(documents._stable_identities(info(1), info(2), info(2), info(1)))
            self.assertFalse(documents._stable_identities(info(1), info(2), info(3), info(1)))
            self.assertFalse(documents._stable_identities(info(1), info(2), info(2), info(3)))
            self.assertFalse(documents._stable_identities(info(1), info(2, inode=2), info(2), info(1)))
            self.assertFalse(documents._stable_identities(info(1), info(2, size=4), info(2), info(1)))

    def test_four_types_roundtrip_and_readonly_schema(self):
        for kind, value in self.samples.items():
            raw = documents.encode_document(value)
            self.assertEqual(documents.decode_document(raw), value)
            self.assertEqual(documents.decode_document(b'\xef\xbb\xbf' + raw), value)
            self.assertEqual(documents.document_digest(raw), __import__('hashlib').sha256(raw).hexdigest())
            self.assertEqual(value['type'], kind)
        schema = documents.schema_document()
        self.assertEqual(schema, json.loads((APP / 'packaging/preset.schema.json').read_bytes()))
        schema['properties'].clear()
        self.assertIn('id', documents.schema_document()['properties'])

    def test_builtins_project_current_catalog_without_machine_or_file_state(self):
        from mixdown_presets import catalog
        from workflow import builtins
        docs = builtin_documents()
        self.assertEqual(len(docs), 10)
        self.assertEqual(len({d['id'] for d in docs}), 10)
        for doc in docs:
            self.reject(doc)
            raw = documents.encode_document(doc, allow_builtin=True)
            self.assertEqual(documents.decode_document(raw, allow_builtin=True), doc)
        for doc, recipe in zip(docs[:6], catalog(), strict=True):
            self.assertEqual(doc['payload']['eq_bands'], recipe['eq_bands'])
            self.assertEqual(doc['payload']['width'], recipe['width'])
            self.assertEqual(doc['payload']['compression_strength'], recipe['compression_strength'])
            self.assertEqual(doc['payload']['track_template']['reverb_send'], recipe['reverb_send'])
        for doc, recipe in zip(docs[6:], builtins().values(), strict=True):
            self.assertEqual(set(doc['payload']['options']), set(documents.PREPARATION_KEYS))
            self.assertEqual(doc['payload']['graph'], recipe['graph'])
            self.assertNotIn('dml_adapter', doc['payload']['options'])
            self.assertNotIn('performance_style', doc['payload']['options'])
        docs[0]['payload']['eq_bands'].clear()
        self.assertEqual(len(builtin_documents()[0]['payload']['eq_bands']), 4)

    def test_unknown_fields_and_private_bindings_rejected(self):
        for name, value in (
            ('source_path', 'C:/private/song.wav'), ('python', 'evil.py'), ('command', 'run'),
            ('accounts', {}), ('$schema', 'https://example.invalid/schema')):
            doc = copy.deepcopy(self.samples['mixdown'])
            doc[name] = value
            self.reject(doc)
            doc = copy.deepcopy(self.samples['mixdown'])
            doc['payload'][name] = value
            self.reject(doc)
        for key in ('track_indices', 'style_regions', 'reference_sources', 'reference_calibration'):
            doc = copy.deepcopy(self.samples['tuning'])
            doc['payload'][key] = []
            self.reject(doc)
        doc = copy.deepcopy(self.samples['preparation'])
        doc['payload']['options']['cpu_threads'] = 64
        self.reject(doc)

    def test_identity_versions_and_metadata_are_strict(self):
        valid = ('0.1.0', '1.2.3-alpha.1', '1.2.3+build.9', '1.2.3-rc.1+local')
        for version in valid:
            doc = copy.deepcopy(self.samples['mixdown'])
            doc['version'] = version
            documents.validate_document(doc)
        for version in ('v1.0.0', '1.0', '01.0.0', '1.0.0-01', '../1.0.0', '1.0.0\n'):
            doc = copy.deepcopy(self.samples['mixdown'])
            doc['version'] = version
            self.reject(doc)
        for identifier in ('builtin.mixdown.clean_lead', '../bad', 'UPPER.case', 'single', 'a.b/c', 'a.b\n'):
            doc = copy.deepcopy(self.samples['mixdown'])
            doc['id'] = identifier
            self.reject(doc)
        for value in ('', ' ', 'x' * 81, '\ud800', '\x00'):
            doc = copy.deepcopy(self.samples['mixdown'])
            doc['metadata']['name'] = value
            self.reject(doc)
        doc = copy.deepcopy(self.samples['style_recipe'])
        doc['metadata']['color'] = 'url(secret)'
        self.reject(doc)

    def test_duplicates_nonfinite_encoding_size_depth(self):
        raw = documents.encode_document(self.samples['mixdown'])
        for broken in (raw.replace(b'"format_version": 1', b'"format_version": 1, "format_version": 1'),
                       raw.replace(b'"width": 1.0', b'"width": NaN'),
                       b'\xff{}', b'{' * 2000, b' ' * (documents.MAX_DOCUMENT_BYTES + 1)):
            with self.assertRaises(PresetError):
                documents.decode_document(broken)
        for value in (float('nan'), float('inf'), True, '0.5'):
            doc = copy.deepcopy(self.samples['mixdown'])
            doc['payload']['compression_strength'] = value
            self.reject(doc)
        doc = copy.deepcopy(self.samples['mixdown'])
        nested = []
        for _ in range(documents.MAX_DEPTH + 1):
            nested = [nested]
        doc['unknown'] = nested
        self.reject(doc, 'too_deep')

    def test_mix_preserves_bypass_values_and_scope(self):
        doc = copy.deepcopy(self.samples['mixdown'])
        doc['payload']['eq_enabled'] = False
        doc['payload']['compression_enabled'] = False
        doc['payload']['compression_strength'] = .333333
        self.assertEqual(documents.decode_document(documents.encode_document(doc))['payload']['compression_strength'], .333333)
        doc['payload']['apply_scope'] = 'vocal_processing'
        self.reject(doc)
        del doc['payload']['width']
        documents.validate_document(doc)
        doc['payload']['eq_bands'][0]['q'] = 3
        self.reject(doc)
        doc = copy.deepcopy(self.samples['mixdown'])
        doc['payload']['eq_bands'][1]['type'] = 'bell'
        self.reject(doc)

    def test_tuning_slots_sources_and_dependency_identity(self):
        doc = copy.deepcopy(self.samples['tuning'])
        self.reject({**doc, 'binding_slots': []})
        doc['payload']['expression_mode'] = 'reference'
        self.reject(doc)
        doc['binding_slots'].append({'id': 'audio', 'kind': 'reference_audio', 'required': True})
        doc['payload']['reference_strength'] = .6
        doc['payload']['reference_controls'] = ['dynamics']
        documents.validate_document(doc)
        doc['payload']['reference_controls'] = ['dynamics', 'dynamics']
        self.reject(doc)
        for mode, slot in (('calibrated', 'calibration_profile'), ('retained', 'retained_project')):
            doc = copy.deepcopy(self.samples['tuning'])
            doc['payload']['expression_mode'] = mode
            doc['binding_slots'].append({'id': 'source', 'kind': slot, 'required': True})
            if mode == 'calibrated':
                doc['payload']['reference_strength'] = .6
            documents.validate_document(doc)
        doc = copy.deepcopy(self.samples['tuning'])
        ref = {'id': 'user.example.soft_ending', 'version': '0.1.0', 'sha256': 'a' * 64}
        doc['payload']['style']['ref'] = ref
        self.reject(doc)
        doc['requires']['dependencies'] = [copy.deepcopy(ref)]
        documents.validate_document(doc)
        doc['requires']['dependencies'][0]['sha256'] = 'b' * 64
        self.reject(doc)

    def test_preparation_does_not_take_machine_options_or_arbitrary_graphs(self):
        doc = copy.deepcopy(self.samples['preparation'])
        doc['payload']['graph']['edges'] = [['source', 'midi']]
        self.reject(doc)
        doc = copy.deepcopy(self.samples['preparation'])
        doc['payload']['options']['voice_mode'] = 'import'
        self.reject(doc)
        doc['binding_slots'] = [{'id': 'stems', 'kind': 'separated_stems', 'required': True}]
        documents.validate_document(doc)
        doc['payload']['options']['alignment_engine'] = 'tifa'
        doc['payload']['options']['midi_backend'] = 'v1'
        self.reject(doc)

    def test_recipe_budget_monotonic_phase_and_no_pitch_or_code(self):
        for points in ([[0, 0], [0, .1], [1, 0]], [[.1, 0], [1, 0]],
                       [[0, 0], [.5, 2], [1, 0]], [[0, 0]]):
            doc = copy.deepcopy(self.samples['style_recipe'])
            doc['payload']['controls'][0]['points'] = points
            self.reject(doc)
        for edit in ('pitch', 'channel', 'operation'):
            doc = copy.deepcopy(self.samples['style_recipe'])
            if edit == 'pitch':
                doc['payload']['pitch']['enabled'] = True
            else:
                doc['payload']['controls'][0][edit] = 'python' if edit == 'operation' else 'gender'
            self.reject(doc)
        doc = copy.deepcopy(self.samples['style_recipe'])
        doc['payload']['controls'] *= 2
        self.reject(doc)

    def test_bounded_regular_read_refuses_redirect_and_mid_read_change(self):
        with tempfile.TemporaryDirectory(dir=native_path(APP / 'work'), prefix='preset-documents-') as folder:
            root = Path(folder)
            path = root / 'sample.json'
            raw = documents.encode_document(self.samples['mixdown'])
            path.write_bytes(raw)
            self.assertEqual(documents.read_document(path), self.samples['mixdown'])
            with self.assertRaises(PresetError):
                documents.read_regular_bytes(root)
            with self.assertRaises(PresetError):
                documents.read_regular_bytes(path, limit=len(raw) - 1)
            original_open = Path.open

            class ChangeOnRead:
                def __init__(self, stream):
                    self.stream = stream
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.stream.close()
                def fileno(self):
                    return self.stream.fileno()
                def read(self, *args):
                    data = self.stream.read(*args)
                    with original_open(path, 'ab') as writer:
                        writer.write(b' ')
                    return data

            def opening(target, mode='r', *args, **kwargs):
                stream = original_open(target, mode, *args, **kwargs)
                return ChangeOnRead(stream) if target.name == path.name and mode == 'rb' else stream

            with patch.object(Path, 'open', opening):
                with self.assertRaises(PresetError) as caught:
                    documents.read_regular_bytes(path)
            self.assertEqual(caught.exception.code, 'file_changed')
            before = path.lstat()
            class Redirect:
                st_mode = before.st_mode
                st_file_attributes = 0x400
            real_lstat = Path.lstat
            with patch.object(Path, 'lstat', lambda p: Redirect() if p.name == root.name else real_lstat(p)):
                with self.assertRaises(PresetError) as caught:
                    documents.read_regular_bytes(path)
            self.assertEqual(caught.exception.code, 'unsafe_path')


if __name__ == '__main__':
    unittest.main()
