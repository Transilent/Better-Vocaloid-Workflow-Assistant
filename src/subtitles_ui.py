"""Timestamp-based subtitle editor with separate NetEase and MIDI inputs."""
from i18n import tr as _tr
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
                            QFileDialog, QComboBox, QDoubleSpinBox, QMessageBox, QCheckBox)
from desktop_theme import page, label, button
from subtitles import Cue, midi_cues, render, validate
from common import output_directory


class SubtitlesPage(QWidget):
    def __init__(self, owner, compact=False):
        super().__init__()
        self.owner, self.source = owner, None
        self.midi_path=None
        self.current_mode='midi'
        self.drafts={'netease':{'rows':[],'source':None,'message':'搜索并选择歌曲，使用网易云已有的歌词时间轴。'},
                     'midi':{'rows':[],'source':None,'message':'导入带歌词事件的 MIDI，选择声部后使用其歌词时间。'}}
        if compact:
            panel=QWidget()
            body=QVBoxLayout(panel)
            body.setContentsMargins(0,0,0,0)
        else:
            panel, body = page(_tr('歌词转字幕'), _tr('使用网易云或 MIDI 已有的歌词时间轴，核对后导出 SRT / WebVTT。'))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        row = QHBoxLayout()
        row.addWidget(label(_tr('匹配方式')))
        self.match_mode=QComboBox()
        self.match_mode.addItem(_tr('网易云匹配'),'netease')
        self.match_mode.addItem(_tr('MIDI 匹配'),'midi')
        self.match_mode.setCurrentIndex(self.match_mode.findData('midi'))
        row.addWidget(self.match_mode)
        row.addStretch()
        row.addWidget(label(_tr('整体偏移（秒）')))
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-3600, 3600)
        self.offset.setDecimals(3)
        row.addWidget(self.offset)
        self.format = QComboBox()
        self.format.addItems(['SRT', 'WebVTT'])
        row.addWidget(self.format)
        body.addLayout(row)
        self.netease_panel=QWidget()
        remote=QHBoxLayout(self.netease_panel)
        remote.setContentsMargins(0,0,0,0)
        self.netease_button=button(_tr('搜索网易云歌词'),lambda: owner.load_reference(subtitles=True),'primary')
        remote.addWidget(self.netease_button)
        remote.addWidget(label(_tr('使用歌曲已有的时间轴；请核对演唱版本与整体偏移。'),'muted'),1)
        body.addWidget(self.netease_panel)
        self.midi_panel=QWidget()
        local=QVBoxLayout(self.midi_panel)
        local.setContentsMargins(0,0,0,0)
        row=QHBoxLayout()
        self.import_button=button(_tr('导入 MIDI'),self.choose,'primary')
        row.addWidget(self.import_button)
        row.addWidget(label(_tr('声部')))
        self.midi_track=QComboBox()
        self.midi_track.setEnabled(False)
        row.addWidget(self.midi_track,1)
        local.addLayout(row)
        self.auto_generate=QCheckBox(_tr('处理完成后从 MIDI 歌词事件自动生成字幕（不转写音频）'))
        local.addWidget(self.auto_generate)
        body.addWidget(self.midi_panel)
        self.message = label(self.drafts['midi']['message'], 'muted')
        body.addWidget(self.message)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_tr('开始（秒）'), _tr('结束（秒）'), _tr('歌词')])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 150)
        if compact: self.table.setMinimumHeight(340)
        body.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button(_tr('添加一行'), self.add_row))
        row.addWidget(button(_tr('删除选中行'), self.remove_rows, 'danger'))
        row.addStretch()
        self.save = button(_tr('导出字幕'), self.export, 'primary')
        self.save.setEnabled(False)
        row.addWidget(self.save)
        body.addLayout(row)
        self.match_mode.currentIndexChanged.connect(self.change_mode)
        self.midi_track.currentIndexChanged.connect(self.select_track)
        self.table.cellChanged.connect(self.update_export_action)
        self.change_mode()

    def update_export_action(self):
        self.save.setEnabled(self.table.rowCount()>0)

    def change_mode(self):
        rows=[[self.table.item(i,j).text() if self.table.item(i,j) else '' for j in range(3)] for i in range(self.table.rowCount())]
        self.drafts[self.current_mode]={'rows':rows,'source':self.source,'message':self.message.text()}
        mode=self.match_mode.currentData()
        self.current_mode=mode
        draft=self.drafts[mode]
        self.table.setRowCount(0)
        for values in draft['rows']:
            i=self.table.rowCount();self.table.insertRow(i)
            for j,text in enumerate(values): self.table.setItem(i,j,QTableWidgetItem(text))
        self.source=draft['source']
        self.message.setText(draft['message'])
        self.netease_panel.setVisible(mode=='netease')
        self.midi_panel.setVisible(mode=='midi')
        if mode!='midi': self.auto_generate.setChecked(False)
        self.update_export_action()

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, _tr('导入带歌词的 MIDI'), str(output_directory()), _tr('MIDI 文件 (*.mid *.midi)'))
        if path:
            self.load_file(path)

    def load_file(self, path):
        try:
            from pathlib import Path
            import mido
            if Path(path).suffix.lower() not in ('.mid','.midi'):
                raise ValueError('MIDI 匹配需要带歌词事件的 MIDI 文件；纯文本不能生成字幕。')
            midi=mido.MidiFile(str(path),charset='utf8')
            tracks=[(i,track.name or f'轨道 {i+1}') for i,track in enumerate(midi.tracks) if any(msg.type=='lyrics' and msg.text.strip() for msg in track)]
            if not tracks: raise ValueError('该 MIDI 没有歌词事件；请选择带歌词的声部。')
            cues=midi_cues(path,tracks[0][0])
            self.match_mode.setCurrentIndex(self.match_mode.findData('midi'))
            self.midi_path=str(path)
            self.midi_track.blockSignals(True)
            self.midi_track.clear()
            for i,title in tracks: self.midi_track.addItem(title,i)
            self.midi_track.blockSignals(False)
            self.midi_track.setEnabled(True)
            self.set_cues(cues,str(path),'MIDI 时间轴已载入；按歌词事件与停顿推断分句，请核对音画同步。')
        except Exception as exc:
            QMessageBox.warning(self, _tr('无法导入歌词'), _tr(str(exc)))

    def select_track(self):
        if self.midi_path is None or self.midi_track.currentIndex()<0: return
        try:
            cues=midi_cues(self.midi_path,self.midi_track.currentData())
            self.set_cues(cues,self.midi_path,'已切换到 '+self.midi_track.currentText()+' 的 MIDI 歌词时间轴。')
        except Exception as exc:
            QMessageBox.warning(self,_tr('无法匹配 MIDI 歌词'),_tr(str(exc)))

    def set_cues(self,cues,source,message):
        validate(cues)
        self.table.setRowCount(0)
        for cue in cues: self.add_row(cue)
        self.source=source
        self.message.setText(message)
        self.update_export_action()

    def load_reference(self,reference):
        if not reference.cues:
            self.message.setText(_tr('该版本没有可用的歌词时间轴；请更换版本或改用 MIDI 匹配。'))
            return False
        validate(reference.cues)
        self.match_mode.setCurrentIndex(self.match_mode.findData('netease'))
        self.set_cues(reference.cues,reference.origin,'网易云时间轴已载入；请核对歌曲版本与整体偏移。')
        return True

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
        self.update_export_action()

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
            path, _ = QFileDialog.getSaveFileName(self, _tr('导出字幕'), str(output_directory() / ('lyrics'+suffix)), _tr('字幕 (*') + suffix + ')')
            if path:
                from pathlib import Path
                destination = Path(path)
                if destination.suffix.lower() != suffix:
                    destination = destination.with_suffix(suffix)
                destination.write_text(text, encoding='utf-8')
                self.message.setText(_tr('字幕已保存：') + str(destination))
        except Exception as exc:
            QMessageBox.warning(self, _tr('无法导出字幕'), _tr(str(exc)))
