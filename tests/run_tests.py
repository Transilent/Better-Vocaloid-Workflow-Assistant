from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import subprocess
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation import application_copy, reset_state
from runner_contract import classify_output, run_bounded_process

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import native_path
sys.path.insert(0, str(APP / 'tools'))
from verify_source_layout import verify as verify_source_layout
verify_source_layout(APP)
runtime = Path(sys.executable).parent
env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
           QT_QPA_PLATFORM='offscreen', VWA_TESTING='1', BVWA_TESTING='1',
           NUMBA_CACHE_DIR=str(APP / 'work/test-numba-cache'))
env['PATH'] = os.pathsep.join([str(runtime), str(runtime / 'DLLs'), str(Path(os.environ['WINDIR']) / 'System32')])
for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH'):
    env.pop(key, None)
env.setdefault('QT_QPA_FONTDIR', str(Path(os.environ['WINDIR']) / 'Fonts'))
env.setdefault('VWA_TEST_QT6_ROOT', str(APP / 'runtime/qt6'))
folder = APP / 'work/test-results'
folder.mkdir(parents=True, exist_ok=True)
reports = {}
selected=set(os.environ.get('BVWA_TEST_SELECTION','').split(','))-{''}
# Qt 6 tests all enter through the same verified bootstrap module.
qt6_tests = (
    'test_quick_desktop.py', 'test_process_recovery.py',
    'test_quick_video.py', 'test_quick_publication.py', 'test_quick_mixdown_state.py',
    'test_quick_tuning_capabilities.py', 'test_quick_workspace_capabilities.py',
    'test_quick_publication_platform_handoffs_20261111.py', 'test_quick_publication_controls.py',
    'test_quick_calibration_subtitle_recovery.py', 'test_quick_support_parity.py',
    'test_quick_task_safety.py', 'test_quick_tuning_session_parity_20261111.py',
    'test_quick_tuning_score_edit.py', 'test_quick_tuning_style_authoring.py',
    'test_quick_preparation_formats.py',
    'test_quick_priority_layout_20261112.py', 'test_quick_component_video_workspace.py',
    'test_quick_workflow_layout.py',
    'test_quick_operation_diagnostics.py',
    'test_quick_ace_tuning.py',
    'test_chorus_user_journeys.py',
    'test_wave0_shell_journeys.py',
    'test_wave7_management_journeys.py',
    'test_wave5_delivery_journeys.py',
    'test_wave6_convert_journeys.py',
    'test_wave1_preparation_journeys.py',
    'test_wave2_tuning_journeys.py',
    'test_wave3_chorus_journeys.py',
    'test_wave4_mixdown_journeys.py',
    'test_wave8_marathon_journeys.py',
    'test_user_experience_guards.py',
)

