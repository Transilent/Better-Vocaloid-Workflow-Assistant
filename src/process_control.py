"""Stop only the process tree created by an owned Popen instance on Windows."""
import ctypes
import os
import subprocess
import time


def terminate_tree(process):
    if process.poll() is not None: return
    if os.name != 'nt':
        process.terminate(); return
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
    def created(handle):
        values=[ctypes.c_ulonglong() for _ in range(4)]
        return values[0].value if kernel.GetProcessTimes(ctypes.c_void_p(handle),*[ctypes.byref(v) for v in values]) else 0
    root_created=created(int(process._handle))
    owned={process.pid:root_created};handles=[]
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
                stamp=created(handle)
                if not stamp or not owned[parent] or stamp<owned[parent]:
                    kernel.CloseHandle(ctypes.c_void_p(handle));continue
                owned[pid]=stamp;handles.append(handle);added=True
            if not added:break
        if not parents:
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
