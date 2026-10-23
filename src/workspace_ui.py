"""Sidebar workspace and focused processing/results pages."""
from i18n import tr as _tr
import json
from pathlib import Path
from PyQt5.QtCore import QEvent, QObject, QSize, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
                            QTextEdit, QComboBox, QCheckBox, QProgressBar, QStackedWidget,
                            QButtonGroup, QTreeWidget, QTreeWidgetItem, QHeaderView, QScrollArea, QFrame)
from common import ROOT, app_path, load_config, output_directory, job_directories
from desktop_theme import apply_theme, button, card, fold, label, page, scroll_content
from results_ui import ResultsPage


def build_processing(window):
    panel, outer = page(_tr('准备工作流'), _tr('选择预设，添加音乐，准备分离音频、歌词与工程。'))
    outer.itemAt(1).widget().hide()  # Reserve the opening viewport for actual options.
    from processing_ui import build_steps
    build_steps(window, outer)

    footer = QWidget()
    footer.setObjectName('footer')
    footer_layout = QVBoxLayout(footer)
    footer_layout.setContentsMargins(16, 10, 16, 10)
    row = QHBoxLayout()
    window.status = label(_tr('准备就绪 · 添加音乐后开始处理'))
    row.addWidget(window.status, 1)
    window.stop = button(_tr('取消处理'), window.cancel, 'danger')
    window.stop.setEnabled(False)
    window.stop.hide()
    row.addWidget(window.stop)
    window.start = button(_tr('开始准备'), window.start_new, 'primary')
    window.start.setMinimumWidth(142)
    row.addWidget(window.start)
    footer_layout.addLayout(row)
    window.progress = QProgressBar()
    window.progress.setRange(0, 6)
    window.progress.setValue(0)
    window.progress.setTextVisible(False)
    window.progress.setFixedHeight(12)
    window.progress.setAccessibleName(_tr('准备工作流进度'))
    footer_layout.addWidget(window.progress)
    window.progress_detail = label(_tr('尚未开始'), 'muted')
    footer_layout.addWidget(window.progress_detail)
    window.stage_strip = QWidget()
    from ui_components import FlowLayout
    stage_row = FlowLayout(window.stage_strip,spacing=10)
    stage_row.setContentsMargins(0,0,0,0)
    window.stage_strip.hide()
    window.stage_labels = {}
    from progress_state import TITLES
    for key, name in TITLES.items():
        value = label(name, 'muted')
        window.stage_labels[key] = value
        stage_row.addWidget(value)
    footer_layout.addWidget(window.stage_strip)
    window.error_panel, error_body = card(_tr('任务需要处理'))
    window.error_message = label('', 'error')
    error_body.addWidget(window.error_message)
    from ui_components import FlowLayout
    recovery_row = FlowLayout()
    recovery_row.addWidget(button(_tr('继续 / 重试'), window.retry_job, 'primary'))
    window.cpu_retry = button(_tr('改用 CPU 继续'), window.retry_cpu)
    recovery_row.addWidget(window.cpu_retry)
    recovery_row.addWidget(button(_tr('查看日志'), lambda: window.log_toggle.setChecked(True)))
    window.recovery_action = button(_tr('检查设置'), window.open_recovery)
    recovery_row.addWidget(window.recovery_action)
    error_body.addLayout(recovery_row)
    footer_layout.addWidget(window.error_panel)
    window.error_panel.hide()
    window.log = QTextEdit()
    window.log.setReadOnly(True)
    window.log.setAcceptRichText(False)
    window.log.document().setMaximumBlockCount(1800)
    window.log.setFixedHeight(140)
    window.log_toggle = fold(_tr('处理日志'), window.log, footer_layout)
    footer_layout.removeWidget(window.log_toggle)
    row.insertWidget(1,window.log_toggle)
    outer.addWidget(footer)
    return panel


def workspace_geometry(screen, offscreen=False):
    if offscreen:
        return (1600, 1000), (1000, 680)
    available = (max(1, screen.width() - 48), max(1, screen.height() - 48))
    initial = (min(1440, available[0]), min(900, available[1]))
    minimum = (min(1000, available[0]), min(680, available[1]))
    return initial, minimum


