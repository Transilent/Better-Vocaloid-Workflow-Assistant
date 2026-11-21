"""Portable identities and local audio bindings for measured SVP calibration presets."""

import hashlib
import json
from pathlib import Path
import re
from functools import lru_cache
from uuid import uuid4

import calibration_session
from common import atomic_json, file_hash, native_path
from expression_calibration import CONTROLS, calibration_identity, read_profile, score_identity
from preset_documents import (CALIBRATION_CAPABILITY, CONTRACTS, encode_document,
                              validate_document)

TYPE = 'voicebank_calibration'
DOCUMENT_SLOTS = ('vocal_tracks', 'reference_audio', 'calibration_renders')


@lru_cache(maxsize=32)
def profile_label(path):
    if not path:
        return ''
    file = Path(path)
    if file.parent.name == 'profiles' and file.parent.parent.name == 'calibration-bindings':
        try:
            value = json.loads(Path(native_path(file)).read_text(encoding='utf-8'))
            name = value.get('preset_name')
            if isinstance(name, str) and name.strip():
                return name[:80]
        except (OSError, ValueError, AttributeError):
            pass
    return file.name


def _voice_settings(track):
    ref = track.get('mainRef', {})
    database = ref.get('database') or {}
    voice = ref.get('voice') or {}
    offsets = {'param' + key[0].upper() + key[1:] for key in CONTROLS}
    return database, {key: value for key, value in voice.items() if key not in offsets}


def from_verified_fit(session, shown, name, *, cancel=lambda: None):
    """Build a manifest only from an applied fit and its verified seven renders."""
    session = Path(session).resolve()
    spec = calibration_session.read_session(session)
    fit = calibration_session.verified_fit_result(session, output_name=Path(shown['output']).name, cancel=cancel)
    if (not fit or fit['calibration_fit'].get('voicebank_calibrated') is not True
            or any(fit[key] != shown[key] for key in ('sha256', 'profile_sha256'))):
        raise ValueError('校准结果或试音已改变，请重新核对。')
    voice = spec['voice']
    config = read_profile(fit['profile'])
    row = config['tracks'][voice]
    receipt = json.loads((session.parent / 'render-receipt.json').read_text(encoding='utf-8'))['renders']
    steps = {probe['parameter']: probe['step'] for probe in row['probes']}
    paths = {'neutral': row['baseline'], **{probe['parameter']: probe['audio'] for probe in row['probes']}}
    folder = Path(spec['render_folder']).resolve()
    if (set(steps) != set(CONTROLS) or row['score_sha256'] != spec['score_sha256']
            or any(Path(clip).resolve() != Path(receipt[channel]['path']).resolve()
                   or Path(clip).resolve().parent != folder for channel, clip in paths.items())):
        raise ValueError('试音目录与已核验的校准结果不匹配。')
    trials = {channel: {'token': token, 'sha256': receipt[channel]['sha256']}
              for channel, token in spec['outputs'].items()}
    for channel, step in steps.items():
        trials[channel]['step'] = step
    pack = validate_document({
        'format': 'vwa.preset', 'format_version': 1, 'id': 'user.' + uuid4().hex,
        'type': TYPE, 'version': '1.0.0', 'metadata': {'name': name.strip()},
        'requires': {'preset_api': 1, 'engine_contract': CONTRACTS[TYPE],
                     'capabilities': [CALIBRATION_CAPABILITY]},
        'binding_slots': [{'id': slot, 'kind': slot, 'required': True} for slot in DOCUMENT_SLOTS],
        'payload': {'voice': voice, 'voicebank': row['database']['name'],
                    'score_sha256': row['score_sha256'],
                    'settings_sha256': calibration_identity([row['database'], row.get('voice', {})]),
                    'strength': fit['calibration_fit']['strength'],
                    'fit_strategy': config.get('fit_strategy', 'detailed'),
                    'render_channel_mode': row.get('render_channel_mode', 'standard'), 'trials': trials},
    })
    cancel()
    return pack, {'folder': str(folder), 'reference': spec['reference']}


