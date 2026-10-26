"""Isolated frontend contracts for tuning choice adapters and honest result states."""
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import os
import sys
import tempfile
import threading
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtCore import QEvent,QPoint,QPointF,QRect,QTimer,Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication,QDoubleSpinBox,QLabel,QProgressBar,QWidget
from ui_components import AdaptiveColumns,FlowLayout,StyleTimeline
import tuning_ui
from style_presets import pitch_default,visible_presets
os.environ['BVWA_TESTING'] = '1'
from tuning_ui import (TuningPage,format_register_gate,format_receipt_summary,format_tuning_result_summary,
                       style_card_description,style_card_keys,style_card_label,style_pitch_default,
                       ui_merged_preset)

application = QApplication.instance() or QApplication([])


class Owner(QWidget):
    job = None
    processing_active = False
    storage = SimpleNamespace(busy=lambda: False)
    updates = SimpleNamespace(busy=lambda: False)

    def __init__(self, root):
        super().__init__()
        self.root = Path(root)

    def output_parent(self):
        return self.root

    def update_component_state(self):
        pass


with tempfile.TemporaryDirectory(prefix='vwa-tuning-frontend-') as temp:
    root = Path(temp)
    recent = str(root / 'prior/session.json')
    state = {'tuning': {'session': recent, 'preferences': {}}, 'untouched': {'sentinel': True}}
    owner = Owner(root)
    with patch.object(tuning_ui, 'workspace_state', return_value=state), \
            patch('workflow.read_state', return_value={'options': {}}), \
            patch.dict(os.environ, {'BVWA_TESTING': '1'}):
        page = TuningPage(owner)

    assert Path(tuning_ui.__file__).parent == APP, 'Exercise the prepared runtime in the isolated application'
    assert page.session is None and page.last_session == recent
    assert len(page.findChildren(AdaptiveColumns)) >= 3
    assert page.mode_choice.combo is page.mode
    shown_styles = [page.performance_style.itemData(i) for i in range(page.performance_style.count())]
    assert shown_styles == list(style_card_keys()) == list(visible_presets()) == ['none', 'gentle', 'sad', 'bold']
    assert bool(page.performance_style.property('stylePresetChoices')) is True
    assert [button.property('stylePreset') for button in page.performance_style_choice.buttons] == shown_styles
    assert style_card_label('bold') == '明亮欢快〔实验〕'
    assert '情绪（悲伤）' in style_card_description('sad')
    assert '声线（柔和）' in style_card_description('gentle')
    assert tuple(page.style_detail) == ('onset', 'sustain', 'connection', 'release')
    assert page.style_detail_toggle.text() == '分段细调' and not page.style_detail_toggle.isChecked()
    assert [page.style_mode.itemData(i) for i in range(page.style_mode.count())] == ['whole', 'regions']
    assert isinstance(page.style_timeline, StyleTimeline)
    assert isinstance(page.region_editor_entry,QWidget) and page.region_editor_entry.isHidden()
    assert page.style_card.objectName() == 'card' and page.style_card.isAncestorOf(page.region_editor_entry)
    assert page.start_style_editor_button.text() == '开始编辑'
    assert page.start_style_editor_button.property('role') == 'primary'
    assert page.start_style_editor_button.minimumHeight() == 68
    assert page.fullscreen_region_button is page.start_style_editor_button
    assert page.new_region_button.text() == '新建下一段'
    assert page.region_tool_context.text() == '下一段设置'
    assert page._region_storage.parentWidget() is page and page._region_storage.isHidden()
    assert page._region_embed_layout is page._region_storage.layout()
    assert type(page.style_region_panel) is QWidget
    assert page.style_region_panel.parentWidget() is page._region_storage
    assert page._region_embed_layout.indexOf(page.style_region_panel) >= 0
    assert page.style_region_panel.isHidden() and page._region_storage.isHidden()
    assert page.form.layout().indexOf(page.style_region_panel) == -1
    assert page.form.layout().indexOf(page._region_storage) == -1
    assert page.style_timeline.minimumHeight()==340
    assert isinstance(page.region_start_editor,QDoubleSpinBox) and page.region_start_editor.decimals()==3
    assert isinstance(page.region_end_editor,QDoubleSpinBox) and page.region_end_editor.decimals()==3
    for editor in (page.region_start_editor,page.region_end_editor):
        assert editor.minimum()<=-1_000_000 and editor.maximum()>=1_000_000
        assert editor.keyboardTracking() is False
    assert page.add_region_button.isHidden() and page.update_region_button.isHidden()
    visible_region_buttons=(page.delete_region_button,page.clear_regions_button,
                            page.undo_region_button,page.redo_region_button)
    assert [control.text() for control in visible_region_buttons] == ['删除','清空','撤销','重做']
    assert page.region_style_choice.combo is page.performance_style
    assert page.region_strength_choice.combo is page.style_strength
    assert page.performance_style_choice.combo is page.performance_style
    assert page.style_strength_choice.combo is page.style_strength
    assert [button.property('stylePreset') for button in page.region_style_choice.buttons] == shown_styles
    for signal in (page.style_timeline.regionSelected,page.style_timeline.regionEditRequested,
                   page.style_timeline.regionDeleteRequested):
        assert page.style_timeline.receivers(signal)>=1
    assert [shortcut.key().toString() for shortcut in page._region_shortcuts] == [
        'Ctrl+Z','Ctrl+Y','Ctrl+Shift+Z']
    assert all(shortcut.context()==Qt.WidgetWithChildrenShortcut for shortcut in page._region_shortcuts)
    region_actions=next(item.layout() for item in (page.style_region_panel.layout().itemAt(i)
                        for i in range(page.style_region_panel.layout().count()))
                        if isinstance(item.layout(),FlowLayout) and
                        item.layout().indexOf(page.delete_region_button)>=0)
    region_actions._do_layout(QRect(0,0,320,220),False)
    assert min(button.geometry().left() for button in visible_region_buttons)>=0
    assert max(button.geometry().right() for button in visible_region_buttons)<=319
    assert len({button.geometry().y() for button in visible_region_buttons})>1
    assert page._region_storage.isHidden() and page.style_region_panel.isHidden()
    assert 'style_regions' not in page.preferences()
    assert page._region_autosave_timer.interval()==150
    assert page._region_autosave_timer.isSingleShot()
    assert page.style_timeline.receivers(page.style_timeline.selectionCommitted)>=1
    navigation=StyleTimeline()
    assert navigation.pan_mode() is False
    navigation.set_pan_mode(True)
    assert navigation.pan_mode() is True and navigation.hand_mode_button.isChecked()
    assert not navigation.draw_mode_button.isChecked()
    navigation.set_pan_mode(False)
    assert navigation.pan_mode() is False and navigation.draw_mode_button.isChecked()
    navigation_project={'time':{'tempo':[{'position':0,'bpm':120}]},'tracks':[
        {'name':'Lead','mainRef':{'blickOffset':0},'mainGroup':{'notes':[
            {'onset':7680,'duration':3840,'pitch':60,'lyrics':'a'}]}}]}
    navigation.resize(720,340);navigation.show();application.processEvents()
    with patch.object(navigation,'focus_note_start',wraps=navigation.focus_note_start) as scheduled:
        navigation.set_project(navigation_project,[0]);application.processEvents()
        assert scheduled.call_count>=1
        assert navigation._initial_note_view_pending is False
    assert navigation.focus_note_start() is True
    assert navigation._canvas._notes and navigation._canvas._notes[0]['start']>0
    navigation.close();navigation.deleteLater();application.processEvents()
    assert all(editor.value() == 100 for editor in page.style_detail.values())

    # Wide layouts expose input/mapping and both independent setting groups.
    # Each adaptive pair collapses without fixed full-screen dimensions.
    page.workspace.resize(2000, 100)
    page.workspace.reflow()
    assert page.workspace._stacked is False
    page.workspace.resize(1400, 100)
    page.workspace.reflow()
    assert page.workspace._stacked is False
    page.workspace.resize(1100, 100)
    page.workspace.reflow()
    assert page.workspace._stacked is True
    page.settings_columns.resize(1100, 100)
    page.settings_columns.reflow()
    assert page.settings_columns._stacked is False
    page.settings_columns.resize(1000, 100)
    page.settings_columns.reflow()
    assert page.settings_columns._stacked is True

    page_titles = [item.text() for item in page.findChildren(QLabel)]
    assert not any(item.startswith('05 ·') for item in page_titles)
    assert not any('处理状态与结果' == item for item in page_titles)
    assert not page.result_detail_toggle.isChecked()
    assert not page.result_summary.isVisibleTo(page)
    assert page.action_footer.parentWidget() is not page.form
    assert page.progress.parentWidget() is page.action_footer
    assert page.status.parentWidget() is page.action_footer
    assert page.result_summary.parentWidget() is not page.action_footer
    assert not page.run_state.isVisibleTo(page)
    action_layout = next(page.action_footer.layout().itemAt(i).layout()
                         for i in range(page.action_footer.layout().count())
                         if type(page.action_footer.layout().itemAt(i).layout()) is FlowLayout)
    action_buttons = [page.apply_button, page.stop_button, page.open_button, page.register_button]
    action_layout._do_layout(QRect(0, 0, 1400, 100), False)
    assert len({button.y() for button in action_buttons}) == 1
    action_layout._do_layout(QRect(0, 0, 420, 200), False)
    assert len({button.y() for button in action_buttons}) > 1
    assert not page.log_toggle.isChecked()

    assert isinstance(page.progress, QProgressBar)
    assert (page.progress.minimum(), page.progress.maximum(), page.progress.value()) == (0, 100, 0)
    assert not page.progress.isTextVisible() and page.progress.format() == ''
    assert (page.progress.minimumHeight(), page.progress.maximumHeight()) == (12, 12)
    assert page.progress_detail.parentWidget() is page.action_footer
    assert page.stage_strip.parentWidget() is page.action_footer and type(page.stage_strip.layout()) is FlowLayout
    assert isinstance(page._progress_timer, QTimer) and page._progress_timer.parent() is page
    assert set(page.stage_labels) == {'snapshot', 'source', 'style', 'protection', 'save', 'report'}
    assert all(value.text().startswith('○ ') and not value.isHidden() for value in page.stage_labels.values())
    page._set_progress('running')
    page._run_outcome = 'running'
    assert (page.progress.minimum(), page.progress.maximum(), page.progress.value()) == (0, 100, 0)
    assert page._progress_timer.isActive() and not page.progress.isTextVisible()
    page._worker_message('从固定输入快照开始处理')
    assert page.progress_detail.text() == '正在准备工程…'
    assert '从固定输入快照开始处理' in page.log.toPlainText(), 'Keep the original diagnostic message in the log'
    assert page.stage_labels['snapshot'].text() == '● 准备工程'
    page._worker_message('处理参数来源：reference')
    assert page.stage_labels['snapshot'].text() == '✓ 准备工程'
    assert page.stage_labels['source'].text() == '● 参数来源'
    page._worker_message('统一保形加密与保护校验')
    assert page.stage_labels['source'].text() == '✓ 参数来源'
    assert page.stage_labels['style'].text() == '○ 风格', 'A stage never received as current must remain pending'
    assert page.stage_labels['protection'].text() == '● 曲线处理'
    page._progress_timer.stop()
    page._set_progress('idle')
    assert (page.progress.minimum(), page.progress.maximum(), page.progress.value()) == (0, 100, 0)
    assert not page._progress_timer.isActive()
    assert all(value.text().startswith('○ ') for value in page.stage_labels.values())
    assert page.performance_style_choice.combo is page.performance_style
    assert page.expression_density_choice.combo is page.expression_density
    for name in ('reference_dynamics', 'reference_tension', 'reference_breathiness', 'reference_voicing',
                 'dynamics_strength', 'expression_density', 'performance_style', 'style_strength',
                 'style_scope', 'style_pitch', 'calibration_enabled', 'calibration_file',
                 'calibration_choose', 'calibration_probes'):
        assert getattr(owner, name) is getattr(page, name)

    page.mode_choice.buttons[1].click()
    assert page.mode.currentData() == 'reference'
    page.mode.setCurrentIndex(-1)
    assert page.mode.currentIndex() == -1
    assert not any(button.isChecked() for button in page.mode_choice.buttons)
    page.mode.setCurrentIndex(page.mode.findData('preserve'))

    assert not page.style_scope_choice.buttons[page.style_scope.findData('custom')].isEnabled()
    assert '实验' in page.performance_style_choice.buttons[1].text().replace('\n', '')
    assert '不叠加风格' in page.performance_style_choice.buttons[0].text().replace('\n', '')

    page.apply_preferences({'dynamics_strength': .42, 'style_strength': .57,
                            'expression_density': 'dense', 'performance_style': 'soft_ballad',
                            'style_scope': 'lead'})
    assert page.dynamics_strength.findData(.42) >= 0
    assert page.style_strength.findData(.57) >= 0
    assert page.dynamics_strength.currentData() == .42
    assert page.style_strength.currentData() == .57
    assert page.dynamics_strength is not page.style_strength
    assert page.performance_style.currentData() == 'gentle'
    assert '已归并' in page.style_hint.text() and '旧工程保持原样' in page.style_hint.text()

    page.apply_preferences({'performance_style': 'sad'})
    assert page.performance_style.currentData() == 'sad' and page.style_pitch.isChecked()

    legacy_styles = {
        'soft_ballad': ('gentle', False),
        'intimate_narration': ('gentle', False),
        'melancholy': ('sad', True),
        'fragile_tender': ('sad', True),
        'power_pop': ('bold', False),
        'bright_joy': ('bold', True),
    }
    for legacy, (expected_style, expected_pitch) in legacy_styles.items():
        assert ui_merged_preset(legacy) == expected_style
        assert style_pitch_default(legacy) is expected_pitch
        assert pitch_default(legacy) is expected_pitch, 'Old IDs must retain their recipe pitch defaults'
        page.apply_preferences({'performance_style': legacy, 'style_strength': .625})
        assert page.performance_style.currentData() == expected_style
        assert page.style_strength.currentData() == .625
        assert page.style_pitch.isChecked() is expected_pitch
        assert page.preferences()['style_pitch'] is expected_pitch
        page.apply_preferences({'performance_style': legacy, 'style_strength': .625,
                                'style_pitch': False})
        assert page.performance_style.currentData() == expected_style
        assert not page.style_pitch.isChecked()

    for damaged in ([], {}):
        page.apply_preferences({'performance_style': damaged, 'style_pitch': False})
        assert page.performance_style.currentData() == 'bold'
        assert not page.style_pitch.isChecked()

    page.performance_style.setCurrentIndex(page.performance_style.findData('none'))
    page.style_pitch.setChecked(False)
    page.performance_style.setCurrentIndex(page.performance_style.findData('gentle'))
    assert page.style_pitch.isChecked(), 'The new gentle card links pitch on by default'
    page.style_pitch.setChecked(False)
    page.performance_style.setCurrentIndex(page.performance_style.findData('none'))
    page.performance_style_choice.buttons[page.performance_style.findData('bold')].click()
    assert page.performance_style.currentData() == 'bold' and page.style_pitch.isChecked()

    detail = {'onset': .125, 'sustain': .3333, 'connection': .625, 'release': .0}
    page.apply_preferences({'performance_style': 'gentle', 'style_strength': .57,
                            'style_detail': detail, 'style_pitch': False})
    assert page.preferences()['style_detail'] == detail
    assert all(page.style_detail[key].value() == amount * 100 for key, amount in detail.items())
    for switched in ('bold', 'none', 'gentle'):
        page.performance_style.setCurrentIndex(page.performance_style.findData(switched))
        assert page.preferences()['style_detail'] == detail, 'Style switches retain detail coefficients'
    page.apply_preferences({'performance_style': 'gentle', 'style_detail': {'sustain': .4}})
    assert page.preferences()['style_detail'] == {'onset': 1.0, 'sustain': .4,
                                                   'connection': 1.0, 'release': 1.0}

    page.style_mode.setCurrentIndex(page.style_mode.findData('regions'))
    assert not page.region_editor_entry.isHidden()
    assert page.style_card.isAncestorOf(page.region_editor_entry)
    assert page.style_region_panel.isHidden() and page._region_storage.isHidden()
    assert page._region_embed_layout.indexOf(page.style_region_panel) >= 0
    assert not page.style_timeline.isEnabled(), 'An empty page has no project timeline to draw'
    assert not page.start_style_editor_button.isEnabled()
    page.start_style_editor_button.click();application.processEvents()
    assert page._region_dialog is None, 'The editor entry must stay disabled without a project'
    page.style_mode.setCurrentIndex(page.style_mode.findData('whole'))
    assert page.region_editor_entry.isHidden()
    assert page.style_region_panel.isHidden() and page._region_storage.isHidden()
    page.performance_style.setCurrentIndex(page.performance_style.findData('none'))
    assert not page.style_detail_toggle.isEnabled()
    assert all(not editor.isEnabled() for editor in page.style_detail.values())
    page.performance_style.setCurrentIndex(page.performance_style.findData('gentle'))
    assert page.style_detail_toggle.isEnabled()
    assert all(editor.isEnabled() for editor in page.style_detail.values())
    with patch.object(page, 'busy', return_value=True):
        page.refresh()
    assert not page.style_detail_toggle.isEnabled()
    assert all(not editor.isEnabled() for editor in page.style_detail.values())
    assert page.preferences()['style_detail'] == {'onset': 1.0, 'sustain': .4,
                                                   'connection': 1.0, 'release': 1.0}
    page.style_pitch.setChecked(False)

    partial = {
        'status': 'partial', 'cached': False, 'output': str(root / 'candidate.svp'),
        'receipt': str(root / 'candidate.svp.tuning.json'), 'receipt_verified': False,
        'commit_state': 'committed_unreceipted',
        'report': {
            'track_indices': [0, 2],
            'style': {'status': 'partial', 'applied_controls': 2, 'skipped_controls': 1},
            'expression_density': {'mode': 'dense', 'status': 'partial_unsupported',
                                   'added_points': 12, 'unsupported_nonlinear_controls': 1,
                                   'unsupported_fractional_time_controls': 1},
            'pitch_changed': False, 'notes_and_timing_unchanged': True}}
    summary = format_tuning_result_summary(partial)
    assert '部分应用' in summary and '#1、#3' in summary
    region_summary = format_tuning_result_summary({**partial, 'report': {**partial['report'],
        'style': {**partial['report']['style'], 'mode': 'regions', 'label': '时间片段风格'}}})
    assert '时间片段风格：部分应用' in region_summary
    assert '已应用 2 项' in summary and '跳过 1 项' in summary
    assert '新增 12 点' in summary and '非线性保留 1 项' in summary and '浮点时间保留 1 项' in summary
    assert '待补全' in format_receipt_summary(partial)
    assert '补全结果记录' in format_register_gate(root / 'job', partial)
    pending_report = {**partial, 'status': 'applied', 'receipt_verified': True, 'report_pending': True,
                      'report': {**partial['report'], 'status': 'applied'}}
    assert '报告待补全' in format_receipt_summary(pending_report)
    assert '补全处理报告' in format_register_gate(root / 'job', pending_report)
    verified = {**partial, 'receipt_verified': True, 'commit_state': 'receipted'}
    assert '已通过' in format_receipt_summary(verified)
    assert '可登记' in format_register_gate(root / 'job', verified)

    page.job = root / 'job'
    page._set_progress('running')
    page._progress_timer.stop()
    page.progress.setValue(37)
    page.completed(partial)
    assert page.open_button.isEnabled() and not page.register_button.isEnabled()
    assert '补全结果记录' in page.register_gate.text()
    assert '部分应用' in page.run_state.text()
    assert page.progress.value() == 37 and page.progress.value() < page.progress.maximum()
    assert page.progress.format() == '' and not page.progress.isTextVisible()
    assert page.progress_detail.text() == '部分应用 · 请查看结果明细'
    assert page.stage_labels['save'].text() == '○ 保存工程'
    assert page.stage_labels['report'].text() == '○ 检查结果'
    assert page.stage_labels['style'].text() == '– 风格'
    page.result_detail_toggle.setChecked(True)
    application.processEvents()
    assert page.result_summary.isVisibleTo(page)
    assert '部分应用' in page.result_summary.text() and '#1、#3' in page.result_summary.text()
    page.completed(pending_report)
    assert not page.register_button.isEnabled()
    assert '补全处理报告' in page.register_gate.text()
    assert page.progress.value() == 37 and page.progress.value() < page.progress.maximum()
    assert page.progress_detail.text() == '工程已检查 · 请补全报告'
    assert page.stage_labels['save'].text() == '✓ 保存工程'
    assert page.stage_labels['report'].text() == '○ 检查结果'
    page.completed({**verified, 'status': 'unknown', 'report': {**verified['report'], 'status': 'unknown'}})
    assert page.progress.value() == 37 and page.progress.value() < page.progress.maximum()
    assert page.progress_detail.text() == '结果状态未确认 · 请查看日志'
    page.completed(verified)
    assert page.register_button.isEnabled() and '可登记' in page.register_gate.text()
    assert page.progress.value() == 37 and page.progress.value() < page.progress.maximum()

    # Job association affects registration only. A standalone SVP can still
    # complete a verified generation and report.
    standalone = {**verified, 'status': 'applied', 'report': {**verified['report'], 'status': 'applied'}}
    page.job = None
    page.completed(standalone)
    assert (page.progress.minimum(), page.progress.maximum(), page.progress.value()) == (0, 100, 100)
    assert page.progress.format() == '' and not page.progress.isTextVisible()
    assert page.progress_detail.text() == '处理完成 · 可打开工程'
    assert '未关联准备任务' in page.status.text()
    assert not page.register_button.isEnabled()
    assert '本地工程' in page.register_gate.text() and '手动登记' in page.register_gate.text()
    page.job = root / 'job'
    page.completed(standalone)
    assert (page.progress.minimum(), page.progress.maximum(), page.progress.value()) == (0, 100, 100)

    # Failure preserves the visible position but cannot complete the bar.
    page._reset_result_panel()
    page._set_progress('running')
    page._progress_timer.stop()
    page.progress.setValue(48)
    page._run_outcome = 'running'
    page._failed('fixture failure')
    assert page.progress.value() == 48 and page.progress.value() < page.progress.maximum()
    assert not page._progress_timer.isActive() and page.progress.format() == ''
    assert '失败原因' in page.progress_detail.text()

    # Cancel freezes immediately. A late worker message or result must not
    # repaint a terminal success over the stopping state.
    page._reset_result_panel()
    page._run_outcome = 'running'
    page._set_progress('running')
    page._progress_timer.stop()
    page.progress.setValue(52)
    stop_event = threading.Event()
    page.worker = SimpleNamespace(isRunning=lambda: True, stop=stop_event)
    page.cancel()
    assert page.progress.value() == 52 and page.progress.value() < page.progress.maximum()
    assert not page._progress_timer.isActive()
    assert page.progress_detail.text().startswith('正在停止')
    stopped_stages = {key: value.text() for key, value in page.stage_labels.items()}
    page._worker_message('叠加所选风格增量')
    assert page.progress_detail.text().startswith('正在停止')
    assert {key: value.text() for key, value in page.stage_labels.items()} == stopped_stages
    page.worker = None
    page._failed('fixture cancellation')
    assert page.progress.value() == 52 and page.progress.value() < page.progress.maximum()
    page.completed(standalone)
    assert page.progress.value() == 52 and page.progress.value() < page.progress.maximum()
    assert page.progress_detail.text().startswith('工程与处理记录已保留')

    page.deleteLater()
    application.processEvents()

    # A real mouse release on the project timeline is the public draw gesture.
    # Three separated notes make every region prove that user-visible boundaries
    # expand to whole-note edges before they are staged.
    release_project=root/'release-project.svp'
    quarter=705600000
    note_duration=quarter//2
    release_notes=[{'onset':index*quarter,'duration':note_duration,
        'pitch':60+index*2,'lyrics':chr(97+index)} for index in range(3)]
    release_track={'name':'Lead','mainRef':{'blickOffset':0,'database':{'name':'Fixture'}},
        'groups':[],'mainGroup':{'uuid':'lead','notes':release_notes},'parameters':{}}
    release_data={'version':153,'time':{'tempo':[{'position':0,'bpm':120}],'meter':[]},
                  'tracks':[release_track]}
    release_project.write_text(json.dumps(release_data,ensure_ascii=False),encoding='utf-8')
    release_root=root/'release-root'
    release_state={'tuning':{'session':None,'preferences':{}},'untouched':{'sentinel':True}}
    first_detail={'onset':.2,'sustain':.35,'connection':.45,'release':.55}
    second_detail={'onset':.8,'sustain':.75,'connection':.65,'release':.6}
    with patch.multiple(tuning_ui,ROOT=release_root,workspace_state=lambda:release_state), \
            patch.dict(os.environ,{'VWA_TESTING':'','BVWA_TESTING':'1'}):
        release_page=TuningPage(owner)
        assert release_page.load_project(release_project)
        release_page.style_mode.setCurrentIndex(release_page.style_mode.findData('regions'))
        release_page.performance_style.setCurrentIndex(
            release_page.performance_style.findData('none'))
        assert release_page.start_style_editor_button.isEnabled()
        release_page.start_style_editor_button.click();application.processEvents()
        assert release_page.performance_style.currentData()=='gentle'
        assert release_page._region_dialog is not None
        release_page.style_strength_spin.setValue(61)
        for key,amount in first_detail.items():release_page.style_detail[key].spin.setValue(amount*100)
        release_page.style_pitch.setChecked(True);application.processEvents()
        assert release_page._new_region_tool['performance_style']=='gentle'

        timeline=release_page.style_timeline;canvas=timeline._canvas
        bar=timeline._view.horizontalScrollBar();vertical=timeline._view.verticalScrollBar()
        bar.setValue(bar.minimum());vertical.setValue(vertical.minimum());application.processEvents()
        def target_x(seconds):
            return min(max(0,round(canvas.time_to_x(seconds)-bar.value())),canvas.width()-1)
        lane_y=canvas.RULER_HEIGHT+32-vertical.value()
        committed=[]
        timeline.selectionCommitted.connect(committed.append)
        def draw_region(start,end):
            before=len(committed)
            begin=QPoint(target_x(start),lane_y);finish=QPoint(target_x(end),lane_y)
            QTest.mousePress(canvas,Qt.LeftButton,pos=begin)
            QApplication.sendEvent(canvas,QMouseEvent(QEvent.MouseMove,QPointF(finish),
                Qt.NoButton,Qt.LeftButton,Qt.NoModifier))
            QTest.mouseRelease(canvas,Qt.LeftButton,pos=finish);application.processEvents()
            assert len(committed)==before+1
            return committed[-1]

        note_ranges=sorted((note['start'],note['end']) for note in canvas._notes)
        assert len(note_ranges)==3
        assert all(abs(a-b)<1e-9 for a,b in zip(
            [bound for pair in note_ranges for bound in pair],[0.,.25,.5,.75,1.,1.25]))
        assert all(abs(a-b)<1e-9 for a,b in zip(
            timeline.complete_note_range(.04,.12),(0.,.25)))

        drawn=draw_region(.04,.12)
        complete_first=timeline.complete_note_range(*drawn)
        assert len(release_page.style_regions)==1
        first=copy.deepcopy(release_page.style_regions[0])
        assert first['preset']=='gentle'
        assert abs(first['strength']-.61)<1e-12
        assert first['detail']==first_detail and first['pitch'] is True
        assert abs(first['start']-complete_first[0])<1e-9
        assert abs(first['end']-complete_first[1])<1e-9
        # The list may stay highlighted after drawing, but the active tool still
        # targets a new region until the user explicitly selects an old one.
        assert release_page._region_tool_intent=='new'
        assert release_page.region_list.currentRow()==0
        assert release_page.style_timeline.selection()==(first['start'],first['end'])

        release_page.performance_style.setCurrentIndex(
            release_page.performance_style.findData('sad'))
        release_page.style_strength_spin.setValue(72)
        for key,amount in second_detail.items():release_page.style_detail[key].spin.setValue(amount*100)
        release_page.style_pitch.setChecked(False);application.processEvents()
        assert release_page._region_tool_intent=='new'
        assert release_page.style_regions==[first]
        assert release_page._new_region_tool=={
            'performance_style':'sad','style_strength':.72,
            'style_detail':second_detail,'style_pitch':False}
        assert not release_page._region_autosave_timer.isActive()

        drawn=draw_region(.54,.62)
        complete_second=timeline.complete_note_range(*drawn)
        assert len(release_page.style_regions)==2
        assert release_page.style_regions[0]==first
        second=copy.deepcopy(release_page.style_regions[1])
        assert second['preset']=='sad' and abs(second['strength']-.72)<1e-12
        assert second['detail']==second_detail and second['pitch'] is False
        assert abs(second['start']-complete_second[0])<1e-9
        assert abs(second['end']-complete_second[1])<1e-9
        assert release_page._region_tool_intent=='new'
        assert release_page.region_list.currentRow()==1
        assert release_page.style_timeline.selection()==(second['start'],second['end'])

        draft_path=release_page._style_draft_path()
        assert draft_path==release_root/'cache/style-region-drafts'/(
            release_page._style_input_sha256+'.json')
        release_page._flush_style_autosave()
        draft=json.loads(draft_path.read_text(encoding='utf-8'))
        assert draft['style_regions']==[first,second]
        assert draft['track_indices']==[0]
        assert draft['editor_intent']=='new'
        assert draft['next_tool']==release_page._new_region_tool
        assert draft['selected_index']==1
        assert tuple(draft['selection'])==(second['start'],second['end'])

        # Reopening the draft preserves both recipes and leaves the next tool
        # independent from either highlighted region.
        reloaded=TuningPage(owner);assert reloaded.load_project(release_project)
        assert reloaded.style_mode.currentData()=='regions'
        assert reloaded.style_regions==[first,second]
        assert reloaded._region_tool_intent=='new'
        assert reloaded.region_list.currentRow()==1
        assert reloaded.style_timeline.selection()==(second['start'],second['end'])
        assert reloaded.performance_style.currentData()=='sad'
        assert reloaded.style_strength.currentData()==.72
        assert reloaded.preferences()['style_detail']==second_detail
        assert not reloaded.style_pitch.isChecked()

        # Numeric and grip edits are user-visible and therefore also expand to
        # whole notes. Targets inside the second note must remain unchanged.
        reloaded.select_style_region(1)
        assert reloaded._region_tool_intent=='edit'
        reloaded.region_end_editor.setValue(.61)
        reloaded.region_start_editor.setValue(.54);application.processEvents()
        assert reloaded.style_regions==[first,second]
        reloaded.style_timeline.regionEditRequested.emit(1,.54,.61);application.processEvents()
        assert reloaded.style_regions==[first,second]

        # Editing an old row requires that explicit selection. The next tool is
        # retained independently and restored by the dedicated new-region button.
        reloaded.select_style_region(0)
        assert reloaded._region_tool_intent=='edit'
        assert reloaded.performance_style.currentData()=='gentle'
        reloaded.style_strength_spin.setValue(74)
        reloaded.style_detail['sustain'].spin.setValue(41)
        reloaded.style_pitch.setChecked(False)
        assert reloaded._region_autosave_timer.isActive()
        QTest.qWait(220);application.processEvents()
        edited_first=copy.deepcopy(reloaded.style_regions[0])
        assert edited_first['preset']=='gentle'
        assert abs(edited_first['strength']-.74)<1e-12
        assert edited_first['detail']['sustain']==.41 and edited_first['pitch'] is False
        assert reloaded.style_regions[1]==second
        assert reloaded._new_region_tool['performance_style']=='sad'
        assert reloaded.new_region_button.text()=='新建下一段'
        reloaded.new_region_button.click();application.processEvents()
        assert reloaded._region_tool_intent=='new'
        assert reloaded.region_list.currentRow()==-1
        assert reloaded.style_timeline.selection() is None
        assert reloaded.performance_style.currentData()=='sad'
        assert reloaded.style_strength.currentData()==.72
        assert reloaded.preferences()['style_detail']==second_detail
        assert not reloaded.style_pitch.isChecked()

        # The public raw API remains compatible with old callers. User-facing
        # add/update paths align those raw ranges to full notes.
        reloaded.clear_style_regions();reloaded.begin_new_style_region()
        reloaded.stage_style_selection((.04,.12))
        raw=reloaded.style_regions[0]
        assert abs(raw['start']-.04)<1e-9 and abs(raw['end']-.12)<1e-9
        try:reloaded._validate_complete_style_regions()
        except ValueError:pass
        else:raise AssertionError('A half-note raw region must be rejected before generation')

        reloaded.clear_style_regions();reloaded.begin_new_style_region()
        reloaded.style_timeline.set_selection(.04,.12);reloaded.add_style_region()
        added=reloaded.style_regions[0]
        added_complete=reloaded.style_timeline.complete_note_range(.04,.12)
        assert abs(added['start']-added_complete[0])<1e-9
        assert abs(added['end']-added_complete[1])<1e-9
        reloaded.style_timeline.set_selection(.54,.62);reloaded.update_style_region()
        updated=reloaded.style_regions[0]
        updated_complete=reloaded.style_timeline.complete_note_range(.54,.62)
        assert abs(updated['start']-updated_complete[0])<1e-9
        assert abs(updated['end']-updated_complete[1])<1e-9
        reloaded._validate_complete_style_regions()

        dialog=release_page._region_dialog
        dialog.close();application.processEvents()
        for _ in range(3):application.processEvents()
        assert release_page._region_dialog is None
        for item in (release_page,reloaded):
            item.close();item.deleteLater();application.processEvents()

print('PASS: responsive tuning grid, next-region intent, whole-note draw/numeric/grip/add/update boundaries, independent draft tools, 12px untracked-label progress, actual stage messages, terminal caps, late-signal protection and receipt gate contracts')
