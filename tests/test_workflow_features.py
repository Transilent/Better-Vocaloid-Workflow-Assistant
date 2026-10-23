"""Real isolated workflow execution, persistence, task safety and Qt canvas interaction."""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP/'vendor')]
import common
import pipeline
import workflow
import task_store
import storage
import progress_state
import error_recovery
import numpy as np
import soundfile as sf
import mido
from PyQt5.QtCore import Qt, QPointF, QEvent, QThread, pyqtSignal
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton, QInputDialog
from app import Window
# Resolve immutable component/runtime module paths before isolating private state.
import workspace_ui, components_ui, updates_ui, subtitles_ui, results_ui, processing_workflow, storage_ui

application = QApplication([])
config = common.load_config()
with tempfile.TemporaryDirectory(prefix='workflow-features-', dir=APP/'work') as folder:
    root = Path(folder)/'assistant'
    root.mkdir()
    common.atomic_json(root/'config.json', config)
    with contextlib.ExitStack() as stack:
        for module in (common, workflow, task_store, storage):
            stack.enter_context(patch.object(module, 'ROOT', root))
        source = Path(folder)/'歌 with spaces.wav'
        samples = np.zeros((44100*2, 2), dtype='float32')
        samples[1000:10000] = .1
        sf.write(source, samples, 44100, subtype='FLOAT')
        stems = {}
        for name in ('lead', 'backing', 'instrumental'):
            path = Path(folder)/(name+'.wav')
            sf.write(path, samples/3, 44100, subtype='FLOAT')
            stems[name] = str(path)
        graph = workflow.builtins()['内置 · 仅分离音频']['graph']
        job = pipeline.create_job(local_music=source, parent=root/'jobs', voice_mode='import', imported_stems=stems,
                                  workflow_graph=graph, separation_device='cpu', midi_device='cpu')
        pipeline.run(job)
        state = task_store.read_json(job/'status.json')
        assert state['state'] == 'done' and state['planned_steps'] == ['metadata', 'download', 'audio', 'separation']
        assert not (job/'midi').exists(), 'Graph terminal must stop real execution before MIDI'
        assert pipeline.check_audio(job/'audio/lead.wav').frames == len(samples)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            pipeline.run(job)
        assert output.getvalue().count('已完成，跳过') == 4
        snap = progress_state.snapshot(job)
        assert snap['completed'] == 4 and snap['elapsed'] >= 0

        style_options=dict(workflow.DEFAULT_OPTIONS, midi_steps=32, midi_device='cpu',
                           performance_style='power_pop', style_strength=.85, style_scope='all_vocals')
        style_presets={'我的日语': workflow.builtins()['内置 · 日语双轨']}
        workflow.save_state(workflow.default_graph(True), style_options, style_presets, '自定义')
        restored = workflow.read_state()
        assert restored['options']['midi_steps'] == 32 and restored['presets']['我的日语']['options']['language'] == 'ja'
        assert (restored['options']['performance_style'], restored['options']['style_strength'],
                restored['options']['style_scope']) == ('power_pop', .85, 'all_vocals')
        exported = workflow.export_preferences(style_options)
        assert (exported['performance_style'], exported['style_strength'], exported['style_scope']) == (
            'power_pop', .85, 'all_vocals')
        legacy=dict(style_options)
        for field in ('performance_style','style_strength','style_scope'):
            legacy.pop(field)
        common.atomic_json(root/'cache/workspace.json', {'workflow': {
            'graph': workflow.default_graph(), 'options': legacy, 'preset': '旧工作区',
            'presets': {'旧预设': {'graph': workflow.default_graph(), 'options': legacy}}}})
        old_state = workflow.read_state()
        for options in (old_state['options'], old_state['presets']['旧预设']['options']):
            assert (options['performance_style'], options['style_strength'], options['style_scope']) == (
                'none', .6, 'lead')
        workflow.save_state(workflow.default_graph(True), style_options, style_presets, '自定义')
        request = task_store.read_json(job/'request.json')
        assert request['tools']['midi_device'] == 'cpu'
        invalids = [ {'nodes': {'source':[0,0], 'midi':[2,3]}, 'edges': [['source', 'midi']]},
                     {'nodes': workflow.default_graph()['nodes'], 'edges': []},
                     {'nodes': {'source':[float('nan'),0], 'separation':[0,0]}, 'edges': [['source','separation']]} ]
        for graph in invalids:
            try:
                workflow.validate_graph(graph)
                raise AssertionError('Invalid graph accepted')
            except ValueError:
                pass
        style_valid=dict(workflow.DEFAULT_OPTIONS, performance_style='soft_ballad', style_strength=.3,
                         style_scope='all_vocals')
        normalized=workflow.validate_options(style_valid)
        assert normalized['style_strength']==.3
        for key,value in [('style_pitch',1),('style_pitch','true'),('performance_style','unknown'),('style_scope','unknown'),
                          ('style_strength',True),('style_strength',float('nan')),
                          ('style_strength',float('inf')),('style_strength',-.01),
                          ('style_strength',1.01),('style_strength','0.5')]:
            invalid=dict(style_valid);invalid[key]=value
            try:
                workflow.validate_options(invalid)
                raise AssertionError(f'Invalid style option accepted: {key}={value!r}')
            except ValueError:
                pass
        try:
            pipeline.create_job(local_music=source, workflow_graph=workflow.default_graph(True), recognize_lyrics=False)
            raise AssertionError('Subtitle graph without lyric events accepted')
        except ValueError:
            pass

        full = pipeline.create_job(local_music=source, parent=root/'jobs', voice_mode='single', workflow_graph=workflow.default_graph(True))
        calls = []
        def fake_separation(job, request):
            import shutil
            for name in ('vocals', 'instrumental'):
                shutil.copy2(job/'audio/source.wav', job/'audio'/(name+'.wav'))
            common.atomic_json(job/'separation-report.json', {'fixture': True})
        def fake_inference(job, request):
            calls.append('midi')
            out = job/'midi'
            out.mkdir(exist_ok=True)
            midi = mido.MidiFile(charset='utf8')
            midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000),
                mido.MetaMessage('lyrics', text='你好', time=480), mido.Message('note_on', note=60, velocity=80),
                mido.Message('note_off', note=60, time=480)]))
            midi.save(out/'vocals.mid')
            common.atomic_json(out/'report.json', {'input_signature': common.midi_signature(job, request, 'vocals'), 'midi_sha256': common.file_hash(out/'vocals.mid')})
        steps = [(key, title, fake_separation if key == 'separation' else fake_inference if key == 'midi' else function) for key, title, function in pipeline.STEPS]
        with patch.object(pipeline, 'STEPS', steps):
            pipeline.run(full)
            pipeline.run(full)
        assert calls == ['midi'], 'Completed graph must reuse validated outputs'
        assert '你好' in (full/'subtitles/vocals.srt').read_text(encoding='utf-8')
        assert task_store.read_json(full/'status.json')['state'] == 'done'

        common.atomic_json(full/'status.json', {'state':'running', 'steps':{}})
        assert next(r for r in task_store.records() if r['path'] == full)['state'] == 'interrupted'
        task_store.rename(full, '重命名歌曲')
        assert next(r for r in task_store.records() if r['path'] == full)['title'] == '重命名歌曲'
        with pipeline.job_lock(full):
            assert task_store.locked(full)
            try:
                task_store.delete(full)
                raise AssertionError('Running task deleted')
            except ValueError:
                pass

        cache = root/'cache/numba'
        cache.mkdir(parents=True)
        (cache/'test.bin').write_bytes(b'x'*100)
        protected = (root/'cache/workspace.json').read_bytes()
        before = common.file_hash(job/'audio/lead.wav')
        assert storage.clean([cache]) == 100
        assert (root/'cache/workspace.json').read_bytes() == protected
        assert common.file_hash(job/'audio/lead.wav') == before
        nested=cache/'user-tasks/task';nested.mkdir(parents=True)
        common.atomic_json(nested/'request.json',{'source_title':'Protected custom output'})
        common.remember_job(nested)
        assert str(cache) not in [str(r['path']) for r in storage.candidates()]
        try:
            storage.clean([cache]);raise AssertionError('A cache ancestor of user work was deleted')
        except ValueError:pass
        assert (nested/'request.json').is_file()
        # Remove only this metadata fixture; keep the task set used later unchanged.
        (nested/'request.json').unlink();nested.rmdir();nested.parent.rmdir();cache.rmdir()
        common.remember_job(job)
        profile=root/'cache/publish-browser/bilibili';profile.mkdir(parents=True)
        with pipeline.job_lock(profile):
            try:
                storage.require_idle();raise AssertionError('Active upload cache was considered idle')
            except ValueError:pass
        for path in (root, root/'cache/workspace.json', job/'audio'):
            try:
                storage.clean([path])
                raise AssertionError('Non-cache path accepted')
            except ValueError:
                pass
        update = root/'cache/updates'
        update.mkdir()
        (update/'pending.json').write_text('{}')
        assert str(update) not in [str(r['path']) for r in storage.candidates()]
        for error, action in [('CUDA out of memory', 'cpu'), ('HTTP Error 403', 'resume'), ('No space left on device', 'storage')]:
            assert error_recovery.explain(error)['action'] == action
        error_recovery.retry_with_cpu(full)
        assert task_store.read_json(full/'request.json')['tools']['midi_device'] == 'cpu'
        common.atomic_json(full/'status.json', {'state':'failed', 'current_step':'midi', 'error':'CUDA out of memory'})

        window = Window()
        window.show()
        application.processEvents()
        assert window.workflow.fields['midi_steps'].currentData() == 32
        assert window.workflow.fields['midi_device'].currentData() == 'cpu'
        window.workflow._persist_test = True
        window.navigate(5)
        window.workflow.preset.setCurrentText('内置 · 仅分离音频')
        assert workflow.validate_graph(window.options()['workflow_graph']) == 'separation'
        canvas = window.workflow
        window.voice_mode.setCurrentIndex(window.voice_mode.findData('import'))
        window.language.setCurrentIndex(window.language.findData('ja'))
        canvas.fields['midi_device'].setCurrentIndex(canvas.fields['midi_device'].findData('cpu'))
        canvas.fields['separation_device'].setCurrentIndex(canvas.fields['separation_device'].findData('cpu'))
        canvas.fields['midi_steps'].setCurrentIndex(canvas.fields['midi_steps'].findData(8))
        with patch.object(QInputDialog, 'getText', return_value=('我的分离工作流', True)):
            QTest.mouseClick(window.save_preset_button, Qt.LeftButton)
        assert window.workflow_preset.findData('我的分离工作流') >= 0
        window.navigate(0)
        assert window.pages.currentIndex() == 0
        window.workflow_preset.setCurrentIndex(window.workflow_preset.findData('内置 · 中文双轨'))
        assert window.options()['language'] == 'zh' and window.options()['voice_mode'] == 'dual'
        assert '双轨 MIDI' in window.output_summary.text(), 'Selecting a MIDI preset must clear separation-only output text'
        window.workflow_preset.setCurrentIndex(window.workflow_preset.findData('我的分离工作流'))
        options = window.options()
        assert canvas.preset.currentText() == '我的分离工作流'
        assert options['language'] == 'ja' and options['voice_mode'] == 'import' and options['midi_steps'] == 8
        assert options['midi_device'] == 'cpu' and options['separation_device'] == 'cpu'
        assert workflow.validate_graph(options['workflow_graph']) == 'separation'
        assert '分离音频' in window.output_summary.text() and 'MIDI' not in window.output_summary.text()
        window.input_mode.setCurrentIndex(window.input_mode.findData('local'))
        window.local_music.setText(str(source))
        window.output_dir.setText(str(root/'jobs'))
        for key, value in stems.items():
            window.stem_fields[key].setText(value)
        # Exercise the actual processing-page Start action and job creation. Imported stems
        # keep this regression deterministic while FFmpeg and the pipeline run for real.
        with patch.object(window, 'launch', side_effect=lambda: pipeline.run(window.job)):
            QTest.mouseClick(window.start, Qt.LeftButton)
        started = task_store.read_json(window.job/'request.json')
        assert started['language'] == 'ja' and started['voice_mode'] == 'import'
        assert started['tools']['midi_device'] == 'cpu' and started['tools']['separation_device'] == 'cpu'
        assert workflow.validate_graph(started['workflow_graph']) == 'separation'
        assert task_store.read_json(window.job/'status.json')['state'] == 'done'
        assert not (window.job/'midi').exists()
        class IdleRunner(QThread):
            line = pyqtSignal(str)
            result = pyqtSignal(int)

            def __init__(self, job):
                super().__init__()

            def start(self):
                pass
        # Reload the same task through the real launch UI. The pipeline has already
        # completed above, so suppress only the duplicate background process here.
        window.workflow_preset.setCurrentIndex(window.workflow_preset.findData('内置 · 中文双轨'))
        with patch('app.Runner', IdleRunner):
            window.launch()
        assert window.workflow_preset.currentData() == '我的分离工作流'
        assert canvas.preset.currentText() == '我的分离工作流'
        assert window.options()['language'] == 'ja' and not window.workflow_preset.isEnabled()
        window.timer.stop()
        window.set_running(False)
        window.workflow.persist(dirty=False)
        restored_window = Window()
        assert restored_window.workflow_preset.currentData() == '我的分离工作流'
        assert restored_window.options()['language'] == 'ja'
        restored_window.close()

        window.navigate(5)
        canvas.preset.setCurrentText('内置 · 仅分离音频')
        assert window.pages.currentIndex() == 0, 'Workflow is edited on the processing page'
        window.midi_enabled.setChecked(True)
        assert workflow.validate_graph(window.options()['workflow_graph']) == 'midi'
        window.subtitles_enabled.setChecked(True)
        assert workflow.validate_graph(window.options()['workflow_graph']) == 'subtitles'
        assert '字幕' in window.output_summary.text()
        window.recognize.setChecked(False)
        assert not window.subtitles_enabled.isChecked() and not window.subtitles_enabled.isEnabled()
        assert workflow.validate_graph(window.options()['workflow_graph']) == 'midi'
        window.recognize.setChecked(True)
        window.subtitles_enabled.setChecked(True)
        window.midi_enabled.setChecked(False)
        assert not window.subtitles_enabled.isChecked()
        assert window.options()['project_formats'] == []
        window.midi_enabled.setChecked(True)
        window.step_cards['midi'].toggle.setChecked(True)
        application.processEvents()
        assert window.language._vwa_choice.isVisible()
        assert not hasattr(canvas,'canvas')
        canvas.fields['midi_steps'].setCurrentIndex(canvas.fields['midi_steps'].findData(8))
        assert window.options()['midi_steps'] == 8
        window.set_running(True)
        assert not canvas.isEnabled() and not window.workflow_preset.isEnabled()
        window.set_running(False)
        window.results.reload()
        for _ in range(300):
            application.processEvents()
            if not (window.results.worker and window.results.worker.isRunning()) and not window.results.rescan: break
            QTest.qWait(10)
        window.results.search.setText('重命名')
        assert window.results.tree.topLevelItemCount() == 1
        window.results.filter.setCurrentIndex(window.results.filter.findData('failed'))
        assert window.results.selected_job == full
        window.show_job(full)
        window.navigate(0)
        assert window.error_panel.isVisible() and window.cpu_retry.isVisible()
        window.close()
        task_store.delete(full)
        assert not full.exists() and job.exists()
print('PASS: graph execution/resume, processing-page saved presets and real start, persistence, ordered expandable steps, legacy preset migration, task filters, safe cleanup and retry')
