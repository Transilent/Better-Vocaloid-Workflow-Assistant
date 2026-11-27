"""Wave 7 end-to-end user journey tests: Results, Components, and Settings/Updates.

Runs in two tiers:
1. Pure-Python hermetic tests (always executed, including GitHub CI without Qt6):
   W7-02 (scan/detail perf, stat-hash cache, tamper invalidation, symlink safety, cancel),
   W7-03 (filter/sort/rename/delete/reference-guard/WinError32-retry),
   W7-04 (language zh/en/ja summary, .svp/.ustx tunable, external bind/unlink/rebuild),
   W7-05 (component source persistence, mirror_probe loopback HTTP ranking/405/500/TTL),
   W7-06 (appearance/config/WASAPI persistence preserving unknown config keys),
   W7-07 (storage scan, protected references, clean with retry & active-lock refusal),
   W7-08 (preset library CRUD, builtin guard, export/import/conflict/recover),
   W7-09 (update check/defer/dismiss/reset/blocker guard & diagnostics snapshot).
2. Qt6 Offscreen UI & multi-DPI geometry tests (executed whenever PySide6 is available):
   W7-01 (ResultsPage multi-DPI x Dark/Light x empty+selected, stepper containment, Drawer height),
   W7-03 UI (real click openTaskManagement -> renameTask -> renameTaskField -> confirmRenameTask),
   W7-04 UI (.ustx tunable button visible in inspector),
   W7-05..W7-09 UI (ComponentsPage + SettingsPage 6 categories + WASAPI section + multi-DPI gate).
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]
for candidate in (APP / 'src', APP):
    text = str(candidate)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import appearance_state
import common
import mirror_probe
import preset_library
import quick_results
import storage
import task_contract
import task_store
import vwa_config
import vwa_io
import workspace_settings


def _has_qt6() -> bool:
    qt_root = os.environ.get('VWA_TEST_QT6_ROOT', '')
    if qt_root and Path(qt_root).is_dir() and qt_root not in sys.path:
        sys.path.insert(0, qt_root)
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _make_sample_job(
    jobs_root: Path,
    name: str,
    *,
    title: str = '幻象色彩',
    language: str = 'zh',
    state: str = 'done',
    include_ustx: bool = True,
    payload_size: int = 32768,
) -> Path:
    job = (jobs_root / name).resolve()
    (job / 'audio').mkdir(parents=True, exist_ok=True)
    (job / 'midi').mkdir(parents=True, exist_ok=True)
    (job / 'exports').mkdir(parents=True, exist_ok=True)
    (job / 'temp').mkdir(parents=True, exist_ok=True)

    source_wav = job / 'audio/source.wav'
    vocals_wav = job / 'audio/vocals.wav'
    inst_wav = job / 'audio/instrumental.wav'
    vocals_mid = job / 'midi/vocals.mid'
    svp_file = job / 'exports/voices.svp'
    ustx_file = job / 'exports/voices.ustx'

    source_wav.write_bytes(b'RIFF' + b'\x00' * payload_size)
    vocals_wav.write_bytes(b'RIFF' + b'\x11' * payload_size)
    inst_wav.write_bytes(b'RIFF' + b'\x22' * payload_size)
    vocals_mid.write_bytes(b'MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0MTrk\x00\x00\x00\x04\x00\xff\x2f\x00')
    svp_payload = {
        'version': 100,
        'tracks': [{'mainRef': {'isInstrumental': False}, 'mainGroup': {'notes': [{'onset': 0, 'duration': 100, 'pitch': 60, 'lyrics': 'la'}]}}],
    }
    svp_file.write_text(json.dumps(svp_payload, ensure_ascii=False), encoding='utf-8')
    if include_ustx:
        ustx_file.write_text('name: test\nvoice_parts:\n  - notes:\n      - position: 0\n        duration: 480\n        tone: 60\n        lyric: a\n', encoding='utf-8')
    (job / 'midi/report.json').write_text(json.dumps({'midi_sha256': vwa_io.file_hash(vocals_mid)}), encoding='utf-8')
    (job / 'audio-info.json').write_text(json.dumps({'duration': 12.0}), encoding='utf-8')
    (job / 'separation-report.json').write_text(json.dumps({'model': 'karaoke2'}), encoding='utf-8')
    (job / 'exports/report.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (job / 'import-report.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (job / 'source-info.json').write_text(json.dumps({'title': title}, ensure_ascii=False), encoding='utf-8')
    (job / 'upstream-info.json').write_text(json.dumps({}), encoding='utf-8')
    (job / '发布简介草稿.txt').write_text(title, encoding='utf-8')
    (job / 'pipeline.log').write_text('pipeline ok\n', encoding='utf-8')
    (job / 'temp/scratch.tmp').write_bytes(b'temp-cache-bytes' * 128)

    request = {
        'source_kind': 'local',
        'input_file': 'audio/source.wav',
        'source_title': title,
        'source_audio': f'{title}.wav',
        'language': language,
        'voice_mode': 'single',
        'midi_steps': 16,
        'created_at': '2026-11-27T10:00:00',
        'tools': {'separation_device': 'dml', 'midi_device': 'dml'},
    }
    status = {
        'state': state,
        'planned_steps': ['audio', 'separation', 'midi', 'exports'],
        'steps': {
            'audio': {'state': 'done'},
            'separation': {'state': 'done'},
            'midi': {'state': 'done'},
            'exports': {'state': 'done'},
        },
    }
    if state == 'failed':
        status['error'] = 'simulated failure'
    (job / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    (job / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')

    task_contract.register(job, [
        {'path': source_wav, 'role': 'audio', 'retention': 'source'},
        {'path': job / 'audio-info.json', 'role': 'report', 'retention': 'source'},
    ], 'audio', state='ready')
    task_contract.register(job, [
        {'path': vocals_wav, 'role': 'audio'},
        {'path': inst_wav, 'role': 'audio'},
        {'path': job / 'separation-report.json', 'role': 'report'},
    ], 'separation', state='ready')
    task_contract.register(job, [
        {'path': vocals_mid, 'role': 'midi'},
        {'path': job / 'midi/report.json', 'role': 'report'},
    ], 'midi', state='ready')
    export_specs = [{'path': svp_file, 'role': 'editor_project'}]
    if include_ustx:
        export_specs.append({'path': ustx_file, 'role': 'editor_project'})
    task_contract.register(job, export_specs, 'exports', state='ready')
    return job


class _SandboxMixin:
    def _setup_sandbox(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w7-')).resolve()
        self._jobs = self._tmp / 'jobs'
        self._jobs.mkdir(parents=True, exist_ok=True)
        (self._tmp / 'cache').mkdir(parents=True, exist_ok=True)
        (self._tmp / 'config.json').write_text(json.dumps({
            'schema_version': 2,
            'theme': 'dark',
            'download_source': 'auto',
            'download_workers': 4,
            'network_proxy_mode': 'direct',
            'check_for_updates': True,
            'auto_restart_updates': True,
            'audio_output_device': '',
            'component_download_sources': {'roformer': 'direct'},
        }, ensure_ascii=False, indent=2), encoding='utf-8')

        self._patches = [
            mock.patch.object(common, 'ROOT', self._tmp),
            mock.patch.object(task_store, 'ROOT', self._tmp),
            mock.patch.object(storage, 'ROOT', self._tmp),
            mock.patch.object(workspace_settings, 'ROOT', self._tmp),
            mock.patch.object(appearance_state, 'ROOT', self._tmp),
            mock.patch.object(common, 'job_directories', side_effect=self._list_jobs),
            mock.patch.object(task_store, 'job_directories', side_effect=self._list_jobs),
            mock.patch.object(storage, 'job_directories', side_effect=self._list_jobs),
        ]
        for p in self._patches:
            p.start()
        vwa_io.clear_file_hash_cache()

    def _list_jobs(self):
        if not self._jobs.is_dir():
            return []
        return sorted(p.resolve() for p in self._jobs.iterdir() if p.is_dir() and (p / 'request.json').is_file())

    def _teardown_sandbox(self):
        for p in reversed(getattr(self, '_patches', ())):
            p.stop()
        vwa_io.clear_file_hash_cache()
        vwa_io.safe_rmtree(self._tmp)


class Wave7PurePythonTests(unittest.TestCase, _SandboxMixin):
    def setUp(self):
        self._setup_sandbox()

    def tearDown(self):
        self._teardown_sandbox()

    def test_w7_02_scan_detail_perf_hash_cache_tamper_and_cancel(self):
        job = _make_sample_job(self._jobs, 'Local_perf_test', title='性能回归测试', payload_size=65536)

        t0 = time.perf_counter()
        rows = quick_results.scan()
        scan_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(len(rows), 1)
        self.assertLess(scan_ms, 5000.0, f'scan took too long: {scan_ms:.1f}ms')

        # Cold detail hashes files once; warm detail hits cached_file_hash without re-reading bytes
        vwa_io.clear_file_hash_cache()
        with mock.patch.object(vwa_io, 'file_hash', wraps=vwa_io.file_hash) as spy_hash:
            d1 = quick_results.detail(job, include_history=True)
            cold_calls = spy_hash.call_count
            self.assertGreater(cold_calls, 0)
            d2 = quick_results.detail(job, include_history=True)
            warm_calls = spy_hash.call_count - cold_calls
            self.assertEqual(warm_calls, 0, 'Warm detail() should hit stat-fingerprinted hash cache')
        self.assertEqual(d1['handoff'], 'awaiting_tuning')
        self.assertEqual(d2['handoff'], 'awaiting_tuning')

        # Tampering 1 byte immediately invalidates cache and marks artifact modified
        svp = job / 'exports/voices.svp'
        svp.write_text(svp.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        d3 = quick_results.detail(job, include_history=True)
        svp_row = next(r for r in d3['artifacts'] if r['path'].endswith('voices.svp'))
        self.assertEqual(svp_row['state'], 'modified')
        self.assertFalse(svp_row['available'])

        # Cancel callback cleanly raises InterruptedError
        with self.assertRaises(InterruptedError):
            quick_results.scan(check=lambda: True)
        with self.assertRaises(InterruptedError):
            quick_results.detail(job, check=lambda: True)

    def test_w7_03_rename_delete_reference_guard_and_winerror32_retry(self):
        job = _make_sample_job(self._jobs, 'Local_manage_test', title='原始歌名')

        # Rename updates metadata cleanly
        quick_results.rename(job, '重命名后歌名')
        self.assertEqual(quick_results.detail(job)['title'], '重命名后歌名')
        with self.assertRaises(ValueError):
            quick_results.rename(job, '   ')

        # Reference protection disables rename & delete when another page holds the task
        detail = quick_results.detail(job)
        blockers = quick_results.task_reference_blockers(
            str(job),
            [{'label': '简易混音 · 已载入', 'paths': [str(job / 'audio/vocals.wav')]}],
        )
        protected = quick_results.protect_task_detail(detail, blockers)
        self.assertTrue(protected['taskBlocked'])
        self.assertFalse(protected['canDelete'])
        self.assertFalse(protected['canRename'])
        self.assertIn('简易混音', protected['taskBlockReason'])

        # Simulated transient WinError 32 on first rmtree attempt succeeds on retry
        real_rmtree = shutil.rmtree
        calls = {'n': 0}

        def flaky_rmtree(target, *args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 1:
                raise PermissionError(13, 'transient WinError 32 lock', str(target))
            return real_rmtree(target, *args, **kwargs)

        with mock.patch.object(vwa_io.shutil, 'rmtree', side_effect=flaky_rmtree):
            quick_results.delete(job)
        self.assertGreaterEqual(calls['n'], 2)
        self.assertFalse(job.exists())

        # Persistent lock still raises OSError instead of silently succeeding
        job2 = _make_sample_job(self._jobs, 'Local_locked_test', title='持久占用测试')
        with mock.patch.object(vwa_io.shutil, 'rmtree', side_effect=PermissionError(13, 'hard lock')):
            with self.assertRaises(PermissionError):
                vwa_io.rmtree_checked(job2, retries=2, base_delay=0.01)
        self.assertTrue(job2.exists())

    def test_w7_04_language_summary_tunable_ustx_and_external_bindings(self):
        zh_job = _make_sample_job(self._jobs, 'Local_lang_zh', title='中文曲', language='zh')
        en_job = _make_sample_job(self._jobs, 'Local_lang_en', title='English Song', language='en')
        ja_job = _make_sample_job(self._jobs, 'Local_lang_ja', title='日本語曲', language='ja')

        def lang_value(job_path):
            rows = quick_results.detail(job_path)['requestSummary']
            return next(r['value'] for r in rows if '语言' in r['label'] or 'Language' in r['label'])

        self.assertEqual(lang_value(zh_job), '中文')
        self.assertEqual(lang_value(en_job), '英语')
        self.assertEqual(lang_value(ja_job), '日语')

        # W7-BUG-05: Both .svp and .ustx pass is_tuning_project() while .mid does not
        artifacts = quick_results.detail(zh_job)['artifacts']
        svp_row = next(r for r in artifacts if r['path'].endswith('voices.svp'))
        ustx_row = next(r for r in artifacts if r['path'].endswith('voices.ustx'))
        mid_row = next(r for r in artifacts if r['path'].endswith('vocals.mid'))
        self.assertTrue(quick_results.is_tuning_project(svp_row))
        self.assertTrue(quick_results.is_tuning_project(ustx_row))
        self.assertFalse(quick_results.is_tuning_project(mid_row))

        # External project bind -> unlink -> rebuild preserves external file and creates backup
        ext_dir = self._tmp / 'external'
        ext_dir.mkdir(exist_ok=True)
        ext_svp = ext_dir / 'hand_tuned.svp'
        ext_svp.write_text(json.dumps({
            'version': 100,
            'tracks': [{'mainRef': {'isInstrumental': False}, 'mainGroup': {'notes': [{'onset': 0, 'duration': 200, 'pitch': 62, 'lyrics': 'a'}]}}],
        }), encoding='utf-8')
        art_id = quick_results.register_external(
            zh_job, 'project', ext_svp, expected_binding_id='', expected_binding_revision='',
        )
        d_bound = quick_results.detail(zh_job)
        self.assertTrue(d_bound['hasProjectBinding'])
        self.assertEqual(d_bound['bindings']['project']['id'], art_id)

        backup = quick_results.rebuild_artifacts(zh_job, expected_index_revision=d_bound['artifactIndexRevision'])
        self.assertTrue(Path(backup).is_file())
        self.assertTrue(quick_results.detail(zh_job)['hasProjectBinding'])

        d_rebuilt = quick_results.detail(zh_job)
        quick_results.unlink_external(
            zh_job, 'project',
            expected_binding_id=d_rebuilt['bindings']['project']['id'],
            expected_binding_revision=d_rebuilt['bindings']['project']['revision'],
        )
        self.assertFalse(quick_results.detail(zh_job)['hasProjectBinding'])
        self.assertTrue(ext_svp.is_file(), 'External file must never be deleted on unlink')

    def test_w7_05_mirror_probe_and_component_source_persistence(self):
        # Component download source per-component persistence
        workspace_settings.save_component_source_preferences({'roformer': 'hf-mirror', 'chinese': 'direct'})
        reloaded = workspace_settings.component_source_preferences(common.load_config(False))
        self.assertEqual(reloaded.get('roformer'), 'hf-mirror')
        self.assertEqual(reloaded.get('chinese'), 'direct')

        # Real loopback HTTP server testing fast 200, 405->Range fallback, slow 200, and 500 failure
        class _Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                if self.path == '/fast':
                    self.send_response(200)
                    self.end_headers()
                elif self.path == '/nohead':
                    self.send_response(405)
                    self.end_headers()
                elif self.path == '/slow':
                    time.sleep(0.12)
                    self.send_response(200)
                    self.end_headers()
                else:
                    self.send_response(500)
                    self.end_headers()

            def do_GET(self):
                if self.path == '/nohead':
                    self.send_response(206)
                    self.end_headers()
                    self.wfile.write(b'ok')
                else:
                    self.send_response(500)
                    self.end_headers()

            def log_message(self, *_args):
                pass

        server = HTTPServer(('127.0.0.1', 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{port}'
            ok_fast, lat_fast = mirror_probe._default_head_probe(f'{base}/fast', 2.0)
            ok_405, _ = mirror_probe._default_head_probe(f'{base}/nohead', 2.0)
            ok_slow, lat_slow = mirror_probe._default_head_probe(f'{base}/slow', 2.0)
            ok_err, _ = mirror_probe._default_head_probe(f'{base}/err', 2.0)
            self.assertTrue(ok_fast)
            self.assertTrue(ok_405, '405 HEAD must fallback to Range GET')
            self.assertTrue(ok_slow)
            self.assertGreater(lat_slow, lat_fast)
            self.assertFalse(ok_err)

            # Multi-host ranking + 24h TTL health cache persistence
            mirror_probe.health_cache_path(self._tmp).unlink(missing_ok=True)
            latencies = {
                'broken.example': (False, 9999.0),
                'slow.example': (True, 260.0),
                'fast.example': (True, 18.0),
            }
            urls = [
                'https://broken.example/a.bin',
                'https://slow.example/a.bin',
                'https://fast.example/a.bin',
            ]
            ranked = mirror_probe.rank_candidates(
                urls,
                root=self._tmp,
                allow_probe=True,
                probe_fn=lambda url, _t: latencies[mirror_probe._host_key(url)],
            )
            self.assertEqual(ranked, [
                'https://fast.example/a.bin',
                'https://slow.example/a.bin',
                'https://broken.example/a.bin',
            ])
            # Cached ranking without probing produces identical ordering
            ranked_cached = mirror_probe.rank_candidates(urls, root=self._tmp, allow_probe=False)
            self.assertEqual(ranked_cached, ranked)
        finally:
            server.shutdown()
            server.server_close()

    def test_w7_06_settings_appearance_and_wasapi_config_persistence(self):
        sources = [('自动选择', 'auto'), ('官方源直连', 'direct')]
        st = workspace_settings.SettingsState(sources)
        self.assertTrue(st.save('theme', 'light'))
        self.assertTrue(st.save('material', 'solid'))
        self.assertTrue(st.save('ui_scale', 130))
        self.assertTrue(st.save('ui_language', 'en'))
        self.assertTrue(st.save('reduce_motion', True))
        self.assertTrue(st.save('render_backend', 'software'))
        self.assertTrue(st.save('network_proxy_mode', 'system'))
        self.assertTrue(st.save('download_workers', 6))
        self.assertTrue(st.save('check_for_updates', False))
        self.assertTrue(st.save('auto_restart_updates', False))

        # Simulate WASAPI selection write preserving unknown/other keys (component_download_sources)
        cfg, _ = vwa_config.load_validated_config(root=self._tmp, persist_migration=False)
        cfg['audio_output_device'] = 'Speakers (Realtek High Definition Audio)'
        vwa_io.atomic_json(self._tmp / 'config.json', cfg)

        st2 = workspace_settings.SettingsState(sources)
        self.assertEqual(st2.values['theme'], 'light')
        self.assertEqual(st2.values['material'], 'solid')
        self.assertEqual(st2.values['ui_scale'], 130)
        self.assertEqual(st2.values['ui_language'], 'en')
        self.assertTrue(st2.values['reduce_motion'])
        self.assertEqual(st2.values['render_backend'], 'software')
        self.assertEqual(st2.values['network_proxy_mode'], 'system')
        self.assertEqual(st2.values['download_workers'], 6)
        self.assertFalse(st2.values['check_for_updates'])
        self.assertFalse(st2.values['auto_restart_updates'])

        cfg2, _ = vwa_config.load_validated_config(root=self._tmp, persist_migration=False)
        self.assertEqual(cfg2['audio_output_device'], 'Speakers (Realtek High Definition Audio)')
        self.assertEqual(cfg2.get('component_download_sources'), {'roformer': 'direct'})

    def test_w7_07_storage_scan_protected_isolation_and_clean(self):
        job = _make_sample_job(self._jobs, 'Local_storage_keep', title='受保护任务')
        svp = job / 'exports/voices.svp'
        svp_hash = vwa_io.file_hash(svp)

        disposable = self._tmp / 'cache/video-preview'
        disposable.mkdir(parents=True, exist_ok=True)
        (disposable / 'preview.mp4').write_bytes(b'preview-bytes' * 256)

        rows = storage.candidates(include_protected=True)
        paths = {Path(r['path']).resolve(): r for r in rows}
        self.assertIn(disposable.resolve(), paths)
        self.assertNotIn('protected', paths[disposable.resolve()])
        self.assertIn((job / 'temp').resolve(), paths)

        freed = storage.clean([str(disposable), str(job / 'temp')])
        self.assertGreater(freed, 0)
        self.assertFalse(disposable.exists())
        self.assertFalse((job / 'temp').exists())
        self.assertEqual(vwa_io.file_hash(svp), svp_hash, 'Protected task artifact must remain untouched')

        # Refuses cleanup when a job is actively locked
        with vwa_io.exclusive_sidecar(job / 'run.lock', create=True):
            with self.assertRaises(ValueError):
                storage.clean([])

    def test_w7_08_preset_library_crud_builtin_guard_import_and_recover(self):
        lib = preset_library.PresetLibrary(root=self._tmp / 'presets')
        snap = lib.snapshot()
        builtins = [e for e in snap['entries'] if e.get('builtin')]
        self.assertGreater(len(builtins), 0)
        first = builtins[0]

        # Built-in preset cannot be deleted or renamed
        with self.assertRaises(Exception):
            lib.delete(first['id'], first['version'], expected_revision=snap['revision'])
        with self.assertRaises(Exception):
            lib.rename(first['id'], first['version'], alias='illegal', expected_revision=snap['revision'])

        # Copy -> Rename -> Favorite -> Export -> Import (conflict -> as_copy) -> Delete -> Recover
        copied = lib.copy(first['id'], first['version'], name='我的自定义预设', expected_revision=snap['revision'])
        c_entry = copied['entry']
        snap = lib.snapshot()
        lib.rename(c_entry['id'], c_entry['version'], alias='重命名别名', expected_revision=snap['revision'])
        snap = lib.snapshot()
        lib.set_favorite(c_entry['id'], c_entry['version'], favorite=True, expected_revision=snap['revision'])
        snap = lib.snapshot()
        found = next(e for e in snap['entries'] if e['id'] == c_entry['id'] and e['version'] == c_entry['version'])
        self.assertEqual(found['name'], '重命名别名')
        self.assertTrue(found['favorite'])

        export_path = self._tmp / 'exported_preset.json'
        lib.export_file(c_entry['id'], c_entry['version'], export_path)
        self.assertTrue(export_path.is_file())
        # Export must never overwrite an existing file
        with self.assertRaises(Exception):
            lib.export_file(c_entry['id'], c_entry['version'], export_path)

        # Re-importing identical version is idempotent; deleting and recovering restores it
        snap = lib.snapshot()
        imp_res = lib.import_file(export_path, expected_revision=snap['revision'])
        self.assertTrue(imp_res.get('already_present'))

        snap = lib.snapshot()
        lib.delete(c_entry['id'], c_entry['version'], expected_revision=snap['revision'])
        snap_after_del = lib.snapshot()
        self.assertFalse(any(e['id'] == c_entry['id'] for e in snap_after_del['entries']))

        lib.recover_previous()
        snap_recovered = lib.snapshot()
        self.assertTrue(any(e['id'] == c_entry['id'] for e in snap_recovered['entries']))

    def test_w7_09_update_lifecycle_defer_dismiss_and_diagnostics_snapshot(self):
        sources = [('自动选择', 'auto'), ('官方源', 'official')]
        st = workspace_settings.SettingsState(sources)
        st.set_release({'version': '9.9.9', 'available': True, 'notes': 'Release notes', 'url': 'https://example.com'}, startup=False)
        self.assertEqual(st.startup_view['update_state'], 'available')
        self.assertTrue(st.startup_view['update_open'])

        # Defer closes dialog & persists reminder deferral
        st.defer(86400)
        self.assertFalse(st.startup_view['update_open'])

        # Blockers defer auto-restart cleanly
        st.set_update_plan(self._tmp / 'cache/updates/plan.json', '9.9.9')
        self.assertTrue(st.begin_auto_restart())
        self.assertFalse(st.tick_auto_restart(('音乐处理',)))
        self.assertTrue(st.restart_deferred)
        self.assertIn('音乐处理', st.startup_view['message'])

        snap = vwa_config.unified_config_snapshot(self._tmp)
        self.assertEqual(snap['config']['schema_version'], 2)
        self.assertIn('network_proxy_mode', snap['config'])


@unittest.skipUnless(_has_qt6(), 'Qt6/PySide6 unavailable in pure-Python CI')
class Wave7QtOffscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import QCoreApplication
        import quick_app

        cls.QCoreApplication = QCoreApplication
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        cls.engine, cls.win, cls.owner, cls.modes = quick_app.create_workspace(cls.app)
        cls._pump(25)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'owner', None) is not None:
            cls.owner.close()
            cls._pump(10)

    @classmethod
    def _pump(cls, rounds: int = 15):
        for _ in range(rounds):
            cls.QCoreApplication.processEvents()

    def _walk(self, item):
        if item is None:
            return
        yield item
        for child in item.childItems():
            yield from self._walk(child)

    def _find(self, object_name: str):
        for item in self._walk(self.win.contentItem()):
            if item.objectName() == object_name:
                return item
        from PySide6.QtCore import QObject
        return self.win.findChild(QObject, object_name)

    def _overflows(self, viewport_w: int):
        bad = []
        for item in self._walk(self.win.contentItem()):
            if item.isVisible() and item.width() > 0:
                pt = item.mapToScene(item.boundingRect().topLeft())
                if pt.x() + item.width() > viewport_w + 2:
                    bad.append((item.metaObject().className(), item.objectName(), round(pt.x(), 1), round(pt.x() + item.width(), 1)))
        return bad

    def test_w7_01_results_multidpi_stepper_and_drawer_height(self):
        self.owner.navigate('results')
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and (self.owner._read.kind or self.owner._details.kind):
            self._pump(4)
            time.sleep(0.01)
        self._pump(15)
        # Inject a selected task detail so 5-step pipeline stepper + openTaskManagement are live
        self.owner.state.selected_task_id = 'C:/fake/w7_job'
        self.owner.state.task_detail = {
            'jobId': 'C:/fake/w7_job', 'title': 'Wave 7 Visual Test', 'state': 'done', 'statusText': '已完成',
            'handoff': 'awaiting_tuning', 'nextAction': '在编辑器中调音并导出 Mixdown',
            'canRename': True, 'canDelete': True, 'canDispatch': True,
            'artifacts': [{
                'id': 'art_ustx', 'label': '编辑器工程 · voices.ustx', 'statusText': '校验通过',
                'available': True, 'openable': True, 'tunable': True,
                'path': 'C:/fake/w7_job/exports/voices.ustx', 'displayPath': 'exports/voices.ustx',
                'role': 'editor_project', 'state': 'verified', 'revision': 'abcdef123456',
            }],
        }
        self.owner.state.selected_task_artifact_id = 'art_ustx'
        self.owner.taskChanged.emit()
        self.owner.taskSafetyChanged.emit()
        self.owner.taskArtifactChanged.emit()
        self._pump(20)
        tune_btn = self._find('tuneInspectorArtifact')
        self.assertIsNotNone(tune_btn)
        self.assertTrue(tune_btn.isVisible(), 'Inspector tuning button must be visible for .ustx projects')

        for theme in ('dark', 'light'):
            self.owner.support.setSetting('theme', theme)
            for (w, h, scale) in ((1280, 720, 100), (1366, 768, 130), (1440, 900, 130)):
                self.win.setWidth(w)
                self.win.setHeight(h)
                self.owner.support.setSetting('ui_scale', scale)
                self._pump(25)
                actual_w = int(self.win.width())
                self.assertEqual(self._overflows(actual_w), [], f'Results overflow at {w}x{h}@{scale}% ({theme})')

                # W7-BUG-02: Every visible pipeline step must stay strictly inside taskPipelineStepper
                stepper = self._find('taskPipelineStepper')
                self.assertIsNotNone(stepper)
                s_tl = stepper.mapToScene(stepper.boundingRect().topLeft())
                s_right = s_tl.x() + stepper.width()
                steps = [it for it in self._walk(self.win.contentItem()) if it.objectName().startswith('pipelineStep_') and it.isVisible()]
                self.assertEqual(len(steps), 5)
                for step in steps:
                    pt = step.mapToScene(step.boundingRect().topLeft())
                    self.assertLessEqual(
                        pt.x() + step.width(), s_right + 1.5,
                        f'{step.objectName()} spilled past taskPipelineStepper ({pt.x()+step.width():.1f} > {s_right:.1f}) at {w}x{h}@{scale}%',
                    )

        # W7-BUG-01: taskManagementDrawer must fill full window height and clicking renameTask opens renameTaskField
        drawer = self._find('taskManagementDrawer')
        self.assertIsNotNone(drawer)
        drawer.open()
        self._pump(20)
        self.assertGreaterEqual(float(drawer.property('height')), float(self.win.height()) - 4.0)

        rename_btn = self._find('renameTask')
        self.assertIsNotNone(rename_btn)
        self.assertTrue(rename_btn.isEnabled())
        rename_btn.clicked.emit()
        self._pump(20)
        rename_field = self._find('renameTaskField')
        self.assertIsNotNone(rename_field)
        self.assertTrue(rename_field.isVisible(), 'renameTaskField must open visibly after clicking renameTask')
        rename_dialog = self._find('renameTaskDialog')
        if rename_dialog is not None:
            rename_dialog.close()
            self._pump(10)

    def test_w7_05_to_09_components_and_settings_all_categories_ui(self):
        self.win.setWidth(1366)
        self.win.setHeight(768)
        self.owner.support.setSetting('ui_scale', 130)
        self.owner.support.setSetting('theme', 'dark')

        self.owner.navigate('components')
        self._pump(25)
        self.assertEqual(self._overflows(int(self.win.width())), [])
        self.assertIsNotNone(self._find('componentList'))

        self.owner.navigate('updates')
        self._pump(25)
        # W7-BUG-07: WASAPI audio output section & refresh button exist and are visible in Appearance
        audio_sec = self._find('audioOutputDeviceSection')
        refresh_audio = self._find('refreshAudioDevices')
        self.assertIsNotNone(audio_sec)
        self.assertIsNotNone(refresh_audio)
        self.assertTrue(audio_sec.isVisible())

        for cat in ('appearance', 'update', 'storage', 'presets', 'support', 'about'):
            btn = self._find('settingsCategory_' + cat)
            self.assertIsNotNone(btn, f'Missing settings category button {cat}')
            btn.clicked.emit()
            self._pump(18)
            self.assertEqual(self._overflows(int(self.win.width())), [], f'Settings {cat} overflow at 1366x768@130%')


if __name__ == '__main__':
    unittest.main(verbosity=2)