# Shared/backend tests and Quick controller tests that do not need Qt 6.
shared_tests = (
    'test_calibration_channels.py', 'test_calibration_session.py', 'test_calibration_workflow.py',
    'test_optional_flows.py',
    'test_startup_preferences.py', 'test_tuning_preset_documents.py', 'test_tuning_preset_workflow.py',
    'test_tuning_project_identity.py', 'test_mixdown_eq_math.py', 'test_mixdown_preset_documents.py',
    'test_preset_documents.py', 'test_preset_library.py', 'test_preset_cli.py', 'test_agent_pitch.py',
    'test_agent_separation_repair.py', 'test_agent_signal_workflow.py', 'test_agent_monophony.py',
    'test_agent_media.py', 'test_agent_views.py', 'test_agent_delivery.py', 'test_agent_song_feedback.py',
    'test_agent_segments.py', 'test_agent_segment_workflow.py', 'test_agent_workflow_dry_run_mcp.py',
    'test_agent_lyric_reconcile.py', 'test_agent_lyric_timeline.py', 'test_agent_timeline_integration.py',
    'test_style_drafts.py', 'test_agent_drafts.py', 'test_agent_pronunciation.py',
    'test_agent_refinement.py', 'test_agent_supervision.py', 'test_agent_export_audit.py',
    'test_agent_reference.py', 'test_agent_structure.py', 'test_agent_preparation.py',
    'test_agent_lyrics.py', 'test_agent_exports.py', 'test_agent_workflow.py', 'test_agent_control.py',
    'test_agent_score.py', 'test_agent_style.py', 'test_agent_mcp.py', 'test_agent_mcp_standard_routing.py',
    'test_agent_mcp_standard_read.py', 'test_agent_mcp_standard_assets.py',
    'test_agent_mcp_standard_decide.py', 'test_agent_mcp_standard_flow.py',
    'test_agent_mcp_standard_integration.py', 'test_i18n.py', 'test_download_sources.py',
    'test_operation_diagnostics.py',
    'test_chinese_model_install.py', 'test_commit_recovery.py', 'test_component_recovery.py',
    'test_runner_contract.py', 'test_pipeline_safety_fixes.py', 'test_tuning_audit_fixes.py',
    'test_preparation_presets.py', 'test_quick_results_external_bindings.py', 'test_quick_media_handoff.py',
    'test_mixdown_realtime_core.py', 'test_tuning_timeline.py', 'test_tuning_pitch_layer.py', 'test_preparation_commands.py',
    'test_quick_results.py', 'test_component_operations.py', 'test_workspace_controller.py',
    'test_workspace_routes.py', 'test_expression_controls.py', 'test_style_catalog.py',
    'test_chorus_workbench.py',
    'test_agent_style_schema.py', 'test_component_health.py', 'test_tuning_transforms.py',
    'test_tuning_engine.py', 'test_tuning_workflow.py', 'test_tuning_retention.py', 'test_tuning_curves.py',
    'test_sv2_tuning.py', 'test_ustx_tuning.py', 'test_vpr_tuning.py', 'test_vsqx_tuning.py', 'test_ace_tuning.py',
    'test_style_pitch.py', 'test_style_presets.py', 'test_context_alignment.py', 'test_context_adapter.py',
    'test_context_reading.py', 'test_context_project.py', 'test_context_structure.py',
    'test_gesture_catalog.py', 'test_context_semantics.py', 'test_context_planner.py',
    'test_context_proposal.py', 'test_context_evidence.py', 'test_context_plan.py',
    'test_context_workflow.py', 'test_task_contract.py', 'test_expression_density.py',
    'test_calibration_detail.py', 'test_calibration_refinement.py', 'test_expression_calibration.py',
    'test_video_mixdown.py', 'test_bilibili_login.py', 'test_mixdown_native_output.py',
    'test_mixdown_realtime_renderer.py', 'test_mixdown_realtime_session.py', 'test_mixdown_limiter.py',
    'test_mixdown_realtime_graph.py', 'test_mixdown_realtime_service.py', 'test_project_memory.py',
    'test_mixdown_export.py', 'test_mixdown_request.py', 'test_mixdown_preview_controller.py',
    'test_mixdown_full_sample_acceptance.py', 'test_mixdown_catalog.py', 'test_audio_mixdown.py',
    'test_reproducible_fixes.py', 'test_manual_bpm.py', 'test_note_benchmark.py', 'test_reference_audio.py',
    'test_reference_corpus.py', 'test_vocal_partition.py', 'test_test_isolation.py',
    'test_dependency_profiles.py', 'test_cache_integrity.py',
    'test_parallel_download.py', 'test_slicing_bounds.py', 'test_v2_backend.py', 'test_lyrics_sources.py',
    'test_project_exports.py', 'test_preparation_vsqx_pitch.py', 'test_preparation_english.py',
    'test_vocal_expression.py', 'test_vocal_timbre.py', 'test_pitch_accuracy.py',
    'test_transcription_quality.py', 'test_core.py', 'test_bilibili_network.py', 'test_japanese_midi.py',
    'test_chinese_lyrics.py', 'test_components.py', 'test_local_music.py', 'test_output_directory.py',
    'test_subtitles.py', 'test_publish_browser.py', 'test_publish_adapters.py',
    'test_bilibili_dual_cover.py', 'test_launchers.py', 'test_source_layout.py',
    'test_architecture_guards.py', 'test_stability_and_perf_guards.py',
    'test_project_convert.py', 'test_project_convert_e2e.py',
    'test_updater_and_infra_upgrade.py', 'test_tier1_infra.py', 'test_tier2_infra.py', 'test_tier3_ux.py',
    'test_amd_gpu_foundation.py',
    'test_hotfix_v211_regressions.py',
    'test_roformer_onnx_experimental.py',
    'test_full_package_migrator.py',
    'test_online_installer.py',
    'test_data_migrator.py',
)

