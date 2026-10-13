"""Content receipts for resumable stages and narrowly scoped inference inputs."""
import configparser
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from common import ROOT, app_path, atomic_json, dependency_manifest, file_hash, resolve_tools
CODE_ROOT = Path(__file__).resolve().parent


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


@lru_cache(maxsize=1)
def pinned_files():
    return {item['file']: item for item in dependency_manifest().get('entries', [])}


@lru_cache(maxsize=128)
def _custom_hash(path, size, modified):
    return file_hash(path)


def model_identity(value):
    """Use the verified distribution catalog for immutable bundled weights.

    Custom models are hashed once per size/mtime within the worker. The separate
    dependency check remains responsible for verifying the installed catalog.
    """
    path = Path(value).resolve()
    catalog = pinned_files()
    if path.is_relative_to(ROOT):
        name = path.relative_to(ROOT).as_posix()
        entries = [item for key, item in catalog.items()
                   if key == name or key.startswith(name + '/')]
        if entries:
            return [(item['file'].removeprefix(name), item['sha256'],
                     (ROOT / item['file']).stat().st_size) for item in sorted(entries, key=lambda x: x['file'])]
    files = sorted(path.rglob('*')) if path.is_dir() else [path]
    return [(p.relative_to(path).as_posix() if path.is_dir() else p.name,
             _custom_hash(str(p), p.stat().st_size, p.stat().st_mtime_ns))
            for p in files if p.is_file()]


def inference_inputs(job, request, voice):
    tools = resolve_tools(request['tools'])
    backend = Path(tools['vocal2midi'])
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(backend / 'settings/vocal2midi.ini', encoding='utf-8-sig')
    settings = dict(parser['General']) if parser.has_section('General') else {}
    # These are the values actually consumed by midi_bridge. GUI, export-list,
    # download and publishing preferences do not affect recognition.
    defaults = {'t0': 0., 'slice_min_sec': 5., 'slice_max_sec': 10.,
                'round_pitch': True, 'seg_thresh': .2, 'seg_rad': .02,
                'est_thresh': .2, 'batch_size': 1, 'asr_batch': 2}
    effective = {}
    for key, default in defaults.items():
        raw = settings.get(key, str(default))
        effective[key] = raw.lower() in ('true', '1') if isinstance(default, bool) else type(default)(raw)
    effective['nsteps'] = request.get('midi_steps', int(settings.get('nsteps', 8)))
    recognize = request.get('recognize_lyrics', True)
    pitch = bool(request.get('export_pitch') and request.get('project_formats'))
    language = request['language']
    models = {'game_model': 'experiments/GAME-1.0.3-medium-onnx'}
    if recognize:
        models['hfa_model'] = 'experiments/1218_hfa_model_new_dict'
        models['asr_model' if language == 'zh' else 'phoneme_asr_model'] = (
            'experiments/Qwen3-ASR-1.7B-dml' if language == 'zh' else 'experiments/romajiASR')
    if pitch:
        models['rmvpe_model'] = 'experiments/RMVPE/rmvpe.onnx'
    identities = {}
    for key, default in models.items():
        path = Path(settings.get(key, default))
        candidate = path if path.is_absolute() else backend / path
        if not candidate.exists() and not path.is_absolute() and (backend / 'backend-version.json').is_file():
            candidate = ROOT / 'dependencies/vocal2midi' / path
        identities[key] = model_identity(candidate)
    code = [(str(p.relative_to(backend)), file_hash(p))
            for directory in ('application', 'inference') for p in sorted((backend / directory).rglob('*.py'))]
    return {'schema': 6, 'audio': file_hash(Path(job) / 'audio' / (voice + '.wav')),
            'voice': voice, 'language': language, 'recognize_lyrics': recognize,
            'lyrics': request.get('backing_lyrics', '') if voice == 'backing' else request.get('lyrics', ''),
            'lyric_mode': request.get('zh_lyric_mode', settings.get('lyric_output_mode_zh', '汉字')) if language == 'zh'
                          else settings.get('lyric_output_mode_ja', '罗马音'),
            'native_project': bool(request.get('project_formats')), 'pitch': pitch,
            'alignment': request.get('alignment_engine', 'hfa'), 'models': identities, 'code': code,
            'parameters': effective, 'device': tools.get('midi_device', 'dml'), 'adapter': tools.get('dml_adapter', 'auto')}


