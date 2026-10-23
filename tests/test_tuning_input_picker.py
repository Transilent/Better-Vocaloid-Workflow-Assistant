"""Use the real task-library records for a two-source tuning input picker."""
import json,os,sys,traceback
from pathlib import Path
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication,QPushButton
from app import Window
from common import atomic_json
import task_store
application=QApplication([]);window=Window();window.show();window.navigate(9)
for _ in range(6):application.processEvents()
assert window.results.stop_scan()
page=window.tuning
visible=[control.text() for control in page.input_card.findChildren(QPushButton) if control.isVisibleTo(page.input_card)]
assert visible==['选择任务','本地工程'],visible
assert not page.project.isVisible() and not page.jobs.isVisible() and not page.destination.isVisible()
Q=705600000
data={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[{'index':0,'numerator':4,'denominator':4}]},
 'tracks':[{'name':name,'groups':[],'mainRef':{'blickOffset':0},'mainGroup':{'uuid':name,
   'notes':[{'onset':0,'duration':Q,'pitch':60,'lyrics':'la'}],'parameters':{}}} for name in ('Lead Vocal','Backing Vocal')]}
job=APP/'jobs/BV1_long_internal_folder_20261022_080000';(job/'exports').mkdir(parents=True)
atomic_json(job/'request.json',{'language':'zh','created_at':'2026-10-22T08:00:00+08:00','voice_mode':'dual'})
atomic_json(job/'source-info.json',{'title':'夏后谈'})
atomic_json(job/'task.json',{'name':'夏后谈 · 校订工程'})
atomic_json(job/'status.json',{'state':'done','elapsed_seconds':125,'planned_steps':[]})
atomic_json(job/'exports/voices.svp',data);original=(job/'exports/voices.svp').read_bytes()
rows=task_store.records();window.results.ready(rows)
assert window.results.tree.topLevelItem(0).text(0)=='夏后谈 · 校订工程'
errors=[];seen={}
def select_task():
    dialog=page.task_picker_dialog
    try:
        tree=dialog.task_tree;item=tree.topLevelItem(0)
        assert item.text(0)==window.results.tree.topLevelItem(0).text(0)
        assert item.text(2)==window.results.tree.topLevelItem(0).text(2)
        assert job.name not in item.text(0) and str(job) not in item.text(0)
        tree.setCurrentItem(item);application.processEvents()
        assert dialog.use_button.isEnabled() and dialog.project_selector.count()==1
        seen['title']=item.text(0);dialog.use_button.click()
    except BaseException:
        errors.append(traceback.format_exc());dialog.reject()
with patch.object(window.results,'reload'):
    QTimer.singleShot(0,select_task);page.choose_job()
assert not errors,errors
assert seen and page.job==job and page.input_path==job/'exports/voices.svp'
assert '夏后谈 · 校订工程' in page.input_summary.text() and '02:05' in page.input_summary.text()
assert str(job) not in page.input_summary.text() and 'SHA-256' not in page.input_summary.text()
assert str(page.input_path) in page.input_summary.toolTip()
local=APP/'work/local-song.svp';atomic_json(local,data)
with patch('tuning_ui.QFileDialog.getOpenFileName',return_value=(str(local),'')):
    page.local_input_button.click()
assert page.job is None and page.input_path==local and 'local-song.svp' in page.input_summary.text()
assert (job/'exports/voices.svp').read_bytes()==original and page.session is None
window.close();application.processEvents()
print('PASS: two visible input actions, actual Results task titles/time, modal project selection, real elapsed record, local import and original input preservation')
