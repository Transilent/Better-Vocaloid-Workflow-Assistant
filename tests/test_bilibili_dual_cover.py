import json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=root
sys.path[:0]=[str(app),str(app/'vendor')]
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
from playwright.sync_api import sync_playwright
from publish_browser import upload_bilibili_covers,browser_engine_options,reset_bilibili_cover_zoom
from publication import load_bundle
from tests.support import ensure_fixture
bundle = ensure_fixture(app)
variants=load_bundle(bundle,'bilibili')['platform_covers']['bilibili']
settings=json.loads((app/'publish-selectors.json').read_text(encoding='utf-8'))['bilibili']
html='''<style>.cover-editor [role=slider]{width:100px;height:16px;background:#ccc}.cover-img{width:100px;height:75px}
.editor_4_3,.editor_16_9{position:relative;width:420px;height:236px}</style>
<input id="video" type="file" accept="video/mp4"><div class="cover-main"><div class="cover-img"></div></div>
<div class="cover-editor"><div class="bcc-dialog"><div class="cover-editor-head">封面制作</div>
  <div class="cover-editor-panel-canvas">
  <div class="active" data-ratio="4_3"><div class="cover-editor-panel-canvas-title"><span class="text">首页推荐封面（4:3）</span></div>
    <label class="sync-checkbox"><input type="checkbox" checked>双比例同步改动</label>
    <div class="scale-slider-4-3"><div role="slider" tabindex="0" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0.4"></div></div>
    <div class="editor_4_3"><canvas class="lower-canvas" width="420" height="236"></canvas><div class="crop-box-fixed" style="position:absolute;left:52.5px;top:0;width:315px;height:236px"></div></div></div>
  <div class="inactive" data-ratio="16_9"><div class="cover-editor-panel-canvas-title"><span class="text">个人空间封面（16:9）</span></div>
    <label class="sync-checkbox"><input type="checkbox" checked>双比例同步改动</label>
    <div class="scale-slider-16-9"><div role="slider" tabindex="0" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0.4"></div></div>
    <div class="editor_16_9"><canvas class="lower-canvas" width="420" height="236"></canvas></div></div>
  </div><input id="cover" type="file" accept="image/jpeg"><div>零成本做视频，你一定不知道的投稿黑科技</div>
  <div class="cover-editor-button"><button>取消</button><button id="done">完成</button></div>
</div></div><div role="slider" id="timeline" aria-valuenow="42" tabindex="0"></div><button id="publish">发布</button>
<script>
window.videoCount=window.published=window.selected=0;
document.querySelector('#video').onchange=()=>window.videoCount++;
document.querySelector('#publish').onclick=()=>window.published++;
const pane=()=>document.querySelector('.cover-editor-panel-canvas > .active');
function draw(p){if(!p.bitmap)return;const c=p.querySelector('canvas'),z=Number(p.querySelector('[role=slider]').getAttribute('aria-valuenow'));
 const ctx=c.getContext('2d');ctx.fillStyle='black';ctx.fillRect(0,0,c.width,c.height);
 const w=p.dataset.ratio==='4_3'?315:c.width,h=c.height,x=(c.width-w)/2;
 ctx.drawImage(p.bitmap,x-w*z/2,-h*z/2,w*(1+z),h*(1+z));}
document.querySelectorAll('.text').forEach(t=>t.onclick=()=>{
 document.querySelectorAll('[data-ratio]').forEach(p=>p.className='inactive');t.closest('[data-ratio]').className='active';});
document.querySelectorAll('.cover-editor [role=slider]').forEach(s=>s.onkeydown=e=>{
 if(e.key==='Home'){s.setAttribute('aria-valuenow','0');draw(s.closest('[data-ratio]'));}});
document.querySelector('#cover').onchange=async function(){window.selected++;const p=pane();
 p.bitmap=await createImageBitmap(this.files[0]);p.selectedName=this.files[0].name;draw(p);};
document.querySelector('#done').onclick=()=>{window.saved={};document.querySelectorAll('[data-ratio]').forEach(p=>{
 window.saved[p.dataset.ratio]={name:p.selectedName,sync:p.querySelector('input').checked,zoom:p.querySelector('[role=slider]').getAttribute('aria-valuenow')};});
 const c=document.querySelector('.editor_4_3 canvas');document.querySelector('.cover-img').style.backgroundImage='url('+c.toDataURL()+')';
 document.querySelector('.cover-editor').remove();};
</script>'''
with sync_playwright() as p:
    options=browser_engine_options(True)
    browser=p.chromium.launch(**options)
    try:
        page=browser.new_page();page.set_content(html)
        try:
            result=upload_bilibili_covers(page,settings,bundle,variants)
        except Exception:
            import base64
            state=page.evaluate("()=>({selected:window.selected,panes:Array.from(document.querySelectorAll('[data-ratio]')).map(p=>({ratio:p.dataset.ratio,class:p.className,file:p.selectedName,bitmap:p.bitmap&&[p.bitmap.width,p.bitmap.height],zoom:p.querySelector('[role=slider]').getAttribute('aria-valuenow')}))})")
            print(json.dumps(state,ensure_ascii=False),flush=True)
            raw=page.locator('.editor_4_3 canvas').evaluate("e=>e.toDataURL()")
            (root/'work/dual-cover-fixture-debug.png').write_bytes(base64.b64decode(raw.split(',')[1]))
            raise
        saved=page.evaluate('window.saved')
        assert saved['4_3']['name']==variants['4:3']['file']
        assert saved['16_9']['name']==variants['16:9']['file']
        assert all(not s['sync'] and s['zoom']=='0' for s in saved.values())
        assert page.evaluate('window.selected')==2 and page.evaluate('window.videoCount')==0
        assert page.evaluate('window.published')==0 and page.locator('#timeline').get_attribute('aria-valuenow')=='42'
        assert len(result)==2 and all(v['pixel_rmse']<=6 for v in result.values())
        # A fresh creator upload can have two empty ratio panes and no canvas.
        empty=browser.new_page();empty.set_content(html)
        empty.evaluate("""() => {
          for(const p of document.querySelectorAll('[data-ratio]')){
            const ratio=p.dataset.ratio;
            p.savedEditor=p.querySelector('.editor_'+ratio);p.savedEditor.remove();
            const box=document.createElement('div');box.className='cover-editor-panel-canvas-empty ratio_'+ratio;
            const input=document.createElement('input');input.type='file';input.accept='image/jpeg';input.style.display='none';
            box.append(input);p.append(box);
            input.onchange=async()=>{window.selected++;p.bitmap=await createImageBitmap(input.files[0]);p.selectedName=input.files[0].name;
              box.replaceWith(p.savedEditor);document.querySelectorAll('[data-ratio]').forEach(x=>x.className='inactive');p.className='active';draw(p);};
          }
        }""")
        empty_result=upload_bilibili_covers(empty,settings,bundle,variants)
        assert empty.evaluate('window.selected')==2 and empty.evaluate('window.published')==0
        assert len(empty_result)==2 and all(v['pixel_rmse']<=6 for v in empty_result.values())
        report={'dual_ratio_upload':True,'fresh_empty_ratio_panes':True,'sync_disabled':True,'zoom_reset':True,'no_video_reupload':True,'no_publish':True,
                'preview_recommendation_text_does_not_block_cover_save':True,'canvas_validation':result}
        (app/'work/双比例封面验证报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:browser.close()
