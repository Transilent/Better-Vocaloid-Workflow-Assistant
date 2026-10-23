"""Real empty-state layouts and critical actions at supported logical viewports."""
import os,sys,time,json
from pathlib import Path
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication,QScrollArea
from app import Window
from desktop_theme import apply_theme
application=QApplication([]);window=Window();window.show();application.processEvents()
out=APP/'work/test-results';out.mkdir(exist_ok=True,parents=True)
names={0:'preparation',9:'tuning',8:'video',3:'publication',2:'tasks',1:'components',4:'settings'}
measurements=[]
def settle():
    for _ in range(4):application.processEvents()
def inside(control):
    assert control.isVisible(),control.objectName() or control.text()
    assert window.rect().contains(control.mapTo(window,control.rect().topLeft()))
    assert window.rect().contains(control.mapTo(window,control.rect().bottomRight())),(control.text(),window.size(),control.geometry())
for mode,material in ((m,s) for m in ('light','dark') for s in ('glass','solid')):
    for scale in (100,115,130):
        window._vwa_appearance.update(ui_scale=scale,material=material)
        apply_theme(window,mode)
        for size in ((1600,1000),(1440,900),(1280,800),(1000,680),(1920,1040),(2512,1352)):
            window.resize(*size);settle()
            assert (window.width(),window.height())==size,(size,window.size())
            for index,name in names.items():
                started=time.perf_counter();window.navigate(index);settle()
                page=window.pages.currentWidget()
                for area in page.findChildren(QScrollArea):
                    if area.isVisible():
                        assert area.horizontalScrollBar().maximum()==0,(name,mode,scale,size,'horizontal overflow',area.widget().minimumSizeHint())
                if index==0:
                    inside(window.start)
                    if mode=='light' and material=='glass' and scale==100 and size==(1600,1000):
                        assert window.grab().save(str(out/'vwa-preparation-initial-check.png'))
                        for important in (window.manual_bpm,window.include_instrumental):
                            inside(important)
                            point=important.mapToGlobal(important.rect().bottomRight())
                            viewport=window.step_area.parentWidget()
                            assert viewport.rect().contains(viewport.mapFromGlobal(point)),(important.text(),'below initial preparation viewport')
                elif index==9:inside(window.tuning.apply_button)
                elif index==8:inside(window.video_mixdown.start)
                elif index==3:
                    window.publishing.select_step(3);settle();inside(window.publishing.prepare_button);inside(window.publishing.upload_button)
                assert not window.testAttribute(Qt.WA_TranslucentBackground)
                assert not window.windowFlags()&Qt.FramelessWindowHint
                measurements.append({'page':name,'theme':mode,'material':material,'scale':scale,'size':size,
                    'sidebar_width':window.sidebar_scroll.width(),
                    'vertical_scroll_ranges':[area.verticalScrollBar().maximum() for area in page.findChildren(QScrollArea) if area.isVisible()],
                    'navigation_ms':round((time.perf_counter()-started)*1000,2)})
                if mode=='light' and material=='glass' and scale==100:
                    assert window.grab().save(str(out/f'vwa-{name}-{size[0]}.png'))
window.close();settle()
(out/'frontend-layout-measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS: seven real pages, six normal/wide viewports, two themes/materials, 100/115/130% UI scale, no page horizontal overflow, reachable actions; offscreen only')
