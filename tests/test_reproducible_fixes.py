"""Regression cases reproduced in the October 19 audit follow-up."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import numpy as np
import soundfile as sf
import mido
from PyQt5.QtGui import QImage, QColor
from PyQt5.QtWidgets import QApplication
import check_dependencies
import common
import cover_art
import dependency_profiles
import pipeline
import publication
import workflow
from app import Window
from artifact_cache import receipt
from project_exports import export_job, outputs_intact
from subtitles import midi_cues

application = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory(dir=APP/'work', prefix='reproduced-fixes-') as folder:
    root = Path(folder)
    # Saving a preset and opening a new window must preserve inactive choices.
    with patch.object(workflow, 'ROOT', root), patch.object(workflow, 'workspace_state', return_value={}):
        window = Window()
        window.workflow.choose_preset('内置 · 中文双轨')
        window.project_checks['vsqx'].setChecked(False)
        window.audio_checks['wav'].setChecked(False)
        window.audio_checks['flac'].setChecked(True)
        window.reference_tension.setChecked(True)
        window.exports_enabled.setChecked(False)
        window.audio_enabled.setChecked(False)
        window.workflow._persist_test = True
        with patch('processing_workflow.QInputDialog.getText', return_value=('保留选择', True)):
            window.workflow.save_preset()
        stored = json.loads((root/'cache/workspace.json').read_text(encoding='utf-8'))
        saved = stored['workflow']['presets']['保留选择']['options']
        assert saved['project_formats'] == ['ustx', 'svp'] and saved['audio_formats'] == ['flac']
        assert not saved['project_export_enabled'] and not saved['audio_export_enabled']
        assert saved['reference_tension']
        task_options = window.options()
        assert task_options['project_formats'] == task_options['audio_formats'] == []
        assert not task_options['reference_tension']
        task_options['parent'] = root/'tasks'
        job = pipeline.create_job('BV1dTNP6QEfE', remember=False, **task_options)
        request = json.loads((job/'request.json').read_text(encoding='utf-8'))
        assert request['audio_formats'] == request['project_formats'] == []
        assert not request['reference_tension']
        restored_options = workflow.request_options(request)
        assert restored_options['project_formats'] == ['ustx', 'svp']
        assert restored_options['audio_formats'] == ['flac'] and restored_options['reference_tension']
        assert pipeline.planned_steps(request)[2] == 'midi'
        window.workflow._persist_test = False
        with patch.object(workflow, 'workspace_state', return_value=stored):
            reopened = Window()
            assert not reopened.audio_enabled.isChecked() and not reopened.exports_enabled.isChecked()
            assert reopened.audio_checks['flac'].isChecked() and reopened.project_checks['svp'].isChecked()
            assert not reopened.project_checks['vsqx'].isChecked() and reopened.reference_tension.isChecked()
            reopened.exports_enabled.setChecked(True); reopened.audio_enabled.setChecked(True)
            assert reopened.options()['project_formats'] == ['ustx', 'svp']
            assert reopened.options()['audio_formats'] == ['flac']
            # Preparation keeps the saved preference but never executes tonal
            # processing. Only the independent tuning mode owns that action.
            assert reopened.options()['prepare_only'] and not reopened.options()['reference_tension']
            tuning_source = root/'preference-check.svp'
            common.atomic_json(tuning_source, {'version':153,
                'time':{'tempo':[{'position':0,'bpm':120}], 'meter':[{'index':0,'numerator':4,'denominator':4}]},
                'tracks':[{'name':'Lead','mainRef':{},'groups':[],
                    'mainGroup':{'uuid':'preference-check','parameters':{},
                        'notes':[{'onset':0,'duration':705600000,'pitch':60,'lyrics':'la'}]}}]})
            assert reopened.tuning.load_project(tuning_source), reopened.tuning.status.text()
            reopened.tuning.mode.setCurrentIndex(reopened.tuning.mode.findData('reference'))
            assert reopened.tuning.options()['reference_tension']
            reopened.midi_enabled.setChecked(False)
            assert reopened.options()['project_formats'] == [] and not reopened.options()['reference_tension']
            reopened.midi_enabled.setChecked(True)
            assert not reopened.options()['reference_tension']
            assert reopened.tuning.options()['reference_tension']
            reopened.workflow.apply_options(restored_options)
            assert not reopened.exports_enabled.isChecked() and reopened.reference_tension.isChecked()
            reopened.close()
        window.close()

    legacy = workflow.validate_options({'project_formats': [], 'audio_formats': ['wav']})
    assert not legacy['project_export_enabled'] and legacy['audio_export_enabled']
    assert not workflow.validate_options({'audio_formats': []})['audio_export_enabled']
    assert not workflow.request_options({'project_formats': [], 'tools': {}})['project_export_enabled']
    for key in ('project_export_enabled', 'audio_export_enabled'):
        try: workflow.validate_options({key: 'false'}); raise AssertionError('Non-boolean switch accepted')
        except ValueError: pass
    # Disabled output must not scan stems or create an exports/audio directory.
    empty = root/'disabled-exports'; empty.mkdir()
    with patch('project_exports.audio_inputs', side_effect=AssertionError('Audio export was disabled')):
        report = export_job(empty, {'project_formats': [], 'audio_formats': []})
        assert report['audio'] == [] and outputs_intact(empty, {'project_formats': [], 'audio_formats': []})
        assert not (empty/'exports/audio').exists()
    with patch.object(pipeline, 'run') as run, patch.object(pipeline, 'remember_job'), \
            patch.object(sys, 'argv', ['pipeline.py', '--source', 'BV1dTNP6QEfE', '--output-dir', str(root/'cli'), '--no-audio-export']):
        assert pipeline.main() == 0
        cli_request = json.loads((run.call_args.args[0]/'request.json').read_text(encoding='utf-8'))
        assert cli_request['audio_formats'] == [] and not cli_request['audio_export_enabled']

    # Real receipts, real reuse, and an actual cover conversion with all network calls forbidden.
    old = pipeline.create_job('BV1dTNP6QEfE', parent=root/'cover-tasks', remember=False, voice_mode='single')
    request = json.loads((old/'request.json').read_text(encoding='utf-8'))
    info = {'bvid': request['bv'], 'title': 'Fixture', 'desc': '',
            'owner': {'name': 'Fixture', 'mid': 1}, 'pic': 'https://i0.hdslb.com/fixture.png'}
    for stage in ('metadata', 'download', 'audio'):
        for name in pipeline.requirements(stage, request):
            path = old/name; path.parent.mkdir(parents=True, exist_ok=True)
            if name == 'audio/source.wav': sf.write(path, np.zeros((4410, 2), dtype='float32'), 44100, subtype='FLOAT')
            elif name == 'source-info.json': common.atomic_json(path, info)
            else: path.write_bytes(b'fixture')
    image = old/'original-cover.png'
    pixels = QImage(64, 36, QImage.Format_RGB32); pixels.fill(QColor('#4d9b82'))
    assert pixels.save(str(image))
    saved = {'bvid': request['bv'], 'url': info['pic'], 'kind': 'original',
             'file': image.name, 'sha256': common.file_hash(image)}
    common.atomic_json(old/'original-cover.json', saved)
    common.atomic_json(old/'status.json', {'steps': {stage: {'state': 'done',
        'receipt': receipt(old, request, stage, pipeline.requirements(stage, request))}
        for stage in ('metadata', 'download', 'audio')}})
    with patch.object(pipeline, 'remember_job'), patch('cover_art.direct_urlopen', side_effect=AssertionError('Offline cache connected')):
        reused = pipeline.reuse_download(old, parent=root/'cover-tasks', voice_mode='single')
        reused_state = json.loads((reused/'status.json').read_text(encoding='utf-8'))
        assert set(reused_state['steps']) == {'metadata', 'download', 'audio'}
        cached, _ = cover_art.job_cover(reused)
        assert cached.read_bytes() == image.read_bytes()
        video = reused/'video/source.mp4'
        metadata = {'path': str(video), 'bytes': video.stat().st_size, 'duration': 1.0, 'width': 64, 'height': 36}
        with patch.object(publication, 'inspect_video', return_value=metadata):
            bundle = publication.prepare(reused, video, {'bilibili': {'title': 'Fixture', 'description': 'Fixture'}},
                                         parent=root/'bundles')
        assert publication.load_bundle(bundle, 'bilibili')['cover']['kind'] == 'original'
        assert cover_art.cached_cover(dict(info, bvid='BV1ccWPeAEoZ'), old) is None
        assert cover_art.cached_cover(dict(info, pic='https://i0.hdslb.com/other.png'), old) is None
        for changed in (dict(saved, file='../outside.png'), dict(saved, sha256='0'*64)):
            common.atomic_json(old/'original-cover.json', changed)
            assert cover_art.cached_cover(info, old) is None
        image.write_bytes(b'corrupt')
        common.atomic_json(old/'original-cover.json', saved)
        assert cover_art.cached_cover(info, old) is None
        # A missing optional cover does not invalidate otherwise reusable stages.
        again = pipeline.reuse_download(old, parent=root/'cover-tasks', voice_mode='single')
        assert not (again/'original-cover.json').exists()
        assert len(json.loads((again/'status.json').read_text())['steps']) == 3

    # Required internal pitch checks are independent of the continuous-pitch export switch.
    missing = 'dependencies/vocal2midi/experiments/RMVPE/rmvpe.onnx'
    common.atomic_json(root/'dependencies/manifest.json', {'entries': [{'file': missing, 'bytes': 10, 'sha256': '0'*64}]})
    for language in ('zh', 'ja'):
        for lyrics in (True, False):
            request = {'midi_backend': 'v2', 'language': language, 'recognize_lyrics': lyrics,
                       'project_formats': [], 'export_pitch': False}
            features = dependency_profiles.task_features(request, {'midi'})
            assert 'pitch' in features
            with patch.object(check_dependencies, 'ROOT', root), patch.object(check_dependencies,
                    'load_config', return_value={'python': sys.executable, 'ffmpeg': sys.executable}):
                assert check_dependencies.check(features=features)['state'] == 'failed'
                try: check_dependencies.validate(features); raise AssertionError('Missing internal pitch model accepted')
                except FileNotFoundError as error: assert '音高分析（RMVPE）' in str(error)
            assert 'pitch' not in dependency_profiles.task_features(request, {'separation'})
    implicit = {'tools': {'vocal2midi': 'dependencies/vocal2midi2'}, 'language': 'ja', 'project_formats': []}
    assert 'pitch' in dependency_profiles.task_features(implicit, {'midi'})
    with patch('os.getcwd', return_value=str(root)):
        assert 'pitch' in dependency_profiles.task_features(implicit, {'midi'})
    native_v1 = root/'legacy-backend'; native_v1.mkdir()
    assert 'pitch' not in dependency_profiles.task_features(dict(implicit, tools={'vocal2midi': str(native_v1)}), {'midi'})

    # Reject a drifted program inventory at build time, before creating a ZIP.
    from tools import build_runtime_upgrade
    builder = root/'builder'
    common.atomic_json(builder/'dependencies/manifest.json', {'entries': []})
    common.atomic_json(builder/'packaging/version.json', {'version': '9.9.9'})
    (builder/'src').mkdir()
    (builder/'src/app.py').write_text('# fixture')
    (builder/'src/unapproved.py').write_text('# fixture')
    with patch.object(build_runtime_upgrade, 'ROOT', builder), \
            patch('tools.verify_source_layout.verify'), \
            patch.object(build_runtime_upgrade.subprocess, 'check_output', return_value=b'{"entries": []}'), \
            patch.object(build_runtime_upgrade, 'package_sources', return_value={
                'app.py': 'src/app.py', 'version.json': 'packaging/version.json', 'unapproved.py': 'src/unapproved.py'}):
        try:
            build_runtime_upgrade.build(builder/'output')
            raise AssertionError('Unapproved program file accepted during runtime build')
        except ValueError as error:
            assert 'unapproved.py' in str(error)
        assert not list((builder/'output').glob('*.zip'))

    def score(name, events, extra=None):
        midi = mido.MidiFile(ticks_per_beat=480, charset='utf8')
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo', tempo=500000)]))
        track, previous = mido.MidiTrack(), 0
        for tick, message in sorted(events, key=lambda row: row[0]):
            track.append(message.copy(time=tick-previous)); previous = tick
        midi.tracks.append(track)
        if extra: midi.tracks.append(extra)
        path = root/(name+'.mid'); midi.save(path)
        return midi_cues(path, track=1)

    events = [(0, mido.MetaMessage('lyrics', text='长')), (0, mido.Message('note_on', note=60, velocity=80)),
              (3840, mido.Message('note_off', note=60)), (4800, mido.MetaMessage('lyrics', text='后')),
              (4800, mido.Message('note_on', note=62, velocity=80)), (5280, mido.Message('note_off', note=62))]
    cues = score('sustained', events)
    assert [(c.start, c.end) for c in cues] == [(0, 4), (5, 5.5)]
    # Last cue follows measured note-off, not a long tail or another voice.
    other = mido.MidiTrack([mido.Message('note_on', note=72, velocity=80),
                           mido.Message('note_off', note=72, time=28800)])
    assert score('unrelated-tail', events[:3]+[(28800, mido.MetaMessage('end_of_track'))], other)[0].end == 4
    melisma = events[:2]+[(768, mido.Message('note_off', note=60)),
        (816, mido.Message('note_on', note=62, velocity=80)), (2880, mido.Message('note_off', note=62))]+events[3:]
    assert score('melisma', melisma)[0].end == 3
    # A missing lyric does not bridge an arbitrarily long silent gap.
    gap = events[:3]+[(9600, mido.Message('note_on', note=62, velocity=80)),
                      (10080, mido.Message('note_on', note=62, velocity=0))]
    assert score('gap', gap)[0].end == 4
    # A late lyric within a held note still uses the measured release.
    held = events[:3]+[(1920, mido.MetaMessage('lyrics', text='续'))]+events[3:]
    assert [(c.start, c.end) for c in score('held', held)] == [(0, 2), (2, 4), (5, 5.5)]

print('PASS: export preferences/reopen/migration/CLI, offline original-cover reuse/publication, internal pitch dependencies and measured subtitle releases')
