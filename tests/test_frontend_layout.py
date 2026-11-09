"""Real empty-state layouts and critical actions at supported logical viewports."""
import os,sys,time,json
from pathlib import Path
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication,QScrollArea
from app import Window
from audio_mixdown_ui import AudioMixdownDialog
from desktop_theme import apply_theme
application=QApplication([]);window=Window();window.show();application.processEvents()
out=APP/'work/test-results';out.mkdir(exist_ok=True,parents=True)
names={0:'preparation',9:'tuning',10:'audio-mixdown',8:'video',3:'publication',2:'tasks',1:'components',4:'settings'}
assert window.nav_buttons[10].parentWidget().layout().indexOf(window.nav_buttons[10]) < window.nav_buttons[8].parentWidget().layout().indexOf(window.nav_buttons[8])
assert window.audio_mixdown.mixer.job is None
measurements=[]
def settle():
    for _ in range(4):application.processEvents()
def wait_until(predicate,label,timeout=12):
    deadline=time.monotonic()+timeout
    while not predicate():
        assert time.monotonic()<deadline,label
        application.processEvents();time.sleep(.01)
def inside(control):
    assert control.isVisible(),control.objectName() or control.text()
    assert window.rect().contains(control.mapTo(window,control.rect().topLeft()))
    assert window.rect().contains(control.mapTo(window,control.rect().bottomRight())),(control.text(),window.size(),control.geometry())
def rack_rects(console):
    rects=[]
    for strip in console.active_strips():
        origin=strip.mapTo(console,QPoint(0,0))
        rects.append(QRect(origin,strip.size()))
    return rects
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
                elif index==10:
                    inside(window.audio_mixdown.choose_task)
                    mixer=window.audio_mixdown.mixer
                    workspace=mixer.workspace
                    assert workspace.column_count() in (1,2)
                    if workspace.width()<980:assert workspace.column_count()==1
                    assert len(mixer.mixer_console.active_strips())==2
                    assert mixer.mixer_console.column_count()==2
                    rects=rack_rects(mixer.mixer_console)
                    assert len(rects)==2
                    assert max(rect.top() for rect in rects)-min(rect.top() for rect in rects)<=1
                    gap=rects[1].left()-rects[0].right()-1
                    assert 6<=gap<=16,gap  # Theme scaling changes the logical gap.
                    left=min(rect.left() for rect in rects)
                    right=mixer.mixer_console.width()-max(rect.right()+1 for rect in rects)
                    assert abs(left-right)<=3,(left,right)
                    assert mixer.preview_card.parentWidget() is not workspace,'Transport stays outside the scrolling editor'
                    assert mixer.player_position.width()>=mixer.width()-90
                    if workspace.column_count()==2:
                        assert workspace._mixer_scroll.geometry().top()==mixer.processing_card.geometry().top()
                        assert workspace._mixer_scroll.geometry().left()<mixer.processing_card.geometry().left()
                    else:
                        assert mixer.processing_card.geometry().top()>workspace._mixer_scroll.geometry().top()
                elif index==8:
                    inside(window.video_mixdown.start)
                    if scale==100 and size==(1600,1000):
                        inside(window.video_mixdown.alignment_analyze)
                elif index==3:
                    window.publishing.select_step(3);settle();inside(window.publishing.prepare_button);inside(window.publishing.upload_button)
                assert not window.testAttribute(Qt.WA_TranslucentBackground)
                assert not window.windowFlags()&Qt.FramelessWindowHint
                measurements.append({'page':name,'theme':mode,'material':material,'scale':scale,'size':size,
                    'sidebar_width':window.sidebar_scroll.width(),
                    'vertical_scroll_ranges':[area.verticalScrollBar().maximum() for area in page.findChildren(QScrollArea) if area.isVisible()],
                    'mix_workspace_columns':window.audio_mixdown.mixer.workspace.column_count(),
                    'mix_rack_columns':window.audio_mixdown.mixer.mixer_console.column_count(),
                    'navigation_ms':round((time.perf_counter()-started)*1000,2)})
                if mode=='light' and material=='glass' and scale==100:
                    assert window.grab().save(str(out/f'vwa-{name}-{size[0]}.png'))
def ready_mixer(mixer):
    worker=getattr(mixer,'_support_worker',None)
    if worker is not None:
        worker.cancel()
        deadline=time.monotonic()+3
        while mixer._support_worker is not None and time.monotonic()<deadline:
            settle();time.sleep(.01)
    mixer._apply_component_support(True,'layout-test')
    mixer.support_panel.hide()
    settle()

window._vwa_appearance.update(ui_scale=100,material='glass');apply_theme(window,'light')
for size in ((1920,1080),(2512,1352)):
    window.resize(*size);window.navigate(10);settle()
    mixer=window.audio_mixdown.mixer;ready_mixer(mixer)
    area=mixer._main_scroll
    assert area.horizontalScrollBar().maximum()==0,(size,'horizontal')
    assert area.verticalScrollBar().maximum()==0,(size,'vertical',area.verticalScrollBar().maximum(),mixer.workspace.height())
    assert mixer.workspace.column_count()==2
    assert window.audio_mixdown.mixer._intro.isHidden()
    assert window.audio_mixdown.mixer._steps.isHidden()
    assert window.audio_mixdown.mixer.export_note.isHidden()

for width,height in ((640,900),(920,1000)):
    dialog=AudioMixdownDialog(None,None)
    dialog.resize(width,height);dialog.show();ready_mixer(dialog)
    dialog._set_vocal_track_count(3,announce=False);settle()
    area=dialog.findChild(QScrollArea)
    assert dialog.width()==width,(width,dialog.width())
    assert area.horizontalScrollBar().maximum()==0,(width,'horizontal overflow')
    assert area.verticalScrollBar().maximum()>0,(width,'must remain scrollable')
    assert len(dialog.mixer_console.active_strips())==4
    if width<=640:
        assert dialog.mixer_console.column_count()==2
    dialog.close()
    wait_until(lambda: not dialog.isVisible() and dialog._audition_cleanup_complete and
               (dialog._audition_close_thread is None or not dialog._audition_close_thread.is_alive()),('dialog close timeout',width))
    dialog.deleteLater();settle()
window.close()
wait_until(lambda: not window.isVisible(),'window close timeout')
settle()
(out/'frontend-layout-measurements.json').write_text(json.dumps(measurements,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS: eight real pages, six normal/wide viewports, two themes/materials, 100/115/130% UI scale, zero full-screen mixdown overflow with ready components, scroll-only narrow dialogs, no page horizontal overflow; offscreen only')
