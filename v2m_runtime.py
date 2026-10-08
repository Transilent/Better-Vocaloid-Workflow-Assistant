"""Keep native runtime working directories and caches inside Better Vocaloid Workflow Assistant."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from common import ROOT


def configure(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
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
    # llama.cpp switches its working directory to its bin folder. Use a
    # Use bundled DLLs so inference paths remain portable.
    # as the working directory in a restricted executor.
    source = root / "inference/qwen3asr_dml/bin"
    local = ROOT / "cache/qwen-runtime"
    binary = local / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    for file in source.glob("*.dll"):
        target = binary / file.name
        if not target.exists() or target.stat().st_size != file.stat().st_size or target.stat().st_mtime < file.stat().st_mtime:
            shutil.copy2(file, target)
    license_file = root / "LICENSE"
    if license_file.exists() and not (local / "Vocal2Midi-LICENSE").exists():
        shutil.copy2(license_file, local / "Vocal2Midi-LICENSE")
    os.environ["PATH"] = str(binary) + os.pathsep + os.environ.get("PATH", "")
    # No installed source is changed. The runtime locates DLLs using its
    # module file location, and its model loader accepts absolute paths.
    # An absolute model path also works when the cache and model are on
    # different drives (os.path.relpath cannot handle that case).
    from inference.qwen3asr_dml import llama
    llama.__file__ = str(local / "llama.py")
    llama.relpath = lambda path, start: str(Path(path).resolve())