# L1 integration tests that depend on external multi-GB runtime packages/binaries
# omitted from the L0 --light sandbox. In --light mode these are transparently
# reported as SKIP (L1 requires: ...) instead of crashing with false reds, while
# full-runtime runs execute every one of them.
L1_RUNTIME_REQUIREMENTS = {
    # External ffmpeg binary
    'test_core.py': ('ffmpeg',),
    'test_local_music.py': ('ffmpeg',),
    'test_video_mixdown.py': ('ffmpeg',),
    'test_reproducible_fixes.py': ('ffmpeg',),
    'test_quick_publication_controls.py': ('ffmpeg',),
    'test_quick_priority_layout_20261112.py': ('ffmpeg',),
    'test_quick_calibration_subtitle_recovery.py': ('ffmpeg',),
    'test_quick_component_video_workspace.py': ('ffmpeg',),
    'test_quick_workspace_capabilities.py': ('ffmpeg',),
    'test_quick_workflow_layout.py': ('ffmpeg', 'full_runtime'),
    'test_launchers.py': ('full_runtime',),
    'test_mixdown_full_sample_acceptance.py': ('ffmpeg', 'full_runtime'),
    # vocal2midi2 / Qwen3-ASR / ONNX inference runtime
    'test_context_workflow.py': ('vocal2midi2',),
    'test_context_proposal.py': ('vocal2midi2',),
    'test_manual_bpm.py': ('vocal2midi2',),
    'test_dependency_profiles.py': ('vocal2midi2',),
    'test_v2_backend.py': ('vocal2midi2',),
    'test_preparation_english.py': ('vocal2midi2',),
    'test_transcription_quality.py': ('vocal2midi2',),
    'test_chinese_lyrics.py': ('vocal2midi2',),
    'test_slicing_bounds.py': ('vocal2midi2',),
    'test_pitch_accuracy.py': ('vocal2midi2',),
    'test_vocal_expression.py': ('vocal2midi2',),
    'test_project_exports.py': ('vocal2midi2', 'export_runtime'),
    'test_quick_preparation_formats.py': ('vocal2midi2',),
    'test_task_contract.py': ('vocal2midi2',),
    'test_cache_integrity.py': ('vocal2midi2',),
    'test_japanese_midi.py': ('vocal2midi2',),
    'test_note_benchmark.py': ('vocal2midi2',),
    # LibreSVIP / wanakana / pypinyin export runtime
    'test_calibration_workflow.py': ('export_runtime',),
    'test_calibration_session.py': ('export_runtime',),
    'test_tuning_audit_fixes.py': ('export_runtime',),
    'test_preparation_vsqx_pitch.py': ('export_runtime',),
    'test_vocal_timbre.py': ('export_runtime',),
    'test_vpr_tuning.py': ('export_runtime',),
    'test_vsqx_tuning.py': ('export_runtime',),
    'test_ustx_tuning.py': ('export_runtime',),
    'test_project_convert_e2e.py': ('export_runtime',),
    # Optional WORLD acoustic analyzer component
    'test_calibration_channels.py': ('world',),
    'test_expression_calibration.py': ('world',),
    'test_quick_tuning_capabilities.py': ('world',),
    # Playwright browser automation
    'test_publish_browser.py': ('playwright',),
    'test_publish_adapters.py': ('playwright',),
    'test_bilibili_dual_cover.py': ('playwright',),
    # yt-dlp Bilibili downloader
    'test_bilibili_login.py': ('yt_dlp',),
    'test_bilibili_network.py': ('yt_dlp',),
    'test_output_directory.py': ('yt_dlp',),
    # backports.zstd codec for ACEP/UFData
    'test_ace_tuning.py': ('backports_zstd',),
    'test_quick_ace_tuning.py': ('backports_zstd',),
}

names = qt6_tests + shared_tests

if selected - set(names):
    raise ValueError('Unknown selected tests: ' + ', '.join(sorted(selected - set(names))))

target_names = [name for name in names if not selected or name in selected]
jobs = max(1, int(os.environ.get('BVWA_TEST_JOBS', '1') or '1'))
light_mode = os.environ.get('BVWA_TEST_LIGHT', '0') == '1'
sandbox_root = Path(os.environ.get('BVWA_TEST_SANDBOX_ROOT', str(APP / 'work/test-sandboxes')))

