"""Exercise the English preparation and editor-export boundaries with fixed acoustic predictions."""

import json
import logging
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

import mido
import numpy as np
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import atomic_json, clean_lyrics
from preparation_languages import HUBERTFA_MODEL
from v2m_runtime import configure

V2 = APP / 'dependencies/vocal2midi2'
configure(V2)
from inference.API import asr_api, game_api
from inference.pipeline import auto_lyric_hybrid as hybrid, lyric_alignment

import agent_exports
from agent_export_audit import audit_export
from agent_lyrics import review_lyrics
from agent_preparation import _target_request, plan_preparation
from artifact_cache import inference_inputs
from dependency_profiles import task_features
from lyrics_sources import from_file
from midi_bridge import run
from pipeline import create_job
from preparation_commands import (
    PreparationDraft, create_from_draft, make_preset_document, progress_view, selected_component_issues,
)
from project_exports import export_projects, read_ustx
from subtitles import midi_cues
from tuning_timeline import ProjectNotes, split_batch_lyrics
from tuning_workflow import read_project
import yaml


def make_word(text, start, end, phones):
    return SimpleNamespace(text=text, start=start, end=end,
                           phonemes=[SimpleNamespace(text=phone, start=onset) for phone, onset in phones])


def midi_events(path):
    return [event.text for track in mido.MidiFile(path, charset='utf8').tracks
            for event in track if event.type == 'lyrics']


def fake_game(**kwargs):
    assert kwargs['language'] == 'en'
    predictions = []
    for durations in kwargs['known_durations_np']:
        if np.any(durations):
            predictions.append((np.array([.02, .16, .08, .22, .19, .23, .10]),
                                np.array([False, True, True, True, True, True, False]),
                                np.array([0, 60, 62, 64, 65, 67, 0])))
        else:
            predictions.append((np.array([1.0]), np.array([True]), np.array([63])))
    return predictions


