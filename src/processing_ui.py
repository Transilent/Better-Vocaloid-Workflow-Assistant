"""Expandable processing steps with shared presets and device controls."""
from i18n import tr as _tr
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
                            QLayout, QComboBox, QCheckBox, QLineEdit,
                            QToolButton, QDialog, QSpinBox, QDoubleSpinBox, QSizePolicy)
from common import ROOT, load_config, output_directory
from desktop_theme import label, button, card, scroll_content


class StepCard(QFrame):
    def __init__(self, title, summary, number, optional=False, expanded=False):
        super().__init__()
        title, summary = _tr(title), _tr(summary)
        self.setObjectName('card')
        layout=QVBoxLayout(self)
        layout.setContentsMargins(18,12,18,12)
        layout.setSpacing(7)
        row=QHBoxLayout()
        row.addWidget(label(number,'success'))
        self.enabled=QCheckBox(title) if optional else None
        if self.enabled:
            self.enabled.setStyleSheet('QCheckBox { font-size: 22px; font-weight: 600; }')
            self.enabled.setChecked(True)
            row.addWidget(self.enabled)
        else: row.addWidget(label(title,'card-title'))
        row.addStretch()
        self.toggle=QToolButton()
        self.toggle.setText(_tr('收起') if expanded else _tr('展开'))
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setStyleSheet('QToolButton { padding: 10px 16px; }')
        self.toggle.setChecked(expanded)
        row.addWidget(self.toggle)
        layout.addLayout(row)
        self.summary=label(summary,'muted')
        self.summary.hide()
        layout.addWidget(self.summary)
        self.details=QWidget()
        self.body=QVBoxLayout(self.details)
        self.body.setContentsMargins(8,20,8,12)
        self.body.setSpacing(20)
        layout.addWidget(self.details)
        self.toggle.toggled.connect(self.expand)
        if self.enabled: self.enabled.toggled.connect(self.details.setEnabled)
        self.expand(expanded)

    def expand(self,value):
        self.details.setVisible(value)
        self.toggle.setText(_tr('收起') if value else _tr('展开'))
        self.toggle.setArrowType(Qt.DownArrow if value else Qt.RightArrow)


class PreparationGrid(QWidget):
    """Arrange the four existing step cards without duplicating their controls."""
    WIDE_BREAKPOINT = 1700
    MEDIUM_BREAKPOINT = 900
    WIDE_RATIOS = (3, 8, 3)

    def __init__(self, source, separation, lyrics, output):
        super().__init__()
        self.source = source
        self.separation = separation
        self.lyrics = lyrics
        self.output = output
        self.cards = (source, separation, lyrics, output)
        self.mode = None
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(20)
        self.grid.setVerticalSpacing(14)
        self.left_group = QWidget(self)
        self.left_group.setObjectName('prep-left-group')
        self.left_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.left_layout = QVBoxLayout(self.left_group)
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(14)
        self.left_layout.setAlignment(Qt.AlignTop)
        self.setObjectName('prep-responsive-grid')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        for card in self.cards:
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.reflow(force=True)

    def _place(self, widget, row, column, row_span=1, column_span=1):
        self.grid.addWidget(widget, row, column, row_span, column_span, Qt.AlignTop)

    def reflow(self, force=False):
        mode = ('wide' if self.width() >= self.WIDE_BREAKPOINT else
                'medium' if self.width() >= self.MEDIUM_BREAKPOINT else 'narrow')
        if mode == self.mode and not force:
            return
        self.mode = mode
        self.grid.removeWidget(self.left_group)
        self.left_group.hide()
        for card in self.cards:
            self.grid.removeWidget(card)
        for card in (self.source, self.separation):
            self.left_layout.removeWidget(card)
            card.setParent(self)
        for column in range(3):
            self.grid.setColumnStretch(column, 0)
        for row in range(4):
            self.grid.setRowStretch(row, 0)
        if mode == 'wide':
            self.left_layout.addWidget(self.source)
            self.left_layout.addWidget(self.separation)
            self.left_group.show()
            self._place(self.left_group, 0, 0)
            self._place(self.lyrics, 0, 1)
            self._place(self.output, 0, 2)
            for column, ratio in enumerate(self.WIDE_RATIOS):
                self.grid.setColumnStretch(column, ratio)
        elif mode == 'medium':
            self._place(self.source, 0, 0)
            self._place(self.output, 0, 1)
            self._place(self.separation, 1, 0, 1, 2)
            self._place(self.lyrics, 2, 0, 1, 2)
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnStretch(1, 1)
        else:
            for row, card in enumerate((self.source, self.output, self.separation, self.lyrics)):
                self._place(card, row, 0)
            self.grid.setColumnStretch(0, 1)
        for card in self.cards:
            card.show()
        self.grid.invalidate()
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def minimumSizeHint(self):
        return QSize(0, self.grid.minimumSize().height())


