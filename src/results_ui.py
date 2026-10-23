"""Searchable task library and task-specific recovery actions."""
from i18n import tr as _tr
import datetime
import json
import os
from pathlib import Path
from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLayout, QLineEdit,
                             QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from common import load_config
from desktop_theme import COLORS, button, card, label, page, scroll_content
from ui_components import AdaptiveColumns, FlowLayout
import task_store

STATES = {'done': '已完成', 'completed': '已完成', 'partial': '阶段完成', 'failed': '失败',
          'running': '处理中', 'cancelled': '已停止', 'interrupted': '已中断', 'ready': '待处理'}
MODES = {'dual': 'Karaoke 2 双轨', 'roformer': 'BS-RoFormer 双轨', 'import': '外部分轨', 'single': '人声单轨'}
DEVICES = {'cpu': 'CPU', 'dml': 'GPU / DirectML'}


def task_library(owner):
    """Find the existing catalogue without making a second scanner or task state."""
    seen=set()
    while owner is not None and id(owner) not in seen:
        seen.add(id(owner))
        library=getattr(owner,'results',None)
        if library is not None:return library
        owner=getattr(owner,'owner',None)
    return None


def task_time(row):
    try:return datetime.datetime.fromtimestamp(row['modified']).strftime('%Y-%m-%d %H:%M')
    except (KeyError,ValueError,TypeError,OSError,OverflowError):return _tr('时间未记录')


def task_record(owner,job):
    path=Path(job)
    for row in getattr(task_library(owner),'rows',()):
        if Path(row['path'])==path:return row
    meta,info,request=(task_store.read_json(path/name) for name in ('task.json','source-info.json','request.json'))
    try:modified=(path/'status.json').stat().st_mtime
    except OSError:
        try:modified=path.stat().st_mtime
        except OSError:modified=None
    return {'path':path,'title':str(meta.get('name') or info.get('title') or request.get('source_title') or request.get('bv') or '未命名任务'),
            'request':request,'status':task_store.read_json(path/'status.json'),'modified':modified}


def task_caption(owner,job):
    if not job:return '未选择任务'
    row=task_record(owner,job)
    return str(row['title'])+' · '+task_time(row)


def task_elapsed(row):
    from progress_state import snapshot
    try:
        data=snapshot(row['path'])
        if not any(key in data['status'] for key in ('elapsed_seconds','run_started')):return _tr('耗时未记录')
        seconds=max(0,int(data['elapsed']));return _tr('已用 {0}:{1}', f'{seconds//60:02}', f'{seconds%60:02}')
    except (OSError,ValueError,TypeError,KeyError):return _tr('耗时未记录')


