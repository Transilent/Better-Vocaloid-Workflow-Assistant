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
import workspace_ui, components_ui, updates_ui, subtitles_ui, results_ui, workflow_ui, storage_ui

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

        workflow.save_state(workflow.default_graph(True), dict(workflow.DEFAULT_OPTIONS, midi_steps=32, midi_device='cpu'),
                            {'我的日语': workflow.builtins()['内置 · 日语双轨']}, '自定义')
        restored = workflow.read_state()
        assert restored['options']['midi_steps'] == 32 and restored['presets']['我的日语']['options']['language'] == 'ja'
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
        canvas.fields['mode'].setCurrentIndex(canvas.fields['mode'].findData('import'))
        canvas.fields['language'].setCurrentIndex(canvas.fields['language'].findData('ja'))
        canvas.fields['midi_device'].setCurrentIndex(canvas.fields['midi_device'].findData('cpu'))
        canvas.fields['separation_device'].setCurrentIndex(canvas.fields['separation_device'].findData('cpu'))
        canvas.fields['midi_steps'].setCurrentIndex(canvas.fields['midi_steps'].findData(8))
        with patch.object(QInputDialog, 'getText', return_value=('我的分离工作流', True)):
            QTest.mouseClick(next(b for b in canvas.findChildren(QPushButton) if b.text() == '保存为预设'), Qt.LeftButton)
        assert window.workflow_preset.findData('我的分离工作流') >= 0
        QTest.mouseClick(canvas.use_button, Qt.LeftButton)
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
        canvas.add_node('midi', QPointF(720, 330))
        assert 'midi' in canvas.graph['nodes']
        assert ['separation','midi'] not in canvas.graph['edges'] and not window.start.isEnabled()
        canvas.canvas.fitInView(canvas.canvas.scene().itemsBoundingRect().adjusted(-60,-100,60,100), Qt.KeepAspectRatio)
        viewport = canvas.canvas.viewport()
        def drag_node(name, target, preview):
            node = canvas.nodes[name]
            anchor = QPointF(70,25)
            start = canvas.canvas.mapFromScene(node.pos()+anchor)
            finish = canvas.canvas.mapFromScene(target+anchor)
            QTest.mousePress(viewport, Qt.LeftButton, pos=start)
            movement = QMouseEvent(QEvent.MouseMove, QPointF(finish), QPointF(viewport.mapToGlobal(finish)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            application.sendEvent(viewport, movement)
            assert bool(canvas.preview) == preview
            QTest.mouseRelease(viewport, Qt.LeftButton, pos=finish)
            application.processEvents()
        drag_node('midi', QPointF(555,174), True)
        assert canvas.graph['nodes']['midi'] == [534,150]
        assert ['separation','midi'] in canvas.graph['edges'] and window.start.isEnabled()
        assert workflow.read_state()['graph'] == canvas.graph
        canvas.finish_move('midi')
        assert canvas.graph['edges'].count(['separation','midi']) == 1
        saved = canvas.graph['nodes']['midi'][:]
        QTest.mouseClick(viewport, Qt.LeftButton, pos=canvas.canvas.mapFromScene(canvas.nodes['midi'].pos()+QPointF(70,25)))
        assert canvas.graph['nodes']['midi'] == saved, 'Editing a node must not move it'
        drag_node('midi', QPointF(720,330), False)
        assert ['separation','midi'] not in canvas.graph['edges'] and not window.start.isEnabled()
        assert not canvas.use_button.isEnabled()
        drag_node('midi', QPointF(555,174), True)
        assert window.start.isEnabled() and canvas.use_button.isEnabled()
        canvas.append_node('subtitles')
        assert ['midi','subtitles'] in canvas.graph['edges']
        assert workflow.validate_graph(window.options()['workflow_graph']) == 'subtitles'
        assert '字幕' in window.output_summary.text()
        canvas.nodes['midi'].setPos(720,330)
        canvas.finish_move('midi')
        assert not window.start.isEnabled()
        canvas.arrange()
        assert workflow.validate_graph(canvas.graph) == 'subtitles' and window.start.isEnabled()
        for graph, name in [({'nodes': {'source':[0,0], 'midi':[220,0]}, 'edges': []}, 'midi'),
                            ({'nodes': {'source':[300,0], 'separation':[50,0]}, 'edges': []}, 'separation')]:
            assert workflow.nearby_connection(graph, name) is None, 'Never connect skipped or reversed processing nodes'
        canvas.fields['midi_steps'].setCurrentIndex(canvas.fields['midi_steps'].findData(8))
        assert window.options()['midi_steps'] == 8
        window.set_running(True)
        assert not canvas.isEnabled() and not window.workflow_preset.isEnabled()
        window.set_running(False)
        window.results.reload()
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
print('PASS: graph execution/resume, processing-page saved presets and real start, persistence, Qt proximity snap/detach, task filters, safe cleanup and retry')
