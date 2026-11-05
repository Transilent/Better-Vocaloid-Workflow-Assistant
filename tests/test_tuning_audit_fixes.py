"""Regression contracts for explicit F0 rebuilds and saved runtime settings."""

import copy
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from common import atomic_json
from calibration_session import project_data
from calibration_workflow import prepare_copy
from pitch_accuracy import clear_explicit_pitch_scaffolding
from project_exports import library, remove_pitch_data
from vocal_expression import SV_QUARTER as Q
from workflow import request_options


# AUD-13: task files persist runtime choices under tools.  The projection must
# restore those choices and retain the old auto/auto defaults when absent.
saved = request_options({'tools': {
    'separation_device': 'cpu', 'midi_device': 'cpu',
    'dml_adapter': 2, 'cpu_threads': 8,
}})
assert (saved['dml_adapter'], saved['cpu_threads']) == (2, 8), saved
assert request_options({'dml_adapter': 2, 'cpu_threads': 8})['cpu_threads'] == 8
legacy = request_options({'tools': {}})
assert (legacy['dml_adapter'], legacy['cpu_threads']) == ('auto', 0)
try:
    request_options({'tools': None})
    raise AssertionError('Malformed tool settings were accepted')
except ValueError:
    pass


# AUD-02: only the explicit rebuild boundary clears the old envelope and
# generated pitch controls; authored expression remains available.
group = {
    'pitchControls': [{'control': 'old'}],
    'parameters': {
        'pitchDelta': {'mode': 'linear', 'points': [0, 0]},
        'vibratoEnv': {'mode': 'linear', 'points': [0, 0, 1, 0]},
        'loudness': {'mode': 'linear', 'points': [0, 3]},
        'tension': {'mode': 'linear', 'points': [0, .2]},
    },
}
assert clear_explicit_pitch_scaffolding(group) == ['pitchControls', 'vibratoEnv']
assert set(group['parameters']) == {'pitchDelta', 'loudness', 'tension'}
assert clear_explicit_pitch_scaffolding({'parameters': {}}) == []


with tempfile.TemporaryDirectory(dir=APP / 'work', prefix='tuning-audit-fixes-') as directory:
    root = Path(directory)
    job = root / 'job'
    (job / 'exports').mkdir(parents=True)
    (job / 'audio').mkdir(parents=True)
    rate = 24000
    wave = .08 * np.sin(np.arange(rate * 4) * 2 * np.pi * 220 / rate)
    reference = job / 'audio/lead.wav'
    sf.write(reference, wave, rate, subtype='FLOAT')

    xs = np.arange(300) * Q // 20
    source_delta_values = np.zeros(300)
    source_delta_values[::12] = 1200
    source_env_values = np.zeros(300)
    source_env_values[::12] = 1000
    source_path = job / 'exports/voices.svp'
    source_group = {
        'notes': [
            {'onset': 0, 'duration': 2 * Q, 'pitch': 57, 'lyrics': '因'},
            {'onset': 3 * Q, 'duration': 3 * Q, 'pitch': 57, 'lyrics': '错'},
        ],
        'parameters': {
            'pitchDelta': {
                'mode': 'linear',
                'points': np.column_stack((xs, source_delta_values)).ravel().tolist(),
            },
            'vibratoEnv': {
                'mode': 'linear',
                'points': np.column_stack((xs, source_env_values)).ravel().tolist(),
            },
            'loudness': {'mode': 'linear', 'points': [0, -3, 2 * Q, 3]},
            'tension': {'mode': 'linear', 'points': [0, .2, 2 * Q, -.2]},
        },
    }
    source_track = {
        'name': 'Lead',
        'mainRef': {
            'database': {'languageOverride': 'mandarin', 'phonesetOverride': 'xsampa'},
            'voice': {},
        },
        'mainGroup': source_group,
    }
    source_data = {
        'version': 153,
        'time': {
            'tempo': [{'position': 0, 'bpm': 120}],
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
        },
        'tracks': [
            source_track,
            {'mainRef': {'isInstrumental': True, 'audio': {
                'filename': '../audio/instrumental.wav', 'duration': 4}}},
        ],
    }
    atomic_json(source_path, source_data)

    # The ordinary preparation path is not a rebuild: its authored envelope
    # and pitch curve must remain exactly as supplied.
    ordinary = prepare_copy(source_path, reference, root / 'ordinary')
    ordinary_group = project_data(ordinary['project'])['tracks'][0]['mainGroup']
    assert ordinary_group['parameters']['vibratoEnv'] == source_group['parameters']['vibratoEnv']
    assert ordinary_group['parameters']['pitchDelta'] == source_group['parameters']['pitchDelta']

    measured = SimpleNamespace(
        midi_pitch=(57 + .25 * np.sin(np.arange(400) * .01 * 2 * np.pi * 5.5)).astype('float32'),
        voiced_mask=np.ones(400, dtype=bool),
        time_step_seconds=.01,
    )
    measured.voiced_mask[100:150] = False
    measured.voiced_mask[300:] = False
    with patch('calibration_workflow._infer_pitch', return_value=measured):
        rebuilt = prepare_copy(source_path, reference, root / 'rebuilt', repair_pitch=True)

    rebuilt_data = project_data(rebuilt['project'])
    rebuilt_group = rebuilt_data['tracks'][0]['mainGroup']
    rebuilt_parameters = rebuilt_group['parameters']
    assert 'vibratoEnv' not in rebuilt_parameters
    assert 'pitchControls' not in rebuilt_group
    assert rebuilt_parameters['pitchDelta'] != source_group['parameters']['pitchDelta']
    assert rebuilt_parameters['loudness'] == source_group['parameters']['loudness']
    assert rebuilt_parameters['tension'] == source_group['parameters']['tension']

    # Read the prepared file through the installed LibreSVIP converter.  This
    # exercises the same data path that exposed the envelope scaling defect.
    from agent_pitch import compare_pitch_samples, load_pitch_project
    converters, _, _, _ = library()
    loaded = load_pitch_project(rebuilt['project'], 'svp', converters)
    samples = [
        {'seconds': seconds, 'midi_pitch': float(np.interp(
            round(seconds / .01), np.arange(400), measured.midi_pitch))}
        for seconds in (.25, .5, .75, 1.5, 1.75, 2., 2.5)
    ]
    comparison = compare_pitch_samples(loaded, samples, tolerance_cents=10.)
    assert comparison['status'] == 'match', comparison
    assert comparison['summary']['missing_or_outside'] == 0, comparison

    # The no-pitch export branch uses the same shared cleanup helper and keeps
    # non-pitch expression while removing all explicit pitch layers.
    strip_path = root / 'strip.svp'
    strip_data = copy.deepcopy(source_data)
    strip_data['tracks'][0]['mainGroup']['pitchControls'] = []
    atomic_json(strip_path, strip_data)
    remove_pitch_data(strip_path, 'svp')
    strip_group = json.loads(strip_path.read_text(encoding='utf-8'))['tracks'][0]['mainGroup']
    assert 'pitchDelta' not in strip_group['parameters']
    assert 'vibratoEnv' not in strip_group['parameters']
    assert 'pitchControls' not in strip_group
    assert strip_group['parameters']['loudness'] == source_group['parameters']['loudness']

print('PASS: AUD-02 shared explicit-F0 cleanup and AUD-13 saved runtime settings, with real SVP readback')
