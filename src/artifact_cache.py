"""Content receipts for resumable stages and narrowly scoped inference inputs."""
from pathlib import Path

from common import (ROOT, _custom_hash, app_path, atomic_json, dependency_manifest, file_hash,
                    fingerprint, inference_inputs, model_identity, native_path, pinned_files,
                    resolve_tools)
CODE_ROOT = Path(__file__).resolve().parent


def stage_signature(job, request, key):
    job = Path(job)
    tools = resolve_tools(request['tools'])
    local = request.get('source_kind') == 'local'
    data = {'schema': 1, 'stage': key}
    if key == 'metadata':
        data.update(bv=request.get('bv'), title=request.get('source_title'), lyrics=request.get('lyrics', ''))
    elif key == 'download':
        data.update(source=request.get('source_audio') if local else request.get('bv'))
        if not local:
            from bilibili_session import task_options
            data['authenticated'] = task_options(request['tools'])['use_login']
    elif key == 'audio':
        data.update(source=file_hash(job / (request['input_file'] if local else 'video/source.mp4')),
                    pcm='44100/stereo/float32')
    elif key == 'separation':
        mode = request.get('voice_mode', 'single')
        data.update(audio=file_hash(job / 'audio/source.wav'), mode=mode,
                    separation_flow=request.get('separation_flow','cascade'))
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
                from optional_components import manifest, require_ready
                from roformer_onnx import _resolve_onnx_model
                spec = manifest()
                try:
                    active_model = _resolve_onnx_model(require_ready(), spec)
                    data['roformer_model'] = (active_model.name, active_model.stat().st_size)
                except (OSError, ValueError):
                    data['roformer_model'] = [(e['name'], e['sha256']) for e in spec['weights']]
                data['roformer_adapter'] = file_hash(CODE_ROOT / 'roformer.py')
        data['adapter'] = file_hash(CODE_ROOT / 'mdx.py') if mode != 'import' else 1
        if mode in ('dual','roformer'):
            data['partition_adapter'] = file_hash(CODE_ROOT/'vocal_partition.py')
    elif key == 'subtitles':
        data['reference_lines'] = [request.get('lyric_lines', []), request.get('backing_lyric_lines', [])]
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
    source = sf.info(str(native_path(job / 'audio/source.wav')))
    for path in paths:
        info = sf.info(str(native_path(path)))
        if info.frames <= 0 or info.channels != 2 or info.samplerate != 44100 or info.frames != source.frames:
            return False
        if scan:
            for block in sf.blocks(str(native_path(path)),blocksize=65536,dtype='float32',always_2d=True):
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
