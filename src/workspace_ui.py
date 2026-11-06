"""Sidebar workspace and focused processing/results pages."""
from i18n import tr as _tr
import json
from pathlib import Path
from dataclasses import dataclass
from PyQt5.QtCore import QEvent, QObject, QSize, Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
                            QTextEdit, QComboBox, QCheckBox, QProgressBar, QStackedWidget,
                            QButtonGroup, QTreeWidget, QTreeWidgetItem, QHeaderView, QScrollArea, QFrame, QDialog)
from common import ROOT, app_path, load_config, output_directory, job_directories
from desktop_theme import apply_theme, button, card, fold, label, page, scroll_content
from results_ui import ResultsPage


@dataclass(frozen=True)
class PageSpec:
    """One stack slot plus its stable ID and sidebar projection."""

    page_id: str
    legacy_index: int
    title: str
    sidebar_order: int = None
    sidebar_gap_before: bool = False
    english_title: str = None
    target: str = None
    focus_step: str = None


PAGE_SPECS = (
    PageSpec(page_id='processing', legacy_index=0, title='准备工作流', sidebar_order=0),
    PageSpec(page_id='components', legacy_index=1, title='模型与组件', sidebar_order=5,
             sidebar_gap_before=True, english_title='Models'),
    PageSpec(page_id='results', legacy_index=2, title='任务与结果', sidebar_order=6,
             english_title='Tasks and results'),
    PageSpec(page_id='publishing', legacy_index=3, title='发布', sidebar_order=4),
    PageSpec(page_id='updates', legacy_index=4, title='设置与更新', sidebar_order=7,
             english_title='Settings'),
    PageSpec(page_id='workflow', legacy_index=5, title='工作流', target='processing'),
    PageSpec(page_id='storage', legacy_index=6, title='缓存与磁盘', target='updates'),
    PageSpec(page_id='subtitles', legacy_index=7, title='歌词转字幕', target='processing',
             focus_step='subtitles'),
    PageSpec(page_id='video_mixdown', legacy_index=8, title='合成视频', sidebar_order=3),
    PageSpec(page_id='tuning', legacy_index=9, title='调音工作流', sidebar_order=1),
    PageSpec(page_id='audio_mixdown', legacy_index=10, title='简易混音', sidebar_order=2),
)


class WorkspaceNavigation:
    """Project the page registry onto the stack, sidebar and legacy integer API."""

    def __init__(self, window, specs=PAGE_SPECS):
        self.window = window
        self.specs = tuple(specs)
        self._by_id = {spec.page_id: spec for spec in self.specs}
        self._by_index = {spec.legacy_index: spec for spec in self.specs}
        if len(self._by_id) != len(self.specs) or len(self._by_index) != len(self.specs):
            raise RuntimeError('Page IDs and legacy indices must be unique')
        if sorted(self._by_index) != list(range(len(self.specs))):
            raise RuntimeError('Page legacy indices must be contiguous from zero')
        orders = [spec.sidebar_order for spec in self.specs if spec.sidebar_order is not None]
        if len(orders) != len(set(orders)):
            raise RuntimeError('Sidebar positions must be unique')
        for spec in self.specs:
            if spec.target:
                destination = self._by_id.get(spec.target)
                if destination is None or destination.target:
                    raise RuntimeError(f'Page alias {spec.page_id!r} has an invalid target')
        self._widgets = {}
        self._buttons = {}
        self._attached = []

    @property
    def sidebar(self):
        return tuple(sorted(
            (spec for spec in self.specs if spec.sidebar_order is not None),
            key=lambda spec: spec.sidebar_order,
        ))

    def spec(self, page_id):
        try:
            return self._by_id[page_id]
        except KeyError as exc:
            raise KeyError(f'Unknown page ID: {page_id}') from exc

    def resolve(self, destination):
        if isinstance(destination, int):
            spec = self._by_index.get(destination)
        elif isinstance(destination, str):
            spec = self._by_id.get(destination)
        else:
            raise TypeError('Page navigation requires a stable ID or legacy integer index')
        if spec is None:
            raise KeyError(f'Unknown page navigation target: {destination}')
        focus_step = spec.focus_step
        if spec.target:
            spec = self._by_id[spec.target]
            focus_step = focus_step or spec.focus_step
        return spec, focus_step

    def attach(self, page_id, widget=None):
        spec = self.spec(page_id)
        expected = self.specs[len(self._attached)].page_id
        if page_id != expected:
            raise RuntimeError(f'Expected page {expected!r} before {page_id!r}')
        if spec.target:
            if widget is not None:
                raise RuntimeError(f'Page alias {page_id!r} cannot own a widget')
            widget = QWidget()
        elif widget is None:
            raise RuntimeError(f'Page {page_id!r} requires a widget')
        self.window.pages.addWidget(widget)
        self._widgets[page_id] = widget
        self._attached.append(page_id)

    def finish(self):
        if self._attached != [spec.page_id for spec in self.specs]:
            raise RuntimeError('Not all registered pages were attached')
        if self.window.pages.count() != len(self.specs):
            raise RuntimeError('Page stack does not match the registry')

    def widget(self, page_id):
        self.spec(page_id)
        return self._widgets[page_id]

    def bind_button(self, page_id, item):
        spec = self.spec(page_id)
        if spec.sidebar_order is None or spec.target:
            raise RuntimeError(f'Page {page_id!r} is not a sidebar destination')
        self._buttons[page_id] = item

    def button(self, page_id):
        self.spec(page_id)
        return self._buttons.get(page_id)

    def compatibility_buttons(self):
        buttons = [None] * len(self.specs)
        for page_id, item in self._buttons.items():
            buttons[self._by_id[page_id].legacy_index] = item
        return buttons

    def sidebar_title(self, page_id, language):
        spec = self.spec(page_id)
        if language == 'en':
            return spec.english_title or _tr(spec.title)
        return spec.title

    @property
    def current_page_id(self):
        spec = self._by_index.get(self.window.pages.currentIndex())
        return spec.page_id if spec is not None else None


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


