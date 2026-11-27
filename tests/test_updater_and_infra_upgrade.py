"""L0 unit tests for upgraded 2.x updater, runtime patch, full-update push, storage, and diagnostics."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import appearance_state
import operation_diagnostics
import storage
import updater
import workspace_settings
from tools import update_apply as ua


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_quick_root(root: Path, *, version: str = '2.1.0', extra_deps: dict[str, bytes] | None = None) -> tuple[str, dict]:
    (root / 'version.json').write_text(
        json.dumps({'version': version, 'channel': 'stable', 'desktop_frontend': 'quick', 'qt_version': '6.10.3'}),
        encoding='utf-8',
    )
    (root / 'dependencies').mkdir(parents=True, exist_ok=True)
    dep_files = {
        'dependencies/export/libresvip/__init__.py': b'__version__ = "2.9.2"\n',
        'dependencies/export/libresvip/old_mod.py': b'legacy = True\n',
        'dependencies/ffmpeg/ffmpeg.exe': b'FFMPEG_OLD',
    }
    if extra_deps:
        dep_files.update(extra_deps)
    manifest_entries = []
    for rel, raw in sorted(dep_files.items()):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        manifest_entries.append({'file': rel, 'bytes': len(raw), 'sha256': _sha(raw)})
    manifest = {'format': 1, 'entries': manifest_entries}
    mraw = json.dumps(manifest, indent=2).encode('utf-8')
    (root / 'dependencies/manifest.json').write_bytes(mraw)
    for rel, raw in (
        ('launch.py', b'# launch old\n'),
        ('quick_app.py', b'# quick old\n'),
        ('quick_runtime.py', b'# runtime old\n'),
        ('quick_ui/Main.qml', b'Item {}\n'),
        ('updater.py', b'# updater old\n'),
        ('tools/update_apply.py', (APP / 'tools/update_apply.py').read_bytes()),
        ('obsolete_module.py', b'# old removable module\n'),
        ('user_edited_obsolete.py', b'# user customized\n'),
        ('config.json', b'{"theme":"dark"}'),
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    return _sha(mraw), manifest


class UpdaterAndInfraUpgradeTests(unittest.TestCase):
    def test_01_layered_allowlist_policy_and_security_boundaries(self):
        # Future Quick app modules & QML views are allowed without editing ROOT_FILES
        for allowed in (
            'future_feature_service.py',
            'quick_ui/FuturePanel.qml',
            'quick_ui/FutureHelper.js',
            'assets/new-icon.svg',
            'assets/i18n/ko.json',
            'components/native-audio/vwa_chorus_pitch.dll',
        ):
            ua.validate_file({'file': allowed, 'bytes': 128, 'sha256': 'a' * 64})

        # Unlisted scripts, docs, nested assets, and user data remain strictly rejected
        for forbidden in (
            'docs/Unlisted.md',
            'tools/evil.ps1',
            'tools/Setup-AgentMCP.ps1.bak',
            'packaging/evil.json',
            'assets/sub/evil.svg',
            'config.json',
            'jobs/song.mid',
            'dependencies/export/x.py',
            'components/native-audio/evil.dll',
            '../escape.py',
        ):
            with self.assertRaises(ValueError, msg=f'Should reject {forbidden}'):
                ua.validate_file({'file': forbidden, 'bytes': 128, 'sha256': 'a' * 64})
        # Quick program validation still strictly rejects legacy Widgets entry points
        with self.assertRaises(ValueError):
            ua.validate_quick_program(set(ua.QUICK_PROGRAM_REQUIRED) | {'app.py'})

    def test_02_program_update_removed_cleanup_stale_temp_self_heal_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix='vwa-up-rem-') as tmp:
            root = Path(tmp)
            runtime_sha, _ = _make_quick_root(root)
            obsolete_bytes = (root / 'obsolete_module.py').read_bytes()
            # Simulate stale temp file left by a prior interrupted attempt while target is intact
            (root / 'launch.py.vwa-update').write_bytes(b'stale partial')

            payload_files = {
                'launch.py': b'# launch new\n',
                'quick_app.py': b'# quick new\n',
                'quick_runtime.py': b'# runtime new\n',
                'quick_ui/Main.qml': b'Item { objectName: "v22" }\n',
                'quick_ui/BrandNewPage.qml': b'Item { objectName: "new" }\n',
                'brand_new_module.py': b'VALUE = 42\n',
                'updater.py': b'# updater new\n',
                'tools/update_apply.py': (APP / 'tools/update_apply.py').read_bytes(),
                'version.json': json.dumps({
                    'version': '2.2.0', 'channel': 'stable', 'desktop_frontend': 'quick', 'qt_version': '6.10.3'
                }).encode('utf-8'),
            }
            removed = [
                {'file': 'obsolete_module.py', 'bytes': len(obsolete_bytes), 'sha256': _sha(obsolete_bytes)},
                # Mismatched hash: user edited this file, so updater must skip deleting it
                {'file': 'user_edited_obsolete.py', 'bytes': 10, 'sha256': 'f' * 64},
            ]
            buf = io.BytesIO()
            entries = [{'file': k, 'bytes': len(v), 'sha256': _sha(v)} for k, v in payload_files.items()]
            with zipfile.ZipFile(buf, 'w') as zf:
                for k, v in payload_files.items():
                    zf.writestr(k, v)
                zf.writestr('update-inventory.json', json.dumps({
                    'version': '2.2.0', 'runtime_sha256': runtime_sha,
                    'desktop_frontend': 'quick', 'qt_version': '6.10.3',
                    'entries': entries, 'removed': removed,
                }))
            archive_bytes = buf.getvalue()
            release = {
                'version': '2.2.0', 'available': True, 'notes': 'Release notes 2.2.0',
                'update': {
                    'name': 'VWA-Program-Update.zip', 'bytes': len(archive_bytes),
                    'sha256': _sha(archive_bytes), 'runtime_sha256': runtime_sha,
                    'desktop_frontend': 'quick', 'qt_version': '6.10.3',
                },
            }

            def fake_dl(item, folder, progress, check, **kwargs):
                folder.mkdir(parents=True, exist_ok=True)
                p = folder / item['name']
                p.write_bytes(archive_bytes)
                return p

            plan = updater.prepare_update(release, root=root, downloader=fake_dl)
            # Verify last-notice.json was persisted for restart recovery
            notice = json.loads((root / 'cache/updates/last-notice.json').read_text(encoding='utf-8'))
            self.assertEqual(notice['version'], '2.2.0')
            self.assertIn('Release notes 2.2.0', notice['notes'])

            result = ua.apply_update(plan)
            self.assertEqual(result['state'], 'passed')
            self.assertEqual(result['removed'], 1)
            self.assertEqual(result['skipped_removals'], ['user_edited_obsolete.py'])
            self.assertFalse((root / 'obsolete_module.py').exists())
            self.assertTrue((root / 'user_edited_obsolete.py').is_file())
            self.assertTrue((root / 'brand_new_module.py').is_file())
            self.assertTrue((root / 'quick_ui/BrandNewPage.qml').is_file())
            self.assertFalse((root / 'launch.py.vwa-update').exists())
            self.assertEqual((root / 'config.json').read_bytes(), b'{"theme":"dark"}')

            # Write passed last-result and verify load_update_recovery surfaces just_updated + cleans old stages
            (root / 'cache/updates/last-result.json').write_text(json.dumps(result), encoding='utf-8')
            rec = workspace_settings.load_update_recovery(root)
            self.assertEqual(rec['just_updated']['version'], '2.2.0')
            self.assertIn('Release notes 2.2.0', rec['just_updated']['notes'])
            self.assertEqual(list((root / 'cache/updates').glob('stage-*')), [])

    def test_03_runtime_patch_2x_apply_and_boundary_guard(self):
        with tempfile.TemporaryDirectory(prefix='vwa-rpatch-') as tmp:
            root = Path(tmp)
            src_sha, base_manifest = _make_quick_root(root)
            new_lib_bytes = b'__version__ = "2.10.0"\n'
            new_manifest_entries = [
                {'file': 'dependencies/export/libresvip/__init__.py', 'bytes': len(new_lib_bytes), 'sha256': _sha(new_lib_bytes)},
                {'file': 'dependencies/ffmpeg/ffmpeg.exe', 'bytes': len(b'FFMPEG_OLD'), 'sha256': _sha(b'FFMPEG_OLD')},
            ]
            new_manifest_bytes = json.dumps({'format': 1, 'entries': new_manifest_entries}, indent=2).encode('utf-8')
            dst_sha = _sha(new_manifest_bytes)
            old_mod_bytes = (root / 'dependencies/export/libresvip/old_mod.py').read_bytes()

            patch_files = {
                'version.json': json.dumps({
                    'version': '2.2.0', 'channel': 'stable', 'desktop_frontend': 'quick', 'qt_version': '6.10.3'
                }).encode('utf-8'),
                'dependencies/export/libresvip/__init__.py': new_lib_bytes,
                'dependencies/manifest.json': new_manifest_bytes,
            }
            removed = [
                {'file': 'dependencies/export/libresvip/old_mod.py', 'bytes': len(old_mod_bytes), 'sha256': _sha(old_mod_bytes)},
            ]
            buf = io.BytesIO()
            entries = [{'file': k, 'bytes': len(v), 'sha256': _sha(v)} for k, v in patch_files.items()]
            with zipfile.ZipFile(buf, 'w') as zf:
                for k, v in patch_files.items():
                    zf.writestr(k, v)
                zf.writestr('runtime-patch.json', json.dumps({
                    'version': '2.2.0', 'desktop_frontend': 'quick', 'qt_version': '6.10.3',
                    'source_runtime_sha256': src_sha, 'target_runtime_sha256': dst_sha,
                    'entries': entries, 'removed': removed,
                }))
            archive_bytes = buf.getvalue()
            inventory = {
                'version': '2.2.0',
                'desktop_frontend': 'quick',
                'qt_version': '6.10.3',
                'runtime_patches': [{
                    'name': 'VWA-Runtime-Patch-v2.2.0.zip',
                    'bytes': len(archive_bytes),
                    'sha256': _sha(archive_bytes),
                    'source_runtime_sha256': src_sha,
                    'target_runtime_sha256': dst_sha,
                    'desktop_frontend': 'quick',
                    'qt_version': '6.10.3',
                }],
            }
            chosen = updater.compatible_update(inventory, root=root)
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen['kind'], 'runtime_patch')

            def fake_dl(item, folder, progress, check, **kwargs):
                folder.mkdir(parents=True, exist_ok=True)
                p = folder / item['name']
                p.write_bytes(archive_bytes)
                return p

            plan = updater.prepare_update({'version': '2.2.0', 'notes': 'LibreSVIP 2.10', 'update': chosen},
                                          root=root, downloader=fake_dl)
            res = ua.apply_update(plan)
            self.assertEqual(res['state'], 'passed')
            self.assertTrue(res['runtime_patched'])
            self.assertEqual(res['removed'], 1)
            self.assertEqual((root / 'dependencies/export/libresvip/__init__.py').read_bytes(), new_lib_bytes)
            self.assertFalse((root / 'dependencies/export/libresvip/old_mod.py').exists())
            self.assertEqual(_sha((root / 'dependencies/manifest.json').read_bytes()), dst_sha)

            # Out-of-scope dependency path (e.g. python core) must be rejected
            with self.assertRaises(ValueError):
                ua.validate_runtime_patch_entry({
                    'file': 'dependencies/vocal2midi/python/python.exe', 'bytes': 10, 'sha256': 'a' * 64
                })

    def test_04_universal_full_package_required_push_across_new_and_legacy_clients(self):
        with tempfile.TemporaryDirectory(prefix='vwa-fullpush-') as tmp:
            root = Path(tmp)
            _make_quick_root(root, version='2.1.0')
            notice = '【重要 · 本次需要全量更新】请保留 jobs/ 与 config.json，下载最新完整便携包。'
            inventory = {
                'version': '2.2.0',
                'desktop_frontend': 'quick',
                'qt_version': '6.10.3',
                'full_package_required': True,
                'full_package_notice': notice,
                'min_inplace_version': '2.2.0',
                'release_notes': notice + '\n\n更新内容列表',
            }
            # New client gets available=True, update=None, full_package_required=True
            with patch.object(updater, 'ROOT', root):
                rel = updater.check_release('auto', reader=lambda url, src: json.dumps(inventory).encode('utf-8'))
            self.assertTrue(rel['available'])
            self.assertIsNone(rel['update'])
            self.assertTrue(rel['full_package_required'])
            self.assertIn('jobs/', rel['full_package_notice'])

            # Legacy 0.4.1 client fixture ALSO sees available=True, update=None, and full release_notes!
            fixture = APP / 'tests/fixtures/update-client-0.4.1/updater.py'
            if fixture.is_file():
                spec = importlib.util.spec_from_file_location('_legacy_041_updater', fixture)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                with patch.object(mod, 'ROOT', root):
                    old_rel = mod.check_release('mirror', reader=lambda url, src: json.dumps(inventory).encode('utf-8'))
                self.assertTrue(old_rel['available'])
                self.assertIsNone(old_rel['update'])
                self.assertIn('全量更新', old_rel['notes'])

    def test_05_storage_expanded_caches_disk_precheck_and_redacted_diagnostic_bundle(self):
        with tempfile.TemporaryDirectory(prefix='vwa-infra-') as tmp:
            root = Path(tmp)
            for sub in ('cache/chorus-cas/rmvpe', 'cache/v3', 'cache/updates/stage-old', 'cache/updates/stage-active'):
                (root / sub).mkdir(parents=True, exist_ok=True)
            (root / 'cache/chorus-cas/rmvpe/a.npz').write_bytes(b'1234')
            (root / 'cache/v3/track.wav').write_bytes(b'5678')
            active_plan = root / 'cache/updates/stage-active/plan.json'
            active_plan.write_text('{}', encoding='utf-8')
            (root / 'cache/updates/pending.json').write_text(
                json.dumps({'plan': str(active_plan), 'version': '2.2.0'}), encoding='utf-8'
            )
            (root / 'cache/updates/stage-old/plan.json').write_text('{}', encoding='utf-8')

            with patch.object(storage, 'ROOT', root), patch.object(storage, 'job_directories', return_value=[]):
                rows = storage.candidates()
            paths = {Path(r['path']).resolve() for r in rows}
            self.assertIn((root / 'cache/chorus-cas').resolve(), paths)
            self.assertIn((root / 'cache/v3').resolve(), paths)
            self.assertIn((root / 'cache/updates/stage-old').resolve(), paths)
            self.assertNotIn((root / 'cache/updates/stage-active').resolve(), paths)

            # require_free_space rejects impossible requirement with ENOSPC & Chinese guidance
            with self.assertRaises(OSError) as ctx:
                storage.require_free_space(root, 10**18, '测试导出')
            self.assertIn('可用空间不足', str(ctx.exception))

            # Diagnostic bundle redacts user home and app root
            (root / 'launcher.log').write_text(
                f'Error in C:\\Users\\SecretAlice\\Desktop\\song.wav under {root}\\jobs\\t1\n',
                encoding='utf-8',
            )
            bundle_path = root / 'diag-out.zip'
            info = operation_diagnostics.export_diagnostic_bundle(
                bundle_path, f'Summary path C:\\Users\\SecretAlice\\file.wav {root}', root=root
            )
            self.assertTrue(bundle_path.is_file())
            with zipfile.ZipFile(bundle_path) as zf:
                names = set(zf.namelist())
                self.assertTrue({'summary.txt', 'launcher-tail.txt', 'update-state.json', 'dependency-quick-check.json'} <= names)
                tail = zf.read('launcher-tail.txt').decode('utf-8')
                summ = zf.read('summary.txt').decode('utf-8')
                self.assertNotIn('SecretAlice', tail)
                self.assertNotIn('SecretAlice', summ)
                self.assertIn('<USER_HOME>', tail)
                self.assertIn('<VWA_ROOT>', tail)

    def test_06_appearance_render_backend_and_launcher_exit_code_2(self):
        self.assertEqual(appearance_state.normalize({'render_backend': 'software'})['render_backend'], 'software')
        self.assertEqual(appearance_state.normalize({'render_backend': 'invalid'})['render_backend'], 'auto')
        launcher_cs = (APP / 'tools/windows/Launcher.cs').read_text(encoding='utf-8')
        self.assertIn('ExitCode != 2', launcher_cs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
