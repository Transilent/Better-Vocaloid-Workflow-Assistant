"""Zero-dependency loopback Web UI for VWA Full-Package Downloader & Auto-Migrator.

Uses ONLY Python's standard library (http.server, secrets, webbrowser) so it runs
reliably on Windows Embeddable Python (which omits tkinter) across all historical
VWA installations (0.3.x / 0.4.x / 1.x / 2.x / 3.x).
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from typing import Callable
import webbrowser

# BRIDGE_IMPORT_START
from full_package_migrator import (
    clear_handoff,
    default_target_directory,
    detect_install_variant,
    installed_version,
    load_network_preferences,
    normalize_source,
    run_migration,
)
# BRIDGE_IMPORT_END


def _browse_folder_native(initial_dir: Path) -> str:
    if os.name != 'nt':
        return ''
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description='选择新版本 VWA 的安装父目录';$d.SelectedPath=$env:VWA_BROWSE_INIT;"
        "if($d.ShowDialog()-eq[System.Windows.Forms.DialogResult]::OK){Write-Output $d.SelectedPath}"
    )
    try:
        res = subprocess.run(
            ['powershell.exe', '-NoProfile', '-Sta', '-Command', script],
            capture_output=True, text=True, timeout=120, env=dict(os.environ, VWA_BROWSE_INIT=str(initial_dir)),
        )
        return (res.stdout or '').strip()
    except (OSError, subprocess.SubprocessError):
        return ''


_HTML_TEMPLATE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>VWA __TARGET__ 全量更新与数据迁移器</title><style>
:root{color-scheme:dark;--bg:#0f1117;--card:#181c26;--line:#2a3142;--fg:#eef1f8;--muted:#98a3b8;--acc:#6e9bff;--ok:#43c58e;--err:#ef6a6a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Microsoft YaHei UI",system-ui,sans-serif;display:flex;justify-content:center;padding:32px 16px}
.card{width:100%;max-width:680px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px 26px;box-shadow:0 18px 48px rgba(0,0,0,.4)}
h1{margin:0 0 6px;font-size:20px}.sub{color:var(--muted);margin:0 0 18px;font-size:13px}
.row{display:flex;align-items:center;gap:10px;margin:10px 0;flex-wrap:wrap}.lbl{width:96px;color:var(--muted);flex-shrink:0}
input[type=text],select{flex:1;min-width:200px;background:#10131b;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:inherit}
label.opt{display:inline-flex;align-items:center;gap:6px;cursor:pointer;margin-right:12px}
.bar{height:10px;background:#10131b;border-radius:999px;overflow:hidden;border:1px solid var(--line);margin:18px 0 8px}
.fill{height:100%;width:0%;background:var(--acc);transition:width .2s}.fill.ok{background:var(--ok)}.fill.err{background:var(--err)}
.meta{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;gap:8px;flex-wrap:wrap}
.msg{margin:8px 0 0;min-height:22px;font-size:13px}.msg.err{color:var(--err)}.msg.ok{color:var(--ok)}
.btns{display:flex;gap:10px;margin-top:20px;flex-wrap:wrap}
button{border:1px solid var(--line);background:#23293a;color:var(--fg);border-radius:8px;padding:8px 16px;font:inherit;cursor:pointer}
button.pri{background:var(--acc);border-color:var(--acc);color:#0b1020;font-weight:600}
button:disabled{opacity:.45;cursor:not-allowed}.sp{flex:1}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#23293a;font-size:12px;color:var(--muted);margin-left:6px}
</style></head><body><div class="card">
<h1>升级到 VWA __TARGET__ <span class="pill">当前 __OLD_VER__</span></h1>
<p class="sub">自动竞速下载官方 SHA-256 校验完整包，无损迁移任务、个人设置、预设库、账号会话与已下载模型；原目录保持不变以便随时回退。</p>
<div class="row"><span class="lbl">安装版本</span>
  <label class="opt"><input type="radio" name="variant" value="full" id="v_full"> Full 完整版（含中文模型）</label>
  <label class="opt"><input type="radio" name="variant" value="jpn" id="v_jpn"> JPN 精简版（不含中文模型）</label></div>
<div class="row"><span class="lbl">下载线路</span>
  <select id="source"><option value="auto">自动竞速（推荐）</option><option value="mirror">优先国内加速源</option>
  <option value="direct">GitHub 官方直连</option><option value="gitproxy">GitProxy 加速</option>
  <option value="gh-proxy">GH-Proxy 加速</option><option value="ghfast">ghfast.top 加速</option></select>
  <span class="pill" id="net_info"></span></div>
<div class="row"><span class="lbl">新安装目录</span>
  <input type="text" id="target_dir"><button id="btn_browse" type="button">浏览…</button></div>
<div class="bar"><div class="fill" id="fill"></div></div>
<div class="meta"><span id="pct">0%</span><span id="speed"></span><span id="route"></span></div>
<div class="msg" id="msg">准备就绪，点击下方按钮开始下载并自动迁移。</div>
<div class="btns">
  <button class="pri" id="btn_start">下载 VWA __TARGET__ 并自动迁移</button>
  <button id="btn_cancel" disabled>取消下载</button><span class="sp"></span>
  <button id="btn_skip">__SKIP_LABEL__</button>
</div></div><script>
const TOKEN="__TOKEN__", INIT=__INIT_JSON__, $=id=>document.getElementById(id);
(INIT.variant==="jpn"?$("v_jpn"):$("v_full")).checked=true;
$("source").value=INIT.source; $("target_dir").value=INIT.target_dir;
$("net_info").textContent=`代理: ${INIT.proxy_mode} · ${INIT.workers} 线程`;
const post=(p,b={})=>fetch(p,{method:"POST",headers:{"Content-Type":"application/json","X-VWA-Token":TOKEN},body:JSON.stringify(b)}).then(r=>r.json());
$("btn_browse").onclick=async()=>{const r=await post("/api/browse");if(r.path)$("target_dir").value=r.path;};
$("btn_start").onclick=()=>post("/api/start",{variant:$("v_jpn").checked?"jpn":"full",source:$("source").value,target_dir:$("target_dir").value});
$("btn_cancel").onclick=()=>post("/api/cancel");
$("btn_skip").onclick=async()=>{await post("/api/skip");$("msg").textContent=INIT.bridge_mode?"迁移器已关闭，下次打开 VWA 仍可继续升级。":"已跳过本次升级，正在启动原版本…";};
const fmt=b=>{if(!b)return"0 B";const u=["B","KB","MB","GB"];let i=0;while(b>=1024&&i<3){b/=1024;i++}return b.toFixed(i>=2?1:0)+" "+u[i];};
async function poll(){try{const s=await(await fetch("/api/status")).json();
  $("fill").style.width=s.percent+"%";$("fill").className="fill"+(s.state==="done"?" ok":s.state==="error"?" err":"");
  $("pct").textContent=s.total>0?`${s.percent}% (${fmt(s.done)} / ${fmt(s.total)})`:`${s.percent}%`;
  $("speed").textContent=s.speed_bps>0?`${fmt(s.speed_bps)}/s`:"";$("route").textContent=s.route?`线路: ${s.route}`:"";
  $("msg").textContent=s.message;$("msg").className="msg"+(s.state==="done"?" ok":s.state==="error"?" err":"");
  const busy=s.state==="running";$("btn_start").disabled=busy||s.state==="done";$("btn_cancel").disabled=!busy;$("btn_skip").disabled=busy;
  if(s.closed)return;}catch(e){} setTimeout(poll,350);}
poll();
</script></body></html>"""


