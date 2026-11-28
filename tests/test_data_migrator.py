"""End-to-end tests for the standalone zero-dependency VWA Cross-Version Data Migrator (.NET 4.8 WPF)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = APP / 'tools/build_data_migrator.ps1'
CSC_PATH = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')


def _seed_old_install(root: Path, version: str = '2.1.0') -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'VWA.exe').write_bytes(b'MZ-old-vwa')
    (root / 'version.json').write_text(json.dumps({'version': version}), encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({
        'python': str(root / 'dependencies/vocal2midi/python/python.exe'),
        'ffmpeg': str(root / 'dependencies/ffmpeg/ffmpeg.exe'),
        'theme': 'light',
        'ui_language': 'ja',
        'last_output_dir': str(root / 'jobs/song_alpha'),
        'custom_pref': 99,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'cache').mkdir(exist_ok=True)
    (root / 'cache/workspace.json').write_text(json.dumps({'theme': 'light', 'scale': 1.15}), encoding='utf-8')
    (root / 'cache/recent-items.json').write_text(
        json.dumps([{'path': str(root / 'jobs/song_alpha/audio/lead.wav'), 'kind': 'audio'}]),
        encoding='utf-8',
    )
    (root / 'jobs/song_alpha/audio').mkdir(parents=True)
    (root / 'jobs/song_alpha/status.json').write_text('{"state":"done"}', encoding='utf-8')
    (root / 'jobs/song_alpha/audio/lead.wav').write_bytes(b'RIFF-fake-lead')
    (root / 'user-presets').mkdir(exist_ok=True)
    (root / 'user-presets/library.json').write_text('{"presets":["custom-1"]}', encoding='utf-8')
    (root / 'cache/accounts/bilibili').mkdir(parents=True)
    (root / 'cache/accounts/bilibili/session.json').write_text('{"logged_in":true}', encoding='utf-8')
    (root / 'publish-packages/pkg1').mkdir(parents=True)
    (root / 'publish-packages/pkg1/draft.json').write_text('{"title":"demo"}', encoding='utf-8')
    (root / 'dependencies/optional/bs-roformer/current-cuda').mkdir(parents=True)
    (root / 'dependencies/optional/bs-roformer/current-cuda/installed.json').write_text('{"tier":"cuda"}', encoding='utf-8')
    q_dir = root / 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml'
    q_dir.mkdir(parents=True, exist_ok=True)
    (q_dir / 'qwen3_asr_llm.f16.gguf').write_bytes(b'qwen-llm-bytes')
    (q_dir / 'qwen3_asr_encoder_backend.fp16.onnx').write_bytes(b'qwen-be-bytes')
    (q_dir / 'qwen3_asr_encoder_frontend.fp16.onnx').write_bytes(b'qwen-fe-bytes')


def _seed_new_install(root: Path, version: str = '3.0.0', *, preinstall_qwen_llm: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'VWA.exe').write_bytes(b'MZ-new-vwa-3.0.0')
    (root / 'version.json').write_text(json.dumps({'version': version, 'desktop_frontend': 'quick'}), encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({
        'python': '@/dependencies/vocal2midi/python/python.exe',
        'ffmpeg': '@/dependencies/ffmpeg/ffmpeg.exe',
        'theme': 'dark',
        'new_v3_default': True,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    # Existing target job file that must NEVER be overwritten
    (root / 'jobs/song_alpha').mkdir(parents=True)
    (root / 'jobs/song_alpha/status.json').write_text('{"state":"newer-target"}', encoding='utf-8')
    if preinstall_qwen_llm:
        q_dir = root / 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml'
        q_dir.mkdir(parents=True, exist_ok=True)
        (q_dir / 'qwen3_asr_llm.f16.gguf').write_bytes(b'official-new-qwen-llm')


class DataMigratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = None
        cls.exe_path = None
        if os.name == 'nt' and CSC_PATH.is_file():
            cls._tmpdir = tempfile.TemporaryDirectory(dir=APP / 'work', prefix='data-migrator-')
            cls.exe_path = Path(cls._tmpdir.name) / 'VWA-Data-Migrator.exe'
            ps_exe = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
            ps_bin = str(ps_exe) if ps_exe.is_file() else 'powershell.exe'
            res = subprocess.run(
                [ps_bin, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', str(BUILD_SCRIPT), '-OutputPath', str(cls.exe_path), '-Version', '3.0.0'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
            )
            if res.returncode != 0:
                raise RuntimeError(f'Failed to compile VWA-Data-Migrator:\n{res.stdout}\n{res.stderr}')

    @classmethod
    def tearDownClass(cls):
        if cls._tmpdir is not None:
            cls._tmpdir.cleanup()

    def test_01_sources_and_build_script_exist(self):
        required = (
            'packaging/data-migrator/app.manifest',
            'packaging/data-migrator/I18n.cs',
            'packaging/data-migrator/WindowsHelpers.cs',
            'packaging/data-migrator/MigrationEngine.cs',
            'packaging/data-migrator/MigratorWindow.cs',
            'packaging/data-migrator/Program.cs',
            'tools/build_data_migrator.ps1',
        )
        for rel in required:
            self.assertTrue((APP / rel).is_file(), f'Missing data migrator file: {rel}')

    def test_02_safe_copy_cjk_paths_config_merge_non_destructive_and_receipt(self):
        if self.exe_path is None:
            self.skipTest('Built-in .NET 4.x csc.exe not available on this host')

        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            old_dir = base / '旧版 VWA 2.1.0'
            new_dir = base / '新版 VWA 3.0.0'
            _seed_old_install(old_dir, '2.1.0')
            _seed_new_install(new_dir, '3.0.0', preinstall_qwen_llm=True)

            r = subprocess.run(
                [str(self.exe_path), '--headless', '--source', str(old_dir), '--target', str(new_dir),
                 '--mode', 'copy', '--no-launch'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
            )
            self.assertEqual(r.returncode, 0, f'Copy migration failed:\n{r.stdout}\n{r.stderr}')
            self.assertIn('HEADLESS_MIGRATE_OK', r.stdout)

            # Config smart-merge preserves target internal keys & rewrites old paths & creates backup
            cfg = json.loads((new_dir / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(cfg['python'], '@/dependencies/vocal2midi/python/python.exe')
            self.assertEqual(cfg['ffmpeg'], '@/dependencies/ffmpeg/ffmpeg.exe')
            self.assertTrue(cfg['new_v3_default'])
            self.assertEqual(cfg['theme'], 'light')
            self.assertEqual(cfg['custom_pref'], 99)
            self.assertEqual(Path(cfg['last_output_dir']).resolve(), (new_dir / 'jobs/song_alpha').resolve())
            self.assertTrue((new_dir / 'config.json.before-migrate.bak').is_file())

            # State JSONs migrated & paths rewritten
            self.assertEqual(json.loads((new_dir / 'cache/workspace.json').read_text(encoding='utf-8'))['scale'], 1.15)
            recent = json.loads((new_dir / 'cache/recent-items.json').read_text(encoding='utf-8'))
            self.assertEqual(Path(recent[0]['path']).resolve(), (new_dir / 'jobs/song_alpha/audio/lead.wav').resolve())

            # Existing target files are NOT overwritten; missing files ARE copied
            self.assertEqual((new_dir / 'jobs/song_alpha/status.json').read_text(encoding='utf-8'), '{"state":"newer-target"}')
            self.assertEqual((new_dir / 'jobs/song_alpha/audio/lead.wav').read_bytes(), b'RIFF-fake-lead')
            self.assertTrue((new_dir / 'user-presets/library.json').is_file())
            self.assertTrue((new_dir / 'cache/accounts/bilibili/session.json').is_file())
            self.assertTrue((new_dir / 'publish-packages/pkg1/draft.json').is_file())
            self.assertTrue((new_dir / 'dependencies/optional/bs-roformer/current-cuda/installed.json').is_file())

            # Qwen: preinstalled LLM untouched, missing encoder files copied
            q_dir = new_dir / 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml'
            self.assertEqual((q_dir / 'qwen3_asr_llm.f16.gguf').read_bytes(), b'official-new-qwen-llm')
            self.assertEqual((q_dir / 'qwen3_asr_encoder_backend.fp16.onnx').read_bytes(), b'qwen-be-bytes')
            self.assertEqual((q_dir / 'qwen3_asr_encoder_frontend.fp16.onnx').read_bytes(), b'qwen-fe-bytes')

            # Source remains completely intact in copy mode
            self.assertTrue((old_dir / 'jobs/song_alpha/audio/lead.wav').is_file())
            receipt = json.loads((new_dir / 'cache/updates/migration-receipt.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['source_version'], '2.1.0')
            self.assertEqual(receipt['target_version'], '3.0.0')
            self.assertEqual(receipt['mode'], 'copy')

    def test_03_same_drive_move_and_safety_guards(self):
        if self.exe_path is None:
            self.skipTest('Built-in .NET 4.x csc.exe not available on this host')

        with tempfile.TemporaryDirectory(dir=APP / 'work', ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            old_dir = base / 'OldVWA'
            new_dir = base / 'NewVWA'
            _seed_old_install(old_dir, '1.1.0')
            _seed_new_install(new_dir, '3.0.0')

            # Guard 1: same directory rejected
            r_same = subprocess.run(
                [str(self.exe_path), '--headless', '--source', str(old_dir), '--target', str(old_dir), '--no-launch'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            )
            self.assertNotEqual(r_same.returncode, 0)

            # Guard 2: nested directory rejected
            nested = old_dir / 'sub_new'
            nested.mkdir()
            r_nest = subprocess.run(
                [str(self.exe_path), '--headless', '--source', str(old_dir), '--target', str(nested), '--no-launch'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            )
            self.assertNotEqual(r_nest.returncode, 0)

            # Move mode: moves data files, keeps old program binaries intact
            r_move = subprocess.run(
                [str(self.exe_path), '--headless', '--source', str(old_dir), '--target', str(new_dir),
                 '--mode', 'move', '--skip-qwen', '--no-launch'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
            )
            self.assertEqual(r_move.returncode, 0, f'Move migration failed:\n{r_move.stdout}\n{r_move.stderr}')
            self.assertTrue((new_dir / 'jobs/song_alpha/audio/lead.wav').is_file())
            self.assertFalse((old_dir / 'jobs/song_alpha/audio/lead.wav').exists())
            self.assertTrue((old_dir / 'VWA.exe').is_file())
            self.assertTrue((old_dir / 'config.json').is_file())
            self.assertFalse((new_dir / 'dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/qwen3_asr_llm.f16.gguf').exists())


if __name__ == '__main__':
    unittest.main()
