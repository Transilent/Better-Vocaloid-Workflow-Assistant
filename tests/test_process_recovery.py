"""Cancellation must stop a spawned recognizer even without a nested Windows job."""
import ctypes
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from pipeline import run_process
from common import atomic_json
from process_control import application_lock,terminate_tree

def alive(pid):
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.restype=ctypes.c_void_p
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    code=ctypes.c_ulong()
    try:
        assert kernel.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code))
        return code.value==259
    finally:kernel.CloseHandle(ctypes.c_void_p(handle))

with tempfile.TemporaryDirectory(dir=APP/'work',prefix='process-cancel-') as temp:
    job=Path(temp)
    owner = application_lock(job)
    assert owner is not None and application_lock(job) is None
    owner.unlock()
    recovered = application_lock(job)
    assert recovered is not None
    recovered.unlock()
    child = ('import os,sys;sys.path.insert(0,sys.argv[1]);'
             'from process_control import application_lock;'
             'guard=application_lock(sys.argv[2]);assert guard is not None;os._exit(0)')
    subprocess.run([sys.executable, '-B', '-c', child, str(APP), str(job)], check=True, timeout=20)
    assert (job/'cache/BVWA.lock').exists(), 'The abrupt-exit fixture must leave a stale owner record'
    recovered = application_lock(job)
    assert recovered is not None, 'A crashed owner must not permanently block startup'
    recovered.unlock()
    script=job/'spawn.py'
    script.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"])\n'
        'Path(sys.argv[1]).write_text(str(child.pid))\n'
        'print("recognizer started",flush=True)\ntime.sleep(120)\n',encoding='utf-8')
    flag=job/'child-pid.txt'
    def stop():
        for _ in range(1000):
            if flag.is_file():
                (job/'cancel.flag').touch();return
            time.sleep(.01)
    thread=threading.Thread(target=stop);thread.start()
    with patch.dict(sys.modules,{'win32job':None}):
        try:
            run_process([sys.executable,'-B','-u',str(script),str(flag)],job,'cancel-fixture')
            raise AssertionError('Cancellation ignored')
        except InterruptedError:pass
    thread.join()
    pid=int(flag.read_text())
    for _ in range(100):
        if not alive(pid):break
        time.sleep(.01)
    assert not alive(pid),'Recognizing child survived cancellation'
    # Concurrent JSON writers must produce one complete document, without temp-name collisions.
    errors=[]
    def write(index):
        try:
            for sequence in range(10):atomic_json(job/'concurrent.json',{'writer':index,'sequence':sequence,'payload':'x'*1000})
        except Exception as error:errors.append(error)
    threads=[threading.Thread(target=write,args=(i,)) for i in range(6)]
    for item in threads:item.start()
    for item in threads:item.join()
    assert not errors,errors
    assert len(json.loads((job/'concurrent.json').read_text())['payload'])==1000
    assert not list(job.glob('*.tmp'))
    (job/'cancel.flag').unlink()
    # A real child emits tqdm-style carriage returns while still computing.
    # Observe intermediate counters before it exits, rather than only at EOF.
    progress_script=job/'live_progress.py'
    progress_script.write_text('import sys,time\n'
        'print("--- Stage 1/3: Running mora ASR for Japanese lyric mode ---",flush=True)\n'
        'for done in (1,2,3):\n'
        ' sys.stdout.write(f"Processing: {done*100//3}%|bar| {done}/3 [00:01]\\r");sys.stdout.flush();time.sleep(.6)\n',
        encoding='utf-8')
    observed=set()
    stopped=threading.Event()
    def observe():
        while not stopped.wait(.02):
            try:
                current=json.loads((job/'progress.json').read_text(encoding='utf-8'))
                if current.get('total')==3:observed.add(current['done'])
            except (OSError,ValueError):pass
    watcher=threading.Thread(target=observe);watcher.start()
    try:
        run_process([sys.executable,'-B','-u',str(progress_script)],job,'midi-live-progress')
    finally:
        stopped.set();watcher.join()
    assert {1,2}<=observed,('Intermediate progress was buffered until exit',observed)
    current=json.loads((job/'progress.json').read_text(encoding='utf-8'))
    assert current['done']==3 and current['total']==3
    from error_recovery import failure
    atomic_json(job/'status.json',{'state':'failed','current_step':'exports',
        'error':'exports 失败（退出码 1）；详情见 exports.log。'})
    (job/'exports.log').write_text('libresvip.core.exceptions.NotesOverlappedError: Notes overlapped near bar 39\n',encoding='utf-8')
    recovery=failure(job)
    assert recovery['title']=='工程含有重叠音符' and recovery['action']=='workflow'
    assert 'NotesOverlappedError' in recovery['raw']
    # A successfully selected provider is not the cause of an unrelated failure.
    atomic_json(job/'status.json',{'state':'failed','current_step':'midi',
        'error':'midi_voices 失败（退出码 1）；详情见 midi_voices.log。'})
    (job/'midi_voices.log').write_text(
        '[GAME ONNX] Using DirectML adapter 0: GPU\n'
        "GAME loaded: ['DmlExecutionProvider', 'CPUExecutionProvider']\n"
        'ValueError: invalid alignment dimensions\n',encoding='utf-8')
    recovery=failure(job)
    assert recovery['action']=='resume' and 'ValueError: invalid alignment dimensions' in recovery['raw']
    # Localized ONNX device errors can be written as UTF-16 then wrapped in
    # UnicodeDecodeError. Keep the native reset code in the user-facing cause.
    (job/'midi_voices.log').write_bytes(
        'Non-zero status code: DmlFusedNode 887A0007 GPU reset\n'.encode('utf-16-le')
        + b"UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb2 in position 370: invalid start byte\n")
    recovery=failure(job)
    assert recovery['title']=='显卡推理被中断' and recovery['action']=='cpu'
    assert '0x887A0007' in recovery['raw'] and '显卡设备被重置' in recovery['raw']
    (job/'midi_voices.log').write_text('ModuleNotFoundError: No module named cuda_utils\n',encoding='utf-8')
    assert failure(job)['action']=='components'
    (job/'midi_voices.log').write_text("UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb2\n",encoding='utf-8')
    assert failure(job)['action']=='resume', 'An encoding failure alone does not establish a GPU error'
    from common import preserve_changed_file,file_hash
    edited=job/'edited.mid'
    edited.write_bytes(b'generated MIDI')
    recorded=file_hash(edited)
    assert preserve_changed_file(job,edited,recorded) is None
    edited.write_bytes(b'user-edited MIDI')
    saved=preserve_changed_file(job,edited,recorded)
    assert edited.read_bytes()==(job/saved).read_bytes()==b'user-edited MIDI'
    try:
        preserve_changed_file(job,job.parent/'outside.mid')
        raise AssertionError('Output backup escaped its task')
    except ValueError:pass
