"""Optional karaoke stage: total vocals -> lead/backing via ONNX Runtime WebGPU/CPU."""
from pathlib import Path
import sys

from common import file_hash
from gpu_backend import normalize_roformer_device


def separate_vocals(source, directory, job, device='auto', *, input_kind='vocals'):
    if input_kind not in ('vocals', 'mix'):
        raise ValueError('分离输入类型无效。')
    normalized_device = normalize_roformer_device(device)
    from optional_components import manifest, use_ready

    with use_ready() as environment:
        spec = manifest()
        from roformer_onnx import _resolve_onnx_model, separate_vocals_onnx

        active_model = _resolve_onnx_model(Path(environment), spec)
        allowed_onnx_hashes = set()
        for item in spec.get('weights', ()):
            name = str(item.get('name') or '')
            if name.lower().endswith('.onnx'):
                expected = str(item.get('sha256') or '').strip().lower()
                if expected:
                    allowed_onnx_hashes.add(expected)
                for variant in item.get('variants') or ():
                    if isinstance(variant, dict):
                        v_sha = str(variant.get('sha256') or '').strip().lower()
                        if v_sha:
                            allowed_onnx_hashes.add(v_sha)
            else:
                expected = str(item.get('sha256') or '').strip().lower()
                target = Path(environment) / name
                if expected and target.is_file() and file_hash(target) != expected:
                    raise ValueError('BS-RoFormer 权重／配置校验失败，请重新安装组件。')
        if allowed_onnx_hashes and file_hash(active_model) not in allowed_onnx_hashes:
            raise ValueError('BS-RoFormer 权重／配置校验失败，请重新安装组件。')
        packages_dir = Path(environment) / 'packages'
        if packages_dir.is_dir() and str(packages_dir) not in sys.path:
            sys.path.insert(0, str(packages_dir))

        return separate_vocals_onnx(
            source,
            directory,
            job,
            Path(environment),
            spec,
            device=normalized_device,
            input_kind=input_kind,
        )


def combine_independent(vocals, lead, directory, job):
    """Subtract two independent original-mix predictions without another model."""
    import numpy as np
    import soundfile as sf
    from common import cancelled
    from multi_file_commit import FileTransactionError, commit_file_pair

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    outputs = [directory / 'lead.pending.wav', directory / 'backing.pending.wav']
    error, samples, excess = 0.0, 0, 0
    preserve_pending = False
    try:
        with sf.SoundFile(str(vocals)) as total, sf.SoundFile(str(lead)) as primary:
            if (not total.frames or total.samplerate != 44100 or total.channels != 2 or
                    (total.frames, total.samplerate, total.channels) != (primary.frames, primary.samplerate, primary.channels)):
                raise ValueError('独立提取结果的采样率、声道或长度不一致。')
            with sf.SoundFile(str(outputs[0]), 'w', samplerate=44100, channels=2, subtype='FLOAT') as a, \
                 sf.SoundFile(str(outputs[1]), 'w', samplerate=44100, channels=2, subtype='FLOAT') as b:
                while True:
                    cancelled(job)
                    mix = total.read(65536, dtype='float32', always_2d=True)
                    if not len(mix):
                        break
                    foreground = primary.read(len(mix), dtype='float32', always_2d=True)
                    if foreground.shape != mix.shape or not np.isfinite(mix).all() or not np.isfinite(foreground).all():
                        raise ValueError('独立提取结果包含无效采样。')
                    backing = mix - foreground
                    a.write(foreground)
                    b.write(backing)
                    error = max(error, float(np.max(np.abs(foreground + backing - mix))))
                    samples += len(mix)
                    excess += int(np.count_nonzero(np.max(abs(foreground), axis=1) > np.max(abs(mix), axis=1) + 0.05))
        cancelled(job)
        transaction = commit_file_pair(outputs, [directory / 'lead.wav', directory / 'backing.wav'])
    except FileTransactionError as exc:
        preserve_pending = exc.preserve_staging
        raise
    finally:
        if not preserve_pending:
            for pending in outputs:
                pending.unlink(missing_ok=True)
    return {
        'method': 'total_vocals_minus_independent_lead',
        'samples': samples,
        'sum_max_error': error,
        'lead_exceeds_total_fraction': round(excess / max(samples, 1), 5),
        'note': '独立预测求差可能保留分离误差；请对比试听和声。',
        'transaction': transaction,
    }
