"""Exercise the runtime installer without network or large model fixtures."""
import hashlib
import http.server
import json
import os
import subprocess
import socket
import tempfile
import threading
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SCRIPT = APP / 'tools/Install-Runtime.ps1'
shell = Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'

def run(app, archive=None, release_api=None, proxy_wrapper=None, proxy=None):
    if proxy_wrapper:
        args = [str(shell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(proxy_wrapper),
                str(SCRIPT), str(app), release_api, proxy]
        return subprocess.run(args, capture_output=True, timeout=60)
    args = [str(shell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
            '-AppRoot', str(app)]
    args += ['-ReleaseApiUri', release_api] if release_api else ['-Offline']
    if archive is not None:
        args += ['-Archive', str(archive)]
    return subprocess.run(args, capture_output=True, timeout=60)

with tempfile.TemporaryDirectory(prefix='bootstrap-', dir='\\\\?\\' + str(APP / 'work')) as folder:
    root = Path(folder)
    app = root / '中文目录 with spaces'
    (app / 'dependencies').mkdir(parents=True)
    (app / 'config.json').write_bytes(b'KEEP USER SETTINGS')
    (app / 'jobs').mkdir()
    (app / 'jobs/user-work.txt').write_bytes(b'KEEP USER WORK')
    files = {'dependencies/runtime.txt': b'runtime', 'models/model.txt': b'model',
             'vendor/automation.txt': b'web runtime',
             'dependencies/' + 'long-folder/'*14 + 'long-file.dll': b'long-path runtime'}
    entries = [{'file': n, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
               for n, data in files.items()]
    manifest = app / 'dependencies/manifest.json'
    manifest.write_text(json.dumps({'entries': entries}), encoding='utf-8')
    archive = root / 'portable.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for n, data in files.items():
            z.writestr('BVWA/' + n, data)
        z.writestr('BVWA/config.json', b'MUST NOT OVERWRITE')
        z.writestr('BVWA/cache/session.json', b'MUST NOT IMPORT')
    missing = run(app)
    assert missing.returncode == 1 and b'No portable ZIP found' in missing.stdout, (missing.stdout + missing.stderr).decode(errors='replace')
    result = run(app, archive)
    assert result.returncode == 0, result.stdout.decode(errors='replace')
    assert all((app / n).read_bytes() == data for n, data in files.items())
    assert (app / 'config.json').read_bytes() == b'KEEP USER SETTINGS'
    assert (app / 'jobs/user-work.txt').read_bytes() == b'KEEP USER WORK'
    assert not (app / 'cache/session.json').exists()
    assert not (app / 'cache/runtime-install-in-progress').exists()
    (app / 'dependencies/runtime.txt').write_bytes(b'bad')
    assert run(app, archive).returncode == 0
    assert (app / 'dependencies/runtime.txt').read_bytes() == b'runtime'
    for invalid in ('../outside.txt', 'dependencies/../../outside.txt', 'dependencies/../config.json', 'dependencies/..\\config.json'):
        manifest.write_text(json.dumps({'entries': [dict(entries[0], file=invalid)]}), encoding='utf-8')
        assert run(app, archive).returncode == 1
    assert (app / 'config.json').read_bytes() == b'KEEP USER SETTINGS'
    manifest.write_text(json.dumps({'entries': [dict(entries[0], sha256='0'*64)]}), encoding='utf-8')
    result = run(app, archive)
    assert result.returncode == 1 and b'Runtime checksum mismatch' in result.stdout
    assert (app / 'dependencies/runtime.txt').read_bytes() == b'runtime'
    assert (app / 'cache/runtime-install-in-progress').exists()
    manifest.write_text(json.dumps({'entries': entries}), encoding='utf-8')
    assert run(app, archive).returncode == 0
    assert not (app / 'cache/runtime-install-in-progress').exists()
    download_app = root / 'downloaded runtime'
    (download_app / 'dependencies').mkdir(parents=True)
    (download_app / 'dependencies/manifest.json').write_bytes(manifest.read_bytes())
    (download_app / 'cache/runtime-install').mkdir(parents=True)
    (download_app / 'cache/runtime-install/BVWA-Windows-x64.zip').write_bytes(b'interrupted cached download')
    archive_bytes = archive.read_bytes()
    midpoint = len(archive_bytes) // 2
    pieces = {'BVWA-Windows-x64.zip.001': archive_bytes[:midpoint],
              'BVWA-Windows-x64.zip.002': archive_bytes[midpoint:]}
    routes = {}
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path not in routes:
                self.send_error(404)
                return
            body = routes[self.path]
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if self.path in {'/release', '/inventory'} else 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    # localhost. resolves locally but is not treated as an automatic proxy bypass
    # by .NET, so the refused default proxy is exercised by the control request.
    base = 'http://localhost.:' + str(server.server_port)
    parts = [{'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
             for name, data in pieces.items()]
    routes['/inventory'] = json.dumps({'parts': parts, 'archive': {
        'bytes': len(archive_bytes), 'sha256': hashlib.sha256(archive_bytes).hexdigest()}}).encode()
    for name, data in pieces.items():
        routes['/' + name] = data
    routes['/release'] = json.dumps({'assets': [
        {'name': 'release-assets.json', 'browser_download_url': base + '/inventory'},
        *[{'name': name, 'browser_download_url': base + '/' + name} for name in pieces]]}).encode()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wrapper = root / 'refused-proxy.ps1'
        wrapper.write_text('''Add-Type @'
using System;
using System.Net;
public class RefusedProxy : IWebProxy {
    private readonly Uri destination;
    public RefusedProxy(string value) { destination = new Uri(value); }
    public Uri GetProxy(Uri value) { return destination; }
    public bool IsBypassed(Uri value) { return false; }
    public ICredentials Credentials { get; set; }
}
'@
[Net.WebRequest]::DefaultWebProxy = [RefusedProxy]::new($args[3])
$request = [Net.WebRequest]::CreateHttp([Uri]$args[2])
$request.Timeout = 1000
$refused = $false
try { $request.GetResponse().Dispose() } catch [Net.WebException] { $refused = $true }
if (!$refused) { throw 'Fixture failed to route through the refused default proxy.' }
& $args[0] -AppRoot $args[1] -ReleaseApiUri $args[2]
''', encoding='utf-8-sig')
        with socket.socket() as stopped_proxy:
            stopped_proxy.bind(('127.0.0.1', 0))
            proxy = 'http://127.0.0.1:' + str(stopped_proxy.getsockname()[1])
            result = run(download_app, release_api=base + '/release', proxy_wrapper=wrapper, proxy=proxy)
        assert result.returncode == 0, (result.stdout + result.stderr).decode(errors='replace')
        assert all((download_app / n).read_bytes() == data for n, data in files.items())
        assert (download_app / 'cache/runtime-install/BVWA-Windows-x64.zip').read_bytes() == archive_bytes
        assert all('/' + name in requests for name in pieces)
        assert (download_app / 'cache/runtime-install/BVWA-Windows-x64.zip.sha256').read_text() == hashlib.sha256(archive_bytes).hexdigest()
        # An update-only latest Release points fresh source installations to
        # the previous full package without publishing another multi-GB ZIP.
        routes['/releases/latest'] = json.dumps({'assets': [
            {'name':'release-assets.json','browser_download_url':base+'/update-only'}]}).encode()
        routes['/update-only'] = json.dumps({'version':'0.4.1','portable_release':'v0.4.0'}).encode()
        routes['/releases/tags/v0.4.0'] = routes['/release']
        update_app=root/'update-only release runtime'
        (update_app/'dependencies').mkdir(parents=True)
        (update_app/'dependencies/manifest.json').write_bytes(manifest.read_bytes())
        result=run(update_app,release_api=base+'/releases/latest')
        assert result.returncode==0,(result.stdout+result.stderr).decode(errors='replace')
        assert '/releases/tags/v0.4.0' in requests
        assert all((update_app/n).read_bytes()==data for n,data in files.items())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
print('PASS: offline installer, Unicode/spaces, integrity, retry, boundary and user-data preservation')
print('PASS: Release discovery, part downloads, SHA-256 verification, join and installation despite a refused system proxy')
