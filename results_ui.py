"""Searchable task library and task-specific recovery actions."""
import datetime
import json
import os
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QHeaderView, QLineEdit, QComboBox, QInputDialog, QMessageBox
from desktop_theme import page, button, label
import task_store

STATES = {'done': '已完成', 'completed': '已完成', 'partial': '阶段完成', 'failed': '失败',
          'running': '处理中', 'cancelled': '已停止', 'interrupted': '已中断', 'ready': '待处理'}
MODES = {'dual': 'Karaoke 2 双轨', 'roformer': 'BS-RoFormer 双轨', 'import': '外部分轨', 'single': '人声单轨'}


class ResultsPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.selected_job, self.rows = owner, None, []
        panel, body = page('任务与结果', '搜索任务，检查输出和处理参数，继续未完成的工作。')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索歌曲、BV、任务名或保存目录')
        self.search.textChanged.connect(self.render)
        row.addWidget(self.search, 1)
        self.filter = QComboBox()
        for name, value in [('全部状态', ''), ('已完成', 'done'), ('待处理 / 中断', 'pending'), ('失败', 'failed'), ('处理中', 'running')]:
            self.filter.addItem(name, value)
        self.filter.currentIndexChanged.connect(self.render)
        row.addWidget(self.filter)
        self.sort = QComboBox()
        self.sort.addItems(['最近更新', '名称 A–Z'])
        self.sort.currentIndexChanged.connect(self.render)
        row.addWidget(self.sort)
        row.addWidget(button('刷新', self.reload))
        body.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['歌曲 / 任务', '状态', '更新时间', '处理方案'])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.setColumnWidth(1, 95)
        self.tree.setColumnWidth(2, 175)
        self.tree.setColumnWidth(3, 190)
        self.tree.itemSelectionChanged.connect(self.select)
        self.tree.itemDoubleClicked.connect(lambda *args: self.view_progress())
        body.addWidget(self.tree, 1)
        self.detail = label('', 'muted')
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.detail)
        row = QHBoxLayout()
        owner.folder = button('打开结果目录', owner.open_folder, 'primary')
        owner.resume = button('继续 / 重试', owner.resume_last)
        owner.reprocess = button('用当前工作流重做', owner.reprocess_last)
        for control in (owner.folder, owner.resume, owner.reprocess):
            row.addWidget(control)
        row.addWidget(button('查看进度与错误', self.view_progress))
        row.addStretch()
        self.manage = button('管理任务', self.menu)
        row.addWidget(self.manage)
        body.addLayout(row)
        body.addWidget(label('VOCALOID 6：通过“文件 → 导入”载入 MIDI；带歌词的文件请选择 UTF-8 编码。', 'muted'))
        self.reload()

    def reload(self):
        self.rows = task_store.records()
        self.render()

    def render(self):
        selected = self.selected_job or self.owner.job
        self.tree.blockSignals(True)
        self.tree.clear()
        text, state = self.search.text().casefold().strip(), self.filter.currentData()
        rows = sorted(self.rows, key=lambda r: r['title'].casefold()) if self.sort.currentIndex() == 1 else self.rows
        for row in rows:
            if text and text not in (row['title']+' '+str(row['path'])+' '+str(row['request'].get('bv', ''))).casefold():
                continue
            if state == 'pending' and row['state'] not in ('ready', 'partial', 'cancelled', 'interrupted'):
                continue
            if state and state != 'pending' and row['state'] != state:
                continue
            item = QTreeWidgetItem([row['title'], STATES.get(row['state'], row['state']),
                                    datetime.datetime.fromtimestamp(row['modified']).strftime('%Y-%m-%d %H:%M'),
                                    MODES.get(row['request'].get('voice_mode'), '人声单轨')])
            item.setData(0, Qt.UserRole, str(row['path']))
            item.setData(1, Qt.UserRole, row)
            item.setToolTip(0, str(row['path']))
            item.setForeground(1, QColor('#ed9b91' if row['state'] == 'failed' else '#8fd6bd' if row['state'] == 'done' else '#e3c386'))
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
            language = '日语' if request.get('language') == 'ja' else '中文'
            devices = request.get('tools', {})
            created = request.get('created_at', '')
            self.detail.setText(f'{row["title"]}\n{language} · {request.get("midi_steps", 16)} 步 · 分离 {devices.get("separation_device", "dml")} / MIDI {devices.get("midi_device", "dml")}'+
                                (f' · 创建：{created[:19].replace("T", " ")}' if created else '')+f'\n保存：{self.selected_job}')
        else:
            self.detail.setText('没有符合条件的任务。')
        active = self.owner.processing_active
        self.owner.resume.setEnabled(bool(item) and not active)
        self.owner.reprocess.setEnabled(bool(item) and not active)
        self.owner.folder.setEnabled(bool(item))
        self.manage.setEnabled(bool(item) and not active)

    def view_progress(self):
        if self.selected_job and not self.owner.processing_active:
            self.owner.show_job(self.selected_job)
        self.owner.navigate(0)

    def menu(self):
        if self.owner.processing_active or self.owner.storage.busy() or self.owner.publishing.busy():
            QMessageBox.information(self, '任务正在使用', '请等待处理、缓存管理或发布准备结束后再管理任务文件。')
            return
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        rename = menu.addAction('重命名')
        logs = menu.addAction('打开任务日志')
        subtitles = menu.addAction('导入歌词到字幕工具')
        menu.addSeparator()
        delete = menu.addAction('删除任务及所有输出…')
        selected = menu.exec_(self.manage.mapToGlobal(self.manage.rect().bottomLeft()))
        job = self.selected_job
        if not job:
            return
        try:
            if selected == rename:
                name, ok = QInputDialog.getText(self, '重命名任务', '名称（不会移动文件夹）', text=self.tree.currentItem().text(0))
                if ok:
                    task_store.rename(job, name)
                    self.reload()
            elif selected == logs:
                path = job/'pipeline.log'
                if not path.exists():
                    raise ValueError('此任务尚无处理日志。')
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
                if QMessageBox.question(self, '删除任务', f'删除任务“{self.tree.currentItem().text(0)}”及所有输出（{amount}）？\n{job}\n此操作无法撤销。') == QMessageBox.Yes:
                    task_store.delete(job)
                    if self.owner.job == job:
                        self.owner.job = None
                    self.selected_job = None
                    self.reload()
        except Exception as exc:
            QMessageBox.warning(self, '无法管理任务', str(exc))
