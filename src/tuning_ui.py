"""Independent SVP tuning surface over recoverable source snapshots."""

from common import environment_value
from i18n import tr as _tr
from datetime import datetime
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
from PyQt5.QtCore import QThread,pyqtSignal,QUrl,Qt,QTimer
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLineEdit,QComboBox,QCheckBox,
    QSpinBox,QFileDialog,QTableWidget,QHeaderView,QPlainTextEdit,QProgressBar,QScrollArea,QFrame,QSizePolicy,
    QDialog)
from common import ROOT,atomic_json,workspace_state
from desktop_theme import page,card,label,button,scroll_content,fold
from ui_components import AdaptiveColumns,FlowLayout,choice
from strength_control import StrengthControl,normalized_strength,set_combo_strength,strength_choices


def combo(rows):
    field=QComboBox()
    for title,value in rows:field.addItem(_tr(title),value)
    return field


def _integer(value,default=0):
    return value if type(value) is int and value>=0 else default


_TUNING_STAGE_TITLES=(
    ('snapshot','准备工程'),
    ('source','参数来源'),
    ('style','风格'),
    ('protection','曲线处理'),
    ('save','保存工程'),
    ('report','检查结果'),
)
_TUNING_STAGE_KEYS=tuple(key for key,_ in _TUNING_STAGE_TITLES)
_TUNING_STAGE_INDEX={key:index for index,(key,_) in enumerate(_TUNING_STAGE_TITLES)}


class TuningVisualProgress:
    """A parent-owned visual clock; stage text never depends on this value."""
    def __init__(self,clock=time.monotonic,cap=95,time_constant=14.):
        self._clock=clock;self.cap=min(99,max(1,int(cap)));self.time_constant=max(1.,float(time_constant))
        self._started_at=None;self._value=0.
    @property
    def value(self):return int(self._value)
    @property
    def running(self):return self._started_at is not None
    def reset(self):
        self._started_at=None;self._value=0.;return self.value
    def start(self):
        self.reset();self._started_at=float(self._clock());return self.value
    def sample(self,now=None):
        if self._started_at is None:return self.value
        current=float(self._clock() if now is None else now)
        elapsed=max(0.,current-self._started_at)
        candidate=self.cap*(1.-math.exp(-elapsed/self.time_constant))
        self._value=max(self._value,min(float(self.cap),candidate))
        return self.value
    def pause(self):
        self._started_at=None;return self.value
    def complete(self):
        self._started_at=None;self._value=100.;return self.value


def tuning_stage_for_message(text):
    value=str(text or '')
    if not value:return None
    if '固定输入快照' in value or '输入快照' in value:return 'snapshot'
    if any(token in value for token in ('参数来源','分析参考','基准试音','参数试音','联合拟合','稳定长音')):return 'source'
    if '风格' in value:return 'style'
    if '保形' in value or '保护校验' in value:return 'protection'
    if '报告' in value:return 'report'
    if any(token in value for token in ('工程已提交','回执','复用验证通过','不会重复叠加')):return 'save'
    return None


_STATUS_TEXT={'applied':'已应用','partial':'部分应用','unchanged':'保留原样',
    'skipped':'已跳过','not_selected':'未选择','preserved_original':'保留原始采样',
    'partial_unsupported':'部分保留，未支持曲线未加密','deferred':'延迟到最终曲线处理'}


def format_tuning_result_summary(result):
    """Render only fields present in the tuning report; never infer a pass."""
    if not isinstance(result, dict):
        return _tr('生成后可查看参数变化与工程检查结果。')
    report = result.get('report') if isinstance(result.get('report'), dict) else {}
    status = str(result.get('status') or report.get('status') or 'unknown')
    lines = [_tr('处理结果：') + _tr(_STATUS_TEXT.get(status, status))]
    if status == 'partial':
        lines.append(_tr('未应用的参数保留原样，可打开工程查看。'))
    track_indices = report.get('track_indices')
    if isinstance(track_indices, list) and track_indices:
        lines.append(_tr('处理轨道：') + '、'.join(('#' + str(value + 1) for value in track_indices if type(value) is int)))
    else:
        lines.append(_tr('处理轨道：报告未提供'))
    style = report.get('style') if isinstance(report.get('style'), dict) else None
    if style:
        style_status = str(style.get('status') or 'unknown')
        style_text = _tr('风格：') + _tr(_STATUS_TEXT.get(style_status, style_status))
        if 'applied_controls' in style:
            style_text += _tr(' · 已应用 ') + str(_integer(style.get('applied_controls'))) + _tr(' 项')
        if 'skipped_controls' in style:
            style_text += _tr(' · 跳过 ') + str(_integer(style.get('skipped_controls'))) + _tr(' 项')
        lines.append(style_text)
    else:
        lines.append(_tr('风格：报告未提供'))
    density = report.get('expression_density') if isinstance(report.get('expression_density'), dict) else {}
    density_status = str(density.get('status') or 'unknown')
    density_mode = {'original': '保留原始采样', 'dense': '保形加密'}.get(density.get('mode'), '模式未提供')
    density_text = _tr('曲线密度：') + density_mode + ' · ' + _tr(_STATUS_TEXT.get(density_status, density_status))
    density_text += _tr(' · 新增 ') + str(_integer(density['added_points'])) + _tr(' 点') if 'added_points' in density else _tr(' · 点数未提供')
    if 'unsupported_nonlinear_controls' in density:
        density_text += _tr(' · 非线性保留 ') + str(_integer(density.get('unsupported_nonlinear_controls'))) + _tr(' 项')
    if 'unsupported_fractional_time_controls' in density:
        density_text += _tr(' · 浮点时间保留 ') + str(_integer(density.get('unsupported_fractional_time_controls'))) + _tr(' 项')
    lines.append(density_text)
    if 'pitch_changed' in report:
        lines.append(_tr('局部音高：') + (_tr('有变化') if report.get('pitch_changed') else _tr('未改变')))
    if report.get('notes_and_timing_unchanged') is True:
        lines.append(_tr('保护检查：音符与时序未改变'))
    return '\n'.join(lines)


def report_ready(result):
    if not isinstance(result,dict) or result.get('receipt_verified') is not True:return False
    return result.get('report_pending') is not True and result.get('report_verified') is not False


def register_ready(job,result):
    return bool(job) and report_ready(result)


def format_receipt_summary(result):
    if not isinstance(result,dict):return '结果校验：等待生成。'
    commit_state=str(result.get('commit_state') or '')
    if result.get('report_pending') is True or result.get('report_verified') is False:
        return '处理报告待补全，可打开工程，暂不可登记。'
    if result.get('receipt_verified') is True:
        return '结果校验：已通过'
    if commit_state in ('committed_unreceipted','committed_unreported'):
        return '结果记录待补全，可打开工程，暂不可登记。'
    return '结果尚未通过检查，暂不可登记。'


def format_register_gate(job,result):
    if not isinstance(result,dict):return '任务登记：请先生成工程。'
    if not job:return '任务登记：本地工程可在“任务与结果”中手动登记。'
    if result.get('report_pending') is True or result.get('report_verified') is False:
        return '任务登记：请先补全处理报告。'
    if result.get('receipt_verified') is not True:return '任务登记：请再次生成以补全结果记录。'
    return '任务登记：工程已检查，可登记。'