def combo(items):
    value=QComboBox()
    for text,data in items: value.addItem(_tr(text),data)
    return value


def field_row(body,title,control):
    row=QHBoxLayout()
    caption=label(title)
    caption.setMinimumWidth(100)
    row.addWidget(caption)
    row.addWidget(control,1)
    body.addLayout(row)


def build_steps(window,outer):
    """Build the preparation workspace around the existing workflow controls."""
    from ui_components import AdaptiveColumns, choice, FlowLayout, LyricsEdit

    class TopAlignedColumns(AdaptiveColumns):
        """Prevent a short column from stretching to the height of its neighbour."""
        def reflow(self):
            super().reflow()
            self.grid.setAlignment(self.left, Qt.AlignTop)
            self.grid.setAlignment(self.right, Qt.AlignTop)

    window.step_cards = {}
    window._prep_switching = False

    def adapt(control, columns=2, compact=False):
        view = choice(control, columns=columns, compact=compact)
        view.setObjectName('choice-group')
        return view

    def bounded_edit(placeholder='', height=260, maximum_height=340):
        """Keep a usable editor without letting it occupy a tall wide-screen block."""
        value = LyricsEdit()
        value.setPlaceholderText(_tr(placeholder))
        value.setMinimumHeight(height)
        value.setMaximumHeight(maximum_height)
        return value

    def caption(text, role='card-title'):
        value = label(text, role)
        value.setObjectName('prep-caption')
        return value

    def field_caption(text):
        value = label(text, 'muted')
        value.setObjectName('prep-field-caption')
        value.setMinimumWidth(88)
        return value

    def field_grid(parent, specs, columns=2):
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        for index, (title, control) in enumerate(specs):
            row, col = divmod(index, columns)
            box = QWidget()
            item = QVBoxLayout(box)
            item.setContentsMargins(0, 0, 0, 0)
            item.setSpacing(5)
            if title:
                item.addWidget(field_caption(title))
            if isinstance(control, QWidget):
                item.addWidget(control)
            elif isinstance(control, QLayout):
                item.addLayout(control)
            grid.addWidget(box, row, col)
        for col in range(columns):
            grid.setColumnStretch(col, 1)
        parent.addLayout(grid)
        return grid

    def mode_row(title, view):
        band = QWidget()
        band.setObjectName('prep-mode-row')
        row = QHBoxLayout(band)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(field_caption(title))
        row.addWidget(view, 1)
        return band

    def display_summary(card):
        """A QLabel only needs hiding; new text does not make it visible."""
        card.summary.hide()

    toolbar = QWidget()
    toolbar.setObjectName('prep-toolbar')
    toolbar_layout = FlowLayout(toolbar, spacing=10)
    toolbar_layout.addWidget(field_caption('工作流预设'))
    window.workflow_preset = QComboBox()
    window.workflow_preset.setMinimumWidth(180)
    toolbar_layout.addWidget(window.workflow_preset)
    window.save_preset_button = button(_tr('保存预设'), lambda: window.workflow.save_preset())
    window.delete_preset_button = button(_tr('删除预设'), lambda: window.workflow.delete_preset())
    window.expand_steps = button(_tr('展开全部'), lambda: expand_all(window))
    window.device_button = button(_tr('设备设置'), lambda: window.devices_dialog.exec_())
    for control in (window.save_preset_button, window.delete_preset_button,
                    window.expand_steps, window.device_button):
        control.setMinimumHeight(48)
        toolbar_layout.addWidget(control)
    outer.addWidget(toolbar)
    window.workflow_summary = label('', 'muted')
    window.workflow_summary.hide()
    outer.addWidget(window.workflow_summary)
    window.step_area = scroll_content(outer)
    steps = QVBoxLayout(window.step_area)
    steps.setContentsMargins(0, 0, 0, 0)
    steps.setSpacing(14)
    steps.setAlignment(Qt.AlignTop)

    def step(key, title, summary, number, optional=False, expanded=True):
        value = StepCard(title, summary, number, optional, expanded)
        value.setObjectName('step-surface')
        value.body.setContentsMargins(0, 10, 0, 0)
        value.body.setSpacing(14)
        window.step_cards[key] = value
        steps.addWidget(value)
        return value, value.body

    source, body = step('source', '音乐来源', '添加本地音乐或 B站视频。', '01')
    display_summary(source)
    source.toggle.hide()
    window.input_mode = combo([(_tr('B站链接'), 'bilibili'), (_tr('本地音频'), 'local')])
    window.source_mode_row = mode_row(
        '来源方式', adapt(window.input_mode, columns=2, compact=True))
    source.layout().insertWidget(1, window.source_mode_row)
    window.bv_panel = QWidget()
    bv = QVBoxLayout(window.bv_panel)
    bv.setContentsMargins(0, 0, 0, 0)
    bv.setSpacing(10)
    window.source = QLineEdit(load_config().get('default_source', ''))
    window.source.setPlaceholderText(_tr('粘贴 BV 号或 B站视频链接'))
    window.source.setAccessibleName(_tr('B站视频地址'))
    from bilibili_session import options as bili_options, save_options
    network = bili_options()
    advanced = QDialog(window)
    window.download_advanced_dialog = advanced
    advanced.setWindowTitle(_tr('B站下载与账号'))
    advanced.setMinimumWidth(520)
    controls = QVBoxLayout(advanced)
    controls.setContentsMargins(24, 22, 24, 22)
    controls.setSpacing(12)
    window.download_login = QCheckBox(_tr('下载使用B站登录状态'))
    window.download_login.setChecked(network['use_login'])
    controls.addWidget(window.download_login)
    def login_download():
        window.download_login.setChecked(True)
        window.publishing.launch_browser('bilibili', login_only=True)
        window.status.setText(_tr('请在专用窗口登录B站，完成后关闭窗口，再开始下载。'))
    window.login_button = button(_tr('登录 B站 / 更换账号'), login_download)
    controls.addWidget(window.login_button)
    timeout, retries = QSpinBox(), QSpinBox()
    timeout.setRange(5, 120)
    timeout.setValue(network['timeout'])
    timeout.setSuffix(' 秒')
    retries.setRange(0, 10)
    retries.setValue(network['retries'])
    window.download_timeout = timeout
    window.download_retries = retries
    field_row(controls, '连接超时', timeout)
    field_row(controls, '重试次数', retries)
    controls.addWidget(label(_tr('登录后可使用账号支持的清晰度，登录状态仅保存在本机。'), 'muted'))
    controls.addWidget(button(_tr('完成'), advanced.accept, 'primary'))
    def save_download_options(*args):
        save_options(use_login=window.download_login.isChecked(),
                     timeout=timeout.value(), retries=retries.value())
    window.download_login.toggled.connect(save_download_options)
    timeout.valueChanged.connect(save_download_options)
    retries.valueChanged.connect(save_download_options)
    source_line = QHBoxLayout()
    source_line.setContentsMargins(0, 0, 0, 0)
    source_line.setSpacing(8)
    source_line.addWidget(window.source, 1)
    window.download_settings_button = button(_tr('下载设置'), lambda: advanced.exec_())
    source_line.addWidget(window.download_settings_button)
    bv.addLayout(source_line)
    body.addWidget(window.bv_panel)
    window.music_panel = QWidget()
    music = QHBoxLayout(window.music_panel)
    music.setContentsMargins(0, 0, 0, 0)
    music.setSpacing(8)
    window.local_music = QLineEdit()
    window.local_music.setPlaceholderText(_tr('选择或粘贴音乐文件路径'))
    window.local_music.setAccessibleName(_tr('本地音乐文件'))
    music.addWidget(window.local_music, 1)
    window.choose_music_btn = button(_tr('选择文件'), window.choose_music)
    music.addWidget(window.choose_music_btn)
    body.addWidget(window.music_panel)
    window.input_mode.currentIndexChanged.connect(window.update_source_panel)
    window.update_source_panel()

    output_card = StepCard(_tr('输出内容'), _tr('保留工程与音频输出偏好；MIDI 是基础产物。'), '', False, True)
    output_card.setObjectName('step-surface')
    display_summary(output_card)
    output_card.toggle.hide()
    window.step_cards['output'] = output_card
    window.step_cards['audio'] = output_card
    output = output_card.body
    output.setContentsMargins(0, 10, 0, 0)
    output.setSpacing(8)
    window.exports_enabled = QCheckBox(_tr('工程导出'))
    window.exports_enabled.setChecked(True)
    window.project_options = QWidget()
    projects = FlowLayout(window.project_options, spacing=10)
    window.project_checks = {}
    for fmt, name in [('ustx', 'USTX'), ('vsqx', 'VSQX'), ('svp', 'SVP')]:
        check = QCheckBox(name)
        check.setChecked(True)
        check.setToolTip({'ustx': _tr('OpenUtau 工程'), 'vsqx': _tr('VOCALOID 工程'), 'svp': _tr('Synthesizer V 工程')}[fmt])
        window.project_checks[fmt] = check
        projects.addWidget(check)
    project_line = QHBoxLayout()
    project_line.setContentsMargins(0, 0, 0, 0)
    project_line.setSpacing(10)
    project_line.addWidget(window.exports_enabled)
    project_line.addWidget(window.project_options, 1)
    output.addLayout(project_line)
    window.audio_enabled = QCheckBox(_tr('音频导出'))
    window.audio_enabled.setChecked(True)
    output_card.enabled = window.audio_enabled
    window.audio_checks = {}
    audio_formats = QWidget()
    audio_flow = FlowLayout(audio_formats, spacing=10)
    for fmt in ['wav', 'flac', 'mp3', 'ogg']:
        check = QCheckBox(fmt.upper())
        check.setChecked(fmt == 'wav')
        window.audio_checks[fmt] = check
        audio_flow.addWidget(check)
    audio_line = QHBoxLayout()
    audio_line.setContentsMargins(0, 0, 0, 0)
    audio_line.setSpacing(10)
    audio_line.addWidget(window.audio_enabled)
    audio_line.addWidget(audio_formats, 1)
    output.addLayout(audio_line)
    window.output_panel = QWidget()
    directory = QHBoxLayout(window.output_panel)
    directory.setContentsMargins(0, 0, 0, 0)
    directory.setSpacing(8)
    window.output_dir = QLineEdit()
    initial = output_directory()
    window.output_dir.setText('jobs' if initial == ROOT / 'jobs' else str(initial))
    window.output_dir.setPlaceholderText(_tr('默认：助手目录 / jobs'))
    window.output_dir.setAccessibleName(_tr('保存目录'))
    directory.addWidget(window.output_dir, 1)
    window.choose_output_btn = button(_tr('选择目录'), window.choose_output_directory)
    directory.addWidget(window.choose_output_btn)
    output.addWidget(window.output_panel)
    window.output_summary = label('', 'muted')
    window.output_summary.hide()
    output.addWidget(window.output_summary)

    separation, body = step('separation', '处理方式', '选择如何分离声部。', '02')
    display_summary(separation)
    separation.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    window.voice_mode = combo([(_tr('主唱 / 和声双轨'), 'dual'), (_tr('合并人声 · 单轨 MIDI'), 'single'), (_tr('导入外部分轨'), 'import')])
    # The common separation choice is always available in a single band. Only
    # model details / imported stem paths use the disclosure below it.
    voice_view = adapt(window.voice_mode, columns=3, compact=True)
    window.separation_mode_row = mode_row('分离模式', voice_view)
    separation.layout().insertWidget(1, window.separation_mode_row)
    window.automatic_panel = QWidget()
    automatic = QVBoxLayout(window.automatic_panel)
    automatic.setContentsMargins(0, 0, 0, 0)
    automatic.setSpacing(10)
    window.separator_model = combo([(_tr('Karaoke 2 · 内置'), 'dual'), (_tr('BS-RoFormer · 可选组件'), 'roformer')])
    window.separation_flow = combo([(_tr('先取总人声，再拆分主唱与和声'), 'cascade'), (_tr('从原曲独立提取主唱（实验）'), 'independent')])
    window.separation_flow.setToolTip(_tr('分别提取人声与主唱，再分出和声。仅支持 BS-RoFormer。'))
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    row.addWidget(adapt(window.separator_model, columns=2), 2)
    row.addWidget(adapt(window.separation_flow, columns=2), 3)
    automatic.addLayout(row)
    window.component_status = label('', 'muted')
    automatic.addWidget(window.component_status)
    window.component_link = button(_tr('下载分离组件'), lambda: window.navigate(1))
    automatic.addWidget(window.component_link)
    body.addWidget(window.automatic_panel)
    window.import_panel = QWidget()
    imports = QVBoxLayout(window.import_panel)
    imports.setContentsMargins(0, 0, 0, 0)
    imports.setSpacing(8)
    imports.addWidget(label(_tr('导入从项目起点导出的完整 WAV，包括开头静音。'), 'muted'))
    window.stem_fields = {}
    for key, name in [('lead', '主唱'), ('backing', '和声'), ('instrumental', '伴奏')]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        field = QLineEdit()
        field.setPlaceholderText(name + ' WAV')
        window.stem_fields[key] = field
        row.addWidget(field, 1)
        row.addWidget(button(_tr('选择'), lambda checked=False, k=key: window.choose_stem(k)))
        imports.addLayout(row)
    body.addWidget(window.import_panel)
    separation.toggle.setChecked(False)
    window.voice_mode.currentIndexChanged.connect(
        lambda *args: separation.toggle.setChecked(True)
        if window.voice_mode.currentData() == 'import' else None)
    def show_separation_model(*args):
        if separation.toggle.isChecked():
            text = '收起'
        elif window.voice_mode.currentData() == 'dual':
            text = 'BS-RoFormer' if window.separator_model.currentData() == 'roformer' else 'Karaoke 2'
        else:
            text = '展开设置'
        separation.toggle.setText(text)
    separation.toggle.setAccessibleName(_tr('展开分离模型与导入路径'))
    separation.toggle.setToolTip(_tr('展开分离模型、处理路径和外部分轨设置'))
    separation.toggle.toggled.connect(show_separation_model)
    window.voice_mode.currentIndexChanged.connect(show_separation_model)
    window.separator_model.currentIndexChanged.connect(show_separation_model)
    show_separation_model()

    lyrics_card, lyrics_body = step('reference', '歌词与音符', '填写、导入或匹配参考歌词，并配置歌词识别。', '03')
    display_summary(lyrics_card)
    # Both recovery anchors resolve to the same visible main area. Keep the MIDI
    # stage switch next to its title so the compact output card stays focused.
    window.midi_enabled = QCheckBox(_tr('生成 MIDI 与工程'))
    window.midi_enabled.setChecked(True)
    window.midi_enabled.setAccessibleName(_tr('生成 MIDI 与工程'))
    lyrics_card.layout().itemAt(0).layout().insertWidget(2, window.midi_enabled)
    window.step_cards['midi'] = lyrics_card
    lyrics_card.enabled = window.midi_enabled

    window.reference_mode = combo([(_tr('本地填写'), 'local'), (_tr('网易云匹配'), 'netease')])
    window.reference_mode.setCurrentIndex(window.reference_mode.findData('local'))
    window.netease_reference_panel = QWidget()
    remote = QVBoxLayout(window.netease_reference_panel)
    remote.setContentsMargins(0, 0, 0, 0)
    remote.setSpacing(8)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    window.reference_button = button(_tr('搜索网易云歌词'), window.load_reference)
    row.addWidget(window.reference_button)
    row.addWidget(label(_tr('选中歌曲后填入主唱歌词，请核对演唱版本。'), 'muted'), 1)
    remote.addLayout(row)
    window.reference_status = label(_tr('尚未选择网易云歌曲。'), 'muted')
    remote.addWidget(window.reference_status)
    window.local_reference_panel = QWidget()
    local = QVBoxLayout(window.local_reference_panel)
    local.setContentsMargins(0, 0, 0, 0)
    local.setSpacing(8)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(button(_tr('导入主唱歌词'), lambda: window.import_reference('lead')))
    row.addWidget(button(_tr('导入和声歌词'), lambda: window.import_reference('backing')))
    row.addStretch()
    local.addLayout(row)
    window.local_reference_status = label(_tr('导入歌词或直接填写；留空时从对应人声音频识别歌词。'), 'muted')
    local.addWidget(window.local_reference_status)

    window.lyrics = bounded_edit('主唱原文歌词；请删除署名和非演唱内容。留空时本地识别。')
    window.netease_lyrics = bounded_edit('搜索并选择歌曲后显示原文歌词，可在此修正。')
    window.backing_lyrics = bounded_edit('和声原文歌词（可选；留空时独立识别）')
    window.lyrics.setAccessibleName(_tr('主唱歌词'))
    window.netease_lyrics.setAccessibleName(_tr('网易云主唱歌词'))
    window.backing_lyrics.setAccessibleName(_tr('和声歌词'))

    window.lyric_host = QWidget()
    lyric_host_layout = QVBoxLayout(window.lyric_host)
    lyric_host_layout.setContentsMargins(0, 0, 0, 0)
    lyric_host_layout.setSpacing(8)
    source_row = QHBoxLayout()
    source_row.setContentsMargins(0, 0, 0, 0)
    source_row.setSpacing(10)
    source_row.addWidget(field_caption('歌词来源'))
    source_row.addWidget(adapt(window.reference_mode, columns=2, compact=True), 1)
    lyric_host_layout.addLayout(source_row)
    lyric_host_layout.addWidget(window.local_reference_panel)
    lyric_host_layout.addWidget(window.netease_reference_panel)
    window.lyric_tabs = QWidget()
    tab_row = QHBoxLayout(window.lyric_tabs)
    tab_row.setContentsMargins(0, 0, 0, 0)
    tab_row.setSpacing(6)
    from PyQt5.QtWidgets import QButtonGroup
    window.lyric_tab_group = QButtonGroup(window.lyric_tabs)
    window.lyric_tab_buttons = {}
    for voice, name, active in [('lead', '主唱歌词', True), ('backing', '和声歌词', False)]:
        tab = button(name, None, 'step-nav')
        tab.setCheckable(True)
        tab.setChecked(active)
        window.lyric_tab_group.addButton(tab)
        window.lyric_tab_buttons[voice] = tab
        tab_row.addWidget(tab)
    tab_row.addStretch()
    lyric_host_layout.addWidget(window.lyric_tabs)
    lyric_host_layout.addWidget(window.lyrics)
    lyric_host_layout.addWidget(window.netease_lyrics)
    lyric_host_layout.addWidget(window.backing_lyrics)
    def show_lyrics(voice):
        netease = voice == 'lead' and window.reference_mode.currentData() == 'netease'
        window.local_reference_panel.setVisible(voice == 'lead' and not netease)
        window.netease_reference_panel.setVisible(netease)
        window.lyrics.setVisible(voice == 'lead' and not netease)
        window.netease_lyrics.setVisible(netease)
        window.backing_lyrics.setVisible(voice == 'backing')
        if voice == 'backing':
            window.backing_lyrics.setFocus()
        elif netease:
            window.netease_lyrics.setFocus()
        else:
            window.lyrics.setFocus()
    def select_lyric_tab(voice):
        window.lyric_tab_buttons['lead'].setChecked(voice == 'lead')
        window.lyric_tab_buttons['backing'].setChecked(voice == 'backing')
        show_lyrics(voice)
    window.lyric_tab_buttons['lead'].clicked.connect(lambda checked=False: select_lyric_tab('lead'))
    window.lyric_tab_buttons['backing'].clicked.connect(lambda checked=False: select_lyric_tab('backing'))
    window.relayout_reference = lambda: show_lyrics(
        'backing' if window.lyric_tab_buttons['backing'].isChecked() else 'lead')

    sound_frame = QFrame()
    sound_frame.setObjectName('prep-midi-settings')
    window.note_settings = sound_frame
    sound = QVBoxLayout(sound_frame)
    sound.setContentsMargins(14, 8, 14, 8)
    sound.setSpacing(8)
    sound.setAlignment(Qt.AlignTop)
    window.language = combo([(_tr('中文'), 'zh'), (_tr('日语'), 'ja')])
    window.zh_lyric_mode = combo([(_tr('汉字'), 'hanzi'), (_tr('拼音'), 'pinyin')])
    window.zh_lyric_panel = QWidget()
    zh = QVBoxLayout(window.zh_lyric_panel)
    zh.setContentsMargins(0, 0, 0, 0)
    zh.setSpacing(5)
    zh.addWidget(field_caption('中文格式'))
    zh.addWidget(adapt(window.zh_lyric_mode, columns=2, compact=True))
    field_grid(sound, [('歌词语言', adapt(window.language, columns=2, compact=True)),
                       ('', window.zh_lyric_panel)], columns=2)
    window.manual_bpm = QCheckBox(_tr('手动指定 BPM'))
    window.bpm = QDoubleSpinBox()
    window.bpm.setRange(20., 400.)
    window.bpm.setDecimals(2)
    window.bpm.setValue(120.)
    window.bpm.setSuffix(' BPM')
    window.bpm.setMinimumWidth(160)
    window.bpm.setEnabled(False)
    window.manual_bpm.setToolTip(_tr('未开启时使用 120 BPM 作为时间基准，不自动检测歌曲速度。'))
    window.bpm.setToolTip(_tr('设置 MIDI 和工程的固定速度；保留音符、音高线与伴奏的实际时间。支持小数。'))
    window.manual_bpm.toggled.connect(window.bpm.setEnabled)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(window.manual_bpm)
    row.addWidget(window.bpm, 1)
    sound.addLayout(row)
    window.export_pitch = QCheckBox(_tr('连续音高线'))
    window.export_pitch.setChecked(True)
    window.export_pitch.setToolTip(_tr('将连续音高线写入支持的工程格式。'))
    window.include_instrumental = QCheckBox(_tr('工程引用伴奏'))
    window.include_instrumental.setChecked(True)
    window.include_instrumental.setToolTip(_tr('格式支持时使用相对路径引用伴奏；移动工程需保留任务目录。'))
    project_switches = QWidget()
    project_flow = FlowLayout(project_switches, spacing=10)
    project_flow.addWidget(window.export_pitch)
    project_flow.addWidget(window.include_instrumental)
    sound.addWidget(project_switches)
    aligners = [('HubertFA · 内置', 'hfa')]
    if (ROOT / 'dependencies/vocal2midi2/models/tifa-1.0-onnx/model.onnx').is_file():
        aligners.append(('TiFA', 'tifa'))
    window.alignment_engine = combo(aligners)
    window.midi_steps = combo([(_tr('8 步'), '8'), (_tr('16 步'), '16'), (_tr('32 步'), '32')])
    for index, value in enumerate([8, 16, 32]):
        window.midi_steps.setItemData(index, value)
        window.midi_steps.setItemData(index, f'{value} 步 GAME 推理；更多步数不保证更高识别精度。', Qt.ToolTipRole)
    window.midi_steps.setCurrentIndex(window.midi_steps.findData(16))
    for title, control, columns in [('歌词对齐', window.alignment_engine, 2),
                                    ('提取步数', window.midi_steps, 3)]:
        row = QHBoxLayout()
        row.addWidget(field_caption(title))
        row.addWidget(adapt(control, columns=columns, compact=True), 1)
        sound.addLayout(row)
    window.recognize = QCheckBox(_tr('识别歌词并写入 MIDI'))
    window.recognize.setChecked(True)
    sound.addWidget(window.recognize)
    sound.addWidget(label(_tr('默认 120 BPM 为时间基准，不自动测速。'), 'muted'))
    window.precision = QCheckBox()
    window.precision.setChecked(True)
    window.precision.hide()
    def set_precision(value):
        target = 16 if value else 8
        index = window.midi_steps.findData(target)
        if index >= 0:
            window.midi_steps.setCurrentIndex(index)
    def sync_precision():
        current = window.midi_steps.currentData()
        if isinstance(current, str) and current.isdigit():
            current = int(current)
        window.precision.blockSignals(True)
        window.precision.setChecked(bool(current and current >= 16))
        window.precision.blockSignals(False)
    window.precision.toggled.connect(set_precision)
    window.midi_steps.currentIndexChanged.connect(sync_precision)

    from subtitles_ui import SubtitlesPage
    window.subtitles = SubtitlesPage(window, compact=True)
    window.subtitles_enabled = window.subtitles.auto_generate
    window.subtitles_enabled.setText(_tr('自动生成字幕'))
    window.subtitles_enabled.setToolTip(_tr('开启 MIDI 和歌词识别后，按演唱时间生成字幕。'))
    old_subtitle_layout = window.subtitles_enabled.parentWidget().layout()
    if old_subtitle_layout is not None:
        old_subtitle_layout.removeWidget(window.subtitles_enabled)
    window.subtitle_dialog = QDialog(window)
    window.subtitle_dialog.setWindowTitle(_tr('歌词转字幕'))
    window.subtitle_dialog.resize(920, 680)
    window.subtitle_dialog.setMinimumSize(680, 500)
    subtitle_dialog_layout = QVBoxLayout(window.subtitle_dialog)
    subtitle_dialog_layout.setContentsMargins(20, 18, 20, 18)
    subtitle_dialog_layout.setSpacing(12)
    subtitle_dialog_layout.addWidget(label(_tr('时间轴匹配、轨选择与导入导出'), 'card-title'))
    subtitle_card = StepCard(_tr('字幕工作区'), _tr('使用网易云时间轴或 MIDI 歌词事件。'), '05', False, True)
    subtitle_card.setObjectName('step-surface')
    display_summary(subtitle_card)
    window.step_cards['subtitles'] = subtitle_card
    subtitle_card.body.setContentsMargins(0, 10, 0, 0)
    subtitle_card.body.addWidget(window.subtitles)
    subtitle_scroll = scroll_content(subtitle_dialog_layout)
    subtitle_scroll_layout = QVBoxLayout(subtitle_scroll)
    subtitle_scroll_layout.setContentsMargins(0, 0, 0, 0)
    subtitle_scroll_layout.addWidget(subtitle_card)
    subtitle_dialog_layout.addWidget(button(_tr('完成'), window.subtitle_dialog.accept, 'primary'))
    window.subtitles_dialog = window.subtitle_dialog
    window.subtitle_dialog_layout = subtitle_dialog_layout

    subtitle_tools = QWidget()
    subtitle_tools.setObjectName('prep-subtitles')
    subtitle_layout = FlowLayout(subtitle_tools, spacing=12)
    subtitle_layout.setContentsMargins(14, 12, 14, 12)
    window.subtitle_tools = subtitle_tools
    window.subtitle_layout = subtitle_layout
    subtitle_layout.addWidget(caption('交付与字幕'))
    subtitle_layout.addWidget(window.subtitles_enabled)
    window.subtitle_button = button(_tr('打开字幕工具'), lambda: window.subtitle_dialog.exec_())
    window.open_subtitles_button = window.subtitle_button
    subtitle_layout.addWidget(window.subtitle_button)

    window.lyrics_columns = TopAlignedColumns(window.lyric_host, sound_frame,
                                              breakpoint=880, ratio=(6, 5))
    window.lyrics_columns.setObjectName('prep-lyrics-columns')
    window.lyrics_columns.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    lyrics_body.addWidget(window.lyrics_columns)
    lyrics_body.addWidget(subtitle_tools)

    # Reuse the four original cards in a purpose-built responsive grid. Wide
    # screens put output at the far right; medium screens keep the familiar
    # source/output row and let lyrics use the full row below it.
    steps.removeWidget(source)
    steps.removeWidget(output_card)
    steps.removeWidget(separation)
    steps.removeWidget(lyrics_card)
    window.prep_workspace = PreparationGrid(source, separation, lyrics_card, output_card)
    window.prep_workspace.setObjectName('prep-responsive-workspace')
    steps.addWidget(window.prep_workspace)
    steps.addStretch(1)
    window.reference_mode.currentIndexChanged.connect(window.update_reference_options)
    window.reference_mode.currentIndexChanged.connect(lambda *args: window.relayout_reference())
    window.update_reference_options()
    show_lyrics('lead')

    window.devices_dialog = QDialog(window)
    window.devices_dialog.setWindowTitle(_tr('工作流 · 处理设备'))
    window.devices_dialog.resize(840, 680)
    device_layout = QVBoxLayout(window.devices_dialog)
    device_layout.setContentsMargins(24, 22, 24, 22)
    device_layout.setSpacing(12)
    devices, device_body = card(_tr('处理设备'), _tr('设备选择随工作流预设保存。'))
    device_body.setContentsMargins(18, 16, 18, 16)
    device_body.setSpacing(10)
    window.device_panel = devices
    window.separation_device = combo([('CPU', 'cpu'), ('GPU / DirectML', 'dml')])
    window.midi_device = combo([('CPU', 'cpu'), ('GPU / DirectML', 'dml')])
    window.roformer_device = combo([(_tr('自动 · CUDA 优先'), 'auto'), ('CPU', 'cpu'), ('NVIDIA GPU / CUDA', 'cuda')])
    window.dml_adapter = combo([(_tr('自动选择独立显卡'), 'auto')])
    from runtime_devices import adapters
    for adapter in adapters():
        if not adapter.is_software:
            window.dml_adapter.addItem(
                f'{adapter.index} · {adapter.name} · {adapter.dedicated_vram_bytes / 1024**3:.1f} GiB',
                adapter.index)
    window.cpu_threads = combo([(_tr('自动 · 引擎默认'), 0), ('2', 2), ('4', 4), ('8', 8), ('16', 16)])
    device_fields = [('内置分离', window.separation_device), ('MIDI 提取', window.midi_device),
                     ('BS-RoFormer', window.roformer_device),
                     ('DirectML 显卡 · 分离与 MIDI 共用', window.dml_adapter),
                     ('CPU 线程数', window.cpu_threads)]
    for title, control in device_fields:
        device_body.addWidget(field_caption(title))
        device_body.addWidget(adapt(control, columns=2, compact=True))
    device_body.addWidget(label(_tr('遇到驱动或显存问题时可切换到 CPU。BS-RoFormer 的自动模式优先使用 CUDA。'), 'muted'))
    device_content = scroll_content(device_layout)
    device_body_container = QVBoxLayout(device_content)
    device_body_container.setContentsMargins(0, 0, 0, 0)
    device_body_container.addWidget(devices)
    device_layout.addWidget(button(_tr('完成'), window.devices_dialog.accept, 'primary'))

def expand_all(window):
    cards=[item for item in window.step_cards.values() if not item.toggle.isHidden()]
    expanded=not all(item.toggle.isChecked() for item in cards)
    for item in cards: item.toggle.setChecked(expanded)
    window.expand_steps.setText(_tr('收起全部') if expanded else _tr('展开全部'))


def calibration_context(window, context=None):
    """Compatibility entry point for legacy callers; implementation lives in tuning_ui."""
    from tuning_ui import generate_calibration
    kwargs = {} if context is None else {'context': context}
    return generate_calibration(window, **kwargs)
