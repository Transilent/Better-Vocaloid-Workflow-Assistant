import json
import os
import subprocess
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolation import application_copy, reset_state

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import native_path
sys.path.insert(0, str(APP / 'tools'))
from verify_source_layout import verify as verify_source_layout
verify_source_layout(APP)
runtime = Path(sys.executable).parent
qt = runtime / 'Lib/site-packages/PyQt5/Qt5'
env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', QT_QPA_PLATFORM='offscreen', VWA_TESTING='1', BVWA_TESTING='1',
           NUMBA_CACHE_DIR=str(APP / 'work/test-numba-cache'),
           QT_PLUGIN_PATH=str(qt / 'plugins'), QT_QPA_PLATFORM_PLUGIN_PATH=str(qt / 'plugins/platforms'))
env['PATH'] = os.pathsep.join([str(runtime), str(runtime / 'DLLs'), str(qt / 'bin'), str(Path(os.environ['WINDIR']) / 'System32')])
folder = APP / 'work/test-results'
folder.mkdir(parents=True, exist_ok=True)
reports = {}
selected=set(os.environ.get('BVWA_TEST_SELECTION','').split(','))-{''}
names = ('test_update_bridge.py', 'test_update_catalog.py', 'test_test_isolation.py', 'test_review_fixes.py', 'test_update_compatibility.py', 'test_runtime_upgrade.py','test_process_recovery.py','test_dependency_profiles.py','test_cache_integrity.py','test_parallel_download.py','test_slicing_bounds.py','test_v2_backend.py','test_lyrics_sources.py','test_project_exports.py','test_vocal_expression.py','test_vocal_timbre.py','test_pitch_accuracy.py','test_transcription_quality.py','test_processing_steps.py','test_reference_modes.py','test_core.py', 'test_bilibili_network.py', 'test_japanese_midi.py', 'test_chinese_lyrics.py', 'test_updates.py', 'test_auto_restart.py', 'test_gui.py', 'test_workspace_ui.py', 'test_components.py', 'test_publication_workspace.py', 'test_publication_steps.py', 'test_local_music.py', 'test_output_directory.py', 'test_workflow_features.py', 'test_subtitles.py', 'test_publish_browser.py', 'test_publish_adapters.py', 'test_bilibili_dual_cover.py', 'test_launchers.py', 'test_bootstrap.py', 'test_source_layout.py')
names = ('test_reproducible_fixes.py','test_manual_bpm.py','test_note_benchmark.py','test_reference_audio.py','test_reference_corpus.py','test_vocal_partition.py','test_storage_management.py',) + names
names = ('test_bilibili_login.py',) + names
names = ('test_expression_density.py', 'test_calibration_workflow.py', 'test_calibration_detail.py', 'test_calibration_refinement.py', 'test_calibration_channels.py', 'test_calibration_session.py', 'test_optional_flows.py', 'test_expression_calibration.py', 'test_module_audit.py', 'test_video_mixdown.py') + names
names = ('test_task_contract.py', 'test_task_contract_ui.py') + names
names = ('test_context_evidence.py', 'test_context_plan.py', 'test_context_workflow.py') + names
names = ('test_context_structure.py', 'test_gesture_catalog.py', 'test_context_semantics.py', 'test_context_planner.py', 'test_context_proposal.py') + names
names = ('test_ui_input.py','test_style_pitch.py','test_style_presets.py', 'test_context_alignment.py', 'test_context_adapter.py', 'test_context_reading.py', 'test_context_project.py') + names
names = ('test_tuning_transforms.py','test_tuning_engine.py','test_tuning_workflow.py','test_tuning_ui.py','test_tuning_retention.py','test_tuning_curves.py') + names
# Audit repair regression contracts.
names = ('test_commit_recovery.py', 'test_component_recovery.py', 'test_delivery_integrity.py', 'test_operation_interlocks.py', 'test_tuning_ui_state.py') + names
# VWA frontend regression contracts.
names = ('test_frontend_auxiliary.py', 'test_frontend_foundation.py', 'test_frontend_interlocks.py', 'test_frontend_layout.py', 'test_frontend_preparation.py', 'test_frontend_publication.py', 'test_frontend_tuning.py', 'test_video_preview.py') + names
names = ('test_tuning_input_picker.py','test_tuning_loaded_layout.py','test_frontend_art.py','test_frontend_responsive.py',) + names
names = ('test_task_picker_integration.py','test_calibration_task_picker.py','test_tuning_progress.py',) + names
names = ('test_i18n.py', 'test_strength_control.py', 'test_download_sources.py', 'test_chinese_model_install.py') + names
names = ('test_portable_variants.py',) + names
names = ('test_name_migration.py',) + names
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
            result = subprocess.run([sys.executable, '-B', '-u', str(sandbox / 'tests' / name)],
                                    cwd=sandbox, capture_output=True, env=test_env, timeout=180)
            output, code = result.stdout + result.stderr, result.returncode
            for artifact in (sandbox / 'work/test-results').iterdir():
                if artifact.is_file():
                    shutil.copy2(artifact, folder / (name.removesuffix('.py') + '-' + artifact.name))
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b'') + (exc.stderr or b'') + b'\nTest subprocess timed out.\n'
        except Exception:
            import traceback
            output += traceback.format_exc().encode('utf-8')
        (folder / (name + '.log')).write_bytes(output)
        reports[name] = {'exit_code': code, 'seconds': round(time.monotonic()-started, 2), 'isolated': True}
        print(name + (': PASS' if code == 0 else ': FAIL'), flush=True)
        if code:
            print(output.decode('utf-8', errors='replace')[-4000:], flush=True)
(folder / ('regression-subset.json' if selected else 'regression.json')).write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
sys.exit(0 if all(value['exit_code'] == 0 for value in reports.values()) else 1)
