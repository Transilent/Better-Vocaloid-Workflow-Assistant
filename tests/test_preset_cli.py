"""Exercise the public local-library CLI with an isolated directory."""
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
            sample = run('example', 'mixdown')
            raw = (json.dumps(sample, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
            source = root / 'sample.vwa-preset.json'
            source.write_bytes(raw)
            valid = run('validate', str(source))
            self.assertTrue(valid['valid'])
            self.assertFalse(valid['applied'])
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
            builtin = next(e for e in empty['entries'] if e['builtin'])
            copied = run('copy', builtin['id'], builtin['version'], '--name', 'My variant')
            self.assertTrue(copied['entry']['id'].startswith('user.'))
            run('delete', identifier, version)
            self.assertFalse(any(e['id'] == identifier for e in run('list')['entries']))
            self.assertEqual(target.read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
