"""Unit and E2E tests for VWA 2-hop Full-Package Downloader & Auto-Migrator and Hop-1 bridges."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))

import full_package_migrator as migrator
from tools import build_migrator_bridge as bridge_builder
from tools import update_apply


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_fake_portable_zip(zip_path: Path, version: str = '3.0.0', *, include_qwen: bool = True) -> bytes:
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('VWA/version.json', json.dumps({'version': version, 'channel': 'stable', 'desktop_frontend': 'quick'}))
        zf.writestr('VWA/VWA.exe', b'MZ-fake-vwa-3.0.0')
        zf.writestr('VWA/launch.py', b'print("vwa 3.0.0")\n')
        zf.writestr('VWA/config.example.json', json.dumps({
            'python': '@/dependencies/vocal2midi/python/python.exe',
            'theme': 'dark',
            'ui_language': 'zh-CN',
            'new_300_setting': True,
        }))
        zf.writestr('VWA/components/runtime-features.json', json.dumps({'variant': 'full' if include_qwen else 'jpn'}))
        if include_qwen:
            for rel in migrator.QWEN_WEIGHT_FILES:
                zf.writestr('VWA/' + rel, b'official-qwen-weight')
    return zip_path.read_bytes()


class FullPackageMigratorTests(unittest.TestCase):
    def test_detect_variant_and_split_download_merge_extract_migrate_relaunch(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            # 1. Build a realistic old 2.1.0 JPN installation with rich user data & optional models
            old_root = base / 'VWA-2.1.0'
            (old_root / 'components').mkdir(parents=True)
            (old_root / 'version.json').write_text(json.dumps({'version': '2.1.0'}), encoding='utf-8')
            (old_root / 'components/runtime-features.json').write_text(json.dumps({'variant': 'jpn'}), encoding='utf-8')
            (old_root / 'config.json').write_text(json.dumps({
                'python': str(old_root / 'dependencies/vocal2midi/python/python.exe'),
                'theme': 'light',
                'ui_language': 'ja',
                'last_output_dir': str(old_root / 'jobs/my_song'),
                'custom_pref': 42,
            }), encoding='utf-8')
            (old_root / 'jobs/my_song').mkdir(parents=True)
            (old_root / 'jobs/my_song/request.json').write_text('{"song":"test"}', encoding='utf-8')
            (old_root / 'user-presets').mkdir(parents=True)
            (old_root / 'user-presets/library.json').write_text('{"presets":["p1"]}', encoding='utf-8')
            (old_root / 'cache/accounts/bilibili').mkdir(parents=True)
            (old_root / 'cache/accounts/bilibili/session.json').write_text('{"ok":true}', encoding='utf-8')
            (old_root / 'dependencies/optional/bs-roformer/current-cuda').mkdir(parents=True)
            (old_root / 'dependencies/optional/bs-roformer/current-cuda/installed.json').write_text('{"runtime":"cuda"}', encoding='utf-8')
            (old_root / 'dependencies/optional/bs-roformer-onnx/current-fp16_10s').mkdir(parents=True)
            (old_root / 'dependencies/optional/bs-roformer-onnx/current-fp16_10s/installed.json').write_text('{"precision":"fp16_10s"}', encoding='utf-8')
            # Simulate user manually installed one Chinese Qwen file on JPN
            q0 = old_root / migrator.QWEN_WEIGHT_FILES[0]
            q0.parent.mkdir(parents=True, exist_ok=True)
            q0.write_bytes(b'user-downloaded-qwen')
            # Place handoff marker to verify it gets cleared after migration
            handoff = old_root / migrator.HANDOFF_REL
            handoff.parent.mkdir(parents=True, exist_ok=True)
            handoff.write_text(json.dumps({'target_version': '3.0.0'}), encoding='utf-8')

            self.assertEqual(migrator.detect_install_variant(old_root), 'jpn')

            # 2. Create a 2-part split portable ZIP for 3.0.0 JPN
            full_zip = base / 'VWA-Windows-x64-JPN.zip'
            raw_zip = _make_fake_portable_zip(full_zip, version='3.0.0', include_qwen=False)
            mid = len(raw_zip) // 2
            p1_bytes, p2_bytes = raw_zip[:mid], raw_zip[mid:]
            part_map = {
                'VWA-Windows-x64-JPN.zip.001': p1_bytes,
                'VWA-Windows-x64-JPN.zip.002': p2_bytes,
            }
            fake_meta = {
                'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(raw_zip), 'sha256': _sha256(raw_zip)},
                'parts': [
                    {'name': 'VWA-Windows-x64-JPN.zip.001', 'bytes': len(p1_bytes), 'sha256': _sha256(p1_bytes)},
                    {'name': 'VWA-Windows-x64-JPN.zip.002', 'bytes': len(p2_bytes), 'sha256': _sha256(p2_bytes)},
                ],
            }

            def fake_dl(part, dest, cb):
                data = part_map[part['name']]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                cb(len(data))
                return dest

            target_dir = base / 'VWA-3.0.0'
            launched_roots: list[Path] = []
            with patch.object(migrator, 'launch_migrated_app', side_effect=lambda r: launched_roots.append(r) or True):
                receipt = migrator.run_migration(
                    old_root,
                    '3.0.0',
                    target_dir,
                    variant='auto',
                    auto_launch=True,
                    reader=lambda url, src: fake_meta,
                    downloader=fake_dl,
                )

            self.assertEqual(receipt['source_version'], '2.1.0')
            self.assertEqual(receipt['target_version'], '3.0.0')
            self.assertTrue(receipt['launched'])
            self.assertEqual(launched_roots, [target_dir.resolve()])
            self.assertFalse(handoff.exists())

            # Verify migrated user data & config path rewriting
            new_cfg = json.loads((target_dir / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(new_cfg['theme'], 'light')
            self.assertEqual(new_cfg['ui_language'], 'ja')
            self.assertEqual(new_cfg['custom_pref'], 42)
            self.assertTrue(new_cfg['new_300_setting'])
            self.assertEqual(new_cfg['python'], '@/dependencies/vocal2midi/python/python.exe')
            self.assertEqual(Path(new_cfg['last_output_dir']).resolve(), (target_dir / 'jobs/my_song').resolve())
            self.assertTrue((target_dir / 'jobs/my_song/request.json').is_file())
            self.assertTrue((target_dir / 'user-presets/library.json').is_file())
            self.assertTrue((target_dir / 'cache/accounts/bilibili/session.json').is_file())
            self.assertTrue((target_dir / 'dependencies/optional/bs-roformer/current-cuda/installed.json').is_file())
            self.assertTrue((target_dir / 'dependencies/optional/bs-roformer-onnx/current-fp16_10s/installed.json').is_file())
            self.assertEqual((target_dir / migrator.QWEN_WEIGHT_FILES[0]).read_bytes(), b'user-downloaded-qwen')
            # Old installation stays intact for instant rollback
            self.assertTrue((old_root / 'jobs/my_song/request.json').is_file())

    def test_tampered_part_and_zip_slip_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            plan = {
                'version': '3.0.0',
                'variant': 'jpn',
                'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': 10, 'sha256': _sha256(b'0123456789')},
                'parts': [{'name': 'VWA-Windows-x64-JPN.zip', 'bytes': 10, 'sha256': _sha256(b'0123456789'), 'url': 'x'}],
            }
            with self.assertRaises(ValueError):
                migrator.download_and_merge_package(
                    plan,
                    base / 'cache',
                    downloader=lambda part, dest, cb: (dest.write_bytes(b'TAMPERED!!'), dest)[1],
                )

            evil_zip = base / 'evil.zip'
            with zipfile.ZipFile(evil_zip, 'w') as zf:
                zf.writestr('../escape.txt', b'pwned')
            with self.assertRaises(ValueError):
                migrator.extract_verified_archive(evil_zip, base / 'out')
            self.assertFalse((base / 'escape.txt').exists())

    def test_hop1_bridge_archives_pass_frozen_v2_and_legacy_validators(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            v2_zip = base / 'VWA-Program-Update.zip'
            v2_meta = bridge_builder.build_quick_v2_bridge(v2_zip, target_version='3.0.0')
            self.assertEqual(v2_meta['desktop_frontend'], 'quick')
            self.assertEqual(v2_meta['qt_version'], '6.10.3')
            with zipfile.ZipFile(v2_zip) as zf:
                inv = json.loads(zf.read('update-inventory.json'))
                names = [e['file'] for e in inv['entries']]
                update_apply.validate_quick_program(names)
                for entry in inv['entries']:
                    update_apply.validate_file(entry)
                # Ensure embedded launch.py compiles cleanly
                compile(zf.read('launch.py').decode('utf-8'), 'launch.py', 'exec')

            v1_zip = base / 'BVWA-App-Update.zip'
            v1_meta = bridge_builder.build_legacy_widgets_bridge(v1_zip, target_version='3.0.0')
            self.assertEqual(v1_meta['name'], 'BVWA-App-Update.zip')
            with zipfile.ZipFile(v1_zip) as zf:
                inv1 = json.loads(zf.read('update-inventory.json'))
                self.assertEqual({e['file'] for e in inv1['entries']}, {'app.py', 'launch.py', 'version.json'})
                compile(zf.read('app.py').decode('utf-8'), 'app.py', 'exec')


if __name__ == '__main__':
    unittest.main()
