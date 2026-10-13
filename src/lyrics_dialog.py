"""Search and preview original lyrics without blocking the desktop interface."""
from dataclasses import asdict
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QTextEdit
from desktop_theme import label, button
import lyrics_sources


class Lookup(QThread):
    value=pyqtSignal(object)
    failed=pyqtSignal(str)
    def __init__(self,action,text,parent):
        super().__init__(parent)
        self.action,self.text=action,text
    def run(self):
        try:
            self.value.emit(lyrics_sources.search(self.text) if self.action=='search' else lyrics_sources.fetch(self.text))
        except Exception as exc:
            self.failed.emit(str(exc))


class LyricsDialog(QDialog):
    def __init__(self,parent, purpose='reference'):
        super().__init__(parent)
        self.worker,self.reference=None,None
        self.closing=False
        self.purpose=purpose
        self.setWindowTitle('网易云匹配 · 参考歌词' if purpose=='reference' else '网易云匹配 · 字幕时间轴')
        self.resize(1000,720)
        self.setMinimumSize(760,540)
        body=QVBoxLayout(self)
        body.setContentsMargins(24,24,24,24)
        body.addWidget(label('搜索并核对歌曲版本，原文歌词默认用于主唱校准。' if purpose=='reference' else '搜索歌曲并核对歌词时间轴，用于字幕编辑。','card-title'))
        row=QHBoxLayout()
        self.keyword=QLineEdit()
        self.keyword.setPlaceholderText('歌名 + 歌手，或网易云歌曲 ID／链接')
        self.keyword.returnPressed.connect(self.submit)
        row.addWidget(self.keyword,1)
        self.search_button=button('搜索 / 获取',self.submit,'primary')
        row.addWidget(self.search_button)
        body.addLayout(row)
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['歌名','歌手','专辑','时长'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setColumnWidth(0,260)
        self.table.setColumnWidth(1,190)
        self.table.setColumnWidth(2,230)
        self.table.cellDoubleClicked.connect(self.fetch_selected)
        body.addWidget(self.table,1)
        row=QHBoxLayout()
        row.addWidget(button('获取选中歌曲的歌词',self.fetch_selected))
        self.status=label('输入歌名和歌手搜索，或粘贴网易云歌曲 ID／链接。','muted')
        row.addWidget(self.status,1)
        body.addLayout(row)
        self.preview=QTextEdit()
        self.preview.setAcceptRichText(False)
        self.preview.setPlaceholderText('歌词预览；请核对并去除剩余署名或非演唱内容。')
        self.preview.setMaximumHeight(210)
        self.preview.setReadOnly(purpose=='subtitles')
        body.addWidget(self.preview)
        row=QHBoxLayout()
        row.addWidget(label('参考歌词会与人声音频对齐，歌曲时间戳不会直接移动 MIDI 音符。' if purpose=='reference' else '仅使用歌曲已有时间轴；导入后可在字幕表格中调整文字与时间。','muted'),1)
        self.apply_button=button('填入主唱歌词' if purpose=='reference' else '使用字幕时间轴',self.apply,'primary')
        self.apply_button.setEnabled(False)
        row.addWidget(self.apply_button)
        row.addWidget(button('关闭',self.reject))
        body.addLayout(row)

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def submit(self):
        text=self.keyword.text().strip()
        try:
            lyrics_sources.song_id(text)
            action='fetch'
        except ValueError:
            action='search'
        self.start(action,text)

    def start(self,action,text):
        if self.busy(): return
        self.reference=None
        self.preview.clear()
        self.status.setText('正在搜索…' if action=='search' else '正在获取原文歌词…')
        self.search_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.worker=Lookup(action,text,self)
        self.worker.value.connect(self.received)
        self.worker.failed.connect(self.status.setText)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def finished(self):
        self.search_button.setEnabled(True)
        self.apply_button.setEnabled(self.reference is not None and (self.purpose!='subtitles' or bool(self.reference.cues)))
        if self.closing: super().reject()

    def received(self,value):
        if isinstance(value,list):
            self.table.setRowCount(len(value))
            for row,song in enumerate(value):
                for column,text in enumerate((song['title'],song['artists'],song['album'],f"{int(song['duration'])//60}:{int(song['duration'])%60:02d}")):
                    item=QTableWidgetItem(text)
                    item.setData(Qt.UserRole,song['id'])
                    self.table.setItem(row,column,item)
            self.status.setText(f'找到 {len(value)} 首歌曲。双击歌曲获取歌词。')
        else:
            self.show_reference(value)

    def fetch_selected(self,*args):
        row=self.table.currentRow()
        if row<0:
            self.status.setText('请先在列表中选择歌曲。')
            return
        self.start('fetch',self.table.item(row,0).data(Qt.UserRole))

    def show_reference(self,reference):
        self.reference=reference
        self.preview.setPlainText('\n'.join(cue.text for cue in reference.cues) if reference.cues else reference.text)
        self.status.setText(f'已载入 {len(reference.text)} 个字符、{len(reference.cues)} 个时间行。请核对歌曲版本。')
        self.apply_button.setEnabled(not self.busy() and (self.purpose!='subtitles' or bool(reference.cues)))
        if self.purpose=='subtitles' and not reference.cues:
            self.status.setText('这首歌没有可用的歌词时间轴，请选择其他版本或使用 MIDI 匹配。')

    def apply(self):
        if self.reference is None or self.busy(): return
        if self.purpose=='subtitles':
            if not self.reference.cues:
                self.status.setText('歌词没有时间轴，无法用于字幕。请使用其他歌曲版本或 MIDI 匹配。')
                return
            self.accept()
            return
        text=lyrics_sources.clean_lyrics(self.preview.toPlainText())
        if not text:
            self.status.setText('参考歌词不能为空。')
            return
        self.reference.text=text
        self.accept()

    def reject(self):
        if self.busy():
            self.closing=True
            self.status.setText('正在结束当前请求，请稍候…')
        else: super().reject()

    def closeEvent(self,event):
        if self.busy():
            self.closing=True
            event.ignore()
        else: super().closeEvent(event)

    def serialized(self):
        return {'origin':self.reference.origin,'cues':[asdict(cue) for cue in self.reference.cues]}
