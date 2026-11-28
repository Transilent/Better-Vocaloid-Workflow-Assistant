"""Unit and E2E tests for VWA 2-hop Full-Package Downloader & Auto-Migrator and Hop-1 bridges."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(1, str(APP))

import full_package_migrator as migrator
import full_package_migrator_ui as migrator_ui
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


def _start_range_file_server(payloads: dict[str, bytes]):
    """Start a real local HTTP server supporting Range (206) requests for download tests."""
    stats = {'range_hits': 0, 'get_hits': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _fmt, *_args):
            return

        def do_GET(self):
            name = self.path.lstrip('/')
            data = payloads.get(name)
            if data is None:
                self.send_error(404)
                return
            stats['get_hits'] += 1
            rng = self.headers.get('Range')
            if rng and rng.startswith('bytes='):
                stats['range_hits'] += 1
                spec = rng.removeprefix('bytes=').strip()
                start_s, end_s = spec.split('-', 1)
                start = int(start_s)
                end = int(end_s) if end_s else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
                self.send_header('Content-Length', str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            self.send_response(200)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}', stats


class FullPackageMigratorTests(unittest.TestCase):
    def test_detect_variant_and_split_download_merge_extract_migrate_relaunch(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
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
                'network_proxy_mode': 'direct',
                'download_source': 'ghfast',
                'download_workers': 4,
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
            q0 = old_root / migrator.QWEN_WEIGHT_FILES[0]
            q0.parent.mkdir(parents=True, exist_ok=True)
            q0.write_bytes(b'user-downloaded-qwen')
            handoff = migrator.write_handoff(old_root, '3.0.0', 'ghfast')
            self.assertTrue(handoff.is_file())
            self.assertEqual(migrator.detect_install_variant(old_root), 'jpn')
            prefs = migrator.load_network_preferences(old_root)
            self.assertEqual(prefs['source'], 'ghfast')
            self.assertEqual(prefs['proxy_mode'], 'direct')
            self.assertEqual(prefs['workers'], 4)

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
                    old_root, '3.0.0', target_dir, variant='auto', auto_launch=True,
                    reader=lambda url, src: fake_meta, downloader=fake_dl,
                )

            self.assertEqual(receipt['source_version'], '2.1.0')
            self.assertEqual(receipt['target_version'], '3.0.0')
            self.assertTrue(receipt['launched'])
            self.assertEqual(launched_roots, [target_dir.resolve()])
            self.assertFalse(handoff.exists())

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
            self.assertTrue((old_root / 'jobs/my_song/request.json').is_file())

    def test_parallel_http_range_download_and_chunk_resume(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            blob = (b'VWA-3.0.0-RANGE-TEST-BLOCK-' * 4000)[:100000]
            srv, base_url, stats = _start_range_file_server({'sample.bin': blob})
            try:
                opener = migrator.build_opener('direct')
                partial = base / 'sample.bin.part'
                state_path = base / 'sample.bin.part.state.json'
                progress_hits: list[int] = []
                # Pre-populate chunk 0 to verify resume skips already-completed chunks
                with partial.open('wb') as f:
                    f.truncate(len(blob))
                    f.seek(0)
                    f.write(blob[:32768])
                state_path.write_text(json.dumps({
                    'bytes': len(blob), 'sha256': _sha256(blob), 'completed': [0],
                }), encoding='utf-8')
                migrator._download_chunked(
                    [f'{base_url}/sample.bin'], partial, state_path, len(blob), _sha256(blob),
                    opener=opener, workers=3, progress_cb=progress_hits.append,
                    check_cancel=None, chunk_size=32768,
                )
                self.assertEqual(partial.read_bytes(), blob)
                # 100000 / 32768 = 4 chunks total; chunk 0 was resumed -> exactly 3 Range requests
                self.assertEqual(stats['range_hits'], 3)
                self.assertEqual(max(progress_hits), len(blob))
            finally:
                srv.shutdown()
                srv.server_close()

    def test_local_web_ui_server_csrf_and_e2e_migration_and_skip(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            old_root = base / 'VWA-2.1.0'
            old_root.mkdir()
            (old_root / 'version.json').write_text(json.dumps({'version': '2.1.0'}), encoding='utf-8')
            handoff = migrator.write_handoff(old_root, '3.0.0', 'auto')

            full_zip = base / 'VWA-Windows-x64.zip'
            raw_zip = _make_fake_portable_zip(full_zip, version='3.0.0', include_qwen=True)
            fake_meta = {
                'archive': {'name': 'VWA-Windows-x64.zip', 'bytes': len(raw_zip), 'sha256': _sha256(raw_zip)},
                'parts': [{'name': 'VWA-Windows-x64.zip', 'bytes': len(raw_zip), 'sha256': _sha256(raw_zip)}],
            }
            server, token, state, done_event = migrator_ui.create_web_server(
                old_root, '3.0.0', 'auto', bridge_mode=False, auto_launch=False,
                reader=lambda u, s: fake_meta,
                downloader=lambda part, dest, cb: (dest.write_bytes(raw_zip), cb(len(raw_zip)), dest)[2],
            )
            url = f'http://127.0.0.1:{server.server_address[1]}'
            opener = migrator.build_opener('direct')
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                html = opener.open(f'{url}/').read().decode('utf-8')
                self.assertIn('VWA 3.0.0', html)
                self.assertIn('暂不升级（启动旧版）', html)

                # POST without CSRF token must be rejected with 403
                bad_req = urllib.request.Request(f'{url}/api/start', data=b'{}', method='POST')
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    opener.open(bad_req)
                self.assertEqual(ctx.exception.code, 403)

                # Start migration via authenticated POST
                target_dir = base / 'VWA-3.0.0-web'
                ok_req = urllib.request.Request(
                    f'{url}/api/start',
                    data=json.dumps({'variant': 'full', 'source': 'direct', 'target_dir': str(target_dir)}).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'X-VWA-Token': token},
                    method='POST',
                )
                resp = json.loads(opener.open(ok_req).read().decode('utf-8'))
                self.assertTrue(resp['ok'])

                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline and state['state'] not in ('done', 'error'):
                    time.sleep(0.05)
                self.assertEqual(state['state'], 'done', state['message'])
                self.assertTrue((target_dir / 'VWA.exe').is_file())
                self.assertFalse(handoff.exists())

                # Verify /api/skip clears handoff and returns exit_code=2
                migrator.write_handoff(old_root, '3.0.0', 'auto')
                skip_req = urllib.request.Request(
                    f'{url}/api/skip', data=b'{}',
                    headers={'Content-Type': 'application/json', 'X-VWA-Token': token},
                    method='POST',
                )
                opener.open(skip_req)
                self.assertEqual(state['exit_code'], 2)
                self.assertFalse(handoff.exists())
                self.assertTrue(done_event.wait(timeout=2.0))
            finally:
                server.shutdown()
                server.server_close()

    def test_tampered_part_and_zip_slip_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            plan = {
                'version': '3.0.0', 'variant': 'jpn',
                'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': 10, 'sha256': _sha256(b'0123456789')},
                'parts': [{'name': 'VWA-Windows-x64-JPN.zip', 'bytes': 10, 'sha256': _sha256(b'0123456789'), 'url': 'x'}],
            }
            with self.assertRaises(ValueError):
                migrator.download_and_merge_package(
                    plan, base / 'cache',
                    downloader=lambda part, dest, cb: (dest.write_bytes(b'TAMPERED!!'), dest)[1],
                )
            evil_zip = base / 'evil.zip'
            with zipfile.ZipFile(evil_zip, 'w') as zf:
                zf.writestr('../escape.txt', b'pwned')
            with self.assertRaises(ValueError):
                migrator.extract_verified_archive(evil_zip, base / 'out')
            self.assertFalse((base / 'escape.txt').exists())

    def test_hop1_bridge_archives_all_cohorts_zero_tkinter(self):
        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            summary = bridge_builder.build_all_legacy_bridges(base, target_version='3.0.0')
            self.assertEqual(summary['quick_v2_bridge']['desktop_frontend'], 'quick')
            self.assertEqual(summary['quick_v2_bridge']['qt_version'], '6.10.3')
            manifest = json.loads((base / 'bridge-release-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(manifest['updates']), 3)
            self.assertEqual(
                {u['runtime_sha256'] for u in manifest['updates']},
                {bridge_builder.LEGACY_RUNTIME_SHAS['1.x'],
                 bridge_builder.LEGACY_RUNTIME_SHAS['0.4.0'],
                 bridge_builder.LEGACY_RUNTIME_SHAS['0.3.x']},
            )
            with zipfile.ZipFile(base / 'VWA-Program-Update.zip') as zf:
                inv = json.loads(zf.read('update-inventory.json'))
                update_apply.validate_quick_program([e['file'] for e in inv['entries']])
                for entry in inv['entries']:
                    update_apply.validate_file(entry)
                launch_src = zf.read('launch.py').decode('utf-8')
                self.assertNotIn('import tkinter', launch_src)
                compile(launch_src, 'launch.py', 'exec')

            with zipfile.ZipFile(base / 'BVWA-App-Update.zip') as zf:
                inv1 = json.loads(zf.read('update-inventory.json'))
                self.assertEqual({e['file'] for e in inv1['entries']}, {'app.py', 'launch.py', 'version.json'})
                app_src = zf.read('app.py').decode('utf-8')
                self.assertNotIn('import tkinter', app_src)
                compile(app_src, 'app.py', 'exec')


if __name__ == '__main__':
    unittest.main()