def create_web_server(
    source_root: Path, target_version: str, default_source: str = 'auto', *,
    bridge_mode: bool = False, auto_launch: bool = True,
    reader: Callable[[str, str], dict] | None = None,
    downloader: Callable[[dict, Path, Callable[[int], None]], Path] | None = None,
):
    """Create a loopback-only HTTP server driving the migration workflow (100% stdlib)."""
    old_root = Path(source_root).resolve()
    prefs = load_network_preferences(old_root)
    init_source = normalize_source(default_source if default_source != 'auto' else prefs['source'])
    init_variant = detect_install_variant(old_root)
    init_target = str(default_target_directory(old_root, target_version))
    token = secrets.token_urlsafe(24)

    lock, cancel_flag, done_event = threading.Lock(), threading.Event(), threading.Event()
    state: dict[str, object] = {
        'state': 'idle', 'percent': 0, 'done': 0, 'total': 0, 'speed_bps': 0.0, 'route': '',
        'message': '准备就绪，点击下方按钮开始下载并自动迁移。', 'exit_code': 0, 'closed': False, 'receipt': None,
    }
    speed_ref = {'t': time.monotonic(), 'b': 0}
    client_hb = {'seen': False, 'last': time.monotonic()}

    def _on_progress(done: int, total: int, msg: str) -> None:
        now = time.monotonic()
        with lock:
            dt = now - speed_ref['t']
            if dt >= 0.4 and done >= speed_ref['b']:
                state['speed_bps'] = (done - speed_ref['b']) / dt
                speed_ref['t'], speed_ref['b'] = now, done
            elif done < speed_ref['b']:
                speed_ref['t'], speed_ref['b'], state['speed_bps'] = now, done, 0.0
            state.update(done=int(done), total=int(total),
                         percent=int(min(100, (done * 100) / max(1, total))), message=msg)

    def _worker(variant: str, source: str, target_dir: str) -> None:
        try:
            receipt = run_migration(
                old_root, target_version, Path(target_dir), variant=variant, source=source,
                auto_launch=auto_launch, progress=_on_progress,
                route_cb=lambda lbl: state.__setitem__('route', lbl),
                check_cancel=cancel_flag.is_set, reader=reader, downloader=downloader,
            )
            with lock:
                state.update(
                    state='done', percent=100, speed_bps=0.0, receipt=receipt, exit_code=0,
                    message=f'升级与数据迁移完成！已安装到 {receipt["target_root"]}' + ('（已自动启动新版本）' if receipt.get('launched') else ''),
                )
            threading.Timer(2.0, done_event.set).start()
        except InterruptedError:
            with lock:
                state.update(state='cancelled', speed_bps=0.0, message='已取消；已下载分片保留在缓存中，下次可断点续传。')
        except Exception as exc:
            with lock:
                state.update(state='error', speed_bps=0.0, message=f'升级失败：{exc}')

    init_payload = {
        'variant': init_variant, 'source': init_source, 'target_dir': init_target,
        'proxy_mode': prefs['proxy_mode'], 'workers': prefs['workers'], 'bridge_mode': bridge_mode,
    }
    html_bytes = (
        _HTML_TEMPLATE.replace('__TARGET__', target_version.removeprefix('v'))
        .replace('__OLD_VER__', installed_version(old_root))
        .replace('__SKIP_LABEL__', '关闭迁移器（下次可继续）' if bridge_mode else '暂不升级（启动旧版）')
        .replace('__TOKEN__', token).replace('__INIT_JSON__', json.dumps(init_payload, ensure_ascii=False))
    ).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _fmt, *_args):
            return

        def _send_json(self, code: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
            elif self.path == '/api/status':
                with lock:
                    client_hb['seen'] = True
                    client_hb['last'] = time.monotonic()
                    snap = dict(state)
                self._send_json(200, snap)
            else:
                self.send_error(404)

        def do_POST(self):
            if not secrets.compare_digest(self.headers.get('X-VWA-Token', ''), token):
                self._send_json(403, {'error': 'forbidden'})
                return
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(min(length, 65536)).decode('utf-8') or '{}') if length > 0 else {}
            if self.path == '/api/start':
                with lock:
                    if state['state'] == 'running':
                        self._send_json(409, {'error': 'already running'})
                        return
                    cancel_flag.clear()
                    speed_ref['t'], speed_ref['b'] = time.monotonic(), 0
                    state.update(state='running', percent=0, done=0, total=0, speed_bps=0.0, message='正在准备下载…')
                threading.Thread(
                    target=_worker, daemon=True,
                    args=(str(body.get('variant') or init_variant), str(body.get('source') or init_source),
                          str(body.get('target_dir') or init_target)),
                ).start()
                self._send_json(200, {'ok': True})
            elif self.path == '/api/cancel':
                cancel_flag.set()
                with lock:
                    state['message'] = '正在取消下载…'
                self._send_json(200, {'ok': True})
            elif self.path == '/api/skip':
                if not bridge_mode:
                    clear_handoff(old_root)
                with lock:
                    state.update(exit_code=2 if not bridge_mode else 0, closed=True)
                self._send_json(200, {'ok': True})
                threading.Timer(0.3, done_event.set).start()
            elif self.path == '/api/browse':
                chosen = _browse_folder_native(old_root.parent)
                self._send_json(200, {'path': str(Path(chosen) / f'VWA-{target_version.removeprefix("v")}') if chosen else ''})
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server._vwa_client_hb = client_hb
    server._vwa_lock = lock
    return server, token, state, done_event


