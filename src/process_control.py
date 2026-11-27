"""Stop only the process tree created by an owned Popen instance on Windows,
plus zero-dependency ctypes Win32 Job Object lifecycle management, shared job
lock, and cancellable subprocess runner."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import subprocess
import time

from common import ROOT, SidecarBusy, cancellation_requested, cancelled, exclusive_sidecar, native_path

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_KERNEL32 = None


def _job_kernel32():
    global _JOB_KERNEL32
    if _JOB_KERNEL32 is None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        _JOB_KERNEL32 = k32
    return _JOB_KERNEL32


class Win32JobObject:
    """Zero-dependency Windows Job Object with KILL_ON_JOB_CLOSE.

    Ensures child processes and all of their descendants are automatically
    terminated by the Windows kernel if the parent exits, crashes, or closes
    the job handle — without requiring pywin32.
    """

    def __init__(self, *, kill_on_close: bool = True, name: str | None = None):
        self._handle = None
        self._assigned = False
        if os.name != "nt":
            return
        try:
            k32 = _job_kernel32()
            handle = k32.CreateJobObjectW(None, name)
            if not handle:
                return
            if kill_on_close:
                info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                ok = k32.SetInformationJobObject(
                    handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                )
                if not ok:
                    k32.CloseHandle(handle)
                    return
            self._handle = handle
        except OSError:
            self._handle = None

    @property
    def active(self) -> bool:
        return bool(self._handle)

    @property
    def assigned(self) -> bool:
        return bool(self._handle and self._assigned)

    def assign(self, process_or_handle) -> bool:
        """Assign a subprocess.Popen or raw Win32 process handle to this job."""
        if not self._handle or os.name != "nt":
            return False
        raw_handle = getattr(process_or_handle, "_handle", process_or_handle)
        if not raw_handle:
            return False
        try:
            ok = bool(_job_kernel32().AssignProcessToJobObject(self._handle, int(raw_handle)))
            if ok:
                self._assigned = True
            return ok
        except (OSError, TypeError, ValueError):
            return False

    def terminate(self, exit_code: int = 1) -> bool:
        """Atomically terminate every process assigned to this job object."""
        if not self._handle or os.name != "nt":
            return False
        try:
            return bool(_job_kernel32().TerminateJobObject(self._handle, int(exit_code)))
        except OSError:
            return False

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        self._assigned = False
        if handle and os.name == "nt":
            try:
                _job_kernel32().CloseHandle(handle)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def terminate_tree(process):
    if os.name != 'nt':
        if process.poll() is None:process.terminate()
        return
    # A wrapper may have exited while its children are still running. The
    # retained Popen handle identifies the original process even after exit.
    process.poll()
    kernel=ctypes.windll.kernel32
    class ProcessEntry(ctypes.Structure):
        _fields_=[('size',ctypes.c_ulong),('usage',ctypes.c_ulong),('pid',ctypes.c_ulong),
                  ('heap',ctypes.c_size_t),('module',ctypes.c_ulong),('threads',ctypes.c_ulong),
                  ('parent',ctypes.c_ulong),('priority',ctypes.c_long),('flags',ctypes.c_ulong),
                  ('name',ctypes.c_wchar*260)]
    kernel.CreateToolhelp32Snapshot.restype=ctypes.c_void_p
    kernel.OpenProcess.restype=ctypes.c_void_p
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    parents={}
    if snapshot and snapshot!=ctypes.c_void_p(-1).value:
        try:
            entry=ProcessEntry();entry.size=ctypes.sizeof(entry)
            more=kernel.Process32FirstW(ctypes.c_void_p(snapshot),ctypes.byref(entry))
            while more:
                parents[entry.pid]=entry.parent
                more=kernel.Process32NextW(ctypes.c_void_p(snapshot),ctypes.byref(entry))
        finally:kernel.CloseHandle(ctypes.c_void_p(snapshot))
    def lifetime(handle):
        values=[ctypes.c_ulonglong() for _ in range(4)]
        if kernel.GetProcessTimes(ctypes.c_void_p(handle),*[ctypes.byref(v) for v in values]):
            return values[0].value,values[1].value
        return 0,0
    owned={process.pid:lifetime(int(process._handle))};owner_handles={process.pid:int(process._handle)};handles=[]
    try:
        # Open handles while ancestors are still alive. Creation times exclude
        # stale/recycled parent identifiers; held handles cannot change identity.
        remaining=dict(parents)
        for _ in range(len(parents)+1):
            added=False
            for pid,parent in list(remaining.items()):
                if parent not in owned or pid in owned:continue
                handle=kernel.OpenProcess(0x1001,False,pid)
                remaining.pop(pid)
                if not handle:continue
                stamp,exited=lifetime(handle)
                # Refresh the original ancestor handle after opening the
                # candidate: it may have exited since the snapshot was taken.
                parent_created,parent_exited=lifetime(owner_handles[parent])
                if (not stamp or not parent_created or stamp<parent_created
                        or (parent_exited and stamp>parent_exited)):
                    kernel.CloseHandle(ctypes.c_void_p(handle));continue
                owned[pid]=(stamp,exited);owner_handles[pid]=handle;handles.append(handle);added=True
            if not added:break
        if not parents and process.poll() is None:
            # Retain the OS utility as a secondary route when enumeration is unavailable.
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
        if process.poll() is None:process.terminate()
        for handle in handles:kernel.TerminateProcess(ctypes.c_void_p(handle),1)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            active=[]
            for handle in handles:
                code=ctypes.c_ulong()
                if kernel.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code)) and code.value==259:active.append(handle)
            if not active:break
            time.sleep(.02)
    finally:
        for handle in handles:kernel.CloseHandle(ctypes.c_void_p(handle))


@contextmanager
def job_lock(job):
    try:
        with exclusive_sidecar(Path(job) / "run.lock"):
            yield
    except SidecarBusy:
        raise RuntimeError("这个任务已经在运行，请等待它完成或在原窗口取消。") from None


def run_process(argv, job, label, cooperative=False, *, on_progress_line=None):
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    temp = Path(job) / "temp"
    temp.mkdir(exist_ok=True)
    env.update(TMP=str(temp), TEMP=str(temp))
    cache = ROOT / "cache/numba"
    cache.mkdir(parents=True, exist_ok=True)
    env["NUMBA_CACHE_DIR"] = native_path(cache)
    output = Path(job) / f"{label}.log"
    process = None
    with Win32JobObject(kill_on_close=True) as job_obj:
        try:
            with output.open("wb") as writer, output.open("rb") as reader:
                process = subprocess.Popen(argv, stdout=writer, stderr=subprocess.STDOUT, env=env,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                job_obj.assign(process)
                pending = b""
                active_voice = {'midi_lead': '主唱', 'midi_backing': '和声'}.get(label, '人声')
                stop_started = None
                while True:
                    more = reader.read(65536)
                    pending += more
                    if b"\n" in pending or b"\r" in pending:
                        lines = re.split(br"[\r\n]", pending)
                        pending = lines.pop()
                        for line in lines:
                            text = line.decode("utf-8", errors="replace").strip()
                            if text:
                                if label.startswith('midi'):
                                    if text.startswith(('[VWA Voice] ', '[BVWA Voice] ')):
                                        active_voice = {'lead': '主唱', 'backing': '和声'}.get(text.rsplit(' ', 1)[-1], '人声')
                                    from progress_state import from_log
                                    from_log(job, text, active_voice)
                                if not (on_progress_line is not None and on_progress_line(text)):
                                    print(text, flush=True)
                    code = process.poll()
                    if code is not None:
                        if more:
                            continue
                        if pending:
                            text = pending.decode("utf-8", errors="replace").strip()
                            if text and not (on_progress_line is not None and on_progress_line(text)):
                                print(text, flush=True)
                        break
                    if cancellation_requested(job):
                        stop_started = stop_started or time.monotonic()
                        if not cooperative or time.monotonic() - stop_started > 30:
                            if job_obj.assigned:
                                job_obj.terminate(1)
                            else:
                                terminate_tree(process)
                            process.wait(timeout=10)
                            cancelled(job)
                    time.sleep(0.2)
                cancelled(job)
                if code:
                    raise RuntimeError(f"{label} 失败（退出码 {code}）；详情见 {output.name}。")
        finally:
            if process is not None and process.poll() is None:
                if job_obj.assigned:
                    job_obj.terminate(1)
                else:
                    terminate_tree(process)
                process.wait(timeout=10)
