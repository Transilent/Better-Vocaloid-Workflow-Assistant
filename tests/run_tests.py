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
    'test_quick_priority_layout_20261112.py',
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
    'test_chinese_model_install.py', 'test_commit_recovery.py', 'test_component_recovery.py',
    'test_runner_contract.py', 'test_pipeline_safety_fixes.py', 'test_tuning_audit_fixes.py',
    'test_preparation_presets.py', 'test_quick_results_external_bindings.py', 'test_quick_media_handoff.py',
    'test_mixdown_realtime_core.py', 'test_tuning_timeline.py', 'test_preparation_commands.py',
    'test_quick_results.py', 'test_component_operations.py', 'test_workspace_controller.py',
    'test_workspace_routes.py', 'test_expression_controls.py', 'test_style_catalog.py',
    'test_agent_style_schema.py', 'test_component_health.py', 'test_tuning_transforms.py',
    'test_tuning_engine.py', 'test_tuning_workflow.py', 'test_tuning_retention.py', 'test_tuning_curves.py',
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
    'test_project_exports.py', 'test_vocal_expression.py', 'test_vocal_timbre.py', 'test_pitch_accuracy.py',
    'test_transcription_quality.py', 'test_core.py', 'test_bilibili_network.py', 'test_japanese_midi.py',
    'test_chinese_lyrics.py', 'test_components.py', 'test_local_music.py', 'test_output_directory.py',
    'test_subtitles.py', 'test_publish_browser.py', 'test_publish_adapters.py',
    'test_bilibili_dual_cover.py', 'test_launchers.py', 'test_source_layout.py',
)

names = qt6_tests + shared_tests

if selected - set(names):
    raise ValueError('Unknown selected tests: ' + ', '.join(sorted(selected - set(names))))
print('Preparing isolated application; installed user state will not be changed.', flush=True)
with application_copy(APP, APP / 'work/test-sandboxes') as sandbox:
    for name in names:
        if selected and name not in selected: continue
        started = time.monotonic()
        output, code = b'', 1
        try:
            reset_state(sandbox)
            test_env = dict(env, TEMP=str(sandbox / 'work/temp'), TMP=str(sandbox / 'work/temp'),
                            NUMBA_CACHE_DIR=native_path(sandbox / 'cache/numba'))
            test_file = str(sandbox / 'tests' / name)
            command = [sys.executable, '-B', '-u', test_file]
            if name in qt6_tests:
                # Each Qt 6 test gets its own process, with an explicit verified runtime.
                command = [sys.executable, '-B', '-u', str(sandbox / 'tests/qt6_bootstrap.py'), test_file]
            result = run_bounded_process(command,
                                         cwd=sandbox, env=test_env, timeout=180)
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
        reports[name] = {'exit_code': code, 'seconds': round(time.monotonic()-started, 2),
                         'isolated': True, **classification}
        label = {'skipped': 'SKIP', 'passed': 'PASS', 'passed_with_skips': 'PASS WITH SKIP',
                 'failed': 'FAIL'}[classification['status']]
        print(name + ': ' + label, flush=True)
        if code:
            print(output.decode('utf-8', errors='replace')[-4000:], flush=True)
(folder / ('regression-subset.json' if selected else 'regression.json')).write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(0 if all(value['exit_code'] == 0 for value in reports.values()) else 1)
