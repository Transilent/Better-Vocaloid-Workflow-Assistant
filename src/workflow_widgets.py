"""Reusable full-area step navigation and aspect-preserving cover previews."""
from PyQt5.QtCore import Qt,pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QStackedWidget,QScrollArea,QLabel,QSizePolicy
from desktop_theme import button,label


class CoverPreview(QLabel):
    def __init__(self):
        super().__init__('选择封面后在这里预览')
        self.original=QPixmap()
        self.setObjectName('cover-preview')
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(360,300)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        self.setWordWrap(True)

    def setPixmap(self,pixmap):
        self.original=QPixmap(pixmap)
        self.render_image()

    def render_image(self):
        if not self.original.isNull():
            size=self.contentsRect().size()
            super().setPixmap(self.original.scaled(size,Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.render_image()

    def clear(self):
        self.original=QPixmap()
        super().clear()


class StepWorkspace(QWidget):
    changed=pyqtSignal(int)

    def __init__(self,titles):
        super().__init__()
        self.titles=titles
        self.locked=False
        self.stack=QStackedWidget()
        self.position=label('','muted')
        self.buttons=[]
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(18)
        layout.addWidget(self.stack,1)
        navigation=QHBoxLayout()
        self.navigation=navigation
        self.previous=button('‹ 上一步',lambda: self.select(self.stack.currentIndex()-1))
        navigation.addWidget(self.previous)
        navigation.addStretch()
        for index,title in enumerate(titles):
            item=button(f'{index+1}  {title}',lambda checked=False,i=index: self.select(i),'step-nav')
            item.setCheckable(True)
            item.setAccessibleName(f'步骤 {index+1}：{title}')
            self.buttons.append(item)
            navigation.addWidget(item)
        navigation.addStretch()
        self.next=button('下一步 ›',lambda: self.select(self.stack.currentIndex()+1),'primary')
        navigation.addWidget(self.next)
        layout.addLayout(navigation)
        layout.addWidget(self.position)

    def add_step(self,title,subtitle):
        panel=QWidget()
        panel.setObjectName('step-surface')
        body=QVBoxLayout(panel)
        body.setContentsMargins(28,24,28,24)
        body.setSpacing(20)
        body.addWidget(label(title,'card-title'))
        if subtitle: body.addWidget(label(subtitle,'muted'))
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.stack.addWidget(scroll)
        self.refresh()
        return panel,body

    def select(self,index):
        if self.locked: return
        if 0<=index<self.stack.count():
            self.stack.setCurrentIndex(index)
            self.refresh()
            self.changed.emit(index)

    def refresh(self):
        index=self.stack.currentIndex()
        self.previous.setEnabled(index>0 and not self.locked)
        self.next.setEnabled(index+1<self.stack.count() and not self.locked)
        for i,item in enumerate(self.buttons): item.setChecked(i==index);item.setEnabled(not self.locked)
        if index>=0: self.position.setText(f'{index+1} / {len(self.titles)} · '+self.titles[index])

    def set_locked(self,value):
        self.locked=value
        self.refresh()
