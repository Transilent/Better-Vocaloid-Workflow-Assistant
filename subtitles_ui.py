"""Offline lyric-to-subtitle editor with explicit timing and UTF-8 export."""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
                            QFileDialog, QComboBox, QDoubleSpinBox, QMessageBox)
from desktop_theme import page, label, button
from subtitles import Cue, load, render
from common import output_directory


class SubtitlesPage(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner, self.source = owner, None
        panel, body = page('歌词转字幕', '导入 LRC、TXT、带时间的 CSV 或 MIDI 歌词，校准时间后导出 SRT / WebVTT。')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        row = QHBoxLayout()
        row.addWidget(button('导入歌词文件', self.choose, 'primary'))
        row.addWidget(label('纯文本总时长（秒）'))
        self.duration = QDoubleSpinBox()
        self.duration.setRange(.1, 86400)
        self.duration.setValue(180)
        row.addWidget(self.duration)
        row.addStretch()
        row.addWidget(label('整体偏移（秒）'))
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-3600, 3600)
        self.offset.setDecimals(3)
        row.addWidget(self.offset)
        self.format = QComboBox()
        self.format.addItems(['SRT', 'WebVTT'])
        row.addWidget(self.format)
        body.addLayout(row)
        self.message = label('支持 UTF-8 文本。纯文本的自动时间是估算值，不是音频对齐结果。', 'muted')
        body.addWidget(self.message)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['开始（秒）', '结束（秒）', '歌词'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 150)
        body.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button('添加一行', self.add_row))
        row.addWidget(button('删除选中行', self.remove_rows, 'danger'))
        row.addStretch()
        self.save = button('导出字幕', self.export, 'primary')
        row.addWidget(self.save)
        body.addLayout(row)

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入歌词', str(output_directory()), '歌词文件 (*.lrc *.txt *.csv *.mid *.midi)')
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            cues, note = load(path, self.duration.value())
            self.table.setRowCount(0)
            for cue in cues:
                self.add_row(cue)
            self.source = path
            self.message.setText(note)
        except Exception as exc:
            QMessageBox.warning(self, '无法导入歌词', str(exc))

    def add_row(self, cue=None):
        if not isinstance(cue, Cue):
            try:
                end = float(self.table.item(self.table.rowCount()-1, 1).text()) if self.table.rowCount() and self.table.item(self.table.rowCount()-1, 1) else 0
                if not __import__('math').isfinite(end):
                    end = 0
            except ValueError:
                end = 0
            cue = Cue(end, end+3, '歌词')
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, value in enumerate((round(cue.start, 3), round(cue.end, 3), cue.text)):
            self.table.setItem(index, column, QTableWidgetItem(str(value)))

    def remove_rows(self):
        for index in sorted({item.row() for item in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(index)

    def text(self):
        try:
            cues = [Cue(float(self.table.item(i, 0).text()), float(self.table.item(i, 1).text()), self.table.item(i, 2).text()) for i in range(self.table.rowCount())]
        except (ValueError, AttributeError):
            raise ValueError('请填写每行的开始时间、结束时间和歌词。') from None
        return render(cues, 'srt' if self.format.currentIndex() == 0 else 'vtt', self.offset.value())

    def export(self):
        try:
            text = self.text()
            suffix = '.srt' if self.format.currentIndex() == 0 else '.vtt'
            path, _ = QFileDialog.getSaveFileName(self, '导出字幕', str(output_directory() / ('lyrics'+suffix)), '字幕 (*'+suffix+')')
            if path:
                from pathlib import Path
                destination = Path(path)
                if destination.suffix.lower() != suffix:
                    destination = destination.with_suffix(suffix)
                destination.write_text(text, encoding='utf-8')
                self.message.setText('字幕已保存：'+str(destination))
        except Exception as exc:
            QMessageBox.warning(self, '无法导出字幕', str(exc))
