"""Sidebar workspace and focused processing/results pages."""
import json
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
                            QTextEdit, QComboBox, QCheckBox, QProgressBar, QStackedWidget,
                            QButtonGroup, QTreeWidget, QTreeWidgetItem, QHeaderView)
from common import ROOT, app_path, load_config, output_directory, job_directories
from desktop_theme import apply_theme, button, card, fold, label, page, scroll_content
from results_ui import ResultsPage


def build_processing(window):
    panel, outer = page('调音工作流', '选择预设，添加音乐，展开步骤调整参数。')
    from processing_ui import build_steps
    build_steps(window, outer)

    footer = QWidget()
    footer.setObjectName('footer')
    footer_layout = QVBoxLayout(footer)
    footer_layout.setContentsMargins(0, 0, 0, 0)
    row = QHBoxLayout()
    window.status = label('准备就绪 · 添加音乐后开始处理')
    row.addWidget(window.status, 1)
    window.stop = button('取消处理', window.cancel, 'danger')
    window.stop.setEnabled(False)
    window.stop.hide()
    row.addWidget(window.stop)
    window.start = button('开始处理', window.start_new, 'primary')
    window.start.setMinimumWidth(142)
    row.addWidget(window.start)
    footer_layout.addLayout(row)
    window.progress = QProgressBar()
    window.progress.setRange(0, 6)
    window.progress.setValue(0)
    window.progress.setTextVisible(False)
    window.progress.setFixedHeight(8)
    footer_layout.addWidget(window.progress)
    window.progress_detail = label('尚未开始', 'muted')
    footer_layout.addWidget(window.progress_detail)
    stage_row = QHBoxLayout()
    window.stage_labels = {}
    from progress_state import TITLES
    for key, name in TITLES.items():
        value = label(name, 'muted')
        window.stage_labels[key] = value
        stage_row.addWidget(value)
    footer_layout.addLayout(stage_row)
    window.error_panel, error_body = card('任务需要处理')
    window.error_message = label('', 'error')
    error_body.addWidget(window.error_message)
    recovery_row = QHBoxLayout()
    recovery_row.addWidget(button('继续 / 重试', window.retry_job, 'primary'))
    window.cpu_retry = button('改用 CPU 继续', window.retry_cpu)
    recovery_row.addWidget(window.cpu_retry)
    recovery_row.addWidget(button('查看日志', lambda: window.log_toggle.setChecked(True)))
    window.recovery_action = button('检查设置', window.open_recovery)
    recovery_row.addWidget(window.recovery_action)
    error_body.addLayout(recovery_row)
    footer_layout.addWidget(window.error_panel)
    window.error_panel.hide()
    window.log = QTextEdit()
    window.log.setReadOnly(True)
    window.log.setAcceptRichText(False)
    window.log.document().setMaximumBlockCount(1800)
    window.log.setFixedHeight(140)
    window.log_toggle = fold('处理日志', window.log, footer_layout)
    outer.addWidget(footer)
    return panel


def workspace_geometry(screen, offscreen=False):
    if offscreen:
        return (1600, 1000), (1000, 680)
    available = (max(1, screen.width() - 48), max(1, screen.height() - 48))
    initial = (min(1600, available[0]), min(1000, available[1]))
    minimum = (min(1000, available[0]), min(680, available[1]))
    return initial, minimum


def build_window(window):
    apply_theme(window)
    screen = QApplication.primaryScreen().availableGeometry()
    initial, minimum = workspace_geometry(screen, QApplication.platformName() == 'offscreen')
    window.setMinimumSize(*minimum)
    window.resize(*initial)
    outer = QHBoxLayout(window)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    sidebar = QWidget()
    sidebar.setObjectName('sidebar')
    sidebar.setFixedWidth(208)
    nav = QVBoxLayout(sidebar)
    nav.setContentsMargins(14, 28, 14, 20)
    nav.setSpacing(8)
    icon = label()
    icon.setPixmap(QPixmap(str(ROOT / 'assets/app.png')).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    nav.addWidget(icon)
    brand = label('BVWA', 'heading')
    brand.setProperty('role', 'success')
    brand.setStyleSheet('font-size: 28px; font-weight: 600;')
    nav.addWidget(brand)
    nav.addWidget(label('术力口工作流助手', 'muted'))
    nav.addSpacing(28)
    window.nav_group = QButtonGroup(window)
    titles = ('调音工作流', '模型与组件', '任务与结果', '发布工作流', '设置与更新', '工作流', '缓存与磁盘', '歌词转字幕')
    window.nav_buttons = [None] * len(titles)
    for index in (0, 3, 1, 2, 4):
        title = titles[index]
        item = button(title, lambda checked=False, i=index: window.navigate(i), 'nav')
        item.setCheckable(True)
        window.nav_group.addButton(item)
        nav.addWidget(item)
        window.nav_buttons[index] = item
    nav.addStretch()
    window.theme_picker = QComboBox()
    window.theme_picker.addItem('深色界面', 'dark')
    window.theme_picker.addItem('浅色界面', 'light')
    window.theme_picker.setCurrentIndex(max(0, window.theme_picker.findData(load_config().get('theme', 'light'))))
    window.theme_picker.currentIndexChanged.connect(window.change_theme)
    nav.addWidget(window.theme_picker)
    nav.addWidget(label('分离与 MIDI 在本地运行\n上传后由你确认发布', 'muted'))
    outer.addWidget(sidebar)
    window.pages = QStackedWidget()
    window.tabs = window.pages
    outer.addWidget(window.pages, 1)
    window.pages.addWidget(build_processing(window))
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
    window.pages.addWidget(QWidget())
    window.pages.addWidget(QWidget())
    for control in (window.language, window.zh_lyric_mode, window.voice_mode, window.separator_model, window.roformer_device,
                    window.reference_mode, window.subtitles.match_mode, window.alignment_engine, window.midi_steps, window.separation_device, window.midi_device,
                    window.dml_adapter, window.cpu_threads):
        control.currentIndexChanged.connect(window.quick_options_changed)
    window.separator_model.currentIndexChanged.connect(window.update_component_state)
    window.voice_mode.currentIndexChanged.connect(window.update_import_panel)
    window.language.currentIndexChanged.connect(window.update_lyric_options)
    window.recognize.toggled.connect(window.update_lyric_options)
    for check in (window.recognize, window.midi_enabled, window.subtitles_enabled, window.exports_enabled, window.audio_enabled, window.export_pitch,
                  window.include_instrumental, *window.project_checks.values(), *window.audio_checks.values()):
        check.toggled.connect(window.quick_options_changed)
    window.update_import_panel()
    window.refresh_workflow_summary()
    window.navigate(0)
