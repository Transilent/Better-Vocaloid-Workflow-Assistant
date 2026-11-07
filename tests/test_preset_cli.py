"""Exercise the public local-library CLI with an isolated directory."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import native_path


class CliTests(unittest.TestCase):
    def test_external_document_roundtrip_conflict_and_local_only_changes(self):
        with tempfile.TemporaryDirectory(dir=native_path(APP / 'work'), prefix='preset-cli-') as folder:
            root = Path(folder)
            library = root / 'library'
            env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')

            def run(*args, code=0):
                result = subprocess.run(
                    [sys.executable, '-B', str(APP / 'preset_cli.py'), '--root', str(library), *args],
                    capture_output=True, env=env, timeout=30)
                self.assertEqual(result.returncode, code, result.stderr.decode('utf-8', errors='replace'))
                return json.loads((result.stdout if code == 0 else result.stderr).decode('utf-8'))

            empty = run('list')
            self.assertEqual(len(empty['entries']), 10)
            self.assertFalse(library.exists(), 'Listing must not create a user library')
            builtin = next(e for e in empty['entries'] if e['builtin'])
            builtin_source = root / 'builtin-export.vwa-preset.json'
            run('export', builtin['id'], builtin['version'], str(builtin_source))
            builtin_bytes = builtin_source.read_bytes()
            builtin_valid = run('validate', str(builtin_source))
            self.assertTrue(builtin_valid['valid'])
            self.assertTrue(builtin_valid['will_copy'])
            builtin_imported = run('import', str(builtin_source))
            self.assertTrue(builtin_imported['copied'])
            self.assertTrue(builtin_imported['will_copy'])
            self.assertFalse(builtin_imported['already_present'])
            self.assertTrue(builtin_imported['entry']['id'].startswith('user.'))
            self.assertEqual(builtin_imported['entry']['version'], '0.1.0')
            self.assertEqual(
                builtin_imported['source_sha256'],
                hashlib.sha256(builtin_bytes).hexdigest(),
            )
            self.assertEqual(builtin_source.read_bytes(), builtin_bytes)

            sample = run('example', 'mixdown')
            raw = (json.dumps(sample, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            source = root / 'sample.vwa-preset.json'
            source.write_bytes(raw)
            valid = run('validate', str(source))
            self.assertTrue(valid['valid'])
            self.assertFalse(valid['applied'])
            self.assertFalse(valid['will_copy'])
            imported = run('import', str(source))
            self.assertFalse(imported['already_present'])
            again = run('import', str(source))
            self.assertTrue(again['already_present'])
            identifier, version = sample['id'], sample['version']
            run('rename', identifier, version, '--alias', 'My local label')
            run('favorite', identifier, version, 'on')
            installed = next(e for e in run('list')['entries'] if e['id'] == identifier)
            self.assertEqual(installed['alias'], 'My local label')
            self.assertTrue(installed['favorite'])
            target = root / 'shared.vwa-preset.json'
            run('export', identifier, version, str(target))
            self.assertEqual(target.read_bytes(), raw, 'Local labels must not change the exported package')
            run('export', identifier, version, str(target), code=2)
            sample['metadata']['name'] = 'Conflicting content'
            source.write_text(json.dumps(sample), encoding='utf-8')
            conflict = run('import', str(source), code=2)
            self.assertEqual(conflict['error']['code'], 'preset_version_conflict')
            self.assertEqual(
                conflict['error']['details']['alternative'], 'as_copy')
            self.assertIn('独立副本', conflict['error']['message'])
            preserved = next(e for e in run('list')['entries']
                             if e['id'] == identifier and e['version'] == version)
            self.assertEqual(preserved['alias'], 'My local label')
            self.assertTrue(preserved['favorite'])
            independent = run('import', '--as-copy', str(source))
            self.assertTrue(independent['copied'])
            self.assertTrue(independent['will_copy'])
            self.assertNotEqual(independent['entry']['id'], identifier)
            self.assertTrue(independent['entry']['id'].startswith('user.'))
            self.assertEqual(independent['entry']['version'], '0.1.0')
            self.assertEqual(independent['source_sha256'],
                             hashlib.sha256(source.read_bytes()).hexdigest())
            copied = run('copy', builtin['id'], builtin['version'], '--name', 'My variant')
            self.assertTrue(copied['entry']['id'].startswith('user.'))
            run('delete', identifier, version)
            self.assertFalse(any(e['id'] == identifier for e in run('list')['entries']))
            self.assertEqual(target.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
