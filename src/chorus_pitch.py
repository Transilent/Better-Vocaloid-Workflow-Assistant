"""Batch the installed RMVPE model in an isolated worker process."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def estimate_many(tracks, cache, check, progress):
    import numpy as np

    from common import ROOT
    from chorus_model_worker import model_batch

    model = ROOT / 'dependencies/vocal2midi/experiments/RMVPE/rmvpe.onnx'
    runtime = ROOT / 'dependencies/vocal2midi2'
    if not model.is_file() or not runtime.is_dir():
        raise FileNotFoundError('缺少音高分析运行库。可继续做时间对齐，安装 RMVPE 后再修音。')
    for track in tracks:
        check()
        track.check_source()
    spec = {'model': str(model), 'runtime': str(runtime), 'paths': [track.path for track in tracks]}
    with model_batch(__file__, spec, cache, check, progress,
                     [f'提取音高 · {track.name}' for track in tracks], '音高分析失败') as folder:
        curves = {}
        for index, track in enumerate(tracks):
            track.check_source()
            with np.load(folder / f'{index}.npz', allow_pickle=False) as result:
                curves[track.id] = (result['midi'], float(result['step'].item()))
        return curves


def _run(request):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import numpy as np
    import soundfile as sf

    from pitch_accuracy import prepare_audio, supported_pitch
    from runtime_devices import prepare_environment
    from v2m_runtime import configure

    spec = json.loads(request.read_text(encoding='utf-8'))
    configure(Path(spec['runtime']), pitch_only=True)
    prepare_environment({'midi_device': 'cpu', 'cpu_threads': 4, 'dml_adapter': 'auto'})
    from inference.API.rmvpe_api import RmvpeTranscriber

    runner = RmvpeTranscriber(Path(spec['model']), device='cpu', batch_size=1)
    try:
        for index, source in enumerate(spec['paths']):
            (request.parent / 'progress.txt').write_text(str(index), encoding='utf-8')
            audio, rate = sf.read(source, dtype='float32', always_2d=True)
            result = supported_pitch(runner.infer(prepare_audio(audio, rate), 16000))
            np.savez(request.parent / f'{index}.npz', midi=result.midi_pitch, step=result.time_step_seconds)
    finally:
        runner.shutdown()


if __name__ == '__main__':
    _run(Path(sys.argv[1]))
