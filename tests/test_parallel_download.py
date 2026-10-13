"""Real concurrent Range requests, cancellation/resume and corrupt-range rejection."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
import parallel_download as downloader

payload=bytes(range(256))*32768
active=0
peak=0
lock=threading.Lock()
bad=False

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        global active,peak
        if self.path=='/missing':
            self.send_error(503)
            return
        match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
        if not match:
            self.send_response(200)
            self.end_headers()
            return
        start,end=map(int,match.groups())
        self.send_response(206)
        self.send_header('Content-Range',f'bytes {start+1 if bad and end>0 else start}-{end}/{len(payload)}')
        self.send_header('ETag','"unchanged"')
        self.send_header('Content-Length',str(end-start+1))
        self.end_headers()
        with lock:
            active+=1
            peak=max(peak,active)
        try:
            for position in range(start,end+1,65536):
                time.sleep(.003)
                block=payload[position:min(position+65536,end+1)]
                self.wfile.write(bytes(b^1 for b in block) if self.path=='/corrupt' else block)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass
        finally:
            with lock: active-=1

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True)
thread.start()
url=f'http://127.0.0.1:{server.server_port}'
item={'name':'asset.bin','bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'url':url+'/asset'}
try:
    with tempfile.TemporaryDirectory(dir=APP/'work',prefix='ranges-') as temp:
        folder=Path(temp)
        result=downloader.download(item,folder/'complete',lambda *args:None,workers=4,segment_bytes=1024**2,source='direct')
        assert result.read_bytes()==payload and peak>=2, 'Connections must overlap'
        assert not (result.parent/'asset.bin.segments').exists()
        stop=threading.Event()
        def progress(done,*args):
            if done>=1024**2: stop.set()
        try:
            downloader.download(item,folder/'resume',progress,stop.is_set,workers=3,segment_bytes=1024**2,source='direct')
            raise AssertionError('Cancellation was ignored')
        except InterruptedError: pass
        assert not (folder/'resume/asset.bin').exists()
        assert list((folder/'resume/asset.bin.segments').glob('*.part'))
        parts=list((folder/'resume/asset.bin.segments').glob('*.part'))
        assert len(parts)==1 and parts[0].name=='data.part' and parts[0].stat().st_size==len(payload)
        stop.clear()
        result=downloader.download(item,folder/'resume',lambda *args:None,stop.is_set,workers=4,segment_bytes=1024**2,source='direct')
        assert result.read_bytes()==payload
        bad=True
        try:
            downloader.download(item,folder/'invalid',lambda *args:None,workers=2,segment_bytes=1024**2,source='direct')
            raise AssertionError('Invalid Content-Range accepted')
        except downloader.RangeUnsupported: pass
        assert not (folder/'invalid/asset.bin').exists()
        bad=False
        with patch.object(downloader,'download_urls',return_value=[url+'/missing',url+'/asset']):
            result=downloader.download(item,folder/'fallback',lambda *args:None,workers=2,segment_bytes=1024**2)
            assert result.read_bytes()==payload
        with patch.object(downloader,'download_urls',return_value=[url+'/corrupt',url+'/asset']):
            result=downloader.download(item,folder/'bad-mirror',lambda *args:None,workers=2,segment_bytes=1024**2)
            assert result.read_bytes()==payload, 'A corrupt mirror must not stop a verified alternate route'
        # Migrate a historical part-based download while retaining its prefix.
        legacy=folder/'legacy/asset.bin.segments';legacy.mkdir(parents=True)
        (legacy/'segments.json').write_text(json.dumps({'bytes':len(payload),'sha256':item['sha256'],'segment_bytes':1024**2}))
        (legacy/'000000.part').write_bytes(payload[:1024**2])
        result=downloader.download(item,folder/'legacy',lambda *args:None,workers=2,segment_bytes=1024**2,source='direct')
        assert result.read_bytes()==payload
        try:
            downloader.download(dict(item,sha256='0'*64),folder/'hash',lambda *args:None,workers=2,segment_bytes=1024**2,source='direct')
            raise AssertionError('Invalid SHA-256 accepted')
        except ValueError: pass
        assert not (folder/'hash/asset.bin').exists()
finally:
    server.shutdown()
    server.server_close()
print('PASS: real parallel connections, resume, cancellation, alternate route, Content-Range and SHA-256 guards')