with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='english-preparation-') as folder:
    root = Path(folder)
    audio = root / 'reference.wav'
    sr = 44100
    signal = .25 * np.sin(np.arange(sr * 2) * (2 * np.pi * 262 / sr))
    sf.write(audio, signal, sr)
    raw_reference = 'Don’t melody.\nFlimzork'
    cleaned = "Don't melody Flimzork"
    assert clean_lyrics('Don’t use well‑known words, 42!', language='en') == "Don't use well-known words"
    assert clean_lyrics('春 风', language='zh') == '春风'
    text = root / 'lyrics.txt'
    text.write_text('Lyrics: TEST\n' + raw_reference, encoding='utf-8')
    draft = (PreparationDraft(music_path=str(audio), voice_mode='single').edit('language', 'en')
             .edit('midi_backend', 'v1').edit('output_directory', str(root / 'tasks'))
             .apply_lyrics('lead', from_file(text)))
    assert draft.view['lyrics'] == "Don't melody\nFlimzork"
    document = make_preset_document(draft, 'English fixture')
    assert document['payload']['options']['project_formats'] == ['ustx', 'vsqx', 'svp']
    assert 'lyrics' not in document['payload']['options']
    with patch('feature_components.status', return_value={'ready': False}):
        assert 'Qwen3-ASR' in selected_component_issues(draft)[0]
        assert not selected_component_issues(draft.edit('recognize_lyrics', False))
        assert not selected_component_issues(draft.edit('language', 'ja'))
    with patch('feature_components.status', return_value={'ready': True}):
        job = create_from_draft(draft.edit('export_pitch', False))
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    assert request['midi_backend'] == 'v2' and request['alignment_engine'] == 'hfa'
    assert request['project_formats'] == ['ustx', 'vsqx', 'svp'] and request['lyrics'] == cleaned
    assert request['lyric_lines'] == ["Don't melody", 'Flimzork']
    assert draft.edit('language', 'ja').preferences()['midi_backend'] == 'v1'
    assert draft.edit('language', 'ja').preferences()['project_formats'] == ['ustx', 'vsqx', 'svp']
    assert {'alignment', 'chinese'} <= task_features(request, {'midi'})
    assert 'japanese' not in task_features(request, {'midi'})
    assert not {'alignment', 'chinese', 'japanese'} & task_features({**request, 'recognize_lyrics': False}, {'midi'})
    cli_job = create_job(local_music=audio, language='en', parent=root / 'tasks', remember=False)
    cli_request = json.loads((cli_job / 'request.json').read_text(encoding='utf-8'))
    assert cli_request['midi_backend'] == 'v2'
    explicit = create_job(local_music=audio, language='en', project_formats=['vsqx', 'svp'],
                          parent=root / 'tasks', remember=False)
    assert json.loads((explicit / 'request.json').read_text(encoding='utf-8'))['project_formats'] == ['vsqx', 'svp']
    disabled = create_job(local_music=audio, language='en', project_formats=['vsqx', 'svp'],
                          project_export_enabled=False, parent=root / 'tasks', remember=False)
    assert json.loads((disabled / 'request.json').read_text(encoding='utf-8'))['project_formats'] == []
    for changes in ({'midi_backend': 'v1'}, {'alignment_engine': 'tifa'}):
        try:
            create_job(local_music=audio, language='en', parent=root / 'tasks', remember=False, **changes)
        except ValueError as error:
            assert '英文' in str(error)
        else:
            raise AssertionError(changes)

    options = {'language': 'en', 'lyrics': raw_reference, 'backing_lyrics': "I won't go"}
    plan = plan_preparation(str(audio), options)
    saved = _target_request({}, plan['options'])
    assert saved['language'] == 'en' and saved['lyrics'] == cleaned and saved['backing_lyrics'] == "I won't go"
    assert saved['lyric_lines'] == ["Don't melody", 'Flimzork']
    assert asr_api.QWEN_ASR_LANGUAGE_NAMES['en'] == 'English'
    assert asr_api._filter_qwen_asr_text_for_lyric_flow('Don’t sing 42 中文 words.', 'en') == "Don't sing words"

    (job / 'audio').mkdir()
    sf.write(job / 'audio/vocals.wav', signal, sr)
    sf.write(job / 'audio/instrumental.wav', signal, sr)
    signature = inference_inputs(job, request, 'vocals')
    assert signature['lyric_mode'] == 'word' and 'asr_model' in signature['models']
    assert 'phoneme_asr_model' not in signature['models']
    assert any('ds_cmudict-07b.txt' in item[0] for item in signature['models']['hfa_model'])
    chunks = [{'waveform': signal[:sr], 'offset': 0.0}, {'waveform': signal[sr:], 'offset': 1.0}]

    def fake_hfa(model, directory, language, **_kwargs):
        assert language == 'en'
        assert (directory / 'chunk_0.lab').read_text(encoding='utf-8') == "don't melody"
        assert (directory / 'chunk_1.lab').read_text(encoding='utf-8') == 'flimzork'
        words = [make_word("don't", 0, .22, [('d', 0), ('ow', .02), ('n', .16)]),
                 make_word('melody', .22, .90, [('m', .22), ('eh', .26), ('ax', .48), ('iy', .67)]),
                 make_word('SP', .90, 1.0, [])]
        # Simulate HFA returning lexical anchors after ignoring an unknown word.
        return {'chunk_0': (None, None, words), 'chunk_1': (None, None, words)}

    model = SimpleNamespace(vocab_folder=APP / 'dependencies/vocal2midi' / HUBERTFA_MODEL)
    with (patch.object(hybrid, '_extract_pitch_curve', return_value=None),
          patch.object(hybrid, '_slice_chunks', return_value=chunks),
          patch.object(lyric_alignment, 'batch_transcribe_asr',
                       return_value=([{'text': "Don't melody"}, {'text': 'Flimzork'}], [0, 1])) as asr,
          patch.object(lyric_alignment, 'load_hfa_model', return_value=model),
          patch.object(lyric_alignment, 'run_hubert_fa', side_effect=fake_hfa),
          patch.object(hybrid, 'load_game_model', return_value=object()),
          patch.object(game_api, '_run_game_inference_batch', side_effect=fake_game)):
        logging.basicConfig(level=logging.INFO)
        run(job, 'vocals')
        assert asr.call_args.kwargs['language'] == 'en'
        assert Path(asr.call_args.kwargs['asr_model_path']).name == 'Qwen3-ASR-1.7B-dml'
        midi = job / 'midi/vocals.mid'
        markers = ["don't", '-', 'melody', '+', '+']
        assert midi_events(midi) == markers, midi_events(midi)
        report = json.loads((job / 'midi/report.json').read_text(encoding='utf-8'))
        alignment = report['alignment']
        assert alignment['state'] == 'partial' and alignment['word_events'] == 2, alignment
        assert alignment['fallback'] == [{'chunk': 1, 'start': 1.0, 'end': 2.0,
                                         'reason': 'words_outside_dictionary', 'words': ['flimzork']}]
        atomic_json(job / 'status.json', {'state': 'done', 'steps': {'midi': {'state': 'done'}}})
        assert progress_view(job)['lyric_notices'] == [{'voice': 'vocals', 'state': 'partial', 'chunks': 1}]
        assert [cue.text for cue in midi_cues(midi, reference_lines=["Don't melody"], language='en')] == ["don't melody"]
        assert not any(hint['code'] == 'reference_mismatch' for hint in
                       review_lyrics(midi, reference_text="DON’T melody", language='en')['hints'])
        assert any(hint['code'] == 'reference_mismatch' for hint in
                   review_lyrics(midi, reference_text="DON’T rhythm", language='en')['hints'])

        projects = export_projects(job, request)
        project = read_ustx(job / 'exports/voices.ustx')
        notes = project['voice_parts'][0]['notes']
        assert [note['lyric'] for note in notes[:5]] == ["don't", '+~', 'melody', '+', '+']
        reference = project['wave_parts'][0]['relative_path']
        assert (job / 'exports' / reference).resolve() == job / 'audio/instrumental.wav'
        svp = json.loads((job / 'exports/voices.svp').read_bytes().rstrip(b'\x00'))
        assert svp['tracks'][0]['mainRef']['database']['languageOverride'] == 'english'
        assert svp['tracks'][0]['mainRef']['database']['phonesetOverride'] == 'arpabet'
        assert [note['lyrics'] for note in svp['tracks'][0]['mainGroup']['notes'][:5]] == markers
        vsqx = ET.parse(job / 'exports/voices.vsqx')
        assert vsqx.findtext('.//{*}vVoice/{*}bs') == vsqx.findtext('.//{*}singer/{*}bs') == '1'
        assert vsqx.findtext('.//{*}vVoice/{*}pc') == vsqx.findtext('.//{*}singer/{*}pc') == '4'
        assert [row.findtext('{*}y') for row in vsqx.findall('.//{*}note')[:5]] == markers[:1] + ['+~', *markers[2:]]
        assert [row.findtext('{*}p') for row in vsqx.findall('.//{*}note')[:5]] == [
            'd @U', '- n t', 'm e', 'l0 @', 'd i:'
        ]
        score_path = job / 'exports/voices.vsqx'
        timeline = ProjectNotes.load(score_path)
        first, = timeline.matches("Don't")
        edited, changes = timeline.score_editor.materialize(
            read_project(score_path), [{'note_id': first['id'], 'lyric': 'leave'}])
        elements = ET.fromstring(edited['xml']).findall('.//{*}note')
        assert [row.findtext('{*}p') for row in elements[:2]] == ['l0 i:', '- v']
        assert [row.findtext('{*}y') for row in elements[:2]] == ['leave', '+~']
        assert changes[0]['native_fields']['continuation_phonemes'][0]['after'] == '- v'
        protected = root / 'locked.vsqx'
        vsqx.findall('.//{*}note')[1].find('{*}p').set('lock', '1')
        vsqx.write(protected, encoding='utf-8', xml_declaration=True)
        locked = ProjectNotes.load(protected)
        first, = locked.matches("Don't")
        try:
            locked.score_editor.validate_lyric(first['id'], 'leave')
        except ValueError as error:
            assert '保护' in str(error)
        else:
            raise AssertionError('A locked English continuation was overwritten.')
        assert next(item for item in projects['outputs'] if item['file'].endswith('.vsqx'))['english_pronunciation']['notes'] == len(notes)
        export_plan = agent_exports.plan_export(midi, options={'language': 'en', 'preserve_pitch': False})
        assert export_plan['formats'] == ['ustx']
        export_plan = agent_exports.plan_export(midi, ['ustx', 'svp', 'vsqx'],
                                               {'language': 'en', 'preserve_pitch': False,
                                                'include_instrumental': True, 'instrumental_path': str(audio)})
        runtime = APP / 'dependencies/export'
        receipt = agent_exports.apply_export(midi, export_plan, root / 'agent', export_runtime=runtime)
        agent_notes = read_ustx(Path(receipt['candidates'][0]['candidate_path']))['voice_parts'][0]['notes']
        assert [note['lyric'] for note in agent_notes[:5]] == ["don't", '+~', 'melody', '+', '+']
        assert receipt['candidates'][1]['instrumental_included']
        assert receipt['candidates'][2]['english_pronunciation']['notes'] == len(notes)
        assert not audit_export(midi, receipt, export_runtime=runtime, output_root=root / 'audit')['blockers']

        native = job / 'midi/vocals.ustx'
        score = read_ustx(native)
        source_notes = score['voice_parts'][0]['notes']
        replacement = ["Don't", '+*', 'Flimzork', '+2', '+']
        for note, lyric in zip(source_notes, replacement):
            note['lyric'] = lyric
        native.write_text(yaml.safe_dump(score, allow_unicode=True, sort_keys=False), encoding='utf-8')
        timeline = ProjectNotes.load(native)
        assert timeline.matches("don’t")[0]['lyric'] == "Don't"
        assert split_batch_lyrics("Don't, well‑known!") == ["Don't", 'well‑known']
        phrase = list(timeline.notes.values())[:5]
        rows, unused = timeline.lyric_batch(phrase[0]['trackId'], phrase[0]['start'], phrase[-1]['end'],
                                           'Don’t well-known')
        assert not unused and [row['after'] for row in rows] == ['Don’t', '+*', 'well-known', '+2', '+']
        assert [row['preserved'] for row in rows] == [False, True, False, True, True]
        assert not any(row.get('blocked') for row in rows)
        candidate = export_projects(job, request)
        vsqx_result = next(item for item in candidate['outputs'] if item['file'].endswith('.vsqx'))
        reasons = [item['reason'] for item in vsqx_result['english_pronunciation']['reviews']]
        assert 'numbered_continuation' in reasons and 'unknown_word' in reasons
        assert read_ustx(job / 'exports/voices.ustx')['voice_parts'][0]['notes'][3]['lyric'] == '+2'
        atomic_json(job / 'status.json', {'state': 'done', 'steps': {'midi': {'state': 'done'}, 'exports': {'state': 'done'}}})
        assert progress_view(job)['vsqx_pronunciation_reviews'] == vsqx_result['english_pronunciation']['review_count']

        atomic_json(job / 'request.json', {**request, 'recognize_lyrics': False})
        asr.reset_mock()
        run(job, 'vocals')
        assert not asr.called and not midi_events(midi)
        assert json.loads((job / 'midi/report.json').read_text(encoding='utf-8'))['alignment']['state'] == 'disabled'
        atomic_json(job / 'request.json', request)
        asr.return_value = ([{'text': ''}, {'text': ''}], [0, 1])
        run(job, 'vocals')
        alignment = json.loads((job / 'midi/report.json').read_text(encoding='utf-8'))['alignment']
        assert alignment['state'] == 'unavailable' and alignment['pitch_only_chunks'] == 2
        assert all(item['reason'] == 'no_asr_text' for item in alignment['fallback'])

    # The last voiced frame at 1.010 s rounds to tick 970, just outside
    # the note. Native VSQX readback must use the sounding note at that frame.
    boundary_midi = root / 'boundary.mid'
    clip = mido.MidiFile(ticks_per_beat=480, charset='utf8')
    clip.tracks.append(mido.MidiTrack([
        mido.MetaMessage('lyrics', text="Don't"),
        mido.Message('note_on', note=60, velocity=80),
        mido.Message('note_off', note=60, time=970),
    ]))
    clip.save(boundary_midi)
    boundary_pitch = root / 'boundary.pitch.npz'
    f0 = 60.0 + .31 * np.sin(np.arange(102) * 1.12)
    f0[-2:] = 60.12
    np.savez_compressed(boundary_pitch, midi_pitch=f0, voiced_mask=np.ones(102, dtype=bool),
                        time_step=.01, schema=1)
    plan = agent_exports.plan_export(boundary_midi, ['ustx', 'svp', 'vsqx'], {
        'language': 'en', 'pitch_sources': [{'vocal_track_index': 0, 'path': str(boundary_pitch)}],
    })
    result = agent_exports.apply_export(boundary_midi, plan, root / 'boundary-agent', export_runtime=runtime)
    assert all(row['pitch']['readback']['verified'] for row in result['candidates'])
    native = ET.parse(result['candidates'][-1]['candidate_path'])
    assert native.findtext('.//{*}note/{*}y') == "Don't"
    assert native.findtext('.//{*}note/{*}p') == 'd @U n t'
    assert not audit_export(boundary_midi, result, export_runtime=runtime, output_root=root / 'audit')['blockers']

print('PASS: English preparation, native lyrics and pitch, editing, unknown words, and three-format round trips')
