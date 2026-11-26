"""Long-term stability, cold-start latency, dirty-input resilience, and handle-leak guards."""
from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest import mock

import numpy as np
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src' if (APP / 'src').is_dir() else APP
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chorus_analysis import feature_track
import chorus_cache
from chorus_notes import segment_vocal_notes
from chorus_project import ChorusProject, Recording
import common
import project_io
import publication_draft_service
import tuning_session_store


class StabilityAndPerfGuardTests(unittest.TestCase):
    def test_cold_start_import_latency_and_no_transitive_heavy_ml_imports(self):
        """Core startup & refactored service modules must import fast without pulling ML/browser stacks."""
        code = (
            "import json, sys, time\n"
            f"sys.path.insert(0, {str(SRC)!r})\n"
            "t0 = time.perf_counter()\n"
            "mods = ['common', 'appearance_state', 'workspace_routes', 'workspace_settings', "
            "'workspace_controller', 'quick_results', 'chorus_project', 'chorus_review_service', "
            "'publication_browser_service', 'publication_draft_service', "
            "'mixdown_handoff_service', 'tuning_session_store', 'task_actions_service', "
            "'project_convert_service', 'mdx']\n"
            "for m in mods:\n"
            "    __import__(m)\n"
            "elapsed = time.perf_counter() - t0\n"
            "heavy = sorted(h for h in ('torch', 'onnxruntime', 'scipy', 'librosa', 'playwright', 'yt_dlp') "
            "if h in sys.modules)\n"
            "print(json.dumps({'elapsed': round(elapsed, 4), 'heavy': heavy}))\n"
        )
        proc = subprocess.run(
            [sys.executable, '-B', '-c', code],
            capture_output=True, text=True, timeout=30, check=True,
        )
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(data['heavy'], [], f'Heavy libraries leaked into cold startup: {data["heavy"]}')
        self.assertLess(data['elapsed'], 2.5, f'Cold startup import took {data["elapsed"]}s (> 2.5s budget)')

    def test_dirty_unicode_special_paths_and_mixed_audio_formats_resilience(self):
        """Real users have Chinese/Japanese/space/#/%/& paths, mixed mono/stereo/rates, and silent/short takes."""
        with tempfile.TemporaryDirectory(prefix='vwa-stability-') as tmp:
            tricky = Path(tmp) / '翻唱 合集 [v2] #1 & 100% (日文テスト)'
            tricky.mkdir(parents=True)

            # 1) Mixed sample rates & channels: 44.1k mono, 48k stereo, silent track, ultra-short 0.08s track
            t44 = np.linspace(0.0, 2.0, 44100 * 2, endpoint=False, dtype=np.float32)
            mono_wav = tricky / '主唱 #1 (干声).wav'
            sf.write(mono_wav, 0.2 * np.sin(2 * np.pi * 330.0 * t44), 44100)

            t48 = np.linspace(0.0, 2.0, 48000 * 2, endpoint=False, dtype=np.float32)
            stereo = np.column_stack([
                0.2 * np.sin(2 * np.pi * 330.0 * t48),
                0.18 * np.sin(2 * np.pi * 330.0 * t48 + 0.1),
            ]).astype(np.float32)
            stereo_wav = tricky / '和声 %20 [立体声].wav'
            sf.write(stereo_wav, stereo, 48000)

            silent_wav = tricky / '纯静音 & 空轨.wav'
            sf.write(silent_wav, np.zeros((44100, 1), dtype=np.float32), 44100)

            tiny_wav = tricky / '超短 80ms.wav'
            sf.write(tiny_wav, np.full((3528, 1), 0.05, dtype=np.float32), 44100)

            recs = []
            for p in (mono_wav, stereo_wav, silent_wav, tiny_wav):
                rec = Recording.read(p, 'vocal')
                feat = feature_track(rec, lambda: None)
                self.assertTrue(np.isfinite(feat.energy).all(), p.name)
                self.assertTrue(np.isfinite(feat.shape).all(), p.name)
                recs.append(rec)

            # Project save/load round-trip under tricky path
            proj_path = tricky / '合唱工程 #1.vwachorus.json'
            project = ChorusProject(title='测试 #1', id='stability-proj-1', tracks=tuple(recs), guide=recs[0].id)
            project.save(proj_path)
            loaded = ChorusProject.open(proj_path)
            self.assertEqual(len(loaded.tracks), 4)
            self.assertEqual(loaded.title, '测试 #1')

            # Segmenter handles all-NaN / all-silent curves without crashing
            self.assertEqual(segment_vocal_notes(np.full(200, np.nan, dtype=np.float32), 0.01), ())
            self.assertEqual(segment_vocal_notes(np.array([], dtype=np.float32), 0.01), ())

    def test_corrupted_project_and_bundle_files_fail_gracefully(self):
        """Truncated or garbage JSON/SVP/manifest files must raise clean ValueError/return None."""
        with tempfile.TemporaryDirectory(prefix='vwa-corrupt-') as tmp:
            root = Path(tmp)
            bad_svp = root / 'broken.svp'
            bad_svp.write_bytes(b'{"tracks": [invalid json truncated')
            with self.assertRaises(ValueError):
                project_io.read_source(bad_svp)

            empty_svp = root / 'empty.svp'
            empty_svp.write_bytes(b'')
            with self.assertRaises(ValueError):
                project_io.read_source(empty_svp)

            bad_json = root / 'sidecar.json'
            bad_json.write_text('{not valid json', encoding='utf-8')
            self.assertIsNone(tuning_session_store.read_json_file(bad_json))

            bad_bundle = root / 'bundle'
            bad_bundle.mkdir()
            (bad_bundle / 'manifest.json').write_text('{"state": "broken"}', encoding='utf-8')
            with self.assertRaises(ValueError):
                publication_draft_service._inspect_bundle(bad_bundle)

    def test_repeated_cache_and_atomic_json_cycles_leak_no_handles_tmp_or_memory(self):
        """Simulate prolonged editing sessions: repeated CAS writes, LRU eviction, and atomic JSON saves."""
        with tempfile.TemporaryDirectory(prefix='vwa-handles-') as tmp:
            root = Path(tmp)
            cache_dir = root / 'cas'
            json_target = root / 'state.json'

            rng = np.random.default_rng(20261126)
            chorus_cache.clear_memory_cache()
            # Small 700 KiB disk cap & prune every 4 writes to exercise real compressed .zspan LRU eviction.
            with (
                mock.patch.dict(os.environ, {'VWA_CAS_CACHE_DIR': str(cache_dir)}),
                mock.patch.object(chorus_cache, '_MAX_MEMORY_SPANS', 6),
                mock.patch.object(chorus_cache, '_MAX_DISK_SPAN_BYTES', 700 * 1024),
                mock.patch.object(chorus_cache, '_PRUNE_EVERY_WRITES', 4),
            ):
                tracemalloc.start()
                try:
                    snap_before = None
                    for step in range(30):
                        # 20k float32 stereo ≈ 160 KiB raw -> exercises zlib .zspan compression + LRU eviction
                        pcm = (rng.standard_normal((20000, 2)).astype(np.float32) * 0.1)
                        key = f'span_{step:03d}_' + ('a' * 16)
                        chorus_cache.save_pitch_span(key, pcm)
                        got = chorus_cache.load_pitch_span(key, pcm.shape)
                        self.assertIsNotNone(got)
                        self.assertTrue(np.array_equal(pcm, got), f'CAS roundtrip mismatch at step {step}')

                        common.atomic_json(json_target, {'revision': step, 'items': list(range(50))})
                        if step == 5:
                            gc.collect()
                            snap_before = tracemalloc.take_snapshot()

                    gc.collect()
                    snap_after = tracemalloc.take_snapshot()
                    # Verify zero stray .tmp or partial .span-* files left behind
                    leftover_tmp = list(root.rglob('*.tmp')) + list(cache_dir.rglob('.span-*'))
                    self.assertEqual(leftover_tmp, [], f'Leaked temporary files: {leftover_tmp}')

                    # Disk LRU cap must be respected (allowing headroom for writes since last prune tick)
                    total_disk = sum(p.stat().st_size for p in cache_dir.rglob('*') if p.is_file())
                    self.assertLessEqual(total_disk, 1400 * 1024, f'CAS cache exceeded LRU budget: {total_disk} bytes')

                    # Memory growth across 25 steady-state iterations must stay bounded (< 4 MiB)
                    diff = snap_after.compare_to(snap_before, 'lineno')
                    net_growth = sum(stat.size_diff for stat in diff if stat.size_diff > 0)
                    self.assertLess(net_growth, 4 * 1024 * 1024, f'Unexpected memory growth: {net_growth} bytes')
                finally:
                    tracemalloc.stop()
                    chorus_cache.clear_memory_cache()

            # Verify last atomic_json state is intact and readable
            final_state = json.loads(json_target.read_text(encoding='utf-8'))
            self.assertEqual(final_state['revision'], 29)
            # Exiting TemporaryDirectory verifies no open Windows file handles remain (would raise WinError 32).


if __name__ == '__main__':
    unittest.main(verbosity=2)