class TuningWorker(QThread):
    message=pyqtSignal(str)
    succeeded=pyqtSignal(object)
    failed=pyqtSignal(str)

    def __init__(self,session,options,parent):
        super().__init__(parent);self.session,self.options=session,options;self.stop=threading.Event()

    def check(self):
        if self.stop.is_set():raise InterruptedError('已停止；工程与处理记录已保留，可继续。')

    def run(self):
        try:
            from tuning_workflow import run_session
            self.succeeded.emit(run_session(self.session,self.options,cancel=self.check,progress=self.message.emit))
        except Exception as error:self.failed.emit(str(error))


class TuningPage(QWidget):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.worker=None;self.session=None;self.last_session=None;self.job=None
        self._selecting_scope=False;self._run_outcome='idle'
        self._progress_clock=TuningVisualProgress();self._stage_cursor=-1;self._stage_states={};self._observed_stages=set()
        self._progress_timer=QTimer(self);self._progress_timer.setInterval(160);self._progress_timer.setTimerType(Qt.CoarseTimer)
        self._progress_timer.timeout.connect(self._advance_progress)
        self.input_path=None;self.result=None;self.rows=[];self.loading=True
        panel,outer=page(_tr('调音工作流'),_tr('选择任务或导入 SVP，调整后另存为新工程。'))
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.addWidget(panel)
        content=scroll_content(outer);body=QVBoxLayout(content);body.setContentsMargins(0,0,0,0);body.setSpacing(18)
        self.form=QWidget();form=QVBoxLayout(self.form);form.setContentsMargins(0,0,0,0);form.setSpacing(18);body.addWidget(self.form)

        # The inner pair needs roughly 1080 logical pixels so its non-compact
        # two-choice cards can stay side by side. With the 5:6 outer ratio this
        # reserves three columns for genuinely wide content and keeps a 1920
        # window in a stable two-column grid instead of four stacked choices.
        left=QWidget();left_column=QVBoxLayout(left);left_column.setContentsMargins(0,0,0,0);left_column.setSpacing(18)
        source_settings=QWidget();source_column=QVBoxLayout(source_settings);source_column.setContentsMargins(0,0,0,0);source_column.setSpacing(18)
        style_settings=QWidget();style_column=QVBoxLayout(style_settings);style_column.setContentsMargins(0,0,0,0);style_column.setSpacing(18)
        self.settings_columns=AdaptiveColumns(source_settings,style_settings,breakpoint=1080,ratio=(1,1))
        self.workspace=AdaptiveColumns(left,self.settings_columns,breakpoint=1120,ratio=(5,6));form.addWidget(self.workspace)
        self.settings_columns.grid.setAlignment(Qt.AlignTop)

        source,area=card(_tr('01 · 工程与输入'),_tr('选择已有任务，或导入本地 SVP 工程。'))
        self.input_card=source
        left_column.addWidget(source)
        inputs=QHBoxLayout();inputs.setSpacing(12)
        self.task_input_button=button(_tr('选择任务'),self.choose_job,'primary')
        self.local_input_button=button(_tr('本地工程'),self.choose_project)
        for control in (self.task_input_button,self.local_input_button):
            control.setMinimumHeight(52);inputs.addWidget(control,1)
        area.addLayout(inputs)
        # Retain the original value owners for restore/import callers, outside
        # the visible source picker. Full paths are details, not task titles.
        self.jobs=QComboBox(self);self.jobs.hide();self.projects=QComboBox(self);self.projects.hide()
        self.project=QLineEdit(self);self.project.hide()
        self.input_summary=label(_tr('尚未选择工程。'),'muted');self.input_summary.setTextFormat(Qt.PlainText);area.addWidget(self.input_summary)
        settings=QWidget();settings_area=QVBoxLayout(settings);settings_area.setContentsMargins(0,4,0,0);settings_area.setSpacing(10)
        settings_area.addWidget(label(_tr('新工程保存位置'),'muted'))
        destination_row=QHBoxLayout();destination_row.addWidget(label(_tr('保存目录'),'muted'))
        self.destination=QLineEdit(str(owner.output_parent()/'tuning-sessions'));destination_row.addWidget(self.destination,1)
        destination_row.addWidget(button(_tr('更改位置'),self.choose_destination));settings_area.addLayout(destination_row)
        self.session_context=label('','muted');settings_area.addWidget(self.session_context)
        session_row=FlowLayout();session_row.addWidget(button(_tr('恢复处理记录…'),self.choose_session))
        session_row.addWidget(button(_tr('重新读取当前工程'),self.import_current));settings_area.addLayout(session_row)
        self.input_settings_toggle=fold(_tr('恢复与保存设置'),settings,area)

        tracks_card,track_area=card(_tr('02 · 处理轨道'),_tr('勾选要处理的轨道；使用参考或已有参数时，为每轨选择对应来源。'))
        left_column.addWidget(tracks_card)
        self.scope_summary=label(_tr('尚未载入演唱轨。'));track_area.addWidget(self.scope_summary)
        self.mapping_hint=label(_tr('请先载入工程，再选择处理轨道。'),'muted');track_area.addWidget(self.mapping_hint)
        self.tracks=QTableWidget(0,5);self.tracks.setHorizontalHeaderLabels([_tr('处理轨 / 多选'), _tr('参考角色'), _tr('对应参考音频'), _tr('选择音频'), _tr('沿用来源轨')])
        self.tracks.horizontalHeader().setSectionResizeMode(0,QHeaderView.Interactive);self.tracks.setColumnWidth(0,280)
        self.tracks.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.tracks.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.tracks.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeToContents)
        self.tracks.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeToContents)
        self.tracks.verticalHeader().hide();self.tracks.setMinimumHeight(220);self.tracks.setMaximumHeight(360);track_area.addWidget(self.tracks)
        track_area.addWidget(label(_tr('参考音频须与工程时间对齐；沿用参数时请核对来源轨号。'),'muted'))
        left_column.addStretch()

        source_choice,choice_area=card(_tr('03 · 参数来源'),_tr('选择一种参数来源，再设置应用强度。'))
        source_column.addWidget(source_choice)
        self.mode=combo([(_tr('保留原表现'), 'preserve'), (_tr('参考演唱映射 · 实验'), 'reference'), (_tr('实测声库校准'), 'calibrated'), (_tr('沿用已有参数'), 'retained')])
        self.mode_choice=choice(self.mode,columns=2);choice_area.addWidget(self.mode_choice)
        self.reference_panel=QWidget();refs=QGridLayout(self.reference_panel);refs.setContentsMargins(0,0,0,0);refs.setSpacing(8)
        for ordinal,(name,title) in enumerate([('dynamics','参考力度'),('tension','参考张力'),('breathiness','参考气声'),('voicing','参考发声强度')]):
            field=QCheckBox(_tr(title));setattr(self,'reference_'+name,field);refs.addWidget(field,ordinal//2,ordinal%2)
        choice_area.addWidget(self.reference_panel)
        self.calibration_panel=QWidget();cal=QVBoxLayout(self.calibration_panel);cal.setContentsMargins(0,0,0,0);cal.setSpacing(10)
        profile_row=QHBoxLayout();self.calibration_file=QLineEdit();self.calibration_file.setPlaceholderText(_tr('当前乐谱、声库与渲染设置对应的校准配置'))
        profile_row.addWidget(self.calibration_file,1);self.calibration_choose=button(_tr('选择配置'),self.choose_profile);profile_row.addWidget(self.calibration_choose);cal.addLayout(profile_row)
        self.calibration_probes=button(_tr('准备 / 继续实测校准…'),self.open_calibration);cal.addWidget(self.calibration_probes)
        cal.addWidget(label(_tr('需要当前乐谱、声库与设置对应的七轨试音；实际渲染由你完成。'),'muted'));choice_area.addWidget(self.calibration_panel)
        self.calibration_enabled=QCheckBox();self.calibration_enabled.hide()
        self.retained_panel=QWidget();retain=QVBoxLayout(self.retained_panel);retain.setContentsMargins(0,0,0,0);retain.setSpacing(10)
        retained_row=QHBoxLayout();self.retained_project=QLineEdit();self.retained_project.setPlaceholderText(_tr('同时间的原参数 SVP'))
        retained_row.addWidget(self.retained_project,1);retained_row.addWidget(button(_tr('选择原参数 SVP'),self.choose_retained));retain.addLayout(retained_row)
        retain.addWidget(label(_tr('逐轨来源轨号在下方轨道表中填写。'),'muted'));choice_area.addWidget(self.retained_panel)
        strength_label=label(_tr('参考 / 校准强度'),'muted');choice_area.addWidget(strength_label)
        self.dynamics_strength=combo(strength_choices((.3,.6,.7,1.)))
        self.dynamics_strength_choice=StrengthControl(self.dynamics_strength);choice_area.addWidget(self.dynamics_strength_choice)
        self.dynamics_strength_control=self.dynamics_strength_choice
        self.dynamics_strength_slider=self.dynamics_strength_choice.slider;self.dynamics_strength_spin=self.dynamics_strength_choice.spin
        strength_label.setBuddy(self.dynamics_strength_spin)
        choice_area.addWidget(label(_tr('拖动滑条按 1% 调整，也可直接输入；0% 不应用。'),'muted'))

        style,style_area=card(_tr('04 · 风格与曲线'),_tr('选择演唱风格，再调整强度、音高表现和曲线密度。'))
        style_column.addWidget(style)
        style_area.addWidget(label(_tr('风格预设'),'muted'))
        self.performance_style=combo([(_tr('不叠加风格'), 'none'), (_tr('柔和抒情〔实验〕'), 'soft_ballad'), (_tr('轻声叙述〔实验〕'), 'intimate_narration'), (_tr('有力流行〔实验〕'), 'power_pop')])
        self.performance_style_choice=choice(self.performance_style,columns=2);style_area.addWidget(self.performance_style_choice)
        style_strength_label=label(_tr('风格强度'),'muted');style_area.addWidget(style_strength_label)
        self.style_strength=combo(strength_choices((.3,.6,.85,1.)))
        self.style_strength_choice=StrengthControl(self.style_strength);style_area.addWidget(self.style_strength_choice)
        self.style_strength_control=self.style_strength_choice
        self.style_strength_slider=self.style_strength_choice.slider;self.style_strength_spin=self.style_strength_choice.spin
        style_strength_label.setBuddy(self.style_strength_spin)
        scope_label=label(_tr('风格适用范围'),'muted');style_area.addWidget(scope_label)
        self.style_scope=combo([(_tr('首条演唱轨'), 'lead'), (_tr('全部演唱轨'), 'all_vocals'), (_tr('自定义轨道'), 'custom')])
        self.style_scope.model().item(2).setEnabled(False);self.style_scope.setToolTip(_tr('快捷勾选首轨或全部；手动勾选后显示实际范围，自定义项只表示当前状态。'))
        self.style_scope_choice=choice(self.style_scope,columns=3,compact=True);style_area.addWidget(self.style_scope_choice)
        self.style_pitch=QCheckBox(_tr('附加局部音高表现（实验）'));self.style_pitch.setToolTip(_tr('为所选风格添加局部音高变化，不改基准音符。'));style_area.addWidget(self.style_pitch)
        style_area.addWidget(label(_tr('曲线密度'),'muted'))
        self.expression_density=combo([(_tr('加密采样 · 保留原点和形状'), 'dense'), (_tr('保留原始采样'), 'original')])
        self.expression_density_choice=choice(self.expression_density,columns=2,compact=True);style_area.addWidget(self.expression_density_choice)
        style_area.addWidget(label(_tr('不支持加密的曲线会保留原样。生成后请在声库中试听。'),'muted'))
        source_column.addStretch(1)
        style_column.addStretch(1)

        # Execution feedback belongs to the fixed footer. Detailed evidence is
        # available on demand and never disables the primary actions while busy.
        self.run_state=label(_tr('尚未开始'),'muted')
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12);self.progress.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self.progress.setFormat('');self.progress.setAccessibleName(_tr('调音处理进度'));self.progress.setProperty('role','progress')
        self.progress.setAccessibleDescription(_tr('显示当前调音阶段。'))
        self.progress_detail=label(_tr('等待开始调音。'),'muted')
        self.run_stage=label(_tr('载入工程并确认来源后，点击“生成独立调音工程”。'),'muted')
        self.stage_strip=QWidget();stage_row=FlowLayout(self.stage_strip,spacing=10);stage_row.setContentsMargins(0,0,0,0)
        self.stage_labels={}
        for key,title in _TUNING_STAGE_TITLES:
            title = _tr(title)
            value=label('○ '+title,'muted');value.setStyleSheet('color: #8aa0af; font-size: 14px;')
            self.stage_labels[key]=value;stage_row.addWidget(value)
        self.stage_strip.setAccessibleName(_tr('调音阶段状态'))
        receipt_box=QWidget();receipt_layout=QVBoxLayout(receipt_box);receipt_layout.setContentsMargins(0,0,0,0);receipt_layout.setSpacing(4)
        receipt_layout.addWidget(label(_tr('结果校验'),'muted'));self.receipt_summary=label(_tr('结果校验：等待生成。'),'muted');receipt_layout.addWidget(self.receipt_summary)
        gate_box=QWidget();gate_layout=QVBoxLayout(gate_box);gate_layout.setContentsMargins(0,0,0,0);gate_layout.setSpacing(4)
        gate_layout.addWidget(label(_tr('任务登记'),'muted'));self.register_gate=label(_tr('任务登记：请先生成工程。'),'muted');gate_layout.addWidget(self.register_gate)
        self.gate_columns=AdaptiveColumns(receipt_box,gate_box,breakpoint=760,ratio=(1,1))
        detail_body=QWidget();detail_layout=QVBoxLayout(detail_body);detail_layout.setContentsMargins(0,4,0,0);detail_layout.setSpacing(10)
        detail_layout.addWidget(self.run_state);detail_layout.addWidget(self.gate_columns)
        self.result_summary=label(_tr('生成后可查看参数变化与工程检查结果。'),'muted');detail_layout.addWidget(self.result_summary)
        self.result_location=label('','muted');self.result_location.hide();detail_layout.addWidget(self.result_location)
        detail_layout.addWidget(label(_tr('已保存的工程可以打开；关联任务并通过检查后可登记。'),'muted'))

        body.addStretch()
        self._choice_views=(self.mode_choice,self.dynamics_strength_choice,self.performance_style_choice,
            self.style_strength_choice,self.style_scope_choice,self.expression_density_choice)

        self.action_footer=QWidget();self.action_footer.setObjectName('footer');footer=QVBoxLayout(self.action_footer)
        footer.setContentsMargins(14,10,14,10);footer.setSpacing(8)
        self.status=label(_tr('尚未开始 · 选择工程并确认处理轨道'),'muted');footer.addWidget(self.status)
        footer.addWidget(self.progress);footer.addWidget(self.progress_detail);footer.addWidget(self.stage_strip)
        actions=FlowLayout(spacing=10);self.apply_button=button(_tr('生成独立调音工程'),self.start_run,'primary');actions.addWidget(self.apply_button)
        self.stop_button=button(_tr('停止'),self.cancel,'danger');self.stop_button.setEnabled(False);actions.addWidget(self.stop_button)
        self.open_button=button(_tr('打开结果工程'),self.open_result);self.open_button.setEnabled(False);actions.addWidget(self.open_button)
        self.register_button=button(_tr('登记为该任务的调音工程'),self.register);self.register_button.setEnabled(False);actions.addWidget(self.register_button)
        footer.addLayout(actions)
        detail_outer=QWidget();detail_outer_layout=QVBoxLayout(detail_outer);detail_outer_layout.setContentsMargins(0,0,0,0);detail_outer_layout.setSpacing(0)
        detail_layout.addWidget(self.run_stage)
        detail_scroll=QScrollArea();detail_scroll.setWidgetResizable(True);detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_scroll.setMaximumHeight(240);detail_scroll.setWidget(detail_body)
        self.result_detail_toggle=fold(_tr('结果明细'),detail_scroll,detail_outer_layout)
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMaximumBlockCount(600);self.log.setMaximumHeight(110)
        self.log_toggle=fold(_tr('处理日志'),self.log,detail_outer_layout)
        for toggle in (self.result_detail_toggle,self.log_toggle):
            detail_outer_layout.removeWidget(toggle);actions.addWidget(toggle)
        def show_details(*args):
            detail_outer.setVisible(self.result_detail_toggle.isChecked() or self.log_toggle.isChecked())
        self.result_detail_toggle.toggled.connect(show_details);self.log_toggle.toggled.connect(show_details);show_details()
        footer.addWidget(detail_outer)
        outer.addWidget(self.action_footer)
        # Compatibility aliases preserve stored preferences and the calibration
        # dialog API without binding the tuning input to preparation's job.
        for name in ('reference_dynamics','reference_tension','reference_breathiness','reference_voicing','dynamics_strength',
                     'expression_density','performance_style','style_strength','style_scope','style_pitch','calibration_enabled',
                     'calibration_file','calibration_choose','calibration_probes'):
            setattr(owner,name,getattr(self,name))
        from workflow import read_state
        saved=workspace_state().get('tuning',{});self.last_session=saved.get('session')
        self.apply_preferences(saved.get('preferences',read_state()['options']))
        self.mode.currentIndexChanged.connect(self.refresh)
        self.performance_style.currentIndexChanged.connect(self.refresh)
        self.calibration_enabled.toggled.connect(self.calibration_toggle)
        self.jobs.currentIndexChanged.connect(self.show_job_projects)
        self.style_scope.currentIndexChanged.connect(self.select_scope)
        for field in (self.expression_density,self.dynamics_strength,self.style_strength):field.currentIndexChanged.connect(self.persist)
        for name in ('dynamics','tension','breathiness','voicing'):getattr(self,'reference_'+name).toggled.connect(self.persist)
        self.style_pitch.toggled.connect(self.persist)
        self._sync_choices();self._update_session_context();self._reset_result_panel()
        self.loading=False;self.reload_jobs();self.refresh()
        # Keep the recent path without implicitly restoring the session.

    def resizeEvent(self,event):
        super().resizeEvent(event)
        # Reflow after QScrollArea has established the actual viewport width.
        if hasattr(self,'workspace'):self.workspace.reflow()
        if hasattr(self,'settings_columns'):self.settings_columns.reflow()
        if hasattr(self,'gate_columns'):self.gate_columns.reflow()
        self.refresh_track_geometry()

    def refresh_track_geometry(self):
        # A cell widget also loses the table item's padding. Account for both
        # layers and refresh after theme/font changes, not just on import.
        for number,row in enumerate(getattr(self,'rows',())):
            check=row['check'];check.ensurePolished()
            factor=max(1.,check.font().pixelSize()/18.)
            text_height=check.fontMetrics().height()*len(check.text().splitlines())
            self.tracks.setRowHeight(number,max(round(76*factor),text_height+round(30*factor)))

    @property
    def storage(self):return self.owner.storage
    @property
    def updates(self):return self.owner.updates
    @property
    def processing_active(self):return self.owner.processing_active or self.busy()
    def output_parent(self):return Path(self.destination.text()).expanduser().resolve()
    def busy(self):return bool(self.worker and self.worker.isRunning())

    def _sync_choices(self):
        for view in getattr(self,'_choice_views',()):view.sync()

    def _update_session_context(self):
        current=str(self.session) if self.session else '尚无处理记录'
        recent=str(self.last_session) if self.last_session else '没有历史记录'
        self.session_context.setText((_tr('处理记录已保存，可稍后继续。') if self.session else _tr('处理记录会自动保存。')) + (_tr('\n点击“恢复处理记录”可继续上次工作。') if self.last_session else ''))
        self.session_context.setToolTip(_tr('当前记录：') + current + _tr('\n最近记录：') + recent)

    def _reset_result_panel(self):
        self._run_outcome='idle'
        self.run_state.setText(_tr('尚未开始'));self.run_stage.setText(_tr('载入工程并确认来源后，点击“生成独立调音工程”。'))
        self.progress_detail.setText(_tr('等待开始调音。'))
        self._set_progress('idle')
        self.result_summary.setText(_tr('生成后可查看参数变化与工程检查结果。'))
        self.receipt_summary.setText(format_receipt_summary(None))
        self.result_location.clear();self.result_location.hide()
        self.register_gate.setText(format_register_gate(self.job,None))
        self.result_detail_toggle.setChecked(False);self.log_toggle.setChecked(False)

    def _set_stage(self,key,state):
        title=_tr(dict(_TUNING_STAGE_TITLES)[key])
        prefix,color={'done':('✓ ','#8fd6bd'),'current':('● ','#e3c386'),'error':('× ','#ed9b91'),
            'stopped':('■ ','#e3c386'),'skipped':('– ','#8aa0af')}.get(state,('○ ','#8aa0af'))
        self._stage_states[key]=state
        value=self.stage_labels[key];value.setText(prefix+title);value.setStyleSheet('color: '+color+'; font-size: 14px;')

    def _reset_stage_strip(self):
        self._stage_cursor=-1;self._observed_stages.clear()
        for key,_ in _TUNING_STAGE_TITLES:self._set_stage(key,'pending')

    def _advance_progress(self):
        if self._run_outcome!='running':
            self._progress_timer.stop();return
        self.progress.setValue(self._progress_clock.sample())

    def _set_progress(self,state):
        state=str(state or 'idle')
        self.progress.setRange(0,100);self.progress.setTextVisible(False);self.progress.setFormat('')
        if state=='idle':
            self._progress_timer.stop();self._progress_clock.reset();self.progress.setValue(0);self._reset_stage_strip();return
        if state=='running':
            self._progress_timer.stop();self.progress.setValue(self._progress_clock.start());self._reset_stage_strip()
            self._progress_timer.start();return
        self._progress_timer.stop();self._progress_clock.pause()
        if state=='complete':value=self._progress_clock.complete()
        else:value=min(95,max(0,self.progress.value()))
        self.progress.setValue(value)

    def _show_worker_stage(self,text):
        key=tuning_stage_for_message(text)
        if key is None:return
        index=_TUNING_STAGE_INDEX[key]
        advance=not (key=='save' and any(token in str(text) for token in ('复用','补全已提交')))
        if advance:
            # Completion comes only from a stage that actually appeared as
            # current. Never infer skipped work merely because it has a lower
            # position in the display strip.
            for earlier in self._observed_stages:
                if _TUNING_STAGE_INDEX[earlier]<index and self._stage_states.get(earlier)=='current':
                    self._set_stage(earlier,'done')
            self._observed_stages.add(key)
        self._set_stage(key,'current');self._stage_cursor=max(self._stage_cursor,index)

    def _mark_terminal_stage(self,state):
        key=_TUNING_STAGE_KEYS[max(0,self._stage_cursor)]
        self._set_stage(key,state)

    def _render_result_stages(self,result):
        self._reset_stage_strip();self._set_stage('snapshot','done')
        report=result.get('report') if isinstance(result.get('report'),dict) else {}
        stages=report.get('stages') if isinstance(report.get('stages'),list) else []
        if report.get('primary') is not None or 'primary' in stages:self._set_stage('source','done')
        else:self._set_stage('source','skipped')
        if 'style' in stages:self._set_stage('style','done')
        else:self._set_stage('style','skipped')
        density=report.get('expression_density')
        if isinstance(density,dict) or report.get('protected_content_unchanged') is True:self._set_stage('protection','done')
        else:self._set_stage('protection','skipped')
        if result.get('receipt_verified') is True:self._set_stage('save','done')
        elif result.get('commit_state') in ('committed_unreceipted','committed_unreported','committed') or result.get('report_pending') is True:
            self._set_stage('save','pending')
        elif result.get('receipt_verified') is False:self._set_stage('save','error')
        else:self._set_stage('save','pending')
        if report_ready(result):self._set_stage('report','done')
        elif result.get('report_pending') is True or result.get('report_verified') is False:self._set_stage('report','pending')
        else:self._set_stage('report','pending')

    def _worker_message(self,text):
        value=str(text or '').strip()
        if not value:return
        self.log.appendPlainText(value)
        if self._run_outcome!='running':return
        first=value.splitlines()[0].strip()
        if first:
            stage=tuning_stage_for_message(first)
            detail={'snapshot':'正在准备工程…','source':'正在应用参数…','style':'正在调整演唱风格…',
                'protection':'正在处理曲线并检查音符…','save':'正在保存工程…','report':'正在检查结果…'}.get(stage,first)
            self.progress_detail.setText(detail);self.run_stage.setText(detail);self.status.setText(detail);self._show_worker_stage(first)

    def _failed(self,message):
        value=str(message or '处理未完成；请查看处理日志。')
        if self._run_outcome not in ('running','cancelling'):
            self.log.appendPlainText('迟到的失败信息未覆盖当前状态：'+value);return
        cancelled=self._run_outcome=='cancelling' or any(token in value for token in ('已停止','已取消','InterruptedError'))
        self._run_outcome='cancelled' if cancelled else 'failed'
        self._set_progress(self._run_outcome)
        self.status.setText((_tr('已停止；工程与处理记录已保留。') if cancelled else value))
        self.run_state.setText(_tr('已停止') if cancelled else _tr('执行未完成'))
        detail='工程与处理记录已保留，可稍后继续。' if cancelled else '失败原因见处理日志与结果明细。'
        self.progress_detail.setText(detail);self.run_stage.setText(detail);self._mark_terminal_stage('stopped' if cancelled else 'error')
        self.log.appendPlainText(('已停止：' if cancelled else '处理失败：')+value)

    @staticmethod
    def _set_message(field,text,error=False):
        field.setText(text);field.setProperty('role','error' if error else 'muted')
        field.style().unpolish(field);field.style().polish(field)

    def apply_preferences(self,values):
        for name in ('dynamics_strength','expression_density','performance_style','style_strength','style_scope'):
            field=getattr(self,name);value=values.get(name,field.currentData())
            if name in ('dynamics_strength','style_strength'):
                if normalized_strength(value) is not None:set_combo_strength(field,value)
            elif field.findData(value)>=0:field.setCurrentIndex(field.findData(value))
        for name in ('reference_dynamics','reference_tension','reference_breathiness','reference_voicing','style_pitch'):
            getattr(self,name).setChecked(values.get(name) is True)

    def preferences(self):
        names=('dynamics_strength','expression_density','performance_style','style_strength','style_scope')
        values={name:getattr(self,name).currentData() for name in names}
        # Scope is a shortcut for new inputs. Explicit track_indices own a
        # custom selection in the execution/session contract.
        if values['style_scope']=='custom':values['style_scope']='lead'
        values.update({name:getattr(self,name).isChecked() for name in ('reference_dynamics','reference_tension','reference_breathiness','reference_voicing','style_pitch')})
        return values

    def persist(self,*args):
        if self.loading or environment_value('VWA_TESTING')=='1':return
        if self.session:self.last_session=str(self.session)
        state=workspace_state();state['tuning']={'preferences':self.preferences(),'session':self.last_session}
        atomic_json(ROOT/'cache/workspace.json',state)

    def refresh(self,*args):
        mode=self.mode.currentData()
        self.reference_panel.setVisible(mode=='reference');self.calibration_panel.setVisible(mode=='calibrated');self.retained_panel.setVisible(mode=='retained')
        self.calibration_enabled.blockSignals(True);self.calibration_enabled.setChecked(mode=='calibrated');self.calibration_enabled.blockSignals(False)
        self.dynamics_strength.setEnabled(mode in ('reference','calibrated'))
        for column in (1,2,3):self.tracks.setColumnHidden(column,mode not in ('reference','calibrated'))
        self.tracks.setColumnHidden(4,mode!='retained')
        mapped=mode in ('reference','calibrated','retained')
        self.tracks.horizontalHeader().setSectionResizeMode(0,QHeaderView.Interactive if mapped else QHeaderView.Stretch)
        if mapped and self.tracks.columnWidth(0)<300:self.tracks.setColumnWidth(0,300)
        style=self.performance_style.currentData()!='none'
        self.style_strength.setEnabled(style);self.style_pitch.setEnabled(style);self.style_scope.setEnabled(True)
        self._sync_choices();self._update_session_context();self.sync_scope_label()
        self.register_gate.setText(format_register_gate(self.job,self.result))
        self.register_button.setEnabled(register_ready(self.job,self.result))
        self.persist()

    def calibration_toggle(self,checked):
        if checked:self.mode.setCurrentIndex(self.mode.findData('calibrated'))
        elif self.mode.currentData()=='calibrated':self.mode.setCurrentIndex(self.mode.findData('preserve'))

    def reload_jobs(self):
        selected=self.jobs.currentData();self.jobs.blockSignals(True);self.jobs.clear();self.jobs.addItem(_tr('选择已有准备任务'),None)
        library=getattr(self.owner,'results',None)
        for row in getattr(library,'rows',[]):
            self.jobs.addItem(str(row['title'])+' · '+self._task_time(row),str(row['path']))
        if self.owner.job and self.jobs.findData(str(self.owner.job))<0:
            row=self._task_row(self.owner.job)
            self.jobs.addItem(str(row['title'])+' · '+self._task_time(row),str(self.owner.job))
        self.jobs.setCurrentIndex(max(0,self.jobs.findData(selected)));self.jobs.blockSignals(False);self.show_job_projects()

    @staticmethod
    def _task_time(row):
        from results_ui import task_time
        return task_time(row)

    def _task_row(self,job):
        from results_ui import task_record
        return task_record(self.owner,job)

    def refresh_task_context(self,*args):
        if self.job:
            row=self._task_row(self.job)
            self.input_summary.setText(str(row['title']) + '\n' + self._task_time(row) + ' · ' + self._task_elapsed(row) + '\n' + str(len(self.rows)) + _tr(' 条演唱轨 · ') + str(sum((item['note_count'] for item in self.rows))) + _tr(' 个音符'))

    @staticmethod
    def _task_elapsed(row):
        from results_ui import task_elapsed
        return task_elapsed(row)

    def show_job_projects(self,*args):
        self.projects.clear();value=self.jobs.currentData()
        if not value:return
        try:
            from tuning_workflow import job_projects
            for row in job_projects(value):self.projects.addItem(row['label'] + (_tr(' · 已校验') if row['verified'] else _tr(' · 待检查')),row['path'])
        except Exception as error:self.status.setText(str(error))

    def choose_job(self):
        if self.busy():return
        from results_ui import TaskPickerDialog
        def choices(row):
            from tuning_workflow import job_projects
            projects=job_projects(row['path'])
            if not projects:raise ValueError('这个任务还没有可用的 SVP；请先导出工程，或使用本地工程。')
            return [{'label':project['label']+('' if project['verified'] else ' · 待检查'),'value':project['path']} for project in projects]
        dialog=TaskPickerDialog(self.owner,self,title='从任务与结果选择工程',selected=self.job,choices=choices,confirm_text='使用此工程')
        self.task_picker_dialog=dialog
        try:
            if dialog.exec_()==QDialog.Accepted:
                selected=dialog.selection()
                if selected:self.load_project(selected['value'],job=selected['row']['path'])
        finally:
            dialog.deleteLater();self.task_picker_dialog=None

    def load_job_project(self):
        if self.projects.currentData():self.load_project(self.projects.currentData(),job=self.jobs.currentData())

    def choose_project(self):
        value,_=QFileDialog.getOpenFileName(self,_tr('导入工程'),self.project.text(),'Synthesizer V (*.svp)')
        if value:self.load_project(value)

    def import_current(self):self.load_project(self.project.text())

    def load_project(self,path,*,job=None):
        try:
            if self.busy() or bool(getattr(self,'calibration_dialog',None) and self.calibration_dialog.busy()):
                raise ValueError('请先停止当前调音或校准，再更换输入工程。')
            from tuning_workflow import inspect_project
            info=inspect_project(path);self.set_input(info,job=job);self.session=None;self.result=None
            self._update_session_context();self._reset_result_panel()
            self.status.setText(_tr('工程已载入，请选择轨道和参数来源。'));self.open_button.setEnabled(False);self.register_button.setEnabled(False)
            return True
        except Exception as error:self.status.setText(str(error));return False

    def set_input(self,info,*,job=None,display_path=None):
        self.input_path=Path(display_path or info['path']);self.job=Path(job) if job else None;self.project.setText(str(self.input_path))
        self.rows=[];self.tracks.setRowCount(0)
        vocals=[row for row in info['tracks'] if not row['instrumental']]
        title=self.input_path.name
        context=''
        if self.job:
            task=self._task_row(self.job);title=str(task['title']);context=self._task_time(task)+' · '+self._task_elapsed(task)+'\n'
        self.input_summary.setText(title + '\n' + context + str(len(vocals)) + _tr(' 条演唱轨 · ') + str(sum((row['note_count'] for row in vocals))) + _tr(' 个音符'))
        self.input_summary.setToolTip(str(self.input_path)+'\nSHA-256 '+info['sha256'])
        for ordinal,item in enumerate(vocals):
            number=self.tracks.rowCount();self.tracks.insertRow(number)
            check=QCheckBox(str(item['track_index'] + 1) + ' · ' + item['name'] + _tr('\n{0} 个音符', f"{item['note_count']}"));check.setChecked(ordinal==0)
            check.setToolTip(item['name'] + _tr(' · {0} 个音符\n勾选后作为本次调音的实际目标。', f"{item['note_count']}"));self.tracks.setCellWidget(number,0,check)
            role=combo([(_tr('主唱'), 'lead'), (_tr('和声'), 'backing'), (_tr('合并人声'), 'vocals')]);role.setCurrentIndex(min(ordinal,1));self.tracks.setCellWidget(number,1,role)
            reference=QLineEdit();reference.setPlaceholderText(_tr('选择此轨对应的参考音频'));self.tracks.setCellWidget(number,2,reference)
            if self.job:
                audio=self.job/'audio'/(role.currentData()+'.wav')
                if audio.is_file():reference.setText(str(audio))
            choose=button(_tr('选择音频'),lambda checked=False,field=reference:self.choose_audio(field));self.tracks.setCellWidget(number,3,choose)
            source_track=QSpinBox();source_track.setRange(1,999);source_track.setValue(ordinal+1);source_track.setToolTip(_tr('原参数工程中的实际轨号'));self.tracks.setCellWidget(number,4,source_track)
            row={'track_index':item['track_index'],'name':item['name'],'note_count':item['note_count'],
                'check':check,'voice':role,'path':reference,'source_track':source_track}
            self.rows.append(row);check.toggled.connect(self.sync_scope_label)
        self.refresh_track_geometry();self._sync_row_controls();self.sync_scope_label()

    def select_scope(self,*args):
        scope=self.style_scope.currentData()
        if scope=='custom':return
        self._selecting_scope=True
        try:
            for ordinal,row in enumerate(self.rows):row['check'].setChecked(scope=='all_vocals' or ordinal==0)
        finally:self._selecting_scope=False
        self.sync_scope_label()

    def _sync_row_controls(self):
        for row in self.rows:
            selected=row['check'].isChecked()
            row['voice'].setEnabled(selected);row['path'].setEnabled(selected);row['source_track'].setEnabled(selected)

    def _selected_rows(self):return [row for row in self.rows if row['check'].isChecked()]

    @staticmethod
    def _file_ready(value):
        try:return bool(str(value).strip()) and Path(str(value)).expanduser().is_file()
        except (OSError,ValueError):return False

    def _mapping_message(self):
        mode=self.mode.currentData();selected=self._selected_rows()
        if not self.rows:return '尚未载入演唱轨；请选择可处理的 SVP。'
        if not selected:return '至少勾选一条演唱轨，执行时会以实际 track_indices 为准。'
        track_label=lambda row:f"#{row['track_index']+1} {row.get('name','')}".strip()
        targets=[row['track_index'] for row in selected]
        if len(targets)!=len(set(targets)):return '目标轨映射重复：每条目标轨只能出现一次。'
        if mode in ('reference','calibrated'):
            missing=[track_label(row) for row in selected if not row['path'].text().strip()]
            unreadable=[track_label(row) for row in selected if row['path'].text().strip() and not self._file_ready(row['path'].text())]
            if missing:return '参考音频缺项：'+'、'.join(missing)+'。每条所选轨都必须单独对应音频。'
            if unreadable:return '参考音频不可读取：'+'、'.join(unreadable)+'。请重新选择或检查路径。'
            if mode=='calibrated' and not self.calibration_file.text().strip():
                return '校准配置缺失：当前乐谱与声库对应的配置尚未选择。'
            if mode=='calibrated' and not self._file_ready(self.calibration_file.text()):
                return '校准配置不可读取：请重新选择当前乐谱对应的 JSON。'
            return '参考映射已覆盖所选轨；生成前会由引擎复核音频、角色和时间对应。'
        if mode=='retained':
            if not self.retained_project.text().strip():return '沿用工程缺失：请选择同时间的原参数 SVP。'
            if not self._file_ready(self.retained_project.text()):return '沿用工程不可读取：请重新选择原参数 SVP。'
            return '沿用目标：'+'、'.join(track_label(row)+' → 来源 #'+str(row['source_track'].value()) for row in selected)+'。'
        return '当前为保留原表现；曲线密度仍可独立执行。'

    def _mapping_error(self):
        mode=self.mode.currentData();selected=self._selected_rows()
        if mode not in ('reference','calibrated','retained') or not selected:return False
        if mode in ('reference','calibrated'):
            return (any(not self._file_ready(row['path'].text()) for row in selected) or
                (mode=='calibrated' and not self._file_ready(self.calibration_file.text())))
        return not self._file_ready(self.retained_project.text())

    def _update_mapping_hint(self):
        self._set_message(self.mapping_hint,self._mapping_message(),self._mapping_error())

    def sync_scope_label(self,*args):
        if self._selecting_scope:return
        selected=[i for i,row in enumerate(self.rows) if row['check'].isChecked()]
        scope='lead' if not self.rows or selected==[0] else 'all_vocals' if len(selected)==len(self.rows) else 'custom'
        blocked=self.style_scope.blockSignals(True)
        try:self.style_scope.setCurrentIndex(self.style_scope.findData(scope))
        finally:self.style_scope.blockSignals(blocked)
        selected_rows=self._selected_rows()
        if not self.rows:self.scope_summary.setText(_tr('尚未载入演唱轨。'))
        elif selected_rows:
            labels='、'.join(f"#{row['track_index']+1} {row.get('name','')}" for row in selected_rows)
            self.scope_summary.setText(_tr('已选 ') + str(len(selected_rows)) + '/' + str(len(self.rows)) + _tr(' 条演唱轨：') + labels)
        else:self.scope_summary.setText(_tr('已选 0/') + str(len(self.rows)) + _tr(' 条演唱轨；执行前请勾选目标轨。'))
        self._sync_row_controls();self._update_mapping_hint();self._sync_choices();self.persist();self._update_session_context()

    def choose_audio(self,field):
        value,_=QFileDialog.getOpenFileName(self,_tr('选择对应参考人声'),field.text(),_tr('音频 (*.wav *.flac *.mp3 *.ogg)'))
        if value:field.setText(value)

    def choose_destination(self):
        value=QFileDialog.getExistingDirectory(self,_tr('选择工程保存目录'),self.destination.text())
        if value:self.destination.setText(value)

    def choose_profile(self):
        value,_=QFileDialog.getOpenFileName(self,_tr('选择当前乐谱的校准配置'),self.calibration_file.text(),_tr('校准配置 (*.json)'))
        if value:self.calibration_file.setText(value)

    def choose_retained(self):
        value,_=QFileDialog.getOpenFileName(self,_tr('选择同时间的原参数工程'),self.retained_project.text(),'SVP (*.svp)')
        if value:self.retained_project.setText(value)

    def choose_session(self):
        value,_=QFileDialog.getOpenFileName(self,_tr('恢复调音处理记录'),str(self.last_session or self.output_parent()),_tr('处理记录 (session.json)'))
        if value:self.restore_session(value)

    def restore_session(self,value):
        try:
            if self.busy() or bool(getattr(self,'calibration_dialog',None) and self.calibration_dialog.busy()):
                raise ValueError('请先停止当前调音或校准，再载入其他会话。')
            from tuning_workflow import read_session,inspect_project
            path=Path(value);spec=read_session(path)
            self.set_input(inspect_project(path.parent/spec['input_file']),job=spec.get('job'),display_path=spec['source']);self.session=path;self.result=None;self._update_session_context();self._reset_result_panel()
            options=path.parent/'last-options.json'
            if options.is_file():
                values=json.loads(options.read_text(encoding='utf-8'));self.apply_preferences(values)
                self.mode.setCurrentIndex(self.mode.findData(values['expression_mode']));self.calibration_file.setText(values.get('reference_calibration') or '')
                retained=values.get('retained_sources',[])
                self.retained_project.setText(values.get('retained_project') or (retained[0]['project'] if retained else ''))
                for row in self.rows:
                    row['check'].setChecked(row['track_index'] in (values.get('track_indices') or []))
                    mapping=next((item for item in values.get('reference_sources',[]) if item['track_index']==row['track_index']),None)
                    if mapping:row['voice'].setCurrentIndex(row['voice'].findData(mapping['voice']));row['path'].setText(mapping['path'])
                    prior=next((item for item in retained if item['track_index']==row['track_index']),None)
                    if prior:row['source_track'].setValue(prior.get('source_track_index',0)+1)
                    elif values.get('retained_project'):row['source_track'].setValue(values.get('retained_track_index',0)+1)
            self.open_button.setEnabled(False);self.register_button.setEnabled(False);self.refresh();self.status.setText(_tr('处理记录已恢复，可继续生成工程。'))
            return True
        except Exception as error:self.status.setText(str(error));return False

    def options(self):
        mode=self.mode.currentData();values=self.preferences()
        values['expression_mode']=mode;values['track_indices']=[row['track_index'] for row in self.rows if row['check'].isChecked()]
        for name in ('dynamics','tension','breathiness','voicing'):values['reference_'+name]=mode=='reference' and getattr(self,'reference_'+name).isChecked()
        if mode in ('reference','calibrated'):values['reference_sources']=[{'track_index':row['track_index'],'voice':row['voice'].currentData(),'path':row['path'].text()} for row in self.rows if row['check'].isChecked()]
        if mode=='calibrated':values['reference_calibration']=self.calibration_file.text()
        if mode=='retained':values['retained_sources']=[{'track_index':row['track_index'],'project':self.retained_project.text(),
            'source_track_index':row['source_track'].value()-1} for row in self.rows if row['check'].isChecked()]
        from tuning_engine import settings
        return settings(values)

    def start_run(self):
        if self.busy():return
        calibration=getattr(self,'calibration_dialog',None)
        if self.owner.processing_active or self.storage.busy() or self.updates.busy() or (calibration and calibration.busy()):
            self.status.setText(_tr('请先完成或停止当前处理、校准或缓存操作。'));return
        try:
            if self.input_path is None or self.project.text().strip()!=str(self.input_path):raise ValueError('请先载入工程；手动修改路径后点击重新导入。')
            options=self.options()
            if self.session is None:
                from tuning_workflow import create_session
                destination=self.output_parent()/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
                self.session=create_session(self.input_path,destination,job=self.job)
            self.persist();self.result=None;self._update_session_context();self._reset_result_panel();self.form.setEnabled(False);self.apply_button.setEnabled(False)
            self._run_outcome='running';self.run_state.setText(_tr('正在生成调音工程'));self.run_stage.setText(_tr('正在准备工程…'))
            self.progress_detail.setText(_tr('正在准备工程…'));self.status.setText(_tr('正在生成独立调音工程…'));self._set_progress('running')
            if options.get('performance_style')=='none' or options.get('style_strength',0)<=0:self._set_stage('style','skipped')
            self._sync_choices();self.open_button.setEnabled(False);self.register_button.setEnabled(False);self.stop_button.setEnabled(True)
            self.worker=TuningWorker(self.session,options,self);self.worker.message.connect(self._worker_message)
            self.worker.succeeded.connect(self.completed);self.worker.failed.connect(self._failed);self.worker.finished.connect(self.finished);self.worker.start()
            self.owner.update_component_state()
        except Exception as error:self.status.setText(str(error))

    def completed(self,result):
        self.result=result if isinstance(result,dict) else {}
        if self._run_outcome in ('cancelling','cancelled'):
            self.log.appendPlainText('已请求停止后收到迟到结果，未覆盖停止状态。');return
        status=str(self.result.get('status') or (self.result.get('report') or {}).get('status') or 'unknown')
        state={'applied':'已应用','partial':'部分应用，跳过的局部保留原表现','unchanged':'保留原样'}.get(status,status)
        verified=self.result.get('receipt_verified') is True
        partial=status.startswith('partial')
        ready=register_ready(self.job,self.result)
        report_complete=report_ready(self.result)
        if partial:self._run_outcome='partial'
        elif report_complete and status in ('applied','unchanged'):self._run_outcome='complete'
        elif not verified:self._run_outcome='pending_receipt'
        elif not report_complete:self._run_outcome='pending_report'
        else:self._run_outcome='unconfirmed'
        self._set_progress(self._run_outcome);self._render_result_stages(self.result)
        if not verified:
            message='工程已保存，请再次生成以补全结果记录。'
            if partial:message='部分应用；'+message
            self.run_state.setText((_tr('部分应用 · ') if partial else _tr('工程已保存 · ')) + _tr('结果记录待补全'))
        else:
            message=('已读取保存的结果' if self.result.get('cached') else '已生成调音工程')+' · '+state
            if self.result.get('observer_errors'):message+='；部分处理记录未保存，工程检查已通过。'
            if not report_complete:message+='；请再次生成以补全报告，暂不可登记。'
            elif not self.job:message+='；未关联准备任务，工程可独立使用。'
            if partial:prefix='部分应用 · '
            elif not report_complete:prefix='报告待补全 · '
            elif status in ('applied','unchanged'):prefix='工程已生成 · '
            else:prefix='结果状态未确认 · '
            self.run_state.setText(prefix + _tr('检查已通过'))
        output=str(self.result.get('output') or '')
        self.status.setText(message)
        detail={'complete':'处理完成 · 可打开工程','partial':'部分应用 · 请查看结果明细',
            'pending_receipt':'工程已保存 · 请补全结果记录','pending_report':'工程已检查 · 请补全报告',
            'unconfirmed':'结果状态未确认 · 请查看日志'}.get(self._run_outcome,'处理已结束')
        self.progress_detail.setText(detail);self.run_stage.setText(detail + _tr('；请展开“结果明细”查看。'))
        report=self.result.get('report') if isinstance(self.result.get('report'),dict) else {}
        density=report.get('expression_density') if isinstance(report.get('expression_density'),dict) else {}
        self.result_summary.setText(format_tuning_result_summary(self.result))
        self.receipt_summary.setText(format_receipt_summary(self.result))
        self.register_gate.setText(format_register_gate(self.job,self.result))
        if output:self.result_location.setText(_tr('结果工程：') + output);self.result_location.show()
        else:self.result_location.clear();self.result_location.hide()
        self.log.appendPlainText('曲线处理：新增 '+str(_integer(density.get('added_points')))+' 个采样点；非线性保留 '+str(_integer(density.get('unsupported_nonlinear_controls')))+' 项；浮点时间保留 '+str(_integer(density.get('unsupported_fractional_time_controls')))+' 项。')
        receipt=str(self.result.get('receipt') or '')
        self.log.appendPlainText(('工程回执：' if verified else '待补全回执：')+(receipt or '未提供'))
        self.open_button.setEnabled(bool(output));self.register_button.setEnabled(ready)

    def finished(self):
        if self._run_outcome=='running':
            self._failed('未能取得处理结果，请查看日志后重试。')
        elif self._run_outcome=='cancelling':
            self._run_outcome='cancelled';self.run_state.setText(_tr('已停止'))
            self._set_progress('cancelled');detail='工程与处理记录已保留，可稍后继续。'
            self.progress_detail.setText(detail);self.run_stage.setText(detail);self._mark_terminal_stage('stopped')
        elif self._run_outcome not in ('partial','complete','pending_receipt','pending_report','unconfirmed','failed','cancelled'):
            # Session creation happens after the UI has switched to busy, so a
            # persistent failure must also survive the worker-started guard.
            detail='处理未启动；检查输入、会话目录与处理日志。'
            self.progress_detail.setText(detail);self.run_stage.setText(detail)
        self.form.setEnabled(True);self.apply_button.setEnabled(True);self.stop_button.setEnabled(False);self._sync_choices();self.refresh()
        self.owner.update_component_state()

    def cancel(self):
        if self.busy():
            self.worker.stop.set();self._run_outcome='cancelling';self.stop_button.setEnabled(False);self._set_progress('cancelling')
            self.status.setText(_tr('正在停止，工程与处理记录会保留。'));self.run_state.setText(_tr('正在停止…'))
            detail='正在停止，请稍候。';self.progress_detail.setText(detail);self.run_stage.setText(detail)

    def closeEvent(self,event):
        self._progress_timer.stop();self._progress_clock.pause();super().closeEvent(event)

    def open_result(self):
        output=(self.result or {}).get('output')
        if output:QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))

    def register(self):
        try:
            if not register_ready(self.job,self.result):
                raise ValueError('请先关联任务，并补全工程检查和处理报告。')
            from tuning_workflow import register_result
            path=register_result(self.session,job=self.job);self.status.setText(_tr('已登记调音工程，后续合成仍由你完成：') + str(path))
        except Exception as error:self.status.setText(str(error))

    def open_calibration(self):
        if self.busy():return
        from calibration_ui import CalibrationDialog
        dialog=getattr(self,'calibration_dialog',None)
        if dialog is None:dialog=CalibrationDialog(self);self.calibration_dialog=dialog;self.owner.calibration_dialog=dialog
        if self.input_path:
            if not self.input_path.is_file():
                self.status.setText(_tr('原工程已移动。调音可从快照继续；准备新校准时请重新选择已指定声库的当前工程。'));return
            dialog.project.setText(str(self.input_path))
            selected=next((i for i,row in enumerate(self.rows) if row['check'].isChecked()),0);dialog.track.setCurrentIndex(selected)
            if self.rows:dialog.reference.setText(self.rows[selected]['path'].text());dialog.voice.setCurrentIndex(dialog.voice.findData(self.rows[selected]['voice'].currentData()))
        dialog.show();dialog.raise_()
