"""Task references share the actual Results catalogue, without changing its selection."""
import json,os,sys,time,traceback
from pathlib import Path
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
assert os.environ.get('BVWA_TESTING')=='1' and APP.parent.name=='test-sandboxes','Use tests/run_tests.py'
from PyQt5.QtCore import QTimer,Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from app import Window
from common import atomic_json
from results_ui import TaskPickerDialog,task_caption
import task_store

application=QApplication([]);window=Window();window.show()
def settle():
    for _ in range(8):application.processEvents()
settle();assert window.results.stop_scan();settle()
library=window.results
quarter=705600000
svp={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[]},
     'tracks':[{'name':'Lead','mainRef':{'blickOffset':0},'groups':[],
                'mainGroup':{'uuid':'lead','notes':[{'onset':0,'duration':quarter,'pitch':60,'lyrics':'la'}],'parameters':{}}}]}

def make_task(name,title,*,media=True,project=True,source=True):
    job=APP/'jobs'/name;job.mkdir(parents=True)
    atomic_json(job/'request.json',{'language':'zh','voice_mode':'single','bv':'BV1fixture','created_at':'2026-10-22T11:00:00+08:00'})
    atomic_json(job/'status.json',{'state':'done','elapsed_seconds':125,'planned_steps':[]})
    atomic_json(job/'task.json',{'name':title})
    if source:
        atomic_json(job/'source-info.json',{'title':title,'owner':{'name':'Fixture'},'bvid':'BV1fixture'})
        (job/'发布简介草稿.txt').write_text('UI fixture source draft',encoding='utf-8')
    if project:(job/'exports').mkdir();atomic_json(job/'exports/voices.svp',svp)
    if media:
        (job/'video').mkdir();(job/'video/source.mp4').write_bytes(b'UI fixture only: not decoded or rendered')
    return job

good=make_task('BV1_long_internal_folder_20261022','夏后谈 · 来源甲')
other=make_task('BV2_another_folder_20261022','Dazzling · 来源乙')
missing=make_task('BV3_pending','缺少所需产物',media=False,project=False,source=False)
originals={p:p.read_bytes() for job in (good,other,missing) for p in job.rglob('*') if p.is_file()}
# Exercise the real asynchronous scanner once; modal cases use its resulting rows.
library.reload();deadline=time.monotonic()+15
while library.worker is not None and time.monotonic()<deadline:QTest.qWait(10);application.processEvents()
assert library.worker is None and len(library.rows)==3
assert {row['title'] for row in library.rows}=={'夏后谈 · 来源甲','Dazzling · 来源乙','缺少所需产物'}
rows=list(library.rows);initial_library_selection=library.selected_job
out=APP/'work/test-results';out.mkdir(parents=True,exist_ok=True)

def item_for(dialog,job):
    return next(dialog.task_tree.topLevelItem(i) for i in range(dialog.task_tree.topLevelItemCount())
                if Path(dialog.task_tree.topLevelItem(i).data(0,Qt.UserRole)['path'])==job)

def choose(page,job,*,cancel=False,missing_reason=False):
    errors=[];receivers=library.receivers(library.rows_changed)
    def act():
        dialog=page.task_picker_dialog
        try:
            assert isinstance(dialog,TaskPickerDialog)
            displayed={dialog.task_tree.topLevelItem(i).text(0) for i in range(dialog.task_tree.topLevelItemCount())}
            assert displayed=={row['title'] for row in library.rows}
            dialog.task_tree.setCurrentItem(item_for(dialog,job));settle()
            assert str(job) not in dialog.summary.text() and job.name not in dialog.summary.text()
            if missing_reason:
                assert not dialog.use_button.isEnabled() and '\n' in dialog.summary.text();dialog.reject()
            elif cancel:dialog.reject()
            else:
                dialog.grab().save(str(out/f'task-library-picker-{type(page).__name__}.png'))
                assert dialog.use_button.isEnabled();dialog.use_button.click()
        except BaseException:errors.append(traceback.format_exc());dialog.reject()
    with patch.object(library,'reload'):
        QTimer.singleShot(0,act);page.choose_job()
    assert not errors,errors
    assert library.receivers(library.rows_changed)==receivers,'closed picker retained a catalogue subscription'
    assert library.selected_job==initial_library_selection,'reference selection changed the Results page selection'

