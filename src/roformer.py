"""Optional karaoke stage: total vocals -> lead/backing, identical for zh and ja."""
import json
import os
from pathlib import Path
import sys
import time
from common import ROOT, atomic_json, cancelled, file_hash, native_path, load_config


def separate_vocals(source, directory, job, device='auto', *, input_kind='vocals'):
    if input_kind not in ('vocals', 'mix'):
        raise ValueError('分离输入类型无效。')
    from optional_components import COMPONENT, require_ready, manifest
    from pipeline import job_lock
    environment = require_ready()
    # The optional engine probes PATH even when receiving an in-memory waveform.
    # Use the bundled FFmpeg so a valid portable install is not reported missing.
    binary = str(Path(load_config()['ffmpeg']).parent)
    os.environ['PATH'] = binary + os.pathsep + os.environ.get('PATH', '')
    with job_lock(COMPONENT):
        spec = manifest()
        for item in spec['weights']:
            if file_hash(environment / item['name']) != item['sha256']:
                raise ValueError('BS-RoFormer 权重／配置校验失败，请重新安装组件。')
        sys.path.insert(0, str(environment / 'packages'))
        os.environ['PYMSS_PLUGINS_DIR'] = str(ROOT / 'cache/pymss-plugins')
        os.environ['NUMBA_CACHE_DIR'] = native_path(ROOT / 'cache/numba')
        import numpy as np
        import soundfile as sf
        import torch
        from pymss import MSSeparator
        request=json.loads((Path(job)/'request.json').read_text(encoding='utf-8')) if (Path(job)/'request.json').is_file() else {}
        torch.set_num_threads(request.get('tools',{}).get('cpu_threads',0) or 6)
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('当前环境无法使用 CUDA，请在处理设置中选择“自动”或“CPU”。')
        audio, rate = sf.read(str(source), dtype='float32', always_2d=True)
        if rate != 44100 or audio.shape[1] != 2 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError('BS-RoFormer 需要有效的 44.1 kHz 双声道音频。')
        cancelled(job)
        started = time.monotonic()
        with MSSeparator(model_type='bs_roformer', model_path=str(environment / spec['weights'][0]['name']),
                         config_path=str(environment / 'config.yaml'), device=device, use_tta=False,
                         store_dirs=str(Path(job)/'temp/roformer'),
                         inference_params={'batch_size': 1, 'normalize': False, 'use_amp': True}) as separator:
            def progress(done, total, message):
                cancelled(job)
                from progress_state import publish
                publish(job, 'separation', '主唱 / 和声 · BS-RoFormer', done, total)
                print(f'主唱／和声分离：{done}/{total}', flush=True)
            separator.progress_callback = progress
            print(f'BS-RoFormer · 设备 {separator.device}', flush=True)
            result = separator.separate(audio.T, pbar=False)
            lead = np.asarray(next(value for key, value in result.items() if key.casefold() == 'vocals'), dtype='float32')
            provider = str(separator.device)
            del result
        cancelled(job)
        if lead.shape != audio.shape or not np.isfinite(lead).all():
            raise RuntimeError('BS-RoFormer 输出格式无效。')
        # Silence can be correct for a backing-only passage. Preserve the prediction.
        directory = Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        pending=[directory/'lead.pending.wav',directory/'backing.pending.wav']
        max_error=0.
        try:
            with sf.SoundFile(str(pending[0]),'w',samplerate=rate,channels=2,subtype='FLOAT') as a, \
                 sf.SoundFile(str(pending[1]),'w',samplerate=rate,channels=2,subtype='FLOAT') as b:
                for start in range(0,len(audio),65536):
                    cancelled(job)
                    primary=lead[start:start+65536];mix=audio[start:start+65536]
                    backing=mix-primary
                    a.write(primary);b.write(backing)
                    max_error=max(max_error,float(np.abs(primary+backing-mix).max()))
            pending[0].replace(directory/'lead.wav');pending[1].replace(directory/'backing.wav')
        finally:
            for path in pending:path.unlink(missing_ok=True)
        return {'model': spec['weights'][0]['name'], 'sha256': spec['weights'][0]['sha256'],
                'provider': provider, 'method': 'original_mix_to_lead_and_remainder' if input_kind=='mix' else 'total_vocals_then_karaoke',
                'input_kind': input_kind,
                'sample_rate': rate, 'samples': len(audio), 'timeline_offset_seconds': 0,
                'batch_size': 1, 'chunk_size': 882000, 'overlap_size': 661500, 'tta': False,
                'output_normalization': False, 'processing_seconds': round(time.monotonic() - started, 2),
                'sum_max_error': max_error}


def combine_independent(vocals, lead, directory, job):
    """Subtract two independent original-mix predictions without another model."""
    import numpy as np
    import soundfile as sf
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    outputs = [directory/'lead.pending.wav', directory/'backing.pending.wav']
    error, samples, excess = 0., 0, 0
    try:
        with sf.SoundFile(str(vocals)) as total, sf.SoundFile(str(lead)) as primary:
            if (not total.frames or total.samplerate != 44100 or total.channels != 2 or
                    (total.frames,total.samplerate,total.channels) != (primary.frames,primary.samplerate,primary.channels)):
                raise ValueError('独立提取结果的采样率、声道或长度不一致。')
            with sf.SoundFile(str(outputs[0]), 'w',samplerate=44100,channels=2,subtype='FLOAT') as a, \
                 sf.SoundFile(str(outputs[1]), 'w',samplerate=44100,channels=2,subtype='FLOAT') as b:
                while True:
                    cancelled(job)
                    mix = total.read(65536,dtype='float32',always_2d=True)
                    if not len(mix): break
                    foreground = primary.read(len(mix),dtype='float32',always_2d=True)
                    if foreground.shape!=mix.shape or not np.isfinite(mix).all() or not np.isfinite(foreground).all():
                        raise ValueError('独立提取结果包含无效采样。')
                    backing = mix-foreground
                    a.write(foreground); b.write(backing)
                    error=max(error,float(np.max(np.abs(foreground+backing-mix))))
                    samples+=len(mix); excess+=int(np.count_nonzero(np.max(abs(foreground),axis=1)>np.max(abs(mix),axis=1)+.05))
        cancelled(job)
        for pending,name in zip(outputs,('lead.wav','backing.wav')): pending.replace(directory/name)
    finally:
        for pending in outputs: pending.unlink(missing_ok=True)
    return {'method':'total_vocals_minus_independent_lead','samples':samples,'sum_max_error':error,
            'lead_exceeds_total_fraction':round(excess/max(samples,1),5),
            'note':'独立预测求差可能保留分离误差；请对比试听和声。'}