class AudioMixdownPage(QWidget):
    """Independent mixer with an optional task association."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        context = QWidget()
        row = QHBoxLayout(context)
        row.setContentsMargins(22, 12, 22, 0)
        self.context = label(_tr('独立文件 · 无需先创建任务'), 'muted')
        row.addWidget(self.context, 1)
        self.choose_task = button(_tr('关联任务…'), self.pick_job)
        row.addWidget(self.choose_task)
        self.clear_task = button(_tr('取消关联'), self.unlink_job)
        self.clear_task.hide()
        row.addWidget(self.clear_task)
        outer.addWidget(context)
        from audio_mixdown_ui import AudioMixdownDialog
        self.mixer = AudioMixdownDialog(owner, None, self, embedded=True)
        outer.addWidget(self.mixer, 1)

    def pick_job(self):
        if self.mixer.busy():
            self.mixer.status.setText(_tr('请等待当前处理结束后再更换关联任务。'))
            return
        from results_ui import TaskPickerDialog
        dialog = TaskPickerDialog(self.owner, self, title=_tr('关联混音任务'), selected=self.mixer.job)
        try:
            if dialog.exec_() == QDialog.Accepted:
                selected = dialog.selection()
                if selected:
                    self.set_job(selected['value'])
        finally:
            dialog.deleteLater()

    def set_job(self, job):
        if self.mixer.busy():
            return False
        from results_ui import task_caption
        self.mixer.set_job(job)
        self.context.setText(_tr('关联任务：') + task_caption(self.owner, self.mixer.job) if job
                             else _tr('独立文件 · 无需先创建任务'))
        self.context.setToolTip(str(self.mixer.job or ''))
        self.clear_task.setVisible(bool(job))
        return True

    def unlink_job(self):
        if not self.set_job(None):
            self.mixer.status.setText(_tr('请等待当前处理结束后再取消关联任务。'))

    def busy(self):
        return self.mixer.busy() or self.mixer.preview_busy()

    def cancel(self):
        self.mixer.cancel()
        self.mixer.cancel_preview()

    def shutdown(self):
        """Cooperatively finish owned workers before the application exits."""
        mixer = self.mixer
        if mixer.busy():
            mixer.cancel()
            return False
        if mixer.support_checking():
            if mixer._support_worker is not None:
                mixer._support_worker.cancel()
            return False
        if mixer._preview_worker is not None:
            mixer.cancel_preview()
            return False
        if not mixer._audition_cleanup_complete:
            mixer._stop_preview_player()
            mixer._shutdown_audition_service()
            if not mixer._audition_cleanup_complete:
                return False
        mixer._retire_preview()
        return True


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
    window.navigation = WorkspaceNavigation(window)
    window.nav_group = QButtonGroup(window)
    for spec in window.navigation.sidebar:
        if spec.sidebar_gap_before:
            nav.addSpacing(16)
        title = window.navigation.sidebar_title(spec.page_id, current_language())
        item = button(title, lambda checked=False, page_id=spec.page_id: window.navigate(page_id), 'nav')
        item.setCheckable(True)
        window.nav_group.addButton(item)
        nav.addWidget(item)
        window.navigation.bind_button(spec.page_id, item)
    window.nav_buttons = window.navigation.compatibility_buttons()
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
    window.processing = build_processing(window)
    window.navigation.attach('processing', window.processing)
    from tuning_ui import TuningPage
    window.tuning = TuningPage(window)
    from components_ui import ComponentsPage
    window.components = ComponentsPage(window)
    window.navigation.attach('components', window.components)
    window.results = ResultsPage(window)
    window.navigation.attach('results', window.results)
    from publishing_ui import PublishingTab
    window.publishing = PublishingTab(window)
    window.navigation.attach('publishing', window.publishing)
    from updates_ui import UpdatesPage
    window.updates = UpdatesPage(window)
    window.navigation.attach('updates', window.updates)
    from processing_workflow import ProcessingWorkflow
    window.workflow = ProcessingWorkflow(window)
    window.navigation.attach('workflow')
    window.workflow_preset.currentIndexChanged.connect(window.choose_processing_preset)
    from storage_ui import StoragePage
    window.storage = StoragePage(window,compact=True)
    window.updates.body_layout.insertWidget(1,window.storage)
    from ui_preferences import AppearancePanel
    window.appearance_panel=AppearancePanel(window)
    window.updates.body_layout.insertWidget(0,window.appearance_panel)
    window.navigation.attach('storage')
    window.navigation.attach('subtitles')
    from video_mixdown_ui import VideoMixdownPage
    window.video_mixdown = VideoMixdownPage(window)
    window.navigation.attach('video_mixdown', window.video_mixdown)
    window.navigation.attach('tuning', window.tuning)
    window.audio_mixdown = AudioMixdownPage(window)
    window.navigation.attach('audio_mixdown', window.audio_mixdown)
    window.navigation.finish()
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
    window.navigate('processing')
    apply_theme(window,window.theme_picker.currentData())
