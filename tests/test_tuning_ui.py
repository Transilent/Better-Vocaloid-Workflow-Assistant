"""Offscreen independent source, preparation separation and real session flow."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt,QPoint,QPointF
from PyQt5.QtGui import QWheelEvent
from app import Window
from common import atomic_json

application=QApplication([])
window=Window();window.show();application.processEvents();page=window.tuning
assert window.pages.count()==10
nav=window.nav_buttons[0].parentWidget().layout()
order=[nav.itemAt(i).widget().text() for i in range(nav.count())
       if nav.itemAt(i).widget() is not None and nav.itemAt(i).widget() in window.nav_buttons]
assert order[:4]==['准备工作流','调音工作流','合成视频','发布']
window.navigate(9);assert window.pages.currentWidget() is page
window.navigate(5);assert window.pages.currentIndex()==0
window.navigate(6);assert window.pages.currentIndex()==4
window.navigate(9)

page.performance_style.setCurrentIndex(page.performance_style.findData('power_pop'))
page.style_pitch.setChecked(True)
page.reference_tension.setChecked(True)
page.calibration_enabled.setChecked(True);page.calibration_file.setText('not-configured.json')
window.midi_enabled.setChecked(False);window.exports_enabled.setChecked(False)
assert page.performance_style.isEnabled() and page.expression_density.isEnabled()
assert page.style_pitch.isEnabled() and page.calibration_file.isEnabled()
prep=window.options()
assert prep['prepare_only'] and prep['performance_style']=='none' and not prep['style_pitch']
assert prep['expression_density']=='original' and prep['reference_calibration'] is None
assert not any(prep['reference_'+name] for name in ('dynamics','tension','breathiness','voicing'))
window.workflow.choose_preset('内置 · 日语双轨')
assert page.performance_style.currentData()=='power_pop' and page.style_pitch.isChecked()
for control in (page.performance_style,page.style_strength,page.expression_density):
    before=control.currentData();control.setFocus()
    event=QWheelEvent(QPointF(5,5),QPointF(control.mapToGlobal(QPoint(5,5))),QPoint(),QPoint(0,120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
    QApplication.sendEvent(control,event);application.processEvents();assert control.currentData()==before

Q=705600000
data={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
      'tracks':[{'name':'Lead','mainRef':{'blickOffset':0,'database':{'name':'Test Bank'}},'groups':[],
          'mainGroup':{'uuid':'lead','notes':[{'onset':0,'duration':2*Q,'pitch':60,'lyrics':'la'}],
              'parameters':{'loudness':{'mode':'linear','points':[0,0.,2*Q,-1.]},
                            'unknownVendor':{'points':[0,17],'mode':'custom'}}}}]}

def wait():
    deadline=time.monotonic()+15
    while page.busy() and time.monotonic()<deadline:
        application.processEvents();time.sleep(.01)
    assert page.worker.wait(2000),'Tuning worker did not finish'
    application.processEvents();application.processEvents()
    assert page.result,page.status.text()

with tempfile.TemporaryDirectory(prefix='tuning-ui-',dir=APP/'work') as temp:
    root=Path(temp);source=root/'input.svp';atomic_json(source,data)
    raw=source.read_bytes();before=hashlib.sha256(raw).hexdigest()
    job=root/'job';job.mkdir();atomic_json(job/'request.json',{'language':'zh'})
    page.destination.setText(str(root/'sessions'))
    assert page.load_project(source,job=job)
    assert window.job is None and page.job==job
    page.mode.setCurrentIndex(page.mode.findData('preserve'))
    page.performance_style.setCurrentIndex(page.performance_style.findData('none'))
    page.style_pitch.setChecked(False);page.expression_density.setCurrentIndex(page.expression_density.findData('original'))
    assert page.options()['reference_sources']==[] and page.options()['reference_calibration'] is None
    page.start_run()
    worker_running = page.busy()
    assert worker_running, {
        'status': page.status.text(), 'run_outcome': page._run_outcome,
        'worker': type(page.worker).__name__ if page.worker is not None else None}
    assert page.progress.maximum()==100 and page.progress.value()<page.progress.maximum(), ('The running visual bar must stay below completion',page.status.text(),page._run_outcome,worker_running)
    assert not page.progress.isTextVisible() and page.progress.format()==''
    progress_range = (page.progress.minimum(), page.progress.maximum(), page.progress.value())
    progress_heights = (page.progress.minimumHeight(), page.progress.maximumHeight(), page.progress.height(), page.progress.sizeHint().height())
    timer_active = page._progress_timer.isActive()
    assert progress_range[:2] == (0, 100) and progress_heights[:2] == (12, 12) and timer_active, {
        'range': progress_range, 'heights[min,max,actual,hint]': progress_heights,
        'timer_active': timer_active, 'busy': page.busy(), 'run_outcome': page._run_outcome,
        'status': page.status.text(), 'progress_detail': page.progress_detail.text()}
    wait();first=page.result
    assert first['receipt_verified'] is True and page.progress.maximum()==100
    assert page.progress.value()==page.progress.maximum(), 'Only a verified applied/unchanged result with a complete report reaches 100'
    assert not first['cached'] and first['project_commit_count']==1
    assert Path(first['output']).read_bytes()==raw and source.read_bytes()==raw
    assert not (job/'task.json').exists(),'Generation must not auto-register the result'
    session=page.session;page.start_run();wait()
    assert page.result['cached'] and page.result['project_commit_count']==0
    page.performance_style.setCurrentIndex(page.performance_style.findData('power_pop'))
    page.style_pitch.setChecked(True);page.start_run();wait()
    assert page.result['output']!=first['output'] and Path(first['output']).read_bytes()==raw
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert page.result['report']['final_density_passes']==1
    page.register();assert (job/'task.json').is_file()
    # Recovery is independent of the preparation task and source availability.
    source.rename(root/'moved-source.svp')
    assert page.restore_session(session)
    page.start_run();wait();assert page.result['cached']
    # Reimporting the same path deliberately resets the baseline session.
    source.write_bytes(raw)
    assert page.load_project(source) and page.session is None and page.job is None
    assert window.job is None
    multi=json.loads(json.dumps(data));second=json.loads(json.dumps(data['tracks'][0]))
    second['name']='Backing';second['mainGroup']['uuid']='backing';second['mainGroup']['notes'][0]['pitch']=65
    multi['tracks'].append(second);multi_path=root/'multi.svp';atomic_json(multi_path,multi)
    assert page.load_project(multi_path)
    page.performance_style.setCurrentIndex(page.performance_style.findData('none'));page.style_pitch.setChecked(False)
    page.mode.setCurrentIndex(page.mode.findData('retained'));page.retained_project.setText(str(multi_path))
    page.style_scope.setCurrentIndex(page.style_scope.findData('all_vocals'))
    selected=page.options()['retained_sources']
    assert [(row['track_index'],row['source_track_index']) for row in selected]==[(0,0),(1,1)]
    page.start_run();wait()
    assert len(page.result['report']['primary']['tracks'])==2
    assert all(row['result']['match_policy']=='exact' for row in page.result['report']['primary']['tracks'])
    window.grab().save(str(APP/'work/test-results/tuning-ui.png'))

window.close();application.processEvents()
print('PASS: 10 pages, requested navigation, independent options, source snapshots, actual QThread processing, cache/no stacking, explicit registration, recovery and wheel protection')