def stage_signature(job, request, key):
    job = Path(job)
    tools = resolve_tools(request['tools'])
    local = request.get('source_kind') == 'local'
    data = {'schema': 1, 'stage': key}
    if key == 'metadata':
        data.update(bv=request.get('bv'), title=request.get('source_title'), lyrics=request.get('lyrics', ''))
    elif key == 'download':
        data.update(source=request.get('source_audio') if local else request.get('bv'))
    elif key == 'audio':
        data.update(source=file_hash(job / (request['input_file'] if local else 'video/source.mp4')),
                    pcm='44100/stereo/float32')
    elif key == 'separation':
        mode = request.get('voice_mode', 'single')
        data.update(audio=file_hash(job / 'audio/source.wav'), mode=mode)
        if mode == 'import':
            data['stems'] = {name: file_hash(app_path(path)) for name, path in request['imported_stems'].items()}
        else:
            data['models'] = {name: model_identity(tools[name]) for name in ('mdx_model', 'mdx_metadata')}
            data['parameters'] = {name: tools.get(name) for name in
                                  ('separation_device', 'mdx_denoise', 'mdx_overlap', 'dml_adapter')}
            if mode == 'dual':
                data['chorus'] = {name: model_identity(tools[name]) for name in ('chorus_model', 'chorus_metadata')}
            elif mode == 'roformer':
                data['roformer'] = request.get('roformer_device', 'auto')
                from optional_components import manifest
                data['roformer_model'] = [(e['name'], e['sha256']) for e in manifest()['weights']]
                data['roformer_adapter'] = file_hash(CODE_ROOT / 'roformer.py')
        data['adapter'] = file_hash(CODE_ROOT / 'mdx.py') if mode != 'import' else 1
    elif key == 'subtitles':
        voices = ('vocals',) if request.get('voice_mode') == 'single' else ('lead', 'backing')
        data['midi'] = {voice: file_hash(job / 'midi' / ('' if voice == 'vocals' else voice) / (voice + '.mid'))
                        for voice in voices}
        data['adapter'] = file_hash(CODE_ROOT / 'subtitles.py')
    return fingerprint(data)


def audio_valid(job, key, files, scan=False):
    import soundfile as sf
    import numpy as np
    job = Path(job)
    paths = [job / name for name in files if name.endswith('.wav')]
    source = sf.info(str(job / 'audio/source.wav'))
    for path in paths:
        info = sf.info(str(path))
        if info.frames <= 0 or info.channels != 2 or info.samplerate != 44100 or info.frames != source.frames:
            return False
        if scan:
            for block in sf.blocks(str(path),blocksize=65536,dtype='float32',always_2d=True):
                if not np.isfinite(block).all(): return False
    return source.frames > 0


def receipt(job, request, key, files):
    if key in ('audio', 'separation') and not audio_valid(job, key, files, scan=True):
        raise ValueError('音频缓存验证失败，请检查采样率、声道和时长。')
    return {'signature': stage_signature(job, request, key),
            'sha256': {name: file_hash(Path(job) / name) for name in files}}


def valid_receipt(job, request, key, files, saved):
    try:
        if key in ('audio', 'separation') and not audio_valid(job, key, files, scan=not saved):
            return False
        if not saved:
            return True  # Legacy immutable requests: validate and migrate in place.
        return (saved['signature'] == stage_signature(job, request, key)
                and all(saved['sha256'].get(name) == file_hash(Path(job) / name) for name in files))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False
