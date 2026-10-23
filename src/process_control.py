"""Stop only the process tree created by an owned Popen instance on Windows."""
import ctypes
import os
import subprocess
import time


def application_lock(root):
    """Hold one desktop owner per installation; dead owners are recoverable."""
    from tools.update_apply import application_lock as acquire
    return acquire(root)


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
