"""Single-export calibration dialog; SV audio rendering stays user-triggered."""
from i18n import tr as _tr
from datetime import datetime
import json
from pathlib import Path
import threading
from PyQt5.QtCore import QThread,pyqtSignal,QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QWidget,QLineEdit,QComboBox,QFileDialog,QProgressBar,QScrollArea,QPlainTextEdit,QTableWidget,QTableWidgetItem,QHeaderView,QApplication,QInputDialog
from desktop_theme import button,card,label
from ui_components import AdaptiveColumns,FlowLayout,choice
from strength_control import PercentStrengthControl,normalized_strength,set_combo_strength


_SESSION_PATTERN = 'calibration*/session.json'


def _task_calibration_sessions(row):
    """Return only live, direct calibration records owned by one task row."""
    value = row.get('path') if isinstance(row, dict) else None
    if not value:
        return []
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return []
    if not root.is_dir():
        return []
    found = {}
    try:
        candidates = root.glob(_SESSION_PATTERN)
        for candidate in candidates:
            try:
                session = candidate.resolve(strict=True)
                if session.is_file() and session.parent.parent == root:
                    found[session] = candidate.stat().st_mtime_ns
            except (OSError, RuntimeError, ValueError):
                continue
    except (OSError, RuntimeError):
        return []
    return sorted(found, key=lambda path: (found[path], str(path).casefold()), reverse=True)


def _calibration_session_label(session):
    """Keep full paths out of the task picker and session chooser labels."""
    session = Path(session)
    voice = '声部未知'
    try:
        spec = json.loads(session.read_text(encoding='utf-8-sig'))
        if isinstance(spec, dict):
            voice = {'lead': '主唱', 'backing': '和声', 'vocals': '合并人声'}.get(spec.get('voice'), voice)
    except (OSError, TypeError, ValueError):
        pass
    if (session.parent / 'calibrated.svp').is_file():
        stage = '已有校准工程'
    elif (session.parent / 'fit-report.json').is_file():
        stage = '有拟合报告（待检查）'
    elif (session.parent / 'render-receipt.json').is_file():
        stage = '有试音记录（待检查）'
    else:
        stage = '等待试音'
    try:
        updated = datetime.fromtimestamp(session.stat().st_mtime).strftime('%m-%d %H:%M')
    except (OSError, OverflowError, ValueError):
        updated = '时间未记录'
    return f'{session.parent.name} · {voice} · {stage} · {updated}'


def _task_calibration_choices(row):
    sessions = _task_calibration_sessions(row)
    if not sessions:
        raise ValueError('此任务没有可恢复的校准记录；请先准备试音，或从“本地校准记录…”选择已有记录。')
    return [{'label': _calibration_session_label(session), 'value': str(session)}
            for session in sessions]


def _selected_task_calibration(row, value):
    try:
        selected = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ValueError('所选校准记录已移动或不可读取，请重新选择任务。') from error
    for session in _task_calibration_sessions(row):
        if session == selected:
            return session
    raise ValueError('所选校准记录不属于该任务或已移动，请重新选择。')


class CalibrationWorker(QThread):
    message=pyqtSignal(str)
    succeeded=pyqtSignal(object)
    failed=pyqtSignal(str)
    state=pyqtSignal(object)

    def __init__(self,session,folder,parent,channel_mode='standard',*,output_name='calibrated.svp',strength=None):
        super().__init__(parent);self.session,self.folder=session,folder;self.channel_mode=channel_mode;self.stop=threading.Event()
        self.output_name,self.strength=output_name,strength

    def check(self):
        if self.stop.is_set():raise InterruptedError('已停止等待／拟合，校准记录和试音文件已保留。')

    def run(self):
        try:
            from calibration_session import wait_and_fit
            self.succeeded.emit(wait_and_fit(self.session,self.folder,cancel=self.check,progress=self.message.emit,
                                             channel_mode=self.channel_mode,output_name=self.output_name,strength=self.strength,
                                             on_state=self.state.emit))
        except Exception as error:self.failed.emit(str(error))


class PreparationWorker(CalibrationWorker):
    def __init__(self,parent,**options):
        QThread.__init__(self,parent);self.options=options;self.stop=threading.Event()

    def run(self):
        try:
            from calibration_workflow import prepare_copy
            self.succeeded.emit(prepare_copy(**self.options,cancel=self.check,progress=self.message.emit))
        except Exception as error:self.failed.emit(str(error))


class CalibrationDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.worker=None;self.session=None;self.result=None;self.closing=False;self.auto_renders=None
        self.setWindowTitle(_tr('声库校准 · 一次分轨导出'));self.resize(1160,880);self.setMinimumSize(780,620)
        outer=QVBoxLayout(self);outer.setContentsMargins(22,18,22,16);outer.setSpacing(14)
        outer.addWidget(label(_tr('声库校准'),'heading'))
        outer.addWidget(label(_tr('选用带声库设置的演唱工程和同声库参考演唱，检查后一次导出七轨试音。'),'muted'))
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(0)
        content=QWidget();body=QVBoxLayout(content);body.setContentsMargins(4,4,4,8);body.setSpacing(18)
        left=QWidget();left_column=QVBoxLayout(left);left_column.setContentsMargins(0,0,0,0);left_column.setSpacing(18)
        right=QWidget();right_column=QVBoxLayout(right);right_column.setContentsMargins(0,0,0,0);right_column.setSpacing(18)
        adaptive=AdaptiveColumns(left,right,breakpoint=920,ratio=(6,5));body.addWidget(adaptive);body.addStretch()
        scroll.setWidget(content);outer.addWidget(scroll,1)

        method,method_area=card(_tr('01 · 校准方式'),_tr('1 条基准与 6 条单一参数试音；在 SV 一次分轨导出。'))
        left_column.addWidget(method)
        channel_row=QHBoxLayout();channel_row.addWidget(label(_tr('SV 气声输出'),'muted'));channel_row.addStretch()
        channel_row.addWidget(button(_tr('查看校准步骤'),lambda:self.logic_text.setVisible(not self.logic_text.isVisible())));method_area.addLayout(channel_row)
        self.channels=QComboBox()
        for title,mode in [('无 · 普通单／双声道','standard'),('增加声道输出气声 · 完整人声 + 气声','sv-extra'),
                           ('增加声道输出气声（隔离）· 周期声 + 气声','sv-isolated')]:self.channels.addItem(_tr(title),mode)
        self.channels.setToolTip(_tr('与 SV 保持一致。四声道为交错排列：第 1、3 声道是主声，第 2、4 声道是气声。追加模式不重复叠加气声。'))
        self.channels_choice=choice(self.channels,columns=1);method_area.addWidget(self.channels_choice)
        self.logic_text=label(_tr('① 选择 1 个带声库设置的演唱工程、对应的参考演唱和保存位置。\n② 点击“检查工程与歌词”，先处理“需要处理”的项目，再复核提示位置。\n③ 准备七轨试音，在 SV 一次分轨导出到同一目录。\n④ 回到本窗口等待拟合，完成后打开校准工程试听。\n工程需已指定声库；参考演唱须从零秒覆盖所选演唱轨。\n七轨包含 1 条基准和 6 条单一参数试音，须在同一次导出中生成。\n拟合后另存工程，保留原文件。\n只拟合所选演唱轨，完成后请试听。'),'muted')
        self.logic_text.hide();method_area.addWidget(self.logic_text)
        self.pitch_warning=label('','error');self.pitch_warning.hide();method_area.addWidget(self.pitch_warning)

        inputs,inputs_area=card(_tr('02 · 输入与目标'),_tr('准备或修正会另存副本，原工程与参考音频保持不变。'))
        left_column.addWidget(inputs)
        self.form=QWidget();form=QVBoxLayout(self.form);form.setContentsMargins(0,0,0,0);form.setSpacing(10);inputs_area.addWidget(self.form)
        self.project=QLineEdit();self.reference=QLineEdit();self.folder=QLineEdit()
        self.project.setPlaceholderText(_tr('选择演唱工程（SVP），需已指定声库'))
        self.project.setToolTip(_tr('需包含演唱轨并已指定声库。缺少声库设置时，可先使用“从 SVP 读取声库设置”。'))
        self.reference.setPlaceholderText(_tr('选择参考演唱，从零秒覆盖所选演唱轨'))
        self.reference.setToolTip(_tr('需为普通单／双声道音频，从零秒覆盖所选演唱轨全程。'))
        for field,title,pattern in ((self.project,'待拟合工程','SVP (*.svp)'),(self.reference,'参考演唱','音频 (*.wav *.flac)')):
            form.addWidget(label(title,'muted'));row=QHBoxLayout();row.addWidget(field,1)
            def choose(checked=False,field=field,title=title,pattern=pattern):
                value,_=QFileDialog.getOpenFileName(self,title,field.text(),pattern)
                if value:field.setText(value)
            row.addWidget(button(_tr('选择'),choose));form.addLayout(row)
        target_row=QHBoxLayout();target_row.addWidget(label(_tr('演唱轨'),'muted'));self.track=QComboBox();target_row.addWidget(self.track,1)
        target_row.addWidget(label(_tr('对应声部'),'muted'));self.voice=QComboBox()
        for title,value in [('主唱','lead'),('和声','backing'),('合并人声','vocals')]:self.voice.addItem(_tr(title),value)
        target_row.addWidget(self.voice);form.addLayout(target_row)
        form.addWidget(label(_tr('保存位置'),'muted'));folder_row=QHBoxLayout();folder_row.addWidget(self.folder,1)
        def choose_folder():
            value=QFileDialog.getExistingDirectory(self,_tr('保存校准记录'),self.folder.text())
            if value:self.folder.setText(value)
        folder_row.addWidget(button(_tr('选择'),choose_folder));form.addLayout(folder_row)
        strength_row=QHBoxLayout();strength_label=label(_tr('拟合强度'),'muted');strength_row.addWidget(strength_label)
        initial_strength=normalized_strength(owner.dynamics_strength.currentData())
        if initial_strength is None:initial_strength=.6
        self.strength_control=PercentStrengthControl(initial_strength*100,minimum=1);self.strength=self.strength_control.spin
        self.strength_slider=self.strength_control.slider
        self.strength.setToolTip(_tr('设置拟合变化的应用幅度；滑条按 1% 调整。'))
        strength_label.setBuddy(self.strength);strength_row.addWidget(self.strength_control,1);form.addLayout(strength_row)
        action_row=QHBoxLayout();self.check_inputs=button(_tr('检查工程与歌词'),self.review_inputs);action_row.addWidget(self.check_inputs)
        self.read_bank=button(_tr('从 SVP 读取声库设置'),self.choose_bank);action_row.addWidget(self.read_bank)
        self.repair_pitch=button(_tr('仅重建连续音高'),lambda:self.prepare_input(repair=True));action_row.addWidget(self.repair_pitch);form.addLayout(action_row)
        form.addWidget(button(_tr('在 SV 修正乐谱／声库'),self.open_source))
        form.addWidget(label(_tr('歌词差异只标出位置供试听修正，不会按字数拆分音符。'),'muted'))
        self.review_text=QPlainTextEdit();self.review_text.setReadOnly(True);self.review_text.setMaximumHeight(180)
        self.review_text.setPlaceholderText(_tr('点击“检查工程与歌词”，核对声库、时间范围、音高和歌词。'));form.addWidget(self.review_text)
        left_column.addStretch()

        render,render_area=card(_tr('03 · 在 SV 中分轨导出'),_tr('七轨均选中，关闭 MixDown，从零秒导出到相同终点，保持干声。'))
        right_column.addWidget(render)
        render_area.addWidget(label(_tr('不使用归一化、压缩或混响。已有试音时可调整拟合强度后另存结果。'),'muted'))
        renders_row=QHBoxLayout();renders_row.addWidget(label(_tr('导出目录'),'muted'));self.renders=QLineEdit();renders_row.addWidget(self.renders,1)
        self.renders.setPlaceholderText(_tr('留空时使用本次校准的试音目录'))
        self.renders.setToolTip(_tr('请在 SV 中导出到此目录。等待期间如需更换，请先停止再继续。'))
        def choose_renders():
            value=QFileDialog.getExistingDirectory(self,_tr('SV 导出目录'),self.renders.text())
            if value:self.renders.setText(value)
        self.choose_renders=button(_tr('选择'),choose_renders);renders_row.addWidget(self.choose_renders);render_area.addLayout(renders_row)
        folder_actions=QHBoxLayout();folder_actions.addWidget(button(_tr('打开导出目录'),self.open_render_folder))
        folder_actions.addWidget(button(_tr('复制导出路径'),self.copy_render_folder));render_area.addLayout(folder_actions)
        self.file_status=QTableWidget(7,2);self.file_status.setHorizontalHeaderLabels([_tr('试音用途'), _tr('文件状态')])
        self.file_status.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.file_status.verticalHeader().hide();self.file_status.setEditTriggers(QTableWidget.NoEditTriggers)
        self.file_status.setMinimumHeight(280);self.file_status.setMaximumHeight(360)
        self.file_names=('neutral','loudness','tension','breathiness','voicing','gender','toneShift')
        for i,title in enumerate(('中性参数基准','力度','张力','气声','发声强度','性别','音区')):
            self.file_status.setItem(i,0,QTableWidgetItem(title));self.file_status.setItem(i,1,QTableWidgetItem(_tr('尚未准备')))
            self.file_status.setRowHeight(i,42)
        render_area.addWidget(self.file_status)

        results,result_area=card(_tr('04 · 结果与下一步'),_tr('工程保存、报告补全和任务启用彼此独立。'))
        right_column.addWidget(results)
        self.result_summary=label(_tr('尚无校准结果。'),'muted');result_area.addWidget(self.result_summary)
        self.recovery=label('','error');self.recovery.hide();result_area.addWidget(self.recovery)
        result_row=QHBoxLayout();self.open_result=button(_tr('打开校准工程'),self.show_result);self.open_result.setEnabled(False);result_row.addWidget(self.open_result)
        self.open_neutral=button(_tr('打开中性对照工程'),self.show_neutral);self.open_neutral.setEnabled(False);result_row.addWidget(self.open_neutral);result_area.addLayout(result_row)
        resume_row=FlowLayout(spacing=10)
        self.resume_task_button=button(_tr('从任务恢复校准…'),self.resume_task,'primary')
        self.resume=self.resume_task_button
        self.resume_local=button(_tr('本地校准记录…'),self.resume_directory)
        for control in (self.resume_task_button,self.resume_local):
            resume_row.addWidget(control)
        self.restart=button(_tr('重新准备试音'),self.restart_session)
        resume_row.addWidget(self.restart)
        result_area.addLayout(resume_row)
        result_area.addWidget(label(_tr('补全报告不会重新拟合；只有匹配当前任务的已验证配置才会启用。'),'muted'))
        right_column.addStretch()

        footer=QWidget();footer.setObjectName('footer');footer_layout=QVBoxLayout(footer);footer_layout.setContentsMargins(14,12,14,12);footer_layout.setSpacing(10)
        self.status=label(_tr('准备时另建校准记录，原工程与参考音频保持不变。'),'muted');footer_layout.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.hide();footer_layout.addWidget(self.progress)
        row=QHBoxLayout();self.start=button(_tr('准备试音并等待导出'),self.start_session,'primary');row.addWidget(self.start)
        self.open_trial=button(_tr('打开七轨试音工程'),self.open_batch);self.open_trial.setEnabled(False);row.addWidget(self.open_trial)
        self.cancel=button(_tr('停止'),self.stop,'danger');self.cancel.setEnabled(False);row.addWidget(self.cancel);footer_layout.addLayout(row);outer.addWidget(footer)

        self.project.textChanged.connect(self.load_tracks)
        for signal in (self.reference.textChanged,self.folder.textChanged,self.track.currentIndexChanged,self.voice.currentIndexChanged):
            signal.connect(self.invalidate)
        self.voice.currentIndexChanged.connect(self.guess_reference)
        self.folder.setText(str(owner.output_parent()))
        job=getattr(owner,'job',None)
        if job and (Path(job)/'exports/voices.svp').is_file():self.project.setText(str(Path(job)/'exports/voices.svp'))
        self.guess_reference()

    def busy(self):return bool(self.worker and self.worker.isRunning())

    def invalidate(self,*args):
        if not self.busy():
            if self.auto_renders and self.renders.text()==self.auto_renders:self.renders.clear()
            self.auto_renders=None
            self.session=None;self.result=None;self.open_trial.setEnabled(False);self.open_result.setEnabled(False);self.open_neutral.setEnabled(False)
            self.start.setEnabled(True);self.start.setText(_tr('准备试音并等待导出'))
            self.recovery.hide();self.review_text.clear();self.result_summary.setText(_tr('尚无校准结果。'))

    def load_tracks(self):
        self.invalidate();self.track.clear()
        self.pitch_warning.hide()
        if not Path(self.project.text()).is_file():return
        try:
            from calibration_session import project_data
            data=project_data(self.project.text())
            for index,track in enumerate(t for t in data.get('tracks',[]) if not t.get('mainRef',{}).get('isInstrumental')):
                self.track.addItem(str(index + 1) + ' · ' + track.get('name', '演唱轨'),index)
            from pitch_accuracy import audit_svp_pitch
            if any(audit_svp_pitch(t)['status'] in ('invalid','suspicious') for t in data.get('tracks',[]) if not t.get('mainRef',{}).get('isInstrumental')):
                self.pitch_warning.setText(_tr('工程音高线疑似只有整半音尖峰或格式异常。请先核对连续音高；音高有误时，参数校准也会受影响。'))
                self.pitch_warning.show()
        except (OSError,ValueError,TypeError) as error:self.set_status(str(error))

    def guess_reference(self):
        job=getattr(self.owner,'job',None);voice=self.voice.currentData()
        if job and voice and (Path(job)/'audio'/(voice+'.wav')).is_file():
            self.reference.setText(str(Path(job)/'audio'/(voice+'.wav')))

    def start_session(self):
        if self.busy():return
        if self.owner.storage.busy() or self.owner.updates.busy() or self.owner.processing_active:
            self.set_status('请等待音乐处理、缓存管理或更新完成。');return
        try:
            if self.session is None:
                check=self.review_inputs()
                if not check['ready']:
                    self.failed('准备条件未满足；请按检查结果处理标为“需要处理”的项目。');return
                from calibration_session import create_session
                self.session=create_session(self.project.text(),self.reference.text(),
                    Path(self.folder.text())/('calibration-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')),
                    voice=self.voice.currentData(),track_index=self.track.currentData(),strength=self.strength.value()/100.,
                    render_folder=self.renders.text().strip() or None)
                if not self.renders.text().strip():
                    self.auto_renders=str(self.session.parent/'renders');self.renders.setText(self.auto_renders)
                self.open_trial.setEnabled(True)
                self.open_batch()
            from calibration_session import recoverable_fit_output
            repair_name=recoverable_fit_output(self.session,strength=self.strength.value()/100.,channel_mode=self.channels.currentData())
            output_name=repair_name or ('calibrated-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.svp'
                         if (self.session.parent/'calibrated.svp').exists() or (self.session.parent/'fit-report.json').exists() else 'calibrated.svp')
            self.worker=CalibrationWorker(self.session,self.renders.text(),self,channel_mode=self.channels.currentData(),
                                          output_name=output_name,strength=self.strength.value()/100.)
            self.worker.message.connect(self.set_status);self.worker.failed.connect(self.failed)
            self.worker.state.connect(self.render_state)
            self.worker.succeeded.connect(self.completed);self.worker.finished.connect(self.finished)
            self.form.setEnabled(False);self.renders.setEnabled(False);self.choose_renders.setEnabled(False);self.channels.setEnabled(False)
            self.channels_choice.sync();self.start.setEnabled(False)
            self.resume_task_button.setEnabled(False);self.resume_local.setEnabled(False)
            self.restart.setEnabled(False);self.cancel.setEnabled(True)
            self.progress.show();self.recovery.hide()
            self.set_status('正在检查已保存工程并补全报告，不会重新拟合。' if repair_name
                            else '正在使用已有试音重新拟合，结果将另存。' if output_name!='calibrated.svp'
                            else '七轨试音工程已生成。请在 SV 中一次分轨导出，助手将等待并继续。')
            self.worker.start()
        except (OSError,ValueError,TypeError,KeyError) as error:self.failed(str(error))

    def open_batch(self):
        if self.session and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.parent/'calibration.svp'))):
            self.set_status('未能打开 SV，请手动打开：'+str(self.session.parent/'calibration.svp'))

    def completed(self,report):
        from calibration_session import matches_task
        self.result=report
        if report.get('report_pending') or report.get('report_verified',True) is False:
            self.result_summary.setText(_tr('校准工程已保存，报告待补全；当前任务尚未启用校准。'))
            self.set_status('校准工程已保存，报告待补全；点击“补全校准报告”重试，已有工程会保留。\n'+report['output'])
            return
        self.owner.calibration_file.setText(report['profile'])
        calibrated=bool(report['calibration_fit']['voicebank_calibrated'])
        matched=calibrated and matches_task(report['profile'],getattr(self.owner,'job',None))
        self.owner.calibration_enabled.setChecked(matched)
        self.result_summary.setText((_tr('校准报告已验证；配置与当前任务匹配，已启用。') if matched else _tr('校准报告已验证；配置与当前任务不匹配，未自动启用。') if calibrated else _tr('没有测得足够可靠的参数响应；校准工程已保留。')))
        if matched:
            value=report['calibration_fit'].get('strength',self.strength.value()/100.)
            field=self.owner.dynamics_strength
            set_combo_strength(field,value)
        self.set_status(('校准工程已生成，请在 SV 中试听。' if report['calibration_fit']['voicebank_calibrated']
                            else '没有测得足够可靠的参数响应；工程保留原有曲线。')+'\n'+report['output'])
        if report.get('observer_errors'):
            self.set_status(self.status.text()+'\n工程与报告已保存；部分附加信息未完成，可稍后补充。')

    def set_status(self,text,error=False):
        self.status.setText(text);self.status.setProperty('role','error' if error else 'muted')
        self.status.style().unpolish(self.status);self.status.style().polish(self.status)

    def failed(self,error):
        from calibration_workflow import recovery_hint
        self.set_status(error,True);self.recovery.setText(recovery_hint(error));self.recovery.show();self.result_summary.setText(_tr('本轮未完成；已生成的文件与校准记录会保留。'))

    def finished(self):
        self.form.setEnabled(True);self.renders.setEnabled(True);self.choose_renders.setEnabled(True);self.channels.setEnabled(True);self.channels_choice.sync()
        self.cancel.setEnabled(False)
        self.resume_task_button.setEnabled(True);self.resume_local.setEnabled(True)
        self.restart.setEnabled(True);self.progress.hide();self.open_result.setEnabled(self.result is not None)
        self.open_neutral.setEnabled(bool(self.result and self.result.get('neutral')))
        self.start.setEnabled(True);self.start.setText(_tr('补全校准报告') if self.result and self.result.get('report_pending') else _tr('重新拟合 · 复用试音') if self.result else _tr('继续等待并拟合') if self.session else _tr('准备试音并等待导出'))
        if self.closing:self.close()

    def stop(self):
        if self.busy():self.worker.stop.set();self.cancel.setEnabled(False);self.set_status('正在停止，已生成的文件会保留…')

    def show_result(self):
        output=(self.result or {}).get('output')
        if output:QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))

    def resume_session(self,path=None):
        if self.busy():return
        if not path:path,_=QFileDialog.getOpenFileName(self,_tr('选择校准记录'),'',_tr('校准记录 (*.json)'))
        if not path:return
        try:
            from calibration_session import read_session,project_data
            spec=read_session(path);source=project_data(spec['project'])
            vocal_indices=[i for i,t in enumerate(source['tracks']) if not t.get('mainRef',{}).get('isInstrumental')]
            self.project.setText(spec['project']);self.voice.setCurrentIndex(self.voice.findData(spec['voice']))
            self.track.setCurrentIndex(vocal_indices.index(spec['track_index']))
            self.reference.setText(spec['reference']);self.folder.setText(str(Path(path).parent.parent))
            self.strength.setValue(spec['strength']*100);self.renders.setText(spec['render_folder'])
            self.channels.setCurrentIndex(self.channels.findData(spec.get('render_channel_mode','standard')))
            self.session=Path(path).resolve();self.open_trial.setEnabled(True)
            self.recovery.hide()
            files=list(Path(spec['render_folder']).glob('*.wav'))
            present={name:'found' if any(p.stem.casefold().endswith(token.casefold()) for p in files) else 'missing'
                     for name,token in spec['outputs'].items()}
            self.render_state({'phase':'waiting','files':present,'done':sum(v=='found' for v in present.values()),'total':7})
            from calibration_workflow import latest_result
            self.result=latest_result(self.session)
            from calibration_session import recoverable_fit_output
            repair_name=recoverable_fit_output(self.session,strength=self.strength.value()/100.,channel_mode=self.channels.currentData())
            if repair_name:self.result={'output':str(self.session.parent/repair_name),'report_pending':True,'report_verified':False}
            self.result_summary.setText(_tr('校准工程已找到，报告待补全；可点击“补全校准报告”。') if repair_name else _tr('校准记录已载入；七轨试音尚未完成。') if self.result is None else _tr('已有校准结果已载入，可调整强度后另存。'))
            self.start.setEnabled(True);self.open_result.setEnabled(self.result is not None)
            self.open_neutral.setEnabled(bool(self.result and self.result.get('neutral')))
            self.start.setText(_tr('补全校准报告') if repair_name else _tr('继续等待并拟合') if self.result is None else _tr('重新拟合 · 复用试音'))
            self.set_status('校准记录和独立工程已载入，报告待补全；可直接补全。' if repair_name
                else '校准记录已载入；完成 SV 分轨导出后可继续。' if self.result is None else '已有试音已载入。可调整拟合强度后重新拟合，原结果会保留。')
            self.review_inputs()
        except (OSError,ValueError,TypeError,KeyError) as error:self.set_status(str(error))

    def job_context(self):
        candidate=getattr(self.owner,'job',None)
        expected=Path(candidate)/'audio'/((self.voice.currentData() or 'lead')+'.wav') if candidate else None
        if candidate and (Path(candidate)/'request.json').is_file() and Path(self.reference.text()).resolve()==expected.resolve():return Path(candidate)
        candidate=Path(self.reference.text()).parent.parent
        return candidate if (candidate/'request.json').is_file() else None

    def review_inputs(self):
        from calibration_workflow import inspect_inputs
        report=inspect_inputs(self.project.text(),self.reference.text(),track_index=self.track.currentData(),
                              job=self.job_context(),voice=self.voice.currentData())
        lines=['声库：'+(report['bank'] or '未指定')]
        if 'notes' in report:lines.append(f"音符：{report['notes']} 个；参考演唱需覆盖至 {report['minimum_duration']:.2f} 秒。")
        for issue in report['issues']:lines.append(('需要处理：' if issue['level']=='error' else '建议复核：')+issue['message'])
        for row in report['lyrics']['candidates']:
            lines.append(f"约 {row['start']:.2f}–{row['end']:.2f} 秒　参考「{row['expected'] or '无'}」／乐谱「{row['actual'] or '无'}」")
        if report['lyrics']['status']=='different_script':lines.append('歌词文字体系不同，未进行逐字差异比较。')
        if not report['issues']:lines.append('未发现阻止准备的问题；仍请复核上面的建议。')
        self.review_text.setPlainText('\n'.join(lines));return report

    def open_source(self):
        if Path(self.project.text()).is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.project.text()).resolve())))

    def choose_bank(self):
        path,_=QFileDialog.getOpenFileName(self,_tr('选择已指定目标声库的 SVP'),'','SVP (*.svp)')
        if path:self.prepare_input(template=path)

    def prepare_input(self,*,repair=False,template=None):
        if self.busy():return
        if self.owner.processing_active or self.owner.storage.busy() or self.owner.updates.busy():
            self.failed('请先等待当前音乐处理、缓存管理或更新完成。');return
        job=self.job_context();language=None
        try:
            if not self.folder.text().strip():raise ValueError('请选择校准工程副本的保存位置。')
            if job:
                import json
                language=json.loads((job/'request.json').read_text(encoding='utf-8')).get('language')
            destination=Path(self.folder.text())/('calibration-preparation-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            self.worker=PreparationWorker(self,project=self.project.text(),reference=self.reference.text(),destination=destination,
                track_index=self.track.currentData(),template=template,repair_pitch=repair,language=language)
            self.worker.message.connect(self.set_status);self.worker.failed.connect(self.failed)
            self.worker.succeeded.connect(self.prepared);self.worker.finished.connect(self.finished)
            self.form.setEnabled(False);self.channels.setEnabled(False);self.channels_choice.sync();self.renders.setEnabled(False);self.choose_renders.setEnabled(False)
            self.start.setEnabled(False)
            self.resume_task_button.setEnabled(False);self.resume_local.setEnabled(False)
            self.restart.setEnabled(False);self.cancel.setEnabled(True)
            self.progress.setRange(0,0);self.progress.show();self.recovery.hide();self.worker.start()
        except (OSError,ValueError,TypeError) as error:self.failed(str(error))

    def prepared(self,report):
        self.project.setText(report['project']);self.track.setCurrentIndex(report['track_index'])
        if self.auto_renders and self.renders.text()==self.auto_renders:self.renders.clear()
        self.auto_renders=None
        # The preparation worker can still be finishing when its result arrives.
        # Clear the old calibration explicitly rather than relying on invalidate.
        self.session=None;self.result=None;self.open_trial.setEnabled(False);self.open_result.setEnabled(False);self.open_neutral.setEnabled(False)
        self.recovery.hide();self.result_summary.setText(_tr('校准工程副本已生成；尚未开始拟合。'))
        self.review_inputs();self.set_status('校准工程副本已生成，原工程保留。检查后可准备七轨试音。')

    def render_state(self,state):
        names={'missing':'未找到','writing':'正在写入／等待关闭','stable':'已写入，待校验','validated':'校验通过','found':'已找到（继续时复核）'}
        for i,name in enumerate(self.file_names):self.file_status.item(i,1).setText(names.get(state.get('files', {}).get(name), '等待'))
        if state.get('phase')=='waiting':self.progress.setRange(0,7);self.progress.setValue(state['done'])
        else:self.progress.setRange(0,0)

    def open_render_folder(self):
        path=Path(self.renders.text()) if self.renders.text().strip() else self.session.parent/'renders' if self.session else None
        if path and path.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def copy_render_folder(self):
        if self.renders.text().strip():QApplication.clipboard().setText(str(Path(self.renders.text()).resolve()))

    def show_neutral(self):
        if self.result and self.result.get('neutral'):QDesktopServices.openUrl(QUrl.fromLocalFile(self.result['neutral']))

    def restart_session(self):
        if not self.busy():self.invalidate();self.start_session()

    def _resume_blocker(self):
        if self.busy():
            return '校准任务正在运行，完成或停止后才能从任务恢复。'
        owner=getattr(self,'owner',None)
        if bool(getattr(owner,'processing_active',False)):
            return '音乐处理或调音任务正在运行，请完成或停止后再恢复校准。'
        storage=getattr(owner,'storage',None);updates=getattr(owner,'updates',None)
        if storage is not None and storage.busy():
            return '缓存管理正在运行，请完成后再恢复校准。'
        if updates is not None and updates.busy():
            return '更新操作正在运行，请完成后再恢复校准。'
        return None

    def resume_task(self):
        blocked=self._resume_blocker()
        if blocked:
            self.set_status(blocked,error=True);return
        dialog=None
        try:
            from results_ui import TaskPickerDialog
            dialog=TaskPickerDialog(self.owner,self,title='从任务恢复校准',
                choices=_task_calibration_choices,confirm_text='恢复此校准')
            self.task_picker_dialog=dialog
            if dialog.exec_()!=QDialog.Accepted:return
            selected=dialog.selection()
            if not isinstance(selected,dict):raise ValueError('任务选择器没有返回有效结果。')
            session=_selected_task_calibration(selected.get('row'),selected.get('value'))
            blocked=self._resume_blocker()
            if blocked:
                self.set_status(blocked,error=True);return
            self.resume_session(str(session))
        except (ImportError,OSError,ValueError,TypeError,KeyError) as error:
            self.failed(str(error))
        finally:
            if dialog is not None:dialog.deleteLater()
            self.task_picker_dialog=None

    def resume_directory(self):
        folder=QFileDialog.getExistingDirectory(self,_tr('选择本地校准文件夹'),self.folder.text())
        if not folder:return
        root=Path(folder);sessions=[root/'session.json'] if (root/'session.json').is_file() else list(root.glob('calibration*/session.json'))
        sessions=sorted(sessions,key=lambda p:p.stat().st_mtime_ns,reverse=True)
        if not sessions:self.failed('此文件夹没有校准记录，请选择包含七轨试音工程的文件夹。');return
        selected=sessions[0]
        if len(sessions)>1:
            names=[p.parent.name for p in sessions];name,ok=QInputDialog.getItem(self,_tr('选择校准记录'),_tr('已有校准记录'),names,0,False)
            if not ok:return
            selected=sessions[names.index(name)]
        self.resume_session(str(selected))

    def closeEvent(self,event):
        if self.busy():self.closing=True;self.stop();event.ignore()
        else:super().closeEvent(event)

    def reject(self):
        if self.busy():self.closing=True;self.stop()
        else:super().reject()
