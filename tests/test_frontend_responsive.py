"""Isolated resize round trips and visible, truthful preparation progress."""
import json,os,sys
from pathlib import Path
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtWidgets import QApplication,QScrollArea
from app import Window
from desktop_theme import apply_theme
application=QApplication([]);window=Window();window.show()
def settle():
    for _ in range(6):application.processEvents()
settle()
assert not window.isMaximized()
assert window.progress.isVisible() and window.progress.value()==0
assert window.progress_detail.isVisible()
window.set_running(True);settle();assert window.progress.isVisible()
window.set_running(False);settle();assert window.progress.isVisible()
idle=(window.progress.maximum(),window.progress.value(),window.progress_detail.text())

# The installed reader is represented by a UI fixture, not an inference run.
fixture={'stages':['download','separate','midi'],'completed':1,'current':'separate',
         'status':{'state':'running','steps':{'download':{'state':'done'}}},
         'event':{'detail':'隔离测试阶段'},'elapsed':12}
window.job=APP/'work/progress-fixture'
with patch('progress_state.snapshot',return_value=fixture):
    window.refresh_progress();settle()
    assert window.progress.maximum()==3 and window.progress.value()==1
    assert '1/3' in window.progress_detail.text()
    for state in ('done','cancelled','interrupted','failed'):
        fixture['status']['state']=state
        fixture['completed']=3 if state=='done' else 1
        window.refresh_progress();settle()
        assert window.progress.isVisible()
        assert window.progress.value()==fixture['completed']
window.job=None
window.progress.setRange(0,idle[0]);window.progress.setValue(idle[1]);window.progress_detail.setText(idle[2])

out=APP/'work/test-results';out.mkdir(parents=True,exist_ok=True)
metrics=[]
for mode in ('light','dark'):
    apply_theme(window,mode)
    for size in ((1440,900),(1920,1040),(2512,1352),(1440,900)):
        window.resize(*size);settle()
        for index,name in ((0,'preparation'),(9,'tuning')):
            window.navigate(index);settle()
            page=window.pages.currentWidget()
            assert (window.width(),window.height())==size
            areas=[area for area in page.findChildren(QScrollArea) if area.isVisible()]
            assert all(area.horizontalScrollBar().maximum()==0 for area in areas)
            action=window.start if index==0 else window.tuning.apply_button
            assert action.isVisible() and window.rect().contains(action.mapTo(window,action.rect().bottomRight()))
            metrics.append({'page':name,'theme':mode,'size':size,'sidebar_width':window.sidebar_scroll.width(),
                'character_width':window._vwa_backdrop._character_rect().width(),
                'vertical_scroll_ranges':[area.verticalScrollBar().maximum() for area in areas]})
            if name=='preparation' and size==(2512,1352):
                cards=[window.step_cards[key] for key in ('source','reference','output')]
                points=[card.mapTo(window,card.rect().topLeft()) for card in cards]
                locations=[point.x() for point in points]
                assert locations==sorted(set(locations)),('source, lyrics and output should be left-to-right',locations)
                assert cards[1].width()>cards[0].width() and cards[1].width()>cards[2].width()
                assert window.input_mode._vwa_choice.height()<140, 'Source choices must not stretch into a tall slab'
                metrics[-1]['card_positions']={key:[point.x(),point.y(),card.width(),card.height()] for key,card,point in zip(('source','lyrics','output'),cards,points)}
                viewport=window.step_area._scroll.viewport()
                for control in (window.manual_bpm,window.include_instrumental,window.subtitle_button):
                    assert viewport.rect().contains(control.mapTo(viewport,control.rect().bottomRight())),(control.text(),'below wide initial viewport')
            if name=='tuning':
                assert window.tuning.progress.isVisible() and window.tuning.progress.isEnabled()
                assert window.tuning.task_input_button.isVisible() and window.tuning.local_input_button.isVisible()
                assert not window.tuning.project.isVisible() and not window.tuning.jobs.isVisible()
                if size==(2512,1352):assert areas[0].verticalScrollBar().maximum()==0,'Wide empty-state tuning should fit'
            assert window.grab().save(str(out/f'responsive-{name}-{mode}-{size[0]}.png'))
    samples=[row for row in metrics if row['theme']==mode and row['page']=='preparation']
    assert samples[2]['sidebar_width']>samples[0]['sidebar_width']*1.2
    assert samples[2]['character_width']>samples[0]['character_width']*1.4
    assert samples[0]['sidebar_width']==samples[3]['sidebar_width']
    assert samples[0]['character_width']==samples[3]['character_width']
window.close();settle()
(out/'responsive-measurements.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('PASS: ordinary window, growing navigation/artwork, wide-to-normal round trips, visible idle/running/terminal preparation progress, no fake phase completion')
