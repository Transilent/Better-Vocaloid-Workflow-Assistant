"""Tier 1 OSS engineering infrastructure regression suite (L0).

Verifies:
  1. Zero-dependency ctypes Win32JobObject creates a real Windows Job Object,
     assigns a process tree, and atomically terminates both parent and child
     processes without requiring pywin32.
  2. Standard Python project metadata & tool configs (pyproject.toml, .editorconfig)
     parse cleanly and match repository line-ending conventions.
  3. GitHub Actions L0 CI workflow (.github/workflows/ci.yml) exists and covers
     Prepare-Source, verify_source_layout, ruff, extract_i18n, L0 tests, and CMake.
  4. Native CMake build specification (src/native/CMakeLists.txt + CMakePresets.json)
     declares all 3 native DLLs + CTest without hardcoded personal machine paths.
  5. tools/extract_i18n.py passes with zero catalog errors and zero missing keys.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tomllib
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tools"))
assert os.environ.get("BVWA_TESTING") == "1"

import extract_i18n
from process_control import Win32JobObject, _JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        return False
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    try:
        if not kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return False
        return code.value == 259
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


# 1. Pure-ctypes Win32JobObject kills parent + grandchild with zero pywin32 dependency
if os.name == "nt":
    expected_size = 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112
    assert ctypes.sizeof(_JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == expected_size, (
        ctypes.sizeof(_JOBOBJECT_EXTENDED_LIMIT_INFORMATION),
        expected_size,
    )
    with patch.dict(sys.modules, {"win32job": None}):
        with tempfile.TemporaryDirectory(dir=APP / "work", prefix="job-obj-") as tmp:
            root = Path(tmp)
            child_pid_file = root / "grandchild.pid"
            launcher = root / "spawn_tree.py"
            launcher.write_text(
                "import subprocess, sys, time\n"
                "from pathlib import Path\n"
                "child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(120)'], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
                "time.sleep(120)\n",
                encoding="utf-8",
            )
            with Win32JobObject(kill_on_close=True) as job_obj:
                assert job_obj.active, "Win32JobObject must initialize via ctypes without pywin32"
                proc = subprocess.Popen(
                    [sys.executable, "-B", str(launcher), str(child_pid_file)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                assert job_obj.assign(proc), "AssignProcessToJobObject failed"
                assert job_obj.assigned
                for _ in range(200):
                    if child_pid_file.is_file() and child_pid_file.read_text(encoding="utf-8").strip():
                        break
                    time.sleep(0.02)
                assert child_pid_file.is_file(), "Grandchild PID never written"
                grandchild_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
                assert _pid_alive(grandchild_pid), "Grandchild should be alive before job termination"
                assert job_obj.terminate(1), "TerminateJobObject failed"
                proc.wait(timeout=10)
            for _ in range(100):
                if not _pid_alive(grandchild_pid):
                    break
                time.sleep(0.02)
            assert not _pid_alive(grandchild_pid), "Grandchild survived Win32JobObject termination"


# 2. pyproject.toml & .editorconfig
pyproject_path = APP / "pyproject.toml"
assert pyproject_path.is_file(), "pyproject.toml missing"
pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
assert pyproject["project"]["name"] == "vwa"
assert ">=3.12" in pyproject["project"]["requires-python"]
assert "ruff" in pyproject["tool"]
assert "pyright" in pyproject["tool"]
assert "pytest" in pyproject["tool"]

editorconfig_path = APP / ".editorconfig"
assert editorconfig_path.is_file(), ".editorconfig missing"
ec_text = editorconfig_path.read_text(encoding="utf-8")
assert "root = true" in ec_text
assert "[*.{ps1,bat,cmd}]" in ec_text and "end_of_line = crlf" in ec_text


# 3. GitHub Actions L0 CI workflow
ci_path = APP / ".github/workflows/ci.yml"
assert ci_path.is_file(), ".github/workflows/ci.yml missing"
ci_text = ci_path.read_text(encoding="utf-8")
for required_token in (
    "windows-latest",
    "Prepare-Source.ps1",
    "verify_source_layout.py",
    "ruff check",
    "extract_i18n.py --check",
    "cmake -S src/native",
    "ctest --test-dir",
):
    assert required_token in ci_text, f"CI workflow missing required step/token: {required_token}"


# 4. Native CMakeLists.txt & CMakePresets.json
cmake_path = APP / "src/native/CMakeLists.txt"
presets_path = APP / "CMakePresets.json"
assert cmake_path.is_file(), "src/native/CMakeLists.txt missing"
assert presets_path.is_file(), "CMakePresets.json missing"
cmake_text = cmake_path.read_text(encoding="utf-8")
for target_name in (
    "vwa_native_output",
    "vwa_limiter",
    "vwa_chorus_pitch",
    "vwa_native_output_test",
    "6B376C7FBB1352BA1E0E432AB66069B335EA9405E45CB6C30B97EF0F3C9C3E0E",
    "FetchContent_Declare",
    "add_test(",
):
    assert target_name in cmake_text, f"CMakeLists.txt missing: {target_name}"
assert "D:\\mingw64" not in cmake_text and "D:/mingw64" not in cmake_text, (
    "CMakeLists.txt must not hardcode machine-specific compiler paths"
)
presets = json.loads(presets_path.read_text(encoding="utf-8"))
assert presets.get("version") == 6
assert any(p.get("name") == "windows-mingw-release" for p in presets.get("configurePresets", []))


# 5. i18n extractor & full catalog integrity
report = extract_i18n.audit_i18n(APP)
assert not report["catalog_errors"], f"i18n catalog errors: {report['catalog_errors']}"
assert report["extracted_python_files"] >= 10
assert report["extracted_qml_files"] >= 15
assert report["missing_counts"]["en"] == 0, f"Missing EN keys: {report['missing_by_file']['en']}"
assert report["missing_counts"]["ja"] == 0, f"Missing JA keys: {report['missing_by_file']['ja']}"

print("PASS: Tier 1 OSS infrastructure (ctypes Win32JobObject, pyproject/CI, CMake, 100% i18n audit)")
