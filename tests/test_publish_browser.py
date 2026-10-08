import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = root
sys.path[:0] = [str(app), str(app / "vendor")]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
from publication import load_bundle
from publish_browser import fill_upload, browser_engine_options, safe_click, ManualStep, confirm_cover_dialog, fill_exact

html = r'''<!doctype html><html lang="zh"><meta charset="utf-8"><body>
<button type="button" onclick="window.unsafe=(window.unsafe||0)+1">确定</button>
<label>上传视频<input id="video" type="file" accept="video/mp4"></label>
<div id="form" style="display:none">
<input placeholder="填写标题会有更多赞哦" maxlength="80">
<div class="desc-container"><div class="tiptap" contenteditable="true" style="min-height:50px;border:1px solid;white-space:pre-wrap"></div></div>
<div class="upload-status">等待上传</div>
<input id="cover" type="file" accept="image/jpeg" aria-label="上传封面图片">
<div class="cover-preview"></div>
<button id="publish" type="button" onclick="window.published=(window.published||0)+1">发布</button>
</div>
<script>
window.published=0; window.unsafe=0; window.selected=0;
const mode=new URL(location.href).searchParams.get('mode');
if(mode==='ambiguous') {
  const clone=document.querySelector('#video').cloneNode(true); clone.id='other'; document.body.append(clone);
}
if(mode==='missing-cover') document.querySelector('#cover').remove();
document.querySelector('#video').onchange=async function(){
  window.selected++;
  document.querySelector('#form').style.display='block';
  document.querySelector('.upload-status').textContent='上传中';
  const form=new FormData(); form.append('video',this.files[0]);
  await fetch('/upload', {method:'POST',body:form});
  if(mode!=='stuck') document.querySelector('.upload-status').textContent='上传完成';
};
if(document.querySelector('#cover')) document.querySelector('#cover').onchange=async function(){
  const file=this.files[0];
  const form=new FormData(); form.append('cover',file);
  await fetch('/upload',{method:'POST',body:form});
  const dialog=document.createElement('div'); dialog.setAttribute('role','dialog');
  dialog.innerHTML='<p>设置封面：确认裁剪</p><button type="button">确定</button>';
  dialog.querySelector('button').onclick=function(){
    const image=new Image(); image.src=URL.createObjectURL(file);
    document.querySelector('.cover-preview').replaceChildren(image); dialog.remove();
    window.cropConfirmed=true;
  };
  document.body.append(dialog);
};
</script></body></html>'''
posts = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path.startswith('/bili') and not self.path.startswith('/bili-form'):
            body=b'<html><meta charset="utf-8"><iframe src="/bili-form" style="width:1100px;height:800px"></iframe></html>'
        else:
            body=html.encode('utf-8')
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
        self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        size=int(self.headers['Content-Length']); left=size
        while left:
            block=self.rfile.read(min(left,65536)); left-=len(block)
        posts.append(size)
        self.send_response(200); self.send_header('Content-Type','application/json')
        self.end_headers(); self.wfile.write(b'{}')

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
from tests.support import ensure_fixture
bundle = ensure_fixture(app)
data=load_bundle(bundle,'bilibili'); fields=data['content']['bilibili']
all_settings=json.loads((app / 'publish-selectors.json').read_text(encoding='utf-8'))
settings=all_settings['xiaohongshu']
results={'test_environment':'local DOM fixtures with real browser and HTTP uploads',
         'live_sites':'This test never connects to creator platforms.',
         'external_uploads_in_this_test':0}
try:
    with sync_playwright() as p:
        options=browser_engine_options(headless=True)
        browser=p.chromium.launch(**options)
        try:
            for name,route in [('xiaohongshu','/xhs'),('bilibili_iframe','/bili')]:
                page=browser.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}{route}')
                updates=[]
                selected_settings=all_settings['bilibili' if name=='bilibili_iframe' else 'xiaohongshu']
                report=fill_upload(page,selected_settings,{**data,'fields':fields},bundle / 'cover.jpg',
                                   lambda *args:updates.append(args),timeout=5)
                assert report['state']=='waiting_review',report
                frame=page.frames[-1]
                assert frame.locator('input[placeholder]').input_value()==fields['title']
                lines=lambda text:[line.rstrip() for line in text.splitlines() if line.strip()]
                assert lines(frame.locator('.tiptap').inner_text())==lines(fields['description'])
                assert frame.evaluate('window.published')==0 and frame.evaluate('window.unsafe')==0
                assert frame.evaluate('window.selected')==1 and frame.evaluate('window.cropConfirmed')
                try: safe_click(frame.locator('#publish'))
                except ManualStep: pass
                else: raise AssertionError('Publish button allowed')
                results[name]=report
                page.close()
            page=browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/xhs?mode=ambiguous')
            try: fill_upload(page,settings,{**data,'fields':fields},bundle/'cover.jpg',lambda *a:None,timeout=1)
            except ManualStep: pass
            else: raise AssertionError('Ambiguous video input accepted')
            assert page.evaluate('window.selected')==0
            results['ambiguous_input_rejected']=True; page.close()
            page=browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/xhs?mode=stuck')
            report=fill_upload(page,settings,{**data,'fields':fields},bundle/'cover.jpg',lambda *a:None,timeout=1)
            assert report['state']=='needs_manual' and not report['checks']['upload_complete']
            assert page.evaluate('window.published')==0
            results['unconfirmed_upload_not_reported_as_done']=True; page.close()
            page=browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/xhs?mode=missing-cover')
            report=fill_upload(page,settings,{**data,'fields':fields},bundle/'cover.jpg',lambda *a:None,timeout=1)
            assert report['state']=='needs_manual' and not report['checks']['cover_filled']
            assert report['checks']['title_filled'] and report['checks']['description_filled']
            assert page.evaluate('window.published')==0
            results['missing_cover_partial_result_retained']=True; page.close()
            page=browser.new_page()
            page.set_content('<div role="dialog">确认发布？已选择封面<button onclick="window.published=1">确定</button></div>')
            assert confirm_cover_dialog(page) is False
            try: safe_click(page.locator('button'),cover_confirmation=True)
            except ManualStep: pass
            else: raise AssertionError('Publish confirmation mistaken for cover dialog')
            assert page.evaluate('window.published || 0')==0
            results['publish_confirmation_with_cover_text_rejected']=True
            page.set_content('<input maxlength="3">')
            try: fill_exact(page.locator('input'),'文字不会截断')
            except ManualStep: pass
            else: raise AssertionError('Live field length limit ignored')
            assert page.locator('input').input_value()==''
            results['live_maxlength_checked_before_writing']=True
            page.goto(f'http://127.0.0.1:{server.server_port}/xhs')
            def cancel(): raise InterruptedError('test cancellation')
            try: fill_upload(page,settings,{**data,'fields':fields},bundle/'cover.jpg',lambda *a:None,check=cancel,timeout=1)
            except InterruptedError: pass
            else: raise AssertionError('Cancellation ignored')
            assert page.evaluate('window.selected')==0 and page.evaluate('window.published')==0
            results['cancellation_before_upload']=True
            page.close()
        finally: browser.close()
finally:
    server.shutdown(); server.server_close()
assert len(posts)==7 and max(posts)>Path(data['video']['path']).stat().st_size,posts
results['actual_local_http_uploads']={'requests':len(posts),'largest_request_bytes':max(posts)}
(app / 'work/发布验证报告.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