def run_gui(
    source_root: Path, target_version: str, default_source: str = 'auto', *,
    bridge_mode: bool = False, open_browser: bool = True, idle_tab_timeout: float = 20.0,
) -> int:
    """Launch the zero-dependency local Web UI migrator (works on Windows Embeddable Python)."""
    server, _token, state, done_event = create_web_server(
        source_root, target_version, default_source, bridge_mode=bridge_mode,
    )
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f'VWA {target_version} 全量更新与迁移器已启动：{url}', flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except OSError:
            pass
    if idle_tab_timeout and idle_tab_timeout > 0:
        def _idle_watchdog() -> None:
            hb = getattr(server, '_vwa_client_hb', None)
            lk = getattr(server, '_vwa_lock', None)
            while not done_event.wait(timeout=1.0):
                if hb is None or lk is None:
                    continue
                with lk:
                    seen = bool(hb.get('seen'))
                    stale = (time.monotonic() - float(hb.get('last', 0.0))) >= idle_tab_timeout
                    busy = state.get('state') == 'running'
                if seen and stale and not busy:
                    done_event.set()
                    return
        threading.Thread(target=_idle_watchdog, daemon=True).start()
    try:
        done_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return int(state.get('exit_code', 0))


def ui_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='VWA Full-Package Downloader & Auto-Migrator Web UI')
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--target-version', required=True)
    parser.add_argument('--source', default='auto')
    parser.add_argument('--bridge-mode', action='store_true')
    args = parser.parse_args(argv)
    return run_gui(args.source_root, args.target_version, args.source, bridge_mode=args.bridge_mode)


if __name__ == '__main__':
    raise SystemExit(ui_main())