tuning,video,publish=window.tuning,window.video_mixdown,window.publishing
for page in (tuning,video,publish):choose(page,good)
assert tuning.job==good and tuning.input_path==good/'exports/voices.svp'
assert video.source_job==good and Path(video.fields['video'].text())==good/'video/source.mp4'
assert Path(publish.job.text())==good and not publish.job.isVisible()
assert good.name not in video._context.text() and good.name not in publish.page_context.text()
assert str(good) in video._context.toolTip() and publish.job_display.toolTip()==str(good)

before=(tuning.input_path,video.source_job,video.fields['video'].text(),publish.job.text(),publish.song.text())
for page in (tuning,video,publish):
    choose(page,other,cancel=True);choose(page,missing,missing_reason=True)
assert before==(tuning.input_path,video.source_job,video.fields['video'].text(),publish.job.text(),publish.song.text())

# A rename refreshes displayed identity but leaves the underlying references and drafts intact.
atomic_json(good/'task.json',{'name':'夏后谈 · 已改名'})
library.ready(task_store.records());settle()
assert '夏后谈 · 已改名' in tuning.input_summary.text()
assert task_caption(window,good) in video._context.text() and task_caption(window,good) in publish.page_context.text()
assert before==(tuning.input_path,video.source_job,video.fields['video'].text(),publish.job.text(),publish.song.text())

# Refresh while open, search, no matches and removal after selection stay recoverable.
dialog=TaskPickerDialog(window,window,selected=good)
dialog.search.setText('Dazzling');assert dialog.task_tree.topLevelItemCount()==1
assert dialog.selection()['row']['path']==other
dialog.search.setText('does-not-exist');assert not dialog.use_button.isEnabled()
dialog.search.clear();library.rows_changed.emit(library.rows);assert dialog.task_tree.topLevelItemCount()==3
dialog.task_tree.setCurrentItem(item_for(dialog,other));request=other/'request.json';raw=request.read_bytes();request.unlink()
dialog.accept();assert not dialog.use_button.isEnabled() and '移动或删除' in dialog.summary.text()
request.write_bytes(raw);dialog.reject();dialog.deleteLater();settle()

# Calibration uses the same owner chain and explicit session choices.
from calibration_ui import _task_calibration_choices
for name,voice in (('calibration-lead','lead'),('calibration-backing','backing')):
    atomic_json(good/name/'session.json',{'voice':voice})
dialog=TaskPickerDialog(tuning,window,selected=good,choices=_task_calibration_choices)
assert dialog.library is library and dialog.project_selector.count()==2 and dialog.use_button.isEnabled()
assert {Path(dialog.project_selector.itemData(i)).parent.name for i in range(2)}=={'calibration-lead','calibration-backing'}
dialog.reject();dialog.deleteLater();settle()

for page in (tuning,video,publish):
    with patch.object(page,'busy',return_value=True),patch('results_ui.TaskPickerDialog',side_effect=AssertionError('busy picker opened')):
        page.choose_job()
with patch.object(video,'preview_busy',return_value=True),patch('results_ui.TaskPickerDialog',side_effect=AssertionError('preview picker opened')):
    video.choose_job()
assert not video.use_source_job(missing) and video.source_job==good
for path,raw in originals.items():
    if path==good/'task.json':continue
    assert path.read_bytes()==raw,path

out=APP/'work/test-results';out.mkdir(parents=True,exist_ok=True)
window.show_job(good);window.navigate(0)
for size in ((1000,680),(1440,900)):
    window.resize(*size);settle()
    assert task_caption(window,good) in window.status.text()
    assert window.progress.isVisible() and window.stage_strip.isVisible()
    assert window.rect().contains(window.start.mapTo(window,window.start.rect().bottomRight()))
    window.grab().save(str(out/f'task-library-preparation-{size[0]}.png'))
for index,name in ((9,'tuning'),(8,'video'),(3,'publishing')):
    window.navigate(index);window.resize(1440,900);settle();window.grab().save(str(out/f'task-library-{name}.png'))
window.close();settle()
print('PASS: actual async Results scan, shared tuning/video/publication selection, readable identity/time, per-page missing inputs, cancel/busy immutability, live rename, no subscription leak, stale task rejection and unchanged source artifacts')