def resolve_trials(document, folder, *, cancel=lambda: None, progress=lambda _message: None):
    """Find WAVs by the session's export suffix, then verify their exact bytes."""
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError('请选择七份声库试音所在的文件夹。')
    paths = [item for item in folder.iterdir() if item.suffix.casefold() == '.wav' and item.is_file()]
    verified = {}
    for name, row in document['payload']['trials'].items():
        cancel()
        token = row['token'].casefold()
        choices = [path for path in paths if path.stem.casefold().endswith(token)]
        if len(choices) != 1:
            raise ValueError('试音查找需要唯一匹配：' + row['token'])
        path, = choices
        if path.is_symlink() or path.resolve().parent != folder:
            raise ValueError('请将试音文件放在所选目录中。')
        if str(path) in verified.values():
            raise ValueError('每个试音通道需要独立的 WAV 文件。')
        progress(f'正在核对试音文件 {len(verified) + 1} / 7…')
        if file_hash(path, check=cancel) != row['sha256']:
            raise ValueError('试音文件内容与校准预设不匹配：' + path.name)
        verified[name] = str(path)
    return verified


class LocalCalibrationBindings:
    """Keep local directories apart from exported documents and immutable run profiles."""

    def __init__(self, library_root):
        self.root = Path(library_root) / 'calibration-bindings'

    def _path(self, sha256):
        if not isinstance(sha256, str) or re.fullmatch('[0-9a-f]{64}', sha256) is None:
            raise ValueError('校准预设身份无效。')
        return self.root / (sha256 + '.json')

    def get(self, sha256):
        path = self._path(sha256)
        if not path.is_file():
            return {}
        row = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(row, dict) or set(row) != {'folder', 'reference'}
                or not all(isinstance(value, str) for value in row.values())
                or not Path(row['folder']).is_absolute()
                or row['reference'] and not Path(row['reference']).is_absolute()):
            raise ValueError('本机校准目录关联无效。')
        return row

    def put(self, sha256, folder, reference=''):
        row = {'folder': str(Path(folder).resolve()), 'reference': str(Path(reference).resolve()) if reference else ''}
        atomic_json(self._path(sha256), row)
        return row

    def profile(self, document, sha256, data, track_index, *, cancel=lambda: None, progress=lambda _message: None):
        payload = document['payload']
        track = data['tracks'][track_index]
        if score_identity(track, data) != payload['score_sha256']:
            raise ValueError('当前轨的乐谱或音高与校准预设不匹配。')
        database, voice = _voice_settings(track)
        if database.get('name') != payload['voicebank'] or calibration_identity([database, voice]) != payload['settings_sha256']:
            raise ValueError('当前轨的声库或声库设置与校准预设不匹配。')
        association = self.get(sha256)
        if not association:
            raise ValueError('请先关联校准预设的试音目录。')
        trials = resolve_trials(document, association['folder'], cancel=cancel, progress=progress)
        projected = {
            'schema': 1, 'fit_strategy': payload['fit_strategy'], 'preset_sha256': sha256,
            'preset_name': document['metadata']['name'],
            'tracks': {payload['voice']: {
                'score_sha256': payload['score_sha256'], 'database': database, 'voice': voice,
                'render_channel_mode': payload['render_channel_mode'], 'baseline': trials['neutral'],
                'baseline_sha256': payload['trials']['neutral']['sha256'],
                'probes': [{'parameter': key, 'step': payload['trials'][key]['step'], 'audio': trials[key],
                            'sha256': payload['trials'][key]['sha256']} for key in CONTROLS],
            }},
        }
        path = self.root / 'profiles' / (calibration_identity(projected) + '.json')
        cancel()
        if not path.is_file():
            atomic_json(path, projected)
        if json.loads(Path(native_path(path)).read_text(encoding='utf-8')) != projected:
            raise ValueError('本机校准绑定文件已改变，请检查或重新关联试音目录。')
        reference = association['reference']
        return str(path), reference if reference and Path(reference).is_file() else ''


def save_fit_preset(library, bindings, session, shown, name, *, cancel=lambda: None):
    document, association = from_verified_fit(session, shown, name, cancel=cancel)
    sha256 = hashlib.sha256(encode_document(document)).hexdigest()
    bindings.put(sha256, **association)
    try:
        cancel()
        row = library.put_document(document, expected_revision=library.snapshot()['revision'])
    except (OSError, ValueError, InterruptedError):
        bindings._path(sha256).unlink(missing_ok=True)
        raise
    return row['entry']