with tempfile.TemporaryDirectory(dir=APP/'work',prefix='exited-wrapper-') as temp:
    root=Path(temp);flag=root/'owned-child.pid';script=root/'wrapper.py'
    script.write_text('import subprocess,sys\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(120)"],'
        'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
        'Path(sys.argv[1]).write_text(str(child.pid))\n',encoding='utf-8')
    wrapper=subprocess.Popen([sys.executable,'-B',str(script),str(flag)],
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    wrapper.wait(timeout=15);pid=int(flag.read_text());assert alive(pid)
    kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=ctypes.c_void_p
    child_handle=kernel.OpenProcess(0x1001,False,pid)
    unrelated=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(120)'],
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        terminate_tree(wrapper)
        for _ in range(100):
            if not alive(pid):break
            time.sleep(.01)
        assert not alive(pid),'Exited wrapper left its owned child running'
        assert unrelated.poll() is None,'Unrelated process was terminated'
        terminate_tree(wrapper)  # Repeating cleanup must remain harmless.
    finally:
        if child_handle:
            kernel.TerminateProcess(ctypes.c_void_p(child_handle),1)
            kernel.CloseHandle(ctypes.c_void_p(child_handle))
        unrelated.terminate();unrelated.wait(timeout=10)
print('PASS: recognizer tree cancellation, exited-wrapper cleanup, unrelated process protection, JSON commits and progress')
