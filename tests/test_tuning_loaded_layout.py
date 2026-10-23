"""Imported two-track projects must survive resize and choice-state changes."""
import json,os,sys
from pathlib import Path
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtWidgets import QApplication
from app import Window
from common import atomic_json
from desktop_theme import apply_theme
application=QApplication([]);window=Window();window.show();window.navigate(9);page=window.tuning
def settle():
    for _ in range(10):application.processEvents()
Q=705600000
data={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
 'tracks':[{'name':name,'groups':[],'mainRef':{'blickOffset':0},'mainGroup':{'uuid':name,
   'notes':[{'onset':index*Q,'duration':Q,'pitch':60,'lyrics':'la'} for index in range(count)],'parameters':{}}}
   for name,count in (('Lead Vocal',208),('Backing Vocal',140))]}
source=APP/'work/imported-two-track.svp';atomic_json(source,data);raw=source.read_bytes()
assert page.load_project(source);settle()
out=APP/'work/test-results';out.mkdir(exist_ok=True,parents=True);measurements=[]
for theme in ('light','dark'):
    for scale in (100,115,130):
        window._vwa_appearance['ui_scale']=scale;apply_theme(window,theme);settle()
        for size in ((1440,900),(1920,1040),(2512,1352),(1440,900)):
            window.resize(*size);settle()
            for mode in ('preserve','reference'):
                page.mode.setCurrentIndex(page.mode.findData(mode))
                for style in ('none','power_pop'):
                    page.performance_style.setCurrentIndex(page.performance_style.findData(style));settle()
                    for group in page._choice_views:
                        if not group.isVisibleTo(page.form):continue
                        frame=group.parentWidget()
                        while frame is not None and frame.objectName()!='card':frame=frame.parentWidget()
                        assert frame is not None
                        for radio in group.buttons:
                            if not (group.rect().contains(radio.geometry().topLeft()) and group.rect().contains(radio.geometry().bottomRight())):
                                window.grab().save(str(out/'loaded-clipping-failure.png'))
                                raise AssertionError(('choice clipped',theme,scale,size,radio.text(),group.geometry(),group.minimumHeight(),group.minimumSizeHint(),radio.geometry(),frame.geometry(),frame.minimumSizeHint()))
                            assert frame.rect().contains(radio.mapTo(frame,radio.rect().bottomRight())),('card clipped',theme,scale,size,radio.text(),frame.size())
                    for row in page.rows:
                        check=row['check'];assert '\n' in check.text()
                        assert check.height()>=check.fontMetrics().height()*2,('track text clipped',theme,scale,size,check.text(),check.height(),check.fontMetrics().height(),page.tracks.rowHeight(page.rows.index(row)))
                    if mode=='preserve':
                        assert page.tracks.columnWidth(0)>=page.tracks.viewport().width()-3, 'The only visible column must use the available table width'
                    measurements.append({'theme':theme,'scale':scale,'size':size,'mode':mode,'style':style})
            if scale==100:
                page.mode.setCurrentIndex(page.mode.findData('preserve'));page.performance_style.setCurrentIndex(page.performance_style.findData('none'));settle()
                assert window.grab().save(str(out/f'loaded-tuning-{theme}-{size[0]}.png'))
assert source.read_bytes()==raw
(out/'loaded-layout.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
window.close();settle()
print('PASS: imported 208/140-note tracks, 96 loaded resize/source/style/scale/theme cases, complete track text and no clipped choice/card contents')