class TaskPickerDialog(QDialog):
    """One task identity/list source; each consumer validates its own input."""
    def __init__(self,owner,parent=None,*,title='选择任务',selected=None,choices=None,confirm_text='使用此任务'):
        super().__init__(parent)
        self.library=task_library(owner);self._choices=choices;self._rows=[];self._connected=False
        self._selected=Path(selected) if selected else getattr(self.library,'selected_job',None)
        self.setWindowTitle(title);self.resize(900,620);self.setMinimumSize(660,480)
        body=QVBoxLayout(self);body.setContentsMargins(22,20,22,20);body.setSpacing(12)
        self.search=QLineEdit();self.search.setPlaceholderText(_tr('搜索歌名、任务名或 BV 号'));self.search.setClearButtonEnabled(True);body.addWidget(self.search)
        self.task_tree=QTreeWidget();self.task_tree.setHeaderLabels([_tr('歌曲 / 任务'), _tr('状态'), _tr('更新时间')])
        self.task_tree.setRootIsDecorated(False);self.task_tree.setUniformRowHeights(True)
        self.task_tree.header().setSectionResizeMode(0,QHeaderView.Stretch)
        self.task_tree.setColumnWidth(1,100);self.task_tree.setColumnWidth(2,172);body.addWidget(self.task_tree,1)
        self.summary=label(_tr('选择任务后继续。'),'muted');body.addWidget(self.summary)
        self.project_selector=QComboBox();self.project_selector.setAccessibleName(_tr('可继续使用的文件或版本'));body.addWidget(self.project_selector);self.project_selector.hide()
        actions=QHBoxLayout();self.refresh_button=button(_tr('刷新列表'),self.refresh);actions.addWidget(self.refresh_button);actions.addStretch()
        self.use_button=button(confirm_text,self.accept,'primary');self.use_button.setEnabled(False);actions.addWidget(self.use_button)
        actions.addWidget(button(_tr('取消'),self.reject));body.addLayout(actions)
        self.search.textChanged.connect(self.render);self.task_tree.itemSelectionChanged.connect(self._select)
        self.task_tree.itemDoubleClicked.connect(lambda *args:self.accept())
        if self.library is not None:
            self.library.rows_changed.connect(self.populate);self.library.scan_error.connect(self.scan_failed);self._connected=True
            self.populate(self.library.rows)
        else:
            self.summary.setText(_tr('正在读取任务列表，请稍后再试。'));self.refresh_button.setEnabled(False)

    def refresh(self):
        if self.library is not None:self.library.reload()

    def scan_failed(self,message):
        self.summary.setText(_tr('无法刷新任务列表：\n') + str(message) + _tr('\n请稍后重试。'))

    def populate(self,rows):
        self._rows=list(rows);self.render()

    def render(self,*args):
        current=self.task_tree.currentItem()
        selected=Path(current.data(0,Qt.UserRole)['path']) if current else self._selected
        query=self.search.text().casefold().strip();self.task_tree.blockSignals(True);self.task_tree.clear()
        for row in self._rows:
            if query and query not in (str(row['title'])+' '+str(row.get('request',{}).get('bv',''))).casefold():continue
            item=QTreeWidgetItem([str(row['title']), _tr(STATES.get(row.get('state'), row.get('state', '待处理'))), _tr(task_time(row))])
            item.setData(0,Qt.UserRole,row);item.setToolTip(0,str(row['path']));self.task_tree.addTopLevelItem(item)
            if selected is not None and Path(row['path'])==Path(selected):self.task_tree.setCurrentItem(item)
        if self.task_tree.topLevelItemCount() and not self.task_tree.currentItem():self.task_tree.setCurrentItem(self.task_tree.topLevelItem(0))
        self.task_tree.blockSignals(False);self._select()
        if not self._rows:self.summary.setText(_tr('还没有任务。可先开始处理，或选择本页支持的本地文件。'))
        elif not self.task_tree.topLevelItemCount():self.summary.setText(_tr('没有符合搜索条件的任务。'))

    def _available(self,row):
        path=Path(row['path'])
        if not path.is_dir() or not (path/'request.json').is_file():raise ValueError('任务已移动或删除，请刷新任务列表。')
        return self._choices(row) if self._choices is not None else [{'label':'使用此任务','value':path}]

    def _select(self,*args):
        previous=self.project_selector.currentData();self.project_selector.clear();self.project_selector.hide();self.use_button.setEnabled(False)
        item=self.task_tree.currentItem()
        if item is None:self.summary.setText(_tr('选择任务后继续。'));return
        row=item.data(0,Qt.UserRole)
        self.summary.setText(str(row['title'])+' · '+task_time(row)+' · '+task_elapsed(row))
        try:
            for choice in self._available(row):self.project_selector.addItem(_tr(choice['label']),choice['value'])
            index=self.project_selector.findData(previous)
            if index>=0:self.project_selector.setCurrentIndex(index)
            self.project_selector.setVisible(self.project_selector.count()>1)
            self.use_button.setEnabled(self.project_selector.count()>0)
            if not self.project_selector.count():self.summary.setText(self.summary.text() + _tr('\n此任务没有本页可用的文件或版本。'))
        except (OSError,ValueError,KeyError,TypeError) as error:self.summary.setText(self.summary.text()+'\n'+str(error))

    def selection(self):
        item=self.task_tree.currentItem()
        if item is None or not self.use_button.isEnabled():return None
        return {'row':item.data(0,Qt.UserRole),'value':self.project_selector.currentData()}

    def accept(self):
        selected=self.selection()
        if selected is None:return
        try:
            if not any(choice['value']==selected['value'] for choice in self._available(selected['row'])):
                raise ValueError('所选文件或版本已变化，请重新选择。')
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.use_button.setEnabled(False);self.summary.setText(str(error));return
        super().accept()

    def _disconnect(self):
        if self._connected:
            self._connected=False
            try:
                self.library.rows_changed.disconnect(self.populate);self.library.scan_error.disconnect(self.scan_failed)
            except (TypeError,RuntimeError):pass  # The owning window may already be closing.

    def done(self,result):
        self._disconnect();super().done(result)

    def exec_(self):
        try:
            self.refresh()
            return super().exec_()
        finally:self._disconnect()