if light_mode:
    runnable_names = []
    for name in target_names:
        reqs = L1_RUNTIME_REQUIREMENTS.get(name)
        if reqs:
            reason = f"L1 requires: {', '.join(reqs)} (run without --light for full runtime)"
            reports[name] = {
                'exit_code': 0, 'seconds': 0.0, 'isolated': True, 'light': True,
                'status': 'skipped', 'outcome': 'passed', 'skip_count': 1,
                'skip_source': 'light_env_filter', 'skip_reasons_complete': True,
                'skips': [{'line': None, 'reason': reason, 'message': reason, 'source': 'light_env_filter'}],
                'skip_messages': [reason], 'pass_markers': [],
                'unittest': {'ran': None, 'summary': None, 'skipped': None},
            }
            print(f'{name}: SKIP ({reason})', flush=True)
        else:
            runnable_names.append(name)
    target_names = runnable_names


def _run_one(sandbox, name):
    started = time.monotonic()
    output, code = b'', 1
    try:
        reset_state(sandbox)
        test_env = dict(env, TEMP=str(sandbox / 'work/temp'), TMP=str(sandbox / 'work/temp'),
                        NUMBA_CACHE_DIR=native_path(sandbox / 'cache/numba'))
        test_file = str(sandbox / 'tests' / name)
        command = [sys.executable, '-B', '-u', test_file]
        if name in qt6_tests:
            command = [sys.executable, '-B', '-u', str(sandbox / 'tests/qt6_bootstrap.py'), test_file]
        result = run_bounded_process(command, cwd=sandbox, env=test_env, timeout=180)
        output = result.stdout + result.stderr
        code = 1 if result.timed_out or result.returncode is None else result.returncode
        if result.timed_out:
            output += b'\nTest subprocess timed out; owned process tree reclaimed.\n'
        else:
            for artifact in (sandbox / 'work/test-results').iterdir():
                if artifact.is_file():
                    shutil.copy2(artifact, folder / (name.removesuffix('.py') + '-' + artifact.name))
    except Exception:
        import traceback
        output += traceback.format_exc().encode('utf-8')
    (folder / (name + '.log')).write_bytes(output)
    classification = classify_output(code, output)
    entry = {'exit_code': code, 'seconds': round(time.monotonic() - started, 2),
             'isolated': True, 'light': light_mode, **classification}
    label = {'skipped': 'SKIP', 'passed': 'PASS', 'passed_with_skips': 'PASS WITH SKIP',
             'failed': 'FAIL'}[classification['status']]
    return name, entry, label, code, output


def _sandbox_kwargs():
    import inspect
    sig = inspect.signature(application_copy)
    return {'include_runtime': not light_mode} if 'include_runtime' in sig.parameters else {}


mode_tag = f'L0 light, jobs={jobs}' if light_mode else f'full runtime, jobs={jobs}'
if not target_names:
    print(f'All selected tests deferred to L1 full runtime ({mode_tag}).', flush=True)
elif jobs <= 1 or len(target_names) <= 1:
    print(f'Preparing isolated application ({mode_tag}); installed user state will not be changed.', flush=True)
    with application_copy(APP, sandbox_root, **_sandbox_kwargs()) as sandbox:
        for name in target_names:
            name, entry, label, code, output = _run_one(sandbox, name)
            reports[name] = entry
            print(f'{name}: {label} ({entry["seconds"]}s)', flush=True)
            if code:
                print(output.decode('utf-8', errors='replace')[-4000:], flush=True)
else:
    print(f'Preparing isolated application ({mode_tag}); installed user state will not be changed.', flush=True)
    worker_count = min(jobs, len(target_names))
    buckets = [target_names[i::worker_count] for i in range(worker_count)]

    def run_bucket(bucket):
        rows = []
        with application_copy(APP, sandbox_root, **_sandbox_kwargs()) as sandbox:
            for name in bucket:
                rows.append(_run_one(sandbox, name))
        return rows

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(run_bucket, bucket) for bucket in buckets if bucket]
        for fut in as_completed(futures):
            for name, entry, label, code, output in fut.result():
                reports[name] = entry
                print(f'{name}: {label} ({entry["seconds"]}s)', flush=True)
                if code:
                    print(output.decode('utf-8', errors='replace')[-4000:], flush=True)

(folder / ('regression-subset.json' if selected else 'regression.json')).write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(0 if all(value['exit_code'] == 0 for value in reports.values()) else 1)
