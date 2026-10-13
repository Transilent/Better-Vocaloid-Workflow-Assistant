"""Keep native runtime working directories and caches inside Better Vocaloid Workflow Assistant."""
import os
import sys
import tempfile
from pathlib import Path

from common import ROOT, load_config


def configure(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    # Both backends use the assistant's one FFmpeg installation.
    ffmpeg=Path(load_config()['ffmpeg']).parent
    os.environ['PATH']=str(ffmpeg)+os.pathsep+os.environ.get('PATH','')
    cache = ROOT / "cache/numba"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["NUMBA_CACHE_DIR"] = str(cache)
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
    llama.__file__ = str(source.parent / "llama.py")
    llama.relpath = lambda path, start: str(Path(path).resolve())
