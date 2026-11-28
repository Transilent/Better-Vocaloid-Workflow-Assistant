"""End-to-end tests for the zero-dependency VWA Online Bootstrapper Installer (.NET 4.8 WPF)."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile

APP = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = APP / 'tools/build_online_installer.ps1'
CSC_PATH = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_portable_zip(zip_path: Path, version: str = '3.0.0', *, include_qwen: bool = True, pad_bytes: int = 40000) -> bytes:
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_STORED) as zf:
        zf.writestr('VWA/version.json', json.dumps({'version': version, 'channel': 'stable', 'desktop_frontend': 'quick'}))
        zf.writestr('VWA/VWA.exe', b'MZ-vwa-3.0.0-stub')
        zf.writestr('VWA/launch.py', b'print("vwa 3.0.0")\n')
        zf.writestr('VWA/config.example.json', json.dumps({
            'python': '@/dependencies/vocal2midi/python/python.exe',
            'theme': 'dark',
            'new_v3_flag': True,
        }))
        zf.writestr('VWA/payload.bin', b'VWA3' * (pad_bytes // 4))
        if include_qwen:
            zf.writestr(
                'VWA/dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/qwen3_asr_llm.f16.gguf',
                b'official-qwen-full',
            )
    return zip_path.read_bytes()


def _start_range_server(files: dict[str, bytes]):
    stats = {'range_hits': 0, 'get_hits': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _fmt, *_args):
            return

        def do_GET(self):
            key = self.path.lstrip('/')
            data = files.get(key)
            if data is None:
                self.send_error(404)
                return
            stats['get_hits'] += 1
            rng = self.headers.get('Range')
            if rng and rng.startswith('bytes='):
                stats['range_hits'] += 1
                s_str, e_str = rng.removeprefix('bytes=').split('-', 1)
                start = int(s_str)
                end = min(len(data) - 1, int(e_str) if e_str else len(data) - 1)
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


class OnlineInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = None
        cls.exe_path = None
        if os.name == 'nt' and CSC_PATH.is_file():
            cls._tmpdir = tempfile.TemporaryDirectory(dir=APP / 'work', prefix='online-inst-')
            cls.exe_path = Path(cls._tmpdir.name) / 'VWA-Online-Installer.exe'
            ps_exe = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
            ps_bin = str(ps_exe) if ps_exe.is_file() else 'powershell.exe'
            res = subprocess.run(
                [
                    ps_bin, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(BUILD_SCRIPT),
                    '-OutputPath', str(cls.exe_path),
                    '-Version', '3.0.0',
                ],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
            )
            if res.returncode != 0:
                raise RuntimeError(f'Failed to compile online installer:\n{res.stdout}\n{res.stderr}')

    @classmethod
    def tearDownClass(cls):
        if cls._tmpdir is not None:
            cls._tmpdir.cleanup()

    def test_01_sources_and_build_script_exist(self):
        required = (
            'packaging/online-installer/app.manifest',
            'packaging/online-installer/I18n.cs',
            'packaging/online-installer/ReleaseCatalog.cs',
            'packaging/online-installer/DownloadEngine.cs',
            'packaging/online-installer/WindowsIntegration.cs',
            'packaging/online-installer/InstallerWindow.cs',
            'packaging/online-installer/Program.cs',
            'tools/build_online_installer.ps1',
        )
        for rel in required:
            self.assertTrue((APP / rel).is_file(), f'Missing online installer file: {rel}')

    def test_02_e2e_split_full_range_install_and_jpn_upgrade_preserves_user_data(self):
        if self.exe_path is None:
            self.skipTest('Built-in .NET 4.x csc.exe not available on this host')

        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            # Build 2-part Full archive + single-file JPN archive
            full_raw = _make_portable_zip(base / 'full.zip', version='3.0.0', include_qwen=True, pad_bytes=64000)
            mid = len(full_raw) // 2
            fp1, fp2 = full_raw[:mid], full_raw[mid:]
            jpn_raw = _make_portable_zip(base / 'jpn.zip', version='3.0.0', include_qwen=False, pad_bytes=32000)

            files = {
                'release-assets.json': json.dumps({'version': '3.0.0', 'release_notes': 'VWA 3.0.0'}).encode('utf-8'),
                'portable-metadata-full.json': json.dumps({
                    'archive': {'name': 'VWA-Windows-x64.zip', 'bytes': len(full_raw), 'sha256': _sha256(full_raw)},
                    'parts': [
                        {'name': 'VWA-Windows-x64.zip.001', 'bytes': len(fp1), 'sha256': _sha256(fp1)},
                        {'name': 'VWA-Windows-x64.zip.002', 'bytes': len(fp2), 'sha256': _sha256(fp2)},
                    ],
                }).encode('utf-8'),
                'portable-metadata-jpn.json': json.dumps({
                    'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(jpn_raw), 'sha256': _sha256(jpn_raw)},
                    'parts': [
                        {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(jpn_raw), 'sha256': _sha256(jpn_raw)},
                    ],
                }).encode('utf-8'),
                'VWA-Windows-x64.zip.001': fp1,
                'VWA-Windows-x64.zip.002': fp2,
                'VWA-Windows-x64-JPN.zip': jpn_raw,
            }
            srv, base_url, stats = _start_range_server(files)
            try:
                # 1) Fresh 2-part Full install with multi-threaded Range chunks
                fresh_dir = base / 'FreshFull'
                r1 = subprocess.run(
                    [
                        str(self.exe_path), '--headless',
                        '--manifest-base', base_url,
                        '--version', '3.0.0',
                        '--variant', 'full',
                        '--target-dir', str(fresh_dir),
                        '--route', 'direct',
                        '--proxy-mode', 'direct',
                        '--workers', '3',
                        '--chunk-bytes', '12000',
                        '--parallel-min', '4096',
                        '--lang', 'en',
                        '--no-shortcuts', '--no-launch',
                    ],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                )
                self.assertEqual(r1.returncode, 0, f'Full install failed:\n{r1.stdout}\n{r1.stderr}')
                self.assertGreaterEqual(stats['range_hits'], 4, 'Expected parallel HTTP Range 206 chunk requests')
                self.assertTrue((fresh_dir / 'VWA.exe').is_file())
                self.assertEqual(json.loads((fresh_dir / 'version.json').read_text(encoding='utf-8'))['version'], '3.0.0')
                self.assertTrue((fresh_dir / 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/qwen3_asr_llm.f16.gguf').is_file())
                self.assertEqual(json.loads((fresh_dir / 'cache/appearance.json').read_text(encoding='utf-8'))['ui_language'], 'en')

                # 2) In-place upgrade of existing 2.1.0 directory using JPN variant -> preserves user data
                upg_dir = base / 'ExistingV210'
                (upg_dir / 'jobs/demo').mkdir(parents=True)
                (upg_dir / 'jobs/demo/request.json').write_text('{"song":"keep-me"}', encoding='utf-8')
                (upg_dir / 'user-presets').mkdir(parents=True)
                (upg_dir / 'user-presets/my.json').write_text('{"ok":true}', encoding='utf-8')
                (upg_dir / 'cache/accounts/bili').mkdir(parents=True)
                (upg_dir / 'cache/accounts/bili/cookie.json').write_text('{"session":1}', encoding='utf-8')
                (upg_dir / 'dependencies/optional/bs-roformer').mkdir(parents=True)
                (upg_dir / 'dependencies/optional/bs-roformer/installed.json').write_text('{"tier":"cuda"}', encoding='utf-8')
                manual_q = upg_dir / 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/qwen3_asr_llm.f16.gguf'
                manual_q.parent.mkdir(parents=True, exist_ok=True)
                manual_q.write_bytes(b'manual-user-qwen')
                (upg_dir / 'version.json').write_text(json.dumps({'version': '2.1.0'}), encoding='utf-8')
                (upg_dir / 'config.json').write_text(json.dumps({
                    'python': 'C:/stale/old/python.exe',
                    'theme': 'light',
                    'user_custom': 777,
                }), encoding='utf-8')

                r2 = subprocess.run(
                    [
                        str(self.exe_path), '--headless',
                        '--manifest-base', base_url,
                        '--version', '3.0.0',
                        '--variant', 'jpn',
                        '--target-dir', str(upg_dir),
                        '--route', 'direct',
                        '--proxy-mode', 'direct',
                        '--no-shortcuts', '--no-launch',
                    ],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                )
                self.assertEqual(r2.returncode, 0, f'Upgrade install failed:\n{r2.stdout}\n{r2.stderr}')
                self.assertEqual(json.loads((upg_dir / 'version.json').read_text(encoding='utf-8'))['version'], '3.0.0')
                cfg = json.loads((upg_dir / 'config.json').read_text(encoding='utf-8'))
                self.assertEqual(cfg['theme'], 'light')
                self.assertEqual(cfg['user_custom'], 777)
                self.assertEqual(cfg['python'], '@/dependencies/vocal2midi/python/python.exe')
                self.assertTrue((upg_dir / 'jobs/demo/request.json').is_file())
                self.assertTrue((upg_dir / 'user-presets/my.json').is_file())
                self.assertTrue((upg_dir / 'cache/accounts/bili/cookie.json').is_file())
                self.assertTrue((upg_dir / 'dependencies/optional/bs-roformer/installed.json').is_file())
                self.assertEqual(manual_q.read_bytes(), b'manual-user-qwen')
            finally:
                srv.shutdown()
                srv.server_close()

    def test_03_tampered_sha256_and_zip_slip_rejected(self):
        if self.exe_path is None:
            self.skipTest('Built-in .NET 4.x csc.exe not available on this host')

        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            # Case A: Tampered SHA-256
            bad_bytes = b'TAMPERED_CONTENT_123456'
            files_a = {
                'release-assets.json': json.dumps({'version': '3.0.0'}).encode('utf-8'),
                'portable-metadata-jpn.json': json.dumps({
                    'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(bad_bytes), 'sha256': '0' * 64},
                    'parts': [{'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(bad_bytes), 'sha256': '0' * 64}],
                }).encode('utf-8'),
                'VWA-Windows-x64-JPN.zip': bad_bytes,
            }
            srv_a, url_a, _ = _start_range_server(files_a)
            try:
                out_a = base / 'OutTamper'
                ra = subprocess.run(
                    [str(self.exe_path), '--headless', '--manifest-base', url_a, '--version', '3.0.0',
                     '--variant', 'jpn', '--target-dir', str(out_a), '--proxy-mode', 'direct',
                     '--no-shortcuts', '--no-launch'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
                )
                self.assertNotEqual(ra.returncode, 0)
                self.assertFalse(out_a.exists())
            finally:
                srv_a.shutdown()
                srv_a.server_close()

            # Case B: Valid SHA-256 archive containing Zip Slip traversal
            evil_zip_path = base / 'evil.zip'
            with zipfile.ZipFile(evil_zip_path, 'w') as zf:
                zf.writestr('../escaped.txt', b'pwned')
                zf.writestr('version.json', '{"version":"3.0.0"}')
            evil_bytes = evil_zip_path.read_bytes()
            files_b = {
                'release-assets.json': json.dumps({'version': '3.0.0'}).encode('utf-8'),
                'portable-metadata-jpn.json': json.dumps({
                    'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(evil_bytes), 'sha256': _sha256(evil_bytes)},
                    'parts': [{'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(evil_bytes), 'sha256': _sha256(evil_bytes)}],
                }).encode('utf-8'),
                'VWA-Windows-x64-JPN.zip': evil_bytes,
            }
            srv_b, url_b, _ = _start_range_server(files_b)
            try:
                out_b = base / 'OutSlip'
                rb = subprocess.run(
                    [str(self.exe_path), '--headless', '--manifest-base', url_b, '--version', '3.0.0',
                     '--variant', 'jpn', '--target-dir', str(out_b), '--proxy-mode', 'direct',
                     '--no-shortcuts', '--no-launch'],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
                )
                self.assertNotEqual(rb.returncode, 0)
                self.assertFalse((base / 'escaped.txt').exists())
            finally:
                srv_b.shutdown()
                srv_b.server_close()

    def test_04_resume_from_chunk_state_and_cjk_spaces_path(self):
        if self.exe_path is None:
            self.skipTest('Built-in .NET 4.x csc.exe not available on this host')

        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            jpn_raw = _make_portable_zip(base / 'jpn_resume.zip', version='3.0.0', include_qwen=False, pad_bytes=48000)
            jpn_sha = _sha256(jpn_raw)
            chunk_bytes = 12000
            total_chunks = (len(jpn_raw) + chunk_bytes - 1) // chunk_bytes

            files = {
                'release-assets.json': json.dumps({'version': '3.0.0'}).encode('utf-8'),
                'portable-metadata-jpn.json': json.dumps({
                    'archive': {'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(jpn_raw), 'sha256': jpn_sha},
                    'parts': [{'name': 'VWA-Windows-x64-JPN.zip', 'bytes': len(jpn_raw), 'sha256': jpn_sha}],
                }).encode('utf-8'),
                'VWA-Windows-x64-JPN.zip': jpn_raw,
            }
            srv, base_url, stats = _start_range_server(files)
            try:
                # Target directory with CJK characters and spaces + pre-populated resume cache on target drive
                target_dir = base / '测试 目录 3.0' / 'VWA'
                cache_dir = target_dir.parent / '.vwa-installer-cache' / '3.0.0-jpn'
                cache_dir.mkdir(parents=True, exist_ok=True)
                part_file = cache_dir / 'VWA-Windows-x64-JPN.zip.part'
                buf = bytearray(len(jpn_raw))
                buf[:chunk_bytes] = jpn_raw[:chunk_bytes]
                part_file.write_bytes(bytes(buf))
                (cache_dir / 'VWA-Windows-x64-JPN.zip.part.state.json').write_text(
                    json.dumps({'bytes': len(jpn_raw), 'sha256': jpn_sha, 'completed': [0]}),
                    encoding='utf-8',
                )
                r = subprocess.run(
                    [
                        str(self.exe_path), '--headless',
                        '--manifest-base', base_url,
                        '--version', '3.0.0',
                        '--variant', 'jpn',
                        '--target-dir', str(target_dir),
                        '--route', 'direct',
                        '--proxy-mode', 'direct',
                        '--workers', '3',
                        '--chunk-bytes', str(chunk_bytes),
                        '--parallel-min', '4096',
                        '--no-shortcuts', '--no-launch',
                    ],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                )
                self.assertEqual(r.returncode, 0, f'Resume/CJK install failed:\n{r.stdout}\n{r.stderr}')
                # 1 probe Range (0-0) + (total_chunks - 1) resumed chunks -> strictly less than total_chunks + 1
                self.assertEqual(stats['range_hits'], 1 + (total_chunks - 1))
                self.assertTrue((target_dir / 'VWA.exe').is_file())
                self.assertEqual(json.loads((target_dir / 'version.json').read_text(encoding='utf-8'))['version'], '3.0.0')
                self.assertFalse((target_dir.parent / '.vwa-installer-cache').exists())
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == '__main__':
    unittest.main()
