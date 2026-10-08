import sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=root
sys.path[:0]=[str(app),str(app/'vendor')]
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
from playwright.sync_api import sync_playwright
from publish_browser import browser_engine_options,confirm_cover_dialog,upload_finished,cover_dialog_open,fill_existing,candidates
settings=json.loads((app/'publish-selectors.json').read_text(encoding='utf-8'))['xiaohongshu']
with sync_playwright() as p:
    options=browser_engine_options(True)
    browser=p.chromium.launch(**options)
    try:
        page=browser.new_page()
        page.set_content('''<div class="cover-editor"><div class="bcc-dialog">封面制作
          <div onclick="window.wrong=true">完成</div></div></div>
          <div class="videoup-confirm-modal"><div class="bcc-dialog">
          是否将「4:3封面」的改动同步到「16:9封面」？
          <div onclick="window.synced=true;this.closest('.videoup-confirm-modal').remove()">确认同步</div>
          </div></div>''')
        assert confirm_cover_dialog(page)
        assert page.evaluate('window.synced && !window.wrong')
        assert cover_dialog_open(page)
        page.set_content('<div class="videoup-confirm-modal"><div class="bcc-dialog">确认发布：4:3封面与16:9封面同步<div onclick="window.wrong=true">确认同步</div></div></div>')
        assert not confirm_cover_dialog(page) and not page.evaluate('window.wrong || false')
        page.set_content('''<div class="cover-editor"><div class="cover-editor-head">封面制作</div>
          <div class="editor_4_3"></div><div class="editor_16_9"></div>
          <div class="cover-editor-button"><button>发布</button><button onclick="window.wrong=true">完成</button></div></div>''')
        try:confirm_cover_dialog(page)
        except Exception:pass
        else:raise AssertionError('Publish footer mistaken for cover-only footer')
        assert not page.evaluate('window.wrong || false')
        page.set_content('<div class="cover-container"><div class="uploading">上传中 98%</div></div>')
        assert not upload_finished(page,settings)
        page.set_content('<div class="cover-container"><div class="preview-new">检测为高清视频</div></div>')
        assert upload_finished(page,settings)
        page.locator('.preview-new').evaluate("e=>e.style.display='none'")
        assert not upload_finished(page,settings)
        page.set_content('<div class="cover-container"><div class="fail">上传失败</div></div>')
        assert not upload_finished(page,settings)
        page.set_content('''<input placeholder="填写标题会有更多赞哦"><div class="tiptap" contenteditable="true"></div>
          <input type="file" accept="image/jpeg" aria-label="上传封面图片"><div class="cover-preview"></div>
          <div class="upload-status">上传完成</div><script>
          window.selected=0;document.querySelector('input[type=file]').onchange=function(){
            window.selected++;
            let image=new Image();image.src=URL.createObjectURL(this.files[0]);
            document.querySelector('.cover-preview').append(image);
            let modal=document.createElement('div');modal.className='cover-modal';
            modal.innerHTML='封面裁剪<button onclick="window.confirmed=true;this.parentElement.remove()">确定</button>';
            document.body.append(modal);
          };</script>''')
        from tests.support import ensure_fixture
        bundle = ensure_fixture(app)
        result=fill_existing(page,settings,{'fields':{'title':'封面验证','description':'保留 STAFF'}},bundle/'cover.jpg',lambda *a:None,timeout=2)
        assert result['state']=='waiting_review' and page.evaluate('window.confirmed')
        assert not cover_dialog_open(page)
        again=fill_existing(page,settings,{'fields':{'title':'继续填写','description':'保留 STAFF'}},bundle/'cover.jpg',lambda *a:None,timeout=2,details=result['checks'])
        assert again['state']=='waiting_review' and page.evaluate('window.selected')==1
        assert page.locator('input[placeholder]').input_value()=='继续填写'
        # A previous successful cover must not satisfy a later replacement
        # when the page no longer exposes an image upload control.
        page.locator('input[type=file]').evaluate('e=>e.remove()')
        previous=dict(again['checks'],cover_sha256='previous-cover')
        failed=fill_existing(page,settings,{'fields':{'title':'替换封面','description':'保留 STAFF'}},bundle/'cover.jpg',lambda *a:None,timeout=2,details=previous)
        assert failed['state']=='needs_manual' and not failed['checks']['cover_filled']
        page.set_content('<div style="display:none"><input type="file" accept="image/jpeg"></div><label>上传封面<input type="file" accept="image/jpeg" style="display:none"></label>')
        found=candidates(page,['input[type=file]'],visible=False,container_visible=True)
        assert found and found.evaluate("e=>e.parentElement.tagName")=="LABEL"
    finally:browser.close()
print('PASS: 双比例封面确认只作用于封面；小红书实际 Preview 分支、上传中、失败和隐藏预览分别验证。')
