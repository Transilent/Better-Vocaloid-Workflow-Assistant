"""Keep native runtime working directories and caches inside Vocaloid Workflow Assistant."""
import os
import sys
import tempfile
from pathlib import Path

from common import ROOT, load_config, native_path


def configure_llama_layout(llama, source):
    """Contain the legacy loader shim and reject incompatible upstream layouts."""
    import inspect
    source = Path(source).resolve()
    if not source.is_dir():
        raise FileNotFoundError('Qwen ASR 本地运行库缺失，请检查或恢复运行库。')
    if getattr(llama, '_vwa_layout', None) == str(source):
        return
    loader = getattr(llama, 'load_model', None)
    if not callable(loader) or not callable(getattr(llama, 'init_llama_lib', None)):
        raise RuntimeError('Qwen ASR 加载接口不兼容，请使用助手配套的运行库。')
    # This portable runtime does not read a DLL-directory environment variable.
    # Check its contract before applying the process-local compatibility shim.
    names = getattr(getattr(loader, '__code__', None), 'co_names', ())
    if ('model_path' not in inspect.signature(loader).parameters
            or 'relpath' not in names or '__file__' not in names):
        raise RuntimeError('Qwen ASR 路径接口已改变，需要更新助手的运行时适配层。')
    llama._vwa_original_file = getattr(llama, '_vwa_original_file', llama.__file__)
    llama.__file__ = str(source.parent / 'llama.py')
    llama.relpath = lambda path, start: str(Path(path).resolve())
    llama._vwa_layout = str(source)


def validate_backend_contract():
    """Fail before inference if a runtime update removes a required hook."""
    from inference.API import game_api, hfa_api, lfa_api, rmvpe_api, ustx_api
    import inference.pipeline.auto_lyric_hybrid as hybrid
    import inference.pipeline.lyric_alignment as alignment
    required = [(game_api, name) for name in ('extract_vowel_boundaries', '_find_word_nucleus_start')]
    required += [(hybrid, name) for name in ('_run_game_stage', '_export_outputs')]
    required += [(hfa_api, '_repair_short_word_boundaries'), (alignment, 'run_hubert_fa'),
                 (lfa_api.LyricMatcher, 'align_lyric_with_asr'),
                 (rmvpe_api.RmvpeTranscriber, 'infer'), (ustx_api, '_build_pitd_curve')]
    missing = [name for module, name in required if not callable(getattr(module, name, None))]
    if not isinstance(getattr(game_api, '_NON_SINGABLE_WORD_TOKENS', None), (set, frozenset, list, tuple)):
        missing.append('_NON_SINGABLE_WORD_TOKENS')
    if missing:
        raise RuntimeError('Vocal2Midi 运行库接口不兼容：'+', '.join(missing)+'。请使用助手配套运行库。')
    return {'schema': 1, 'checked_hooks': len(required)+1}


def configure(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    # Both backends use the assistant's one FFmpeg installation.
    ffmpeg=Path(load_config()['ffmpeg']).parent
    os.environ['PATH']=str(ffmpeg)+os.pathsep+os.environ.get('PATH','')
    cache = ROOT / "cache/numba"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["NUMBA_CACHE_DIR"] = native_path(cache)
    os.environ["V2M_PORTABLE_ROOT"] = str(root)
    os.environ["VOCALFLOW_V2M_ROOT"] = str(root)
    temp = Path(os.environ.get("TMP", str(ROOT / "cache/v2m-temp"))).resolve()
    if not temp.is_relative_to(ROOT):
        temp = ROOT / "cache/v2m-temp"
    temp.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = str(temp)
    # Libraries may have queried tempfile before the runtime was configured.
    tempfile.tempdir = str(temp)
    # Native libraries are loaded read-only. Share the existing DLL directory
    # instead of creating another 100 MiB copy for every installation.
    source = root / "inference/qwen3asr_dml/bin"
    if not source.is_dir():
        source=ROOT/'dependencies/vocal2midi/inference/qwen3asr_dml/bin'
    binary = source
    os.environ["PATH"] = str(binary) + os.pathsep + os.environ.get("PATH", "")
    # No installed source is changed. The runtime locates DLLs using its
    # module file location, and its model loader accepts absolute paths.
    # An absolute model path also works when the cache and model are on
    # different drives (os.path.relpath cannot handle that case).
    from inference.qwen3asr_dml import llama
    configure_llama_layout(llama, source)