def _status_color(state, palette=None):
    if palette is None:
        mode = load_config().get('theme', 'light')
        palette = COLORS.get(mode, COLORS['light'])
    if state in ('done', 'completed'):
        return QColor(palette['selected_text'])
    if state in ('failed', 'interrupted'):
        return QColor(palette['danger'])
    if state in ('partial', 'running'):
        return QColor(palette['accent'])
    return QColor(palette['muted'])


def _set_role(widget, role):
    value = role or ''
    if widget.property('role') == value:
        return
    widget.setProperty('role', value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()




def _page_busy(page):
    busy = getattr(page, 'busy', None)
    return bool(callable(busy) and busy())


def _control_value(value):
    text = getattr(value, 'text', None)
    return text() if callable(text) else value


def _field_value(fields, key):
    return _control_value(fields.get(key) if isinstance(fields, dict) else None)


def _path_within(value, job):
    if value is None or not str(value).strip():
        return False
    try:
        path = Path(value).resolve()
        return path == job or path.is_relative_to(job)
    except (OSError, ValueError, TypeError):
        return False


def active_task_operations(owner, job):
    """Name live nonmodal operations that still read or write this task."""
    if not job:
        return ()
    job = Path(job).resolve()
    active = []
    video = getattr(owner, 'video_mixdown', None)
    preview_busy = getattr(video, 'preview_busy', None)
    if callable(preview_busy) and preview_busy():
        snapshot = getattr(video, 'preview_inputs', {})
        if isinstance(snapshot, dict) and any(_path_within(snapshot.get(key), job)
                                              for key in ('source_job', 'video')):
            active.append('视频预览')
    if _page_busy(video):
        snapshot = getattr(video, 'active_inputs', None)
        if isinstance(snapshot, dict) and snapshot:
            references = tuple(snapshot.values())
        else:
            fields = getattr(video, 'fields', {})
            references = (getattr(video, 'source_job', None),) + tuple(
                _field_value(fields, key) for key in ('video', 'mixdown', 'output'))
        if any(_path_within(value, job) for value in references):
            active.append('视频合成')
    calibration = getattr(owner, 'calibration_dialog', None)
    if _page_busy(calibration):
        session = getattr(calibration, 'session', None)
        references = []
        fixed = False
        unreadable = False
        if isinstance(session, dict):
            references.extend(session.get(key) for key in ('project', 'reference', 'render_folder'))
            fixed = True
        elif session:
            try:
                from calibration_session import read_session
                spec = read_session(session)
                references.extend(spec.get(key) for key in ('project', 'reference', 'render_folder'))
                fixed = True
            except (OSError, ValueError, TypeError, KeyError):
                unreadable = True
        if not fixed:
            references.extend(_control_value(getattr(calibration, name, None))
                              for name in ('project', 'reference', 'renders'))
            context = getattr(calibration, 'job_context', None)
            if callable(context):
                try:
                    references.append(context())
                except (OSError, ValueError, TypeError):
                    pass
        if unreadable or any(_path_within(value, job) for value in references):
            active.append('校准')
    return tuple(active)


class TaskScan(QThread):
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def run(self):
        try:
            rows = task_store.records(self.isInterruptionRequested)
            if not self.isInterruptionRequested(): self.result.emit(rows)
        except Exception as exc:
            self.error.emit(str(exc))


class ResultsPage(QWidget):
    rows_changed = pyqtSignal(object)
    scan_error = pyqtSignal(str)

    def __init__(self, owner):
        super().__init__()
        self.owner, self.selected_job, self.rows = owner, None, []
        self.worker, self.rescan = None, False
        panel, page_layout = page(_tr('任务与结果'), _tr('查找已有任务，查看输出并继续未完成的步骤。'))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        content = scroll_content(page_layout)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self.search = QLineEdit()
        self.search.setPlaceholderText(_tr('搜索歌曲、BV 号、任务名或保存目录'))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(48)
        self.search.setAccessibleName(_tr('搜索任务'))
        self.search.textChanged.connect(self.render)
        body.addWidget(self.search)

        tools = FlowLayout(spacing=10)
        self.filter = QComboBox()
        for name, value in [('全部状态', ''), ('已完成', 'done'), ('待处理 / 中断', 'pending'),
                            ('失败', 'failed'), ('处理中', 'running')]:
            self.filter.addItem(_tr(name), value)
        self.filter.currentIndexChanged.connect(self.render)
        self.filter.setMinimumWidth(170)
        tools.addWidget(self.filter)
        self.sort = QComboBox()
        self.sort.addItems([_tr('最近更新'), _tr('名称 A–Z')])
        self.sort.currentIndexChanged.connect(self.render)
        self.sort.setMinimumWidth(150)
        tools.addWidget(self.sort)
        tools.addWidget(button(_tr('刷新'), self.reload))
        body.addLayout(tools)

        list_card, list_body = card(_tr('任务列表'), _tr('选择任务后查看状态、处理和下一步。'))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr('歌曲 / 任务'), _tr('状态'), _tr('更新时间'), _tr('处理方案')])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 108)
        self.tree.setColumnWidth(2, 172)
        self.tree.setColumnWidth(3, 185)
        self.tree.setMinimumHeight(330)
        self.tree.itemSelectionChanged.connect(self.select)
        self.tree.itemDoubleClicked.connect(lambda *args: self.view_progress())
        list_body.addWidget(self.tree, 1)

        detail_card, detail_body = card(_tr('任务详情'), _tr('查看保存位置、处理设置和下一步。'))
        self.detail_status = label(_tr('未选择任务'), 'muted')
        self.detail_status.setAccessibleName(_tr('所选任务状态'))
        detail_body.addWidget(self.detail_status)
        self.detail = label(_tr('选择一个任务以查看状态、保存位置和下一步。'))
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setAccessibleName(_tr('所选任务详情'))
        detail_body.addWidget(self.detail)
        detail_body.addStretch()

        self.columns = AdaptiveColumns(list_card, detail_card, breakpoint=940, ratio=(3, 2))
        body.addWidget(self.columns, 1)

        actions = FlowLayout(spacing=10)
        owner.folder = button(_tr('打开结果目录'), owner.open_folder, 'primary')
        owner.resume = button(_tr('继续 / 重试'), owner.resume_last)
        owner.reprocess = button(_tr('按当前设置重做'), owner.reprocess_last)
        self.files = button(_tr('文件与下一步'), self.show_files)
        self.manage = button(_tr('管理任务'), self.menu)
        for control in (owner.folder, owner.resume, owner.reprocess):
            actions.addWidget(control)
        actions.addWidget(button(_tr('查看进度与错误'), self.view_progress))
        actions.addWidget(self.files)
        actions.addWidget(self.manage)
        body.addLayout(actions)
        body.addWidget(label(_tr('在“文件与下一步”中登记工程、Mixdown 或成品视频，或重新检查已有文件；VOCALOID 6 导入 MIDI 时请选择 UTF-8 编码。'), 'muted'))
        body.addStretch()
        self.reload()

    def reload(self):
        if self.worker and self.worker.isRunning():
            self.rescan = True
            return
        self.worker = TaskScan(self)
        self.worker.result.connect(self.ready)
        self.worker.error.connect(self.scan_failed)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def scan_failed(self,text):
        self.detail.setText(_tr('无法读取任务列表：\n') + text);self.scan_error.emit(text)

    def ready(self, rows):
        self.rows = rows
        self.render()
        self.rows_changed.emit(rows)

    def scan_finished(self):
        old = self.sender()
        if self.worker is old: self.worker = None
        if old is not None: old.deleteLater()
        if self.rescan:
            self.rescan = False
            self.reload()

    def stop_scan(self):
        self.rescan = False
        dialog = getattr(self, 'files_dialog', None)
        if dialog and dialog.busy():
            return False
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            return self.worker.wait(2000)
        return True

    def render(self):
        selected = self.selected_job or self.owner.job
        self.tree.blockSignals(True)
        self.tree.clear()
        text, state = self.search.text().casefold().strip(), self.filter.currentData()
        palette = COLORS.get(load_config().get('theme', 'light'), COLORS['light'])
        rows = sorted(self.rows, key=lambda r: r['title'].casefold()) if self.sort.currentIndex() == 1 else self.rows
        for row in rows:
            if text and text not in (row['title']+' '+str(row['path'])+' '+str(row['request'].get('bv', ''))).casefold():
                continue
            if state == 'pending' and row['state'] not in ('ready', 'partial', 'cancelled', 'interrupted'):
                continue
            if state and state != 'pending' and row['state'] != state:
                continue
            item = QTreeWidgetItem([row['title'], _tr(STATES.get(row['state'], row['state'])), _tr(task_time(row)), _tr(MODES.get(row['request'].get('voice_mode'), '人声单轨'))])
            item.setData(0, Qt.UserRole, str(row['path']))
            item.setData(1, Qt.UserRole, row)
            item.setToolTip(0, str(row['path']))
            item.setForeground(1, _status_color(row['state'], palette))
            self.tree.addTopLevelItem(item)
            if selected == row['path']:
                self.tree.setCurrentItem(item)
        if self.tree.topLevelItemCount() and not self.tree.currentItem():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.blockSignals(False)
        self.select()

    def select(self):
        item = self.tree.currentItem()
        self.selected_job = Path(item.data(0, Qt.UserRole)) if item else None
        if item:
            row = item.data(1, Qt.UserRole)
            request = row['request']
            language = _tr('日语' if request.get('language') == 'ja' else '中文')
            devices = request.get('tools', {})
            created = request.get('created_at', '')
            state = row.get('state', '')
            label_role = 'success' if state in ('done', 'completed') else (
                'error' if state in ('failed', 'interrupted') else '')
            _set_role(self.detail_status, label_role)
            self.detail_status.setText(_tr('{0}  ·  {1}', _tr(STATES.get(state, state or '未知状态')), _tr(MODES.get(request.get('voice_mode'), '人声单轨'))))
            details = [row['title'],
                       _tr('{0} · {1} 步 · 分离 {2} / MIDI {3}', language, str(request.get('midi_steps', 16)),
                           DEVICES.get(devices.get('separation_device', 'dml'), devices.get('separation_device', 'dml')),
                           DEVICES.get(devices.get('midi_device', 'dml'), devices.get('midi_device', 'dml'))),
                       _tr('更新：{0}', datetime.datetime.fromtimestamp(row['modified']).strftime('%Y-%m-%d %H:%M'))]
            if created:
                details.append(_tr('创建：') + created[:19].replace('T', ' '))
            details.append(_tr('保存：') + str(self.selected_job))
            from task_contract import NEXT
            details.append(_tr('下一步：') + _tr(NEXT[row.get('next_action', 'ready')]))
            self.detail.setText('\n'.join(details))
        else:
            _set_role(self.detail_status, 'muted')
            self.detail_status.setText(_tr('未选择任务'))
            self.detail.setText(_tr('没有符合条件的任务，或尚未选择列表项。可清空搜索、切换状态筛选或刷新列表。'))
        active = self.owner.processing_active
        self.owner.resume.setEnabled(bool(item) and not active)
        self.owner.reprocess.setEnabled(bool(item) and not active)
        self.owner.folder.setEnabled(bool(item))
        self.manage.setEnabled(bool(item) and not active)
        self.files.setEnabled(bool(item) and not active)

    def show_files(self):
        if not self.selected_job or self.owner.processing_active: return
        from task_contract_ui import TaskFilesDialog
        self.files_dialog = TaskFilesDialog(self.owner, self.selected_job)
        self.files_dialog.exec_()

    def view_progress(self):
        if self.selected_job and not self.owner.processing_active:
            self.owner.show_job(self.selected_job)
        self.owner.navigate(0)

    def menu(self):
        if self.owner.processing_active or self.owner.storage.busy() or self.owner.publishing.busy():
            QMessageBox.information(self, _tr('任务正在使用'), _tr('请等待处理、缓存管理或发布准备结束后再管理任务文件。'))
            return
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        rename = menu.addAction(_tr('重命名'))
        logs = menu.addAction(_tr('打开任务日志'))
        subtitles = menu.addAction(_tr('导入歌词到字幕工具'))
        menu.addSeparator()
        delete = menu.addAction(_tr('删除任务及所有输出…'))
        selected = menu.exec_(self.manage.mapToGlobal(self.manage.rect().bottomLeft()))
        job = self.selected_job
        if not job:
            return
        try:
            if selected in (rename, delete):
                active = active_task_operations(self.owner, job)
                if active:
                    QMessageBox.information(self, _tr('任务正在使用'), _tr(_tr('该任务仍被正在运行的') + '、'.join(active) + _tr('占用，请先完成或取消该操作。')))
                    return
            if selected == rename:
                name, ok = QInputDialog.getText(self, _tr('重命名任务'), _tr('任务名称（不会更改保存位置）'), text=self.tree.currentItem().text(0))
                if ok:
                    active = active_task_operations(self.owner, job)
                    if active:
                        QMessageBox.information(self, _tr('任务正在使用'), _tr(_tr('该任务仍被正在运行的') + '、'.join(active) + _tr('占用，请先完成或取消该操作。')))
                        return
                    task_store.rename(job, name)
                    self.reload()
            elif selected == logs:
                path = job/'pipeline.log'
                if not path.exists():
                    raise ValueError('此任务还没有处理日志。')
                os.startfile(str(path))
            elif selected == subtitles:
                candidates = [job/'midi/lead/lead.mid', job/'midi/vocals.mid']
                source = next((p for p in candidates if p.is_file()), None)
                if not source:
                    raise ValueError('任务还没有生成 MIDI。')
                self.owner.subtitles.load_file(str(source))
                self.owner.navigate(7)
            elif selected == delete:
                amount = task_store.format_size(task_store.size(job))
                if QMessageBox.question(self, _tr('删除任务'), _tr('删除任务“{0}”及所有输出（{1}）？\n{2}\n此操作无法撤销。', f'{self.tree.currentItem().text(0)}', f'{amount}', f'{job}')) == QMessageBox.Yes:
                    active = active_task_operations(self.owner, job)
                    if active:
                        QMessageBox.information(self, _tr('任务正在使用'), _tr(_tr('该任务仍被正在运行的') + '、'.join(active) + _tr('占用，请先完成或取消该操作。')))
                        return
                    task_store.delete(job)
                    if self.owner.job == job:
                        self.owner.job = None
                    self.selected_job = None
                    self.reload()
        except Exception as exc:
            QMessageBox.warning(self, _tr('无法管理任务'), _tr(str(exc)))
