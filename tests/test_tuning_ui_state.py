"""Production persistence and cross-workflow UI state, with no inference."""
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import os
import sys
import tempfile
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from PyQt5.QtWidgets import QApplication
from app import Window
from common import atomic_json
from tuning_ui import TuningPage
import tuning_ui

application=QApplication([])
window=Window();page=window.tuning

class RunnerDouble:
    def __init__(self,job):
        self.line=SimpleNamespace(connect=lambda callback:None)
        self.result=SimpleNamespace(connect=lambda callback:None)
    def start(self):pass
    def isRunning(self):return False

with tempfile.TemporaryDirectory(prefix='tuning-state-',dir=APP/'work') as folder:
    root=Path(folder);job=root/'job';job.mkdir()
    page.mode.setCurrentIndex(page.mode.findData('calibrated'))
    page.calibration_file.setText('chosen-profile.json')
    page.performance_style.setCurrentIndex(page.performance_style.findData('bold'))
    page.style_pitch.setChecked(True)
    chosen=(page.mode.currentData(),page.calibration_file.text(),page.performance_style.currentData(),page.style_pitch.isChecked())
    for legacy in (None,'old-task-profile.json'):
        request={'language':'zh','bv':'BV1xx411c7mD','voice_mode':'single','reference_calibration':legacy,'prepare_only':True}
        atomic_json(job/'request.json',request);window.job=job
        with patch('app.Runner',RunnerDouble):window.launch()
        window.timer.stop();window.set_running(False)
        assert chosen==(page.mode.currentData(),page.calibration_file.text(),page.performance_style.currentData(),page.style_pitch.isChecked())
        assert json.loads((job/'request.json').read_text(encoding='utf-8'))==request

    with patch.object(page,'busy',return_value=True),patch('components_ui.Installer') as installer:
        window.components.start_install(feature='expression')
        installer.assert_not_called()
    with patch.object(window,'calibration_dialog',SimpleNamespace(busy=lambda:True),create=True),patch('components_ui.Installer') as installer:
        window.components.start_install(feature='expression')
        installer.assert_not_called()

    page.job=job
    committed={'status':'applied','cached':False,'output':str(root/'candidate.svp'),
        'receipt':str(root/'candidate.svp.tuning.json'),'receipt_verified':False,
        'commit_state':'committed_unreceipted','report':{'expression_density':{'added_points':0,'unsupported_nonlinear_controls':0}}}
    page.completed(committed)
    assert '工程已保存，请再次生成' in page.status.text()
    assert page.open_button.isEnabled() and not page.register_button.isEnabled()
    page.completed({**committed,'receipt_verified':True,'commit_state':'receipted','observer_errors':[{'kind':'progress'}]})
    assert page.register_button.isEnabled() and '工程检查已通过' in page.status.text()

    class RenderDouble:
        def __init__(self,function,parent):
            self.running=False
            self.succeeded=SimpleNamespace(connect=lambda callback:None)
            self.failed=SimpleNamespace(connect=lambda callback:None)
            self.finished=SimpleNamespace(connect=lambda callback:None)
        def start(self):self.running=True
        def isRunning(self):return self.running
    video=window.video_mixdown
    (job/'video').mkdir();(job/'video/source.mp4').write_bytes(b'UI fixture only')
    mixdown=root/'mixdown.wav';mixdown.write_bytes(b'UI fixture only')
    video.use_source_job(job,mixdown)
    with patch('video_mixdown_ui.RenderTask',RenderDouble):video.render()
    assert video.busy() and video.active_inputs['source_job']==job.resolve()
    assert video.active_inputs['video']==(job/'video/source.mp4').resolve()
    video.use_source_job(root/'other-job',mixdown)
    assert video.source_job==job.resolve() and video.active_inputs['source_job']==job.resolve()
    video.task.running=False;video.finished();assert video.active_inputs=={}

    from calibration_ui import CalibrationDialog
    dialog=CalibrationDialog(page)
    session_root=root/'calibration';session_root.mkdir()
    dialog.session=session_root/'session.json'
    (session_root/'calibrated.svp').write_bytes(b'committed fixture')
    profile_before=page.calibration_file.text()
    dialog.completed({'output':str(session_root/'calibrated.svp'),'report_pending':True,'report_verified':False})
    assert '报告待补全' in dialog.status.text() and page.calibration_file.text()==profile_before
    dialog.finished();assert dialog.start.text()=='补全校准报告'
    class CalibrationDouble(RenderDouble):
        def __init__(self,*args,**kwargs):
            super().__init__(None,None);self.options=kwargs
            self.message=SimpleNamespace(connect=lambda callback:None)
            self.state=SimpleNamespace(connect=lambda callback:None)
    with patch('calibration_session.recoverable_fit_output',return_value='calibrated.svp',create=True),patch('calibration_ui.CalibrationWorker',CalibrationDouble):
        dialog.start_session()
    assert dialog.worker.options['output_name']=='calibrated.svp'
    assert '补全报告' in dialog.status.text()
    dialog.worker.running=False;dialog.finished();dialog.close()

    Q=705600000
    track={'name':'Lead','mainRef':{'blickOffset':0,'database':{'name':'Fixture'}},'groups':[],
        'mainGroup':{'uuid':'lead','notes':[{'onset':0,'duration':Q,'pitch':60,'lyrics':'la'}],'parameters':{}}}
    data={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[]},'tracks':[]}
    for i in range(3):
        item=copy.deepcopy(track);item['name']=str(i);item['mainGroup']['uuid']=str(i);data['tracks'].append(item)
    source=root/'three.svp';atomic_json(source,data)
    assert page.load_project(source)
    page.mode.setCurrentIndex(page.mode.findData('preserve'))
    page.rows[2]['check'].setChecked(True)
    assert page.style_scope.currentData()=='custom'
    assert page.style_scope.currentText()=='自定义轨道'
    assert page.options()['track_indices']==[0,2]
    assert page.options()['style_scope'] in ('lead','all_vocals')
    page.style_scope.setCurrentIndex(page.style_scope.findData('all_vocals'))
    assert page.options()['track_indices']==[0,1,2]
    assert page.style_scope.currentData()=='all_vocals'
    page.style_scope.setCurrentIndex(page.style_scope.findData('lead'))
    assert page.options()['track_indices']==[0]
    assert page.style_scope.currentData()=='lead'

    page.style_mode.setCurrentIndex(page.style_mode.findData('regions'))
    page.apply_preferences({'performance_style':'gentle','style_strength':.4,
        'style_detail':{'onset':.5,'sustain':.75,'connection':.25,'release':1.0},'style_pitch':True})
    page.style_timeline.set_selection(.05,.35)
    assert page.style_timeline.selection()==(.05,.35)
    page.add_style_region()
    first_region={'start':.05,'end':.35,'preset':'gentle','strength':.4,
                  'detail':{'onset':.5,'sustain':.75,'connection':.25,'release':1.0},'pitch':True}
    assert page.style_regions==[first_region] and page.region_list.count()==1
    option_regions=page.options()['style_regions']
    assert option_regions==[first_region], option_regions
    assert 'style_regions' not in page.preferences(), 'Regions belong to execution/session state, not global preferences'

    page.apply_preferences({'performance_style':'bold','style_strength':.7,
        'style_detail':{'onset':1.0,'sustain':.9,'connection':.8,'release':.7},'style_pitch':False})
    page.style_timeline.set_selection(.38,.48);page.add_style_region()
    assert len(page.style_regions)==2 and page.style_regions[0]['start']==.05 and page.style_regions[1]['start']==.38
    page.style_timeline.set_selection(.15,.4);before=len(page.style_regions);page.add_style_region()
    assert len(page.style_regions)==before and '不能重叠' in page.status.text()

    page.region_list.setCurrentRow(0)
    assert page.performance_style.currentData()=='gentle' and page.style_strength.currentData()==.4
    assert page.preferences()['style_detail']==first_region['detail'] and page.style_pitch.isChecked()
    page.apply_preferences({'performance_style':'gentle','style_strength':.55,
        'style_detail':first_region['detail'],'style_pitch':False});page.update_style_region()
    assert page.style_regions[0]['strength']==.55 and page.style_regions[0]['pitch'] is False

    page.clear_style_regions()
    assert page.style_mode.currentData()=='regions' and not page.style_regions
    page.start_run()
    assert '至少需要一个片段' in page.status.text() and page.session is None and page.worker is None

    recorded={'start':.05,'end':.35,'preset':'sad','strength':.4,
              'detail':{'onset':.5,'sustain':.75,'connection':.25,'release':1.0},'pitch':True}
    page.restore_style_regions([recorded]);saved_options=page.options()
    from tuning_workflow import create_session
    region_session=root/'region-session';session_file=create_session(source,region_session)
    atomic_json(region_session/'last-options.json',saved_options)
    other=copy.deepcopy(data);other['tracks']=other['tracks'][:1];other_path=root/'other.svp';atomic_json(other_path,other)
    assert page.load_project(other_path)
    assert page.style_regions==[] and page.style_mode.currentData()=='whole' and page.options()['style_regions']==[]
    assert page.restore_session(session_file)
    assert page.style_mode.currentData()=='regions' and page.style_regions==[recorded]
    assert page.options()['style_regions']==[recorded]
    with patch.object(page,'busy',return_value=True):page.refresh()
    assert not page.style_timeline.isEnabled() and not page.add_region_button.isEnabled()
    assert not page.delete_region_button.isEnabled() and not page.clear_regions_button.isEnabled()

    state_root=root/'production-state';workspace=state_root/'cache/workspace.json'
    recent=str(root/'prior-session/session.json')
    atomic_json(workspace,{'untouched':{'sentinel':True},'tuning':{'session':recent,'preferences':{}}})
    def state():return json.loads(workspace.read_text(encoding='utf-8'))
    # Construct only the page with production persistence enabled. Window was
    # created in the offscreen test mode, so startup update timers stay disabled.
    with patch.multiple(tuning_ui,ROOT=state_root,workspace_state=state),patch.dict(os.environ,{'VWA_TESTING':'','BVWA_TESTING':''}):
        first=TuningPage(window)
        assert first.session is None and first.last_session==recent
        first.expression_density.setCurrentIndex(first.expression_density.findData('original'))
        detail={'onset':.125,'sustain':.3333,'connection':.625,'release':0.0}
        first.apply_preferences({'performance_style':'gentle','style_strength':.625,
                                 'style_pitch':False,'style_detail':detail})
        first.persist()
        assert state()['tuning']['session']==recent and state()['untouched']=={'sentinel':True}
        second=TuningPage(window)
        assert second.session is None and second.last_session==recent
        assert second.performance_style.currentData()=='gentle'
        assert second.style_strength.currentData()==.625 and not second.style_pitch.isChecked()
        assert second.preferences()['style_detail']==detail
        assert all(second.style_detail[key].value()==amount*100 for key,amount in detail.items())
        with patch('tuning_ui.QFileDialog.getOpenFileName',return_value=('','')) as chooser:
            second.choose_session()
            assert chooser.call_args.args[2]==recent
        # Selecting a new input must keep the previous real session accessible.
        assert second.load_project(source)
        second.persist();assert state()['tuning']['session']==recent
        new_session=root/'new-session/session.json';second.session=new_session;second.persist()
        assert state()['tuning']['session']==str(new_session)
        first.deleteLater();second.deleteLater()

window.close();application.processEvents()
print('PASS: preparation preserves exact tuning detail, custom range, recent session, and active tuning/calibration component interlock')
