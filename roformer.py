"""Optional karaoke stage: total vocals -> lead/backing, identical for zh and ja."""
import json
import os
from pathlib import Path
import sys
import time
from common import ROOT, atomic_json, cancelled, file_hash


def separate_vocals(source, directory, job, device='auto'):
    from optional_components import COMPONENT, require_ready, manifest
    from pipeline import job_lock
    environment = require_ready()
    with job_lock(COMPONENT):
        spec = manifest()
        for item in spec['weights']:
            if file_hash(environment / item['name']) != item['sha256']:
                raise ValueError('BS-RoFormer 权重／配置校验失败，请重新安装组件。')
        sys.path.insert(0, str(environment / 'packages'))
        os.environ['PYMSS_PLUGINS_DIR'] = str(ROOT / 'cache/pymss-plugins')
        os.environ['NUMBA_CACHE_DIR'] = str(ROOT / 'cache/numba')
        import numpy as np
        import soundfile as sf
        import torch
        from pymss import MSSeparator
        torch.set_num_threads(6)
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('当前环境无法使用 CUDA，请在处理设置中选择“自动”或“CPU”。')
        audio, rate = sf.read(str(source), dtype='float32', always_2d=True)
        if rate != 44100 or audio.shape[1] != 2 or not len(audio) or not np.isfinite(audio).all():
            raise ValueError('BS-RoFormer 需要有效的 44.1 kHz 双声道总人声。')
        cancelled(job)
        started = time.monotonic()
        with MSSeparator(model_type='bs_roformer', model_path=str(environment / spec['weights'][0]['name']),
                         config_path=str(environment / 'config.yaml'), device=device, use_tta=False,
                         inference_params={'batch_size': 1, 'normalize': False, 'use_amp': True}) as separator:
            def progress(done, total, message):
                cancelled(job)
                print(f'主唱／和声分离：{done}/{total}', flush=True)
            separator.progress_callback = progress
            print(f'BS-RoFormer · 设备 {separator.device}', flush=True)
            result = separator.separate(audio.T, pbar=False)
            lead = np.asarray(next(value for key, value in result.items() if key.casefold() == 'vocals'), dtype='float32')
            provider = str(separator.device)
        cancelled(job)
        if lead.shape != audio.shape or not np.isfinite(lead).all():
            raise RuntimeError('BS-RoFormer 输出格式无效。')
        # Silence can be correct for a backing-only passage. Preserve the prediction.
        backing = audio - lead
        directory = Path(directory)
        sf.write(str(directory / 'lead.wav'), lead, rate, subtype='FLOAT')
        sf.write(str(directory / 'backing.wav'), backing, rate, subtype='FLOAT')
        return {'model': spec['weights'][0]['name'], 'sha256': spec['weights'][0]['sha256'],
                'provider': provider, 'method': 'total_vocals_then_karaoke',
                'sample_rate': rate, 'samples': len(audio), 'timeline_offset_seconds': 0,
                'batch_size': 1, 'chunk_size': 882000, 'overlap_size': 661500, 'tta': False,
                'output_normalization': False, 'processing_seconds': round(time.monotonic() - started, 2),
                'sum_max_error': float(np.abs(lead + backing - audio).max())}
