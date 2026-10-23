"""Isolated frontend contracts for tuning choice adapters and honest result states."""
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import tempfile
import threading
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from PyQt5.QtCore import QRect,QTimer
from PyQt5.QtWidgets import QApplication,QLabel,QProgressBar,QWidget
from ui_components import AdaptiveColumns,FlowLayout
import tuning_ui
os.environ['BVWA_TESTING'] = '1'
from tuning_ui import TuningPage, format_register_gate, format_receipt_summary, format_tuning_result_summary

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

print('PASS: responsive tuning grid, 12px untracked-label progress, actual stage messages, terminal caps, late-signal protection and receipt gate contracts')
