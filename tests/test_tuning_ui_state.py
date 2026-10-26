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
from PyQt5.QtTest import QTest
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
    # Region drafts are runtime cache. Keep every page in this test off the
    # real user cache, including the pages exercised before production prefs.
    early_state_root=root/'page-root';early_workspace=early_state_root/'cache/workspace.json'
    def early_state():return json.loads(early_workspace.read_text(encoding='utf-8'))
    atomic_json(early_workspace,{'tuning':{'session':None,'preferences':{}}})
    page_root_patch=patch.multiple(tuning_ui,ROOT=early_state_root,workspace_state=early_state)
    page_root_patch.start()
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
        'mainGroup':{'uuid':'lead','notes':[{'onset':index*Q,'duration':Q//2,
            'pitch':60+index*2,'lyrics':chr(97+index)} for index in range(3)],'parameters':{}}}
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
    assert not page.region_editor_entry.isHidden()
    assert page.fullscreen_region_button is page.start_style_editor_button
    assert page.start_style_editor_button.isEnabled()
    assert page.style_region_panel.isHidden() and page._region_storage.isHidden()
    assert page._region_embed_layout.indexOf(page.style_region_panel)>=0
    page.apply_preferences({'performance_style':'gentle','style_strength':.4,
        'style_detail':{'onset':.5,'sustain':.75,'connection':.25,'release':1.0},'style_pitch':True})
    page.style_timeline.set_selection(.04,.12)
    assert page.style_timeline.selection()==(.04,.12)
    page.add_style_region()
    first_region={'start':0.,'end':.25,'preset':'gentle','strength':.4,
                  'detail':{'onset':.5,'sustain':.75,'connection':.25,'release':1.0},'pitch':True}
    assert page.style_regions==[first_region] and page.region_list.count()==1
    option_regions=page.options()['style_regions']
    assert option_regions==[first_region], option_regions
    assert 'style_regions' not in page.preferences(), 'Regions belong to execution/session state, not global preferences'

    page.begin_new_style_region()
    page.apply_preferences({'performance_style':'bold','style_strength':.7,
        'style_detail':{'onset':1.0,'sustain':.9,'connection':.8,'release':.7},'style_pitch':False})
    page.style_timeline.set_selection(.54,.62);page.add_style_region()
    assert len(page.style_regions)==2 and page.style_regions[0]['start']==0 and page.style_regions[1]['start']==.5
    original_regions=copy.deepcopy(page.style_regions)
    before=copy.deepcopy(page.style_regions)
    page.style_timeline.regionEditRequested.emit(1,.02,.30)
    assert page.style_regions==before and '无法更新' in page.status.text()
    page.style_timeline.regionSelected.emit(-1)
    assert page.region_list.currentRow()==-1 and page.style_timeline.selection() is None
    assert page.style_regions==before and page.style_timeline._canvas._active_region==-1
    page.style_timeline.regionSelected.emit(0)
    assert page.region_list.currentRow()==0 and page.performance_style.currentData()=='gentle'

    page.style_timeline.regionDeleteRequested.emit(1)
    assert len(page.style_regions)==1
    page.undo_style_regions()
    assert page.style_regions==before and page.region_list.currentRow()==1

    # A release-time drag request copies the original recipe, changes only its
    # boundaries, sorts the result, and records one reversible edit.
    undo_count=len(page._region_undo)
    page.style_timeline.regionEditRequested.emit(0,1.04,1.10)
    assert [region['start'] for region in page.style_regions]==[.5,1.0]
    dragged=copy.deepcopy(original_regions[0]);dragged['start']=1.;dragged['end']=1.25
    assert page.style_regions[1]==dragged
    assert [(row['start'],row['end']) for row in page.style_timeline._canvas.display_regions()]==[(.5,.75),(1.,1.25)]
    assert page.region_list.currentRow()==1 and page.style_timeline.selection()==(1.,1.25)
    assert len(page._region_undo)==undo_count+1 and not page._region_redo
    page.undo_style_regions()
    assert page.style_regions==original_regions and page.region_list.currentRow()==0
    assert page.style_timeline.selection()==(0.,.25)
    page.redo_style_regions()
    assert page.style_regions==[original_regions[1],dragged] and page.region_list.currentRow()==1
    assert page.style_timeline.selection()==(1.,1.25)
    page.undo_style_regions()

    # Each valid numeric time edit now stages the selected region immediately.
    # No Add/Update action is needed, and both edits remain reversible.
    page.style_timeline.regionSelected.emit(1);before=copy.deepcopy(page.style_regions);undo_count=len(page._region_undo)
    page.region_end_editor.setValue(1.25);page.region_start_editor.setValue(1.04)
    timed=copy.deepcopy(before);timed[1]['start']=1.;timed[1]['end']=1.25
    assert page.style_regions==timed and len(page._region_undo)==undo_count+2
    assert json.loads(page._style_draft_path().read_text(encoding='utf-8'))['style_regions']==timed
    assert page.style_timeline.selection()==(1.,1.25)
    assert (page.region_start_editor.value(),page.region_end_editor.value())==(1.,1.25)
    page.style_timeline.set_selection(.15,.4);before=len(page.style_regions);page.add_style_region()
    assert len(page.style_regions)==before and '不能重叠' in page.status.text()

    page.select_style_region(0)
    assert page.performance_style.currentData()=='gentle' and page.style_strength.currentData()==.4
    assert page.preferences()['style_detail']==first_region['detail'] and page.style_pitch.isChecked()
    page.apply_preferences({'performance_style':'gentle','style_strength':.55,
        'style_detail':first_region['detail'],'style_pitch':False});page.update_style_region()
    assert page.style_regions[0]['strength']==.55 and page.style_regions[0]['pitch'] is False

    before_clear=copy.deepcopy(page.style_regions)
    page.clear_style_regions()
    assert page.style_mode.currentData()=='regions' and not page.style_regions
    page.undo_style_regions()
    assert page.style_regions==before_clear and page.style_mode.currentData()=='regions'
    page.redo_style_regions();assert not page.style_regions
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
    assert page.style_regions==[] and page.style_mode.currentData()=='regions'
    assert page.style_mode_choice.buttons[1].isChecked()
    assert not page.region_editor_entry.isHidden() and page.start_style_editor_button.isEnabled()
    assert page.options()['style_regions']==[]
    assert page._region_undo==[] and page._region_redo==[]
    assert page.restore_session(session_file)
    assert page.style_mode.currentData()=='regions' and page.style_regions==[recorded]
    assert page._region_undo==[] and page._region_redo==[]
    assert page.options()['style_regions']==[recorded]
    page.style_timeline.set_selection(.6,.7);page.add_style_region();before=copy.deepcopy(page.style_regions)
    with patch.object(page,'busy',return_value=True):
        page.refresh()
        assert not page.style_timeline.isEnabled() and not page.add_region_button.isEnabled()
        assert not page.delete_region_button.isEnabled() and not page.clear_regions_button.isEnabled()
        assert not page.undo_region_button.isEnabled() and not page.redo_region_button.isEnabled()
        assert not page.region_start_editor.isEnabled() and not page.region_end_editor.isEnabled()
        assert not page.start_style_editor_button.isEnabled()
        page.undo_style_regions();page.redo_style_regions();page.style_timeline.regionEditRequested.emit(0,.9,1.1)
        page.style_timeline.regionDeleteRequested.emit(0)
        assert page.style_regions==before

    # Fullscreen hosts the same editor and data. Returning to the page keeps
    # edits, selection, zoom and undo state, rather than recreating controls.
    page.refresh();page.region_list.setCurrentRow(0)
    original_timeline=page.style_timeline;fullscreen_before=copy.deepcopy(page.style_regions)
    assert page.start_style_editor_button.isEnabled()
    page.start_style_editor_button.click();application.processEvents()
    dialog=page._region_dialog
    assert dialog is not None and dialog.isFullScreen()
    assert page._region_dialog_scroll.widget() is page.style_region_panel
    assert page.style_timeline is original_timeline and page.style_regions==fullscreen_before
    assert page.style_timeline.isVisibleTo(dialog)
    assert page.style_timeline.height()>=dialog.height()*.75,'Fullscreen should give the piano roll the main vertical area'
    page._region_settings_button.setChecked(True);application.processEvents()
    inspector_width=page.style_timeline.width()
    page._region_settings_button.setChecked(False);application.processEvents()
    assert page.style_timeline.width()>=inspector_width+250,'Collapsing settings should release width to the piano roll'
    assert page.style_regions==fullscreen_before
    assert page.style_timeline.selection()==(fullscreen_before[0]['start'],fullscreen_before[0]['end'])
    page.style_timeline.regionEditRequested.emit(0,.06,.36)
    assert page.style_regions[0]['start']==0 and page.style_regions[0]['end']==.36
    page.undo_style_regions();assert page.style_regions==fullscreen_before
    page.style_timeline.set_selection(.1,.2);page.style_timeline.set_zoom(1.3)
    history_size=(len(page._region_undo),len(page._region_redo))
    dialog.close()
    for _ in range(6):application.processEvents()
    assert page._region_dialog is None and page._region_embed_layout.indexOf(page.style_region_panel)>=0
    assert page.style_region_panel.parentWidget() is page._region_storage
    assert page.style_region_panel.isHidden() and page._region_storage.isHidden()
    assert page.form.layout().indexOf(page.style_region_panel)==-1
    assert not page.region_editor_entry.isHidden()
    assert page.style_timeline is original_timeline and page.style_timeline.selection()==(.1,.2)
    assert page.style_timeline.zoom_factor()==1.3 and page.style_regions==fullscreen_before
    assert (len(page._region_undo),len(page._region_redo))==history_size

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
        preferences_detail={'onset':.125,'sustain':.3333,'connection':.625,'release':0.0}
        first.apply_preferences({'performance_style':'gentle','style_strength':.625,
                                 'style_pitch':False,'style_detail':preferences_detail})
        first.persist()
        assert state()['tuning']['session']==recent and state()['untouched']=={'sentinel':True}
        second=TuningPage(window)
        assert second.session is None and second.last_session==recent
        assert second.performance_style.currentData()=='gentle'
        assert second.style_strength.currentData()==.625 and not second.style_pitch.isChecked()
        assert second.preferences()['style_detail']==preferences_detail
        assert all(second.style_detail[key].value()==amount*100 for key,amount in preferences_detail.items())
        with patch('tuning_ui.QFileDialog.getOpenFileName',return_value=('','')) as chooser:
            second.choose_session()
            assert chooser.call_args.args[2]==recent
        # Selecting a new input must keep the previous real session accessible.
        assert second.load_project(source)
        second.persist();assert state()['tuning']['session']==recent
        new_session=root/'new-session/session.json';second.session=new_session;second.persist()
        assert state()['tuning']['session']==str(new_session)
        first.deleteLater();second.deleteLater()

    # Project-specific drafts replace the old same-SHA clear contract. They
    # restore regions, mode/selection/tool and track_indices, remain isolated
    # by input SHA, survive corrupt JSON safely and flush on close.
    draft_root=root/'draft-production-state'
    draft_workspace=draft_root/'cache/workspace.json'
    atomic_json(draft_workspace,{'untouched':{'sentinel':True},
                                 'tuning':{'session':None,'preferences':{}}})
    def draft_state():return json.loads(draft_workspace.read_text(encoding='utf-8'))
    draft_source=root/'draft-three.svp'
    draft_two=copy.deepcopy(data);draft_two['tracks']=draft_two['tracks'][:2]
    draft_other=root/'draft-other.svp';atomic_json(draft_other,draft_two)
    source_before=source.read_bytes();other_before=draft_other.read_bytes()
    detail={'onset':.2,'sustain':.35,'connection':.45,'release':.55}
    expected={
        'start':.05,'end':.35,'preset':'sad','strength':.625,
        'detail':detail,'pitch':False}
    assert set(expected)=={'start','end','preset','strength','detail','pitch'}
    with patch.multiple(tuning_ui,ROOT=draft_root,workspace_state=draft_state), \
            patch.dict(os.environ,{'VWA_TESTING':'','BVWA_TESTING':''}):
        first=TuningPage(window)
        assert first.load_project(source)
        first.style_mode.setCurrentIndex(first.style_mode.findData('regions'))
        first.rows[1]['check'].setChecked(True);first.rows[2]['check'].setChecked(True)
        assert first._selected_track_indices()==[0,1,2]
        first.apply_preferences({'performance_style':'sad','style_strength':.625,
                                 'style_pitch':False,'style_detail':detail})
        first.stage_style_selection((expected['start'],expected['end']))
        assert first.style_regions==[expected] and first.region_list.currentRow()==0
        assert first._region_tool_intent=='new'
        first.style_technique.setCurrentIndex(first.style_technique.findData('chinese_opera'))
        first.technique_strength_choice.spin.setValue(70)
        first.technique_pitch.setChecked(True)
        assert first.style_regions==[expected]
        first._flush_style_autosave()
        source_sha=first._style_input_sha256
        draft_path=draft_root/'cache/style-region-drafts'/(source_sha+'.json')
        assert draft_path.is_file()
        payload=json.loads(draft_path.read_text(encoding='utf-8'))
        full_payload=draft_path.read_bytes()
        assert set(payload)=={'schema_version','input_sha256','style_regions','mode',
                              'selection','selected_index','tool','track_indices','editor_intent','next_tool'}
        assert payload['schema_version']==1 and payload['input_sha256']==source_sha
        assert payload['style_regions']==[expected] and payload['mode']=='regions'
        assert payload['track_indices']==[0,1,2] and payload['selected_index']==0
        assert payload['selection']==[expected['start'],expected['end']]
        saved_tool={'performance_style':'sad','style_strength':.625,'style_pitch':False,
                    'style_detail':detail,'style_technique':'chinese_opera',
                    'technique_strength':.7,'technique_pitch':True}
        assert payload['tool']==saved_tool
        assert payload['editor_intent']=='new' and payload['next_tool']==saved_tool

        # Version-one drafts predating the explicit next-tool fields remain
        # readable and start in new-segment intent without rewriting a region.
        legacy_payload=copy.deepcopy(payload)
        legacy_payload.pop('editor_intent');legacy_payload.pop('next_tool')
        for key in ('style_technique','technique_strength','technique_pitch'):
            legacy_payload['tool'].pop(key)
        legacy_tool={**saved_tool,'style_technique':'none',
                     'technique_strength':.6,'technique_pitch':True}
        atomic_json(draft_path,legacy_payload)

        # Reopening the same input restores all draft-owned state, including
        # explicit track selection and undo history reset to the loaded draft.
        second=TuningPage(window);assert second.load_project(source)
        assert second._selected_track_indices()==[0,1,2]
        assert second.style_mode.currentData()=='regions'
        assert second.style_regions==[expected]
        assert second.region_list.currentRow()==0
        assert second.style_timeline.selection()==(expected['start'],expected['end'])
        assert second.performance_style.currentData()=='sad'
        assert second.style_strength.currentData()==.625
        assert not second.style_pitch.isChecked()
        assert second.preferences()['style_detail']==expected['detail']
        assert second._region_undo==[] and second._region_redo==[]
        assert second._region_tool_intent=='new' and second._new_region_tool==legacy_tool
        assert (second.style_technique.currentData(),second.technique_strength.currentData(),
                second.technique_pitch.isChecked())==('none',.6,True)
        assert second.style_regions==[expected]
        second._flush_style_autosave()
        normalized=json.loads(draft_path.read_text(encoding='utf-8'))
        assert normalized['tool']==legacy_tool and normalized['next_tool']==legacy_tool
        draft_path.write_bytes(full_payload)
        assert second._restore_style_draft() and second._new_region_tool==saved_tool

        # A different project gets a different SHA and starts clean without
        # replacing the first project's draft.
        original_payload=full_payload
        assert second.load_project(draft_other)
        assert second._selected_track_indices()==[0]
        assert second.style_mode.currentData()=='regions' and second.style_regions==[]
        other_draft=second._style_draft_path()
        assert other_draft!=draft_path and other_draft.is_file()
        second.style_mode.setCurrentIndex(second.style_mode.findData('regions'))
        second.apply_preferences({'performance_style':'gentle','style_strength':.5,
                                 'style_pitch':True,'style_detail':detail})
        second.stage_style_selection((.4,.8))
        second._flush_style_autosave()
        assert other_draft.is_file() and draft_path.read_bytes()==original_payload
        assert second.style_regions[0]['preset']=='gentle'

        # Corrupt draft JSON for the same input is ignored. The source SVP and
        # generic preferences shape stay independent from this local cache.
        second.load_project(source);draft_path.write_text('{broken',encoding='utf-8')
        third=TuningPage(window);assert third.load_project(source)
        assert third.style_regions==[] and third.style_mode.currentData()=='whole'
        assert '无法恢复' in third.region_draft_status.text()
        assert 'style_regions' not in third.preferences()
        assert source.read_bytes()==source_before and draft_other.read_bytes()==other_before
        draft_path.write_bytes(original_payload)

        # A selected region owns the tool change. The 150 ms single-shot timer
        # updates that row, and undo persists both the resulting region list
        # and its available history.
        third._restore_style_draft()
        assert third.style_regions==[expected]
        assert third._new_region_tool==saved_tool
        assert (third.style_technique.currentData(),third.technique_strength.currentData(),
                third.technique_pitch.isChecked())==('chinese_opera',.7,True)
        # An ordinary six-field region owns the editor while selected. The next
        # tool keeps the opera settings and restores them through the explicit
        # new-region action.
        third.select_style_region(0)
        assert third._region_tool_intent=='edit'
        assert (third.style_technique.currentData(),third.technique_strength.currentData(),
                third.technique_pitch.isChecked())==('none',.6,True)
        assert third.style_regions==[expected]
        third.new_region_button.click();application.processEvents()
        assert third._region_tool_intent=='new'
        assert (third.style_technique.currentData(),third.technique_strength.currentData(),
                third.technique_pitch.isChecked())==('chinese_opera',.7,True)
        third.select_style_region(0)
        third.performance_style.setCurrentIndex(third.performance_style.findData('bold'))
        third.style_strength_spin.setValue(74)
        third.style_detail['sustain'].spin.setValue(41)
        assert third._region_autosave_timer.interval()==150
        assert third._region_autosave_timer.isSingleShot()
        assert third._region_autosave_timer.isActive()
        QTest.qWait(220);application.processEvents()
        assert not third._region_autosave_timer.isActive()
        changed=third.style_regions[0]
        assert set(changed)=={'start','end','preset','strength','detail','pitch'}
        assert changed['preset']=='bold' and abs(changed['strength']-.74)<1e-12
        assert changed['detail']['sustain']==.41
        third._flush_style_autosave()
        saved=json.loads(draft_path.read_text(encoding='utf-8'))
        assert saved['style_regions']==[changed]
        third.undo_style_regions()
        third._flush_style_autosave()
        undone=json.loads(draft_path.read_text(encoding='utf-8'))
        assert undone['style_regions']==[expected] and undone['selected_index']==0
        assert undone['editor_intent']=='edit' and undone['next_tool']==payload['next_tool']
        third.redo_style_regions();third._flush_style_autosave()
        redone=json.loads(draft_path.read_text(encoding='utf-8'))
        assert redone['style_regions']==[changed]
        assert redone['editor_intent']=='edit' and redone['next_tool']==payload['next_tool']
        third.delete_style_region();third._flush_style_autosave()
        deleted=json.loads(draft_path.read_text(encoding='utf-8'))
        assert deleted['style_regions']==[] and deleted['selected_index']==-1
        assert deleted['editor_intent']=='new'
        third.undo_style_regions();third._flush_style_autosave()
        restored=json.loads(draft_path.read_text(encoding='utf-8'))
        assert restored['style_regions']==[changed]

        # Pending style edits flush on close, so a restart sees the final value
        # even if its 150 ms timer has not fired yet.
        third.region_list.setCurrentRow(0)
        third.style_strength_spin.setValue(38)
        assert third._region_autosave_timer.isActive()
        third.close();application.processEvents()
        closed=json.loads(draft_path.read_text(encoding='utf-8'))
        assert closed['style_regions'][0]['strength']==.38
        fourth=TuningPage(window);assert fourth.load_project(source)
        assert fourth.style_regions[0]['strength']==.38
        assert fourth._selected_track_indices()==[0,1,2]
        assert fourth.style_mode.currentData()=='regions'
        assert 'style_regions' not in fourth.preferences()

        # The minimum remains 25 ms, overlap is rejected, and 128 is the hard
        # ceiling. Both the public staging API and undo preserve these guardrails.
        fourth.clear_style_regions()
        before=len(fourth.style_regions)
        fourth.stage_style_selection((.10,.124))
        assert len(fourth.style_regions)==before
        fourth.stage_style_selection((.10,.125))
        shortest=fourth.style_regions[0]
        assert abs(shortest['end']-shortest['start']-.025)<1e-9
        fourth.region_list.setCurrentRow(-1)
        fourth.stage_style_selection((.12,.30))
        assert len(fourth.style_regions)==1 and '重叠' in fourth.region_draft_status.text()
        # The exact 25 ms boundary is checked above. Use 30 ms here so the
        # independent count-limit fixture does not hinge on float subtraction.
        many=[{'start':i*.04,'end':i*.04+.030,'preset':'gentle','strength':.5,
               'detail':detail,'pitch':True} for i in range(128)]
        fourth.restore_style_regions(many)
        assert len(fourth.style_regions)==128
        fourth.stage_style_selection((7.,7.1))
        assert len(fourth.style_regions)==128
        fourth.undo_style_regions()
        assert len(fourth.style_regions)==128
        fourth._flush_style_autosave()

        for item in (first,second,third,fourth):item.deleteLater()
        application.processEvents()

    page_root_patch.stop()
window.close();application.processEvents()
print('PASS: project-specific drafts restore opera tool state, default missing technique fields, close opera for ordinary regions and isolate projects, ignore corrupt cache, autosave with undo and flush on close')
