"""Shared Quick draft compilation and cancellation ownership."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import wave

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

import common
import workflow
from preparation_commands import create_from_draft
from workspace_controller import WorkspaceController


def make_audio(path):
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(bytes(320))
    return path


with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='prep-draft-') as folder:
    root = Path(folder)
    controller = WorkspaceController([('自动', 'auto')])
    controller.set_music(str(make_audio(root / '本地源.wav')))
    for name, value in (
        ('output_directory', str(root / 'results')), ('target', 'separation'),
        ('voice_mode', 'import'), ('lyrics', '这一行会保存'), ('lyrics_lookup', '12345'),
        ('advanced_expanded', True), ('project_formats', ['svp']),
    ):
        controller.set_preparation_field(name, value)
    try:
        create_from_draft(controller.draft)
    except ValueError as exc:
        assert '完整 WAV' in str(exc), exc
    else:
        raise AssertionError('An incomplete external stem set was accepted')
    assert not (root / 'results').exists()

    for voice in ('lead', 'backing', 'instrumental'):
        controller.set_draft(controller.draft.set_stem(voice, str(make_audio(root / f'{voice}.wav'))))
    frozen = controller.draft
    exposed = frozen.view
    exposed['imported_stems'].clear()
    exposed['project_formats'].clear()
    controller.navigate('updates')
    controller.set_preparation_field('lyrics', '另一行')
    controller.navigate('results')
    controller.navigate('processing')
    assert controller.draft.view['advanced_expanded'] and controller.draft.view['lyrics_lookup'] == '12345'
    job = create_from_draft(frozen)
    request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
    assert job.parent == root / 'results'
    assert request['source_title'] == '本地源' and request['lyrics'] == '这一行会保存'
    assert set(request['imported_stems']) == {'lead', 'backing', 'instrumental'}
    assert request['prepare_only'] and workflow.validate_graph(request['workflow_graph']) == 'separation'
    assert not request['project_formats'] and request['export_preferences']['project_formats'] == ['svp']

    token_a, token_b = 'a' * 32, 'b' * 32
    (job / f'.cancel-{token_a}.flag').touch()
    with patch.dict(os.environ, {'VWA_RUN_CANCEL_TOKEN': token_b}):
        assert not common.cancellation_requested(job)
        (job / 'cancel.flag').touch()
        assert common.cancellation_requested(job)
        (job / 'cancel.flag').unlink()
    with patch.dict(os.environ, {'VWA_RUN_CANCEL_TOKEN': token_a}):
        assert common.cancellation_requested(job)

assert not any(name == 'PyQt5' or name == 'PySide6' for name in sys.modules)
print('PASS: shared draft preserves source/stems/preferences, rejects invalid jobs, isolates cancellation')