class WorkspaceChrome(QObject):
    """Grow navigation with a wide window while leaving content typography alone."""
    def __init__(self, window, sidebar, scroll, layout, icon, brand, full_name):
        super().__init__(window)
        self.window, self.sidebar, self.scroll = window, sidebar, scroll
        self.layout, self.icon, self.brand, self.full_name = layout, icon, brand, full_name
        self.pixmap = QPixmap(str(ROOT/'assets/app.png'))
        self._key = None
        window.installEventFilter(self)
        self.refresh()

    def refresh(self, force=False):
        width, height = self.window.width(), self.window.height()
        growth = min(1., max(0., (width-1440)/800.))
        factor = getattr(self.window, '_vwa_appearance', {}).get('ui_scale', 100)/100.
        key = (round(growth*100), width < 1300, height < 900, factor)
        if key == self._key and not force:
            return
        self._key = key
        side_width = round(((236 if width < 1300 else 252)+80*growth)*min(1.1, factor))
        self.scroll.setFixedWidth(side_width)
        self.sidebar.setFixedWidth(side_width-8)
        self.layout.setContentsMargins(16, 22, 16, 16)
        self.layout.setSpacing(8+round(growth*3))
        logo = round((56+20*growth)*factor)
        self.icon.setPixmap(self.pixmap.scaled(logo, logo, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.brand.setStyleSheet(f'font-size: {round((28+8*growth)*factor)}px; font-weight: 600;')
        self.full_name.setStyleSheet(f'font-size: {round((12+2*growth)*factor)}px;')
        for item in self.window.nav_buttons:
            if item is None:
                continue
            item.setStyleSheet(f'font-size: {round((18+3*growth)*factor)}px;')
            item.setMinimumHeight(round((50+12*growth if height >= 900 else 46)*factor))
            item.setIconSize(QSize(round((22+5*growth)*factor), round((22+5*growth)*factor)))

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Resize:
            self.refresh()
        return False


def build_window(window):
    apply_theme(window)
    screen = QApplication.primaryScreen().availableGeometry()
    initial, minimum = workspace_geometry(screen, QApplication.platformName() == 'offscreen')
    window.setMinimumSize(*minimum)
    window.resize(*initial)
    if QApplication.platformName() != 'offscreen':
        window.move(screen.center()-window.rect().center())
    outer = QHBoxLayout(window)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    sidebar = QWidget()
    sidebar.setObjectName('sidebar')
    sidebar.setFixedWidth(216)
    nav = QVBoxLayout(sidebar)
    nav.setContentsMargins(14, 22, 14, 16)
    nav.setSpacing(8)
    icon = label()
    icon.setPixmap(QPixmap(str(ROOT / 'assets/app.png')).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    brand_row=QHBoxLayout()
    brand_row.addWidget(icon)
    brand = label('VWA', 'heading')
    brand.setProperty('role', 'success')
    brand.setStyleSheet('font-size: 28px; font-weight: 600;')
    brand_row.addWidget(brand,1)
    nav.addLayout(brand_row)
    from i18n import current_language
    local_name = label(_tr('术力口工作流助手'), 'muted')
    local_name.setVisible(current_language() != 'en')
    nav.addWidget(local_name)
    full_name=label('Vocaloid Workflow Assistant','muted')
    full_name.setStyleSheet('font-size: 12px;')
    nav.addWidget(full_name)
    nav.addSpacing(16)
    window.nav_group = QButtonGroup(window)
    titles = ('准备工作流', '模型与组件', '任务与结果', '发布', '设置与更新', '工作流', '缓存与磁盘', '歌词转字幕', '合成视频', '调音工作流')
    window.nav_buttons = [None] * len(titles)
    for index in (0, 9, 8, 3, 1, 2, 4):
        if index==1:
            nav.addSpacing(16)
        title = titles[index]
        if current_language() == 'en':
            title = {1: 'Models', 2: 'Tasks and results', 4: 'Settings'}.get(index, _tr(title))
        item = button(title, lambda checked=False, i=index: window.navigate(i), 'nav')
        item.setCheckable(True)
        window.nav_group.addButton(item)
        nav.addWidget(item)
        window.nav_buttons[index] = item
    nav.addStretch()
    window.theme_picker = QComboBox()
    window.theme_picker.addItem(_tr('深色界面'), 'dark')
    window.theme_picker.addItem(_tr('浅色界面'), 'light')
    window.theme_picker.setCurrentIndex(max(0, window.theme_picker.findData(load_config().get('theme', 'light'))))
    window.theme_picker.currentIndexChanged.connect(window.change_theme)
    window.theme_picker.hide()  # Appearance settings observe this original value owner.
    sidebar_scroll=QScrollArea()
    sidebar_scroll.setFrameShape(QFrame.NoFrame)
    sidebar_scroll.setWidgetResizable(True)
    sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sidebar_scroll.setFixedWidth(230)
    sidebar_scroll.setWidget(sidebar)
    outer.addWidget(sidebar_scroll)
    window.sidebar, window.sidebar_scroll = sidebar, sidebar_scroll
    window._workspace_chrome = WorkspaceChrome(window, sidebar, sidebar_scroll, nav, icon, brand, full_name)
    window.pages = QStackedWidget()
    window.tabs = window.pages
    outer.addWidget(window.pages, 1)
    window.pages.addWidget(build_processing(window))
    from tuning_ui import TuningPage
    window.tuning = TuningPage(window)
    from components_ui import ComponentsPage
    window.components = ComponentsPage(window)
    window.pages.addWidget(window.components)
    window.results = ResultsPage(window)
    window.pages.addWidget(window.results)
    from publishing_ui import PublishingTab
    window.publishing = PublishingTab(window)
    window.pages.addWidget(window.publishing)
    from updates_ui import UpdatesPage
    window.updates = UpdatesPage(window)
    window.pages.addWidget(window.updates)
    from processing_workflow import ProcessingWorkflow
    window.workflow = ProcessingWorkflow(window)
    window.pages.addWidget(QWidget())  # Index retained for saved navigation and recovery actions.
    window.workflow_preset.currentIndexChanged.connect(window.choose_processing_preset)
    from storage_ui import StoragePage
    window.storage = StoragePage(window,compact=True)
    window.updates.body_layout.insertWidget(1,window.storage)
    from ui_preferences import AppearancePanel
    window.appearance_panel=AppearancePanel(window)
    window.updates.body_layout.insertWidget(0,window.appearance_panel)
    window.pages.addWidget(QWidget())
    window.pages.addWidget(QWidget())
    from video_mixdown_ui import VideoMixdownPage
    window.video_mixdown = VideoMixdownPage(window)
    window.pages.addWidget(window.video_mixdown)
    window.pages.addWidget(window.tuning)
    window.results.rows_changed.connect(window.tuning.refresh_task_context)
    window.results.rows_changed.connect(lambda rows:window.publishing.refresh_review())
    window.results.rows_changed.connect(lambda rows:window.video_mixdown._set_context())
    for control in (window.language, window.zh_lyric_mode, window.voice_mode, window.separator_model, window.roformer_device, window.separation_flow,
                    window.reference_mode, window.subtitles.match_mode, window.alignment_engine, window.midi_steps, window.separation_device, window.midi_device,
                    window.dml_adapter, window.cpu_threads):
        control.currentIndexChanged.connect(window.quick_options_changed)
    window.separator_model.currentIndexChanged.connect(window.update_component_state)
    window.voice_mode.currentIndexChanged.connect(window.update_import_panel)
    window.language.currentIndexChanged.connect(window.update_lyric_options)
    window.recognize.toggled.connect(window.update_lyric_options)
    window.bpm.valueChanged.connect(window.quick_options_changed)
    for check in (window.recognize, window.manual_bpm, window.midi_enabled, window.subtitles_enabled, window.exports_enabled, window.audio_enabled, window.export_pitch,
                  window.include_instrumental, *window.project_checks.values(), *window.audio_checks.values()):
        check.toggled.connect(window.quick_options_changed)
    window.update_import_panel()
    window.refresh_workflow_summary()
    window.navigate(0)
    apply_theme(window,window.theme_picker.currentData())
