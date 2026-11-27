"""Export receipt reading, caching, signing, and commit helpers shared by project and context exports."""
from __future__ import annotations

import json
from pathlib import Path

from artifact_cache import fingerprint
from common import atomic_json, file_hash
from project_io import retained_signature, voices_for

ROOT = Path(__file__).resolve().parent


def read_receipts(job):
    try:
        data = json.loads((job / 'exports/receipts.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def project_input(job, request):
    instrumental = job / 'audio/instrumental.wav'
    pitch = bool(request.get('export_pitch', False))
    folders = {v: job / 'midi' / ('' if v == 'vocals' else v) for v in voices_for(request)}
    return fingerprint({
        'schema': 4, 'language': request['language'], 'pitch': pitch,
        'native': {v: file_hash(folder / (v + '.ustx')) for v, folder in folders.items()},
        'absolute_pitch': {v: file_hash(folder / (v + '.pitch.npz')) for v, folder in folders.items()
                           if pitch and (folder / (v + '.pitch.npz')).is_file()},
        'pitch_receipts': {v: file_hash(folder / 'report.json') for v, folder in folders.items()
                           if pitch and (folder / 'report.json').is_file()},
        'pitch_adapter': file_hash(ROOT / 'pitch_accuracy.py') if pitch else None,
        'instrumental': file_hash(instrumental) if request.get('include_instrumental', True) and instrumental.is_file() else None,
        'converter': file_hash(ROOT / 'project_exports.py'),
    })


def project_signatures(job, request):
    """Expression settings affect SVP export, never MIDI inference or other formats."""
    from vocal_expression import expression_settings
    enabled, strength, timbre = expression_settings(request)
    from calibration_refinement import density_settings
    density_mode = density_settings(request)
    base = project_input(job, request)
    signatures = {fmt: fingerprint([base, fmt]) for fmt in request.get('project_formats', [])}
    if 'vsqx' in signatures and request.get('export_pitch'):
        signatures['vsqx'] = fingerprint([signatures['vsqx'], file_hash(ROOT / 'vocaloid_pitch.py')])
    if 'vsqx' in signatures and request['language'] == 'en':
        from vocaloid_english import DICTIONARY_PATH, resolve_dictionary_path
        signatures['vsqx'] = fingerprint([signatures['vsqx'], file_hash(ROOT / 'vocaloid_english.py'),
                                          file_hash(resolve_dictionary_path() or (ROOT / DICTIONARY_PATH))])
    if 'svp' in signatures and (enabled or timbre) and strength > 0:
        signatures['svp'] = fingerprint([
            base, 'svp', strength,
            file_hash(ROOT / 'vocal_expression.py'),
            enabled, timbre,
            [file_hash(ROOT / name) for name in ('vocal_timbre.py', 'expression_component.py')] if timbre else [],
            {voice: file_hash(job / 'audio' / (voice + '.wav')) for voice in voices_for(request)},
        ])
    if request.get('reference_calibration') and 'svp' in signatures:
        from expression_calibration import signature
        from common import app_path
        signatures['svp'] = fingerprint([
            signatures['svp'], signature(app_path(request['reference_calibration'])), strength,
            {voice: file_hash(job / 'audio' / (voice + '.wav')) for voice in voices_for(request)},
            [file_hash(ROOT / name) for name in ('vocal_timbre.py', 'expression_component.py')],
        ])
    if request.get('retained_expression') and not request.get('reference_calibration') and 'svp' in signatures:
        signatures['svp'] = fingerprint([signatures['svp'], retained_signature(job, request)])
    if 'svp' in signatures:
        signatures['svp'] = fingerprint([
            signatures['svp'], density_mode,
            [file_hash(ROOT / name) for name in
             ('tuning_engine.py', 'calibration_refinement.py', 'vocal_expression.py',
              'expression_calibration.py', 'calibration_workflow.py')],
        ])
    return signatures


def cached_output(job, path, signature, receipts):
    item = receipts.get(path.relative_to(job).as_posix(), {})
    if item.get('active') is False:
        return None
    try:
        return (item if item.get('input_signature') == signature
                and path.stat().st_size == item['bytes']
                and file_hash(path) == item['sha256'] else None)
    except (OSError, KeyError):
        return None


def commit_receipt(job, path, signature, receipts, **details):
    item = {'file': path.relative_to(job).as_posix(), 'bytes': path.stat().st_size,
            'sha256': file_hash(path), 'input_signature': signature, **details}
    receipts[item['file']] = item
    atomic_json(job / 'exports/receipts.json', receipts)
    from task_contract import index_export
    index_export(job, item, replace=True)
    return item
