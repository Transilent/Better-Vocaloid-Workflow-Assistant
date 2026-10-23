"""Four publication steps with visible drafts, real cover variants, and review."""
from i18n import tr as _tr
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout,
                             QHBoxLayout, QLineEdit, QTextEdit, QVBoxLayout,
                             QWidget)

from desktop_theme import button, card, fold, label
from ui_components import AdaptiveColumns, choice
from workflow_widgets import CoverPreview, StepWorkspace


def _summary_row(layout, key, title, checks):
    row = QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(label(title, 'muted'))
    row.addStretch()
    value = label(_tr('待检查'), 'muted')
    value.setTextInteractionFlags(Qt.TextSelectableByMouse)
    checks[key] = value
    row.addWidget(value)
    layout.addLayout(row)
    return value


def build(tab):
    outer = QVBoxLayout(tab)
    outer.setContentsMargins(28, 24, 28, 20)
    outer.setSpacing(16)
    outer.addWidget(label(_tr('发布'), 'heading'))
    outer.addWidget(label(_tr('核对成品、来源、封面与平台草稿，最后在平台网页确认发布。'), 'muted'))
    tab.page_context = label(_tr('尚未选择成品与来源任务'), 'muted')
    tab.page_context.setTextInteractionFlags(Qt.TextSelectableByMouse)
    outer.addWidget(tab.page_context)

    tab.workspace = StepWorkspace(('成品与来源', '封面', '平台文稿', '核对与填写'))
    tab.form_panel = tab.workspace.stack
    outer.addWidget(tab.workspace, 1)
    tab.workspace.changed.connect(tab.step_changed)
    tab.workspace.next.clicked.disconnect()
    tab.workspace.next.clicked.connect(tab.next_step)

    source, body = tab.workspace.add_step(
        '01 · 成品与来源',
        '选择调音后的成品视频，再关联提供本家资料与简介草稿的任务。',
    )
    video_card, video_controls = card(_tr('成品视频'), _tr('选择本次要发布的成品。'))
    tab.video = QLineEdit()
    tab.video.setPlaceholderText(_tr('选择调音与剪辑完成的视频'))
    tab.row(video_controls, '视频', tab.video, '选择视频', tab.choose_video)
    video_controls.addWidget(label(_tr('生成时会固定当前成品；更换视频后请重新生成。'), 'muted'))
    video_controls.addStretch()

    source_card, source_controls = card(_tr('来源任务'), _tr('提供本家、STAFF、简介和 B站原封面。'))
    tab.job = QLineEdit(tab)
    tab.job.setReadOnly(True)
    tab.job.setPlaceholderText(_tr('选择本首歌对应的来源任务'))
    tab.job.hide()  # Keep the existing path value owner for saved publication data.
    tab.job_display=label(_tr('尚未选择来源任务'),'muted')
    source_controls.addWidget(tab.job_display)
    tab.choose_task_button=button(_tr('从任务与结果选择'),tab.choose_job)
    source_controls.addWidget(tab.choose_task_button)
    source_controls.addWidget(label(_tr('按 BV 读取本家资料'), 'muted'))
    tab.bv = QLineEdit()
    tab.bv.setPlaceholderText(_tr('粘贴本家 BV 号或 B站链接'))
    source_row = QHBoxLayout()
    source_row.addWidget(tab.bv, 1)
    tab.fetch = button(_tr('读取本家资料'), tab.fetch_source)
    source_row.addWidget(tab.fetch)
    source_controls.addLayout(source_row)
    tab.source_info = label(_tr('来源任务会提供本家、STAFF 与 B站原封面。'), 'muted')
    tab.source_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
    source_controls.addWidget(tab.source_info)

    bundle_row = QHBoxLayout()
    bundle_row.addWidget(button(_tr('载入已有发布资料'), tab.choose_bundle))
    bundle_row.addWidget(button(_tr('打开资料目录'), tab.open_bundle))
    bundle_row.addStretch()
    source_controls.addLayout(bundle_row)
    source_controls.addStretch()
    body.addWidget(AdaptiveColumns(video_card, source_card, breakpoint=920, ratio=(3, 2)), 1)

    cover, body = tab.workspace.add_step(
        '02 · 封面',
        '从 B站原封面、来源原视频截图或自选图片开始，生成发布资料时自动制作平台尺寸封面。',
    )
    controls_card, controls = card(_tr('封面来源'), _tr('选择封面来源；下方选项会随类型变化。'))
    tab.cover_mode = QComboBox()
    for title, value in (('B站原封面', 'original'),
                         ('来源原视频截图', 'frame'),
                         ('自选图片', 'custom')):
        tab.cover_mode.addItem(_tr(title), value)
    tab.cover_mode_choice = choice(tab.cover_mode, columns=3, compact=True)
    for option in tab.cover_mode_choice.buttons:
        option.setMinimumHeight(64)
    controls.addWidget(tab.cover_mode_choice)

    tab.frame_panel = QWidget()
    frame = QVBoxLayout(tab.frame_panel)
    frame.setContentsMargins(0, 0, 0, 0)
    frame.setSpacing(8)
    frame.addWidget(label(_tr('截图时间'), 'muted'))
    tab.cover_seconds = QDoubleSpinBox()
    tab.cover_seconds.setRange(0, 86400)
    tab.cover_seconds.setDecimals(2)
    tab.cover_seconds.setValue(20)
    tab.cover_seconds.setSuffix(' 秒')
    frame.addWidget(tab.cover_seconds)
    controls.addWidget(tab.frame_panel)

    tab.custom_cover_panel = QWidget()
    custom = QVBoxLayout(tab.custom_cover_panel)
    custom.setContentsMargins(0, 0, 0, 0)
    custom.setSpacing(8)
    tab.cover_file = QLineEdit()
    tab.cover_file.setPlaceholderText(_tr('JPG / PNG / WebP 图片'))
    custom.addWidget(tab.cover_file)
    custom.addWidget(button(_tr('选择图片'), tab.choose_cover))
    controls.addWidget(tab.custom_cover_panel)

    tab.cover_button = button(_tr('获取 / 刷新来源预览'), tab.preview_cover, 'primary')
    controls.addWidget(tab.cover_button)
    controls.addWidget(label(
        _tr('生成发布资料后，B站 4:3 与 16:9 封面会完整补边。'),
        'muted',
    ))
    controls.addStretch()

    preview_card, preview_controls = card(_tr('封面预览'), _tr('查看原图或 B站补边封面。'))
    tab.cover_asset_mode = QComboBox()
    for title, value in (('原图', 'original'),
                         ('B站 4:3', '4:3'),
                         ('B站 16:9', '16:9')):
        tab.cover_asset_mode.addItem(_tr(title), value)
    tab.cover_asset_choice = choice(tab.cover_asset_mode, columns=3, compact=True)
    preview_controls.addWidget(tab.cover_asset_choice)
    tab.cover_preview = CoverPreview()
    preview_controls.addWidget(tab.cover_preview, 1)
    tab.cover_detail = label(_tr('预览保持原图比例。'), 'muted')
    tab.cover_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
    preview_controls.addWidget(tab.cover_detail)
    tab.cover_asset_hint = label(
        _tr('B站 4:3 与 16:9 封面尚未生成；生成发布资料后可在此查看。'),
        'muted',
    )
    tab.cover_asset_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
    preview_controls.addWidget(tab.cover_asset_hint)
    body.addWidget(AdaptiveColumns(controls_card, preview_card, breakpoint=900, ratio=(1, 2)), 1)

    tab.cover_mode.currentIndexChanged.connect(tab.update_cover_options)
    tab.cover_asset_mode.currentIndexChanged.connect(tab.show_cover_asset)
    tab.cover_file.textChanged.connect(tab.invalidate_cover_preview)
    tab.job.textChanged.connect(tab.invalidate_cover_preview)
    tab.cover_seconds.valueChanged.connect(tab.invalidate_cover_preview)
    tab.update_cover_options()

    draft, body = tab.workspace.add_step(
        '03 · 平台文稿',
        '分别编辑两个平台的标题、简介和启用状态；生成发布资料时会一并保存。',
    )
    basics, basic_controls = card(_tr('本次翻唱信息'), _tr('用于生成标题与署名，之后仍可分别修改两个平台。'))
    tab.song, tab.voice, tab.credit = QLineEdit(), QLineEdit(), QLineEdit()
    for field, text in ((tab.song, '歌名'),
                        (tab.voice, '翻唱歌姬（可留空）'),
                        (tab.credit, '调音署名（可留空）')):
        field.setPlaceholderText(text)
    identity = QGridLayout()
    identity.setHorizontalSpacing(16)
    identity.setVerticalSpacing(8)
    for column, (title, field) in enumerate((('歌名', tab.song),
                                             ('歌姬', tab.voice),
                                             ('署名', tab.credit))):
        identity.addWidget(label(title, 'muted'), 0, column)
        identity.addWidget(field, 1, column)
        identity.setColumnStretch(column, 1)
    basic_controls.addLayout(identity)

    def template():
        tab.apply_title()
        tab.append_credit()

    basic_controls.addWidget(button(_tr('应用标题与署名'), template))
    body.addWidget(basics)

    platform_panels = []
    for platform, name in (('bilibili', 'B站'), ('xiaohongshu', '小红书')):
        panel, controls = card(name + _tr('草稿'), _tr('勾选后才会进入本次发布资料。'))
        enabled = QCheckBox(_tr('纳入 ') + name + _tr(' 发布草稿'))
        enabled.setChecked(True)
        enabled.setAccessibleName(name + _tr(' 发布启用状态'))
        controls.addWidget(enabled)
        draft_state = label(_tr('已启用 · 待核对标题与简介'), 'muted')
        controls.addWidget(draft_state)
        controls.addWidget(label(_tr('标题'), 'muted'))
        title = QLineEdit()
        title.setPlaceholderText(_tr('【歌姬翻唱】歌名，也可自行编辑'))
        title.setMinimumHeight(48)
        controls.addWidget(title)
        controls.addWidget(label(_tr('简介与 STAFF'), 'muted'))
        description = QTextEdit()
        description.setAcceptRichText(False)
        description.setPlaceholderText(_tr('本家、歌姬与 STAFF 列表'))
        description.setMinimumHeight(260)
        controls.addWidget(description, 1)
        counter = label('', 'muted')
        controls.addWidget(counter)
        tab.fields[platform] = {
            'enabled': enabled,
            'title': title,
            'description': description,
            'counter': counter,
            'draft_state': draft_state,
            'panel': panel,
        }
        for signal in (title.textChanged, description.textChanged):
            signal.connect(tab.count_text)
            signal.connect(tab.refresh_action)
        enabled.toggled.connect(tab.count_text)
        enabled.toggled.connect(tab.refresh_action)
        platform_panels.append(panel)
    body.addWidget(AdaptiveColumns(platform_panels[0], platform_panels[1],
                                   breakpoint=1060, ratio=(1, 1)), 1)

    upload, body = tab.workspace.add_step(
        '04 · 核对与填写',
        '核对成品、来源、封面和文稿，再生成发布资料；生成后可打开平台填写。',
    )
    summary_card, summary = card(_tr('核对摘要'), _tr('汇总当前选择与资料状态；这里不代表已在平台发布。'))
    tab.review = label(_tr('完成前三步后，在这里核对发布资料。'), 'muted')
    tab.review.setTextInteractionFlags(Qt.TextSelectableByMouse)
    summary.addWidget(tab.review)
    tab.review_checks = {}
    for key, title in (('video', '成品视频'),
                       ('source', '来源任务'),
                       ('cover', '封面与补边封面'),
                       ('drafts', '平台文稿'),
                       ('bundle', '发布资料'),
                       ('browser', '平台填写')):
        _summary_row(summary, key, title, tab.review_checks)
    summary.addWidget(label(_tr('最终发布始终由你在平台网页确认；助手不会提交最终投稿。'), 'muted'))
    summary.addStretch()

    accounts_card, accounts_controls = card(
        _tr('平台填写'),
        _tr('登录后会保留浏览器会话，方便再次填写。'),
    )
    tab.account_status = {}
    tab.account_actions = {}
    for platform, name in (('bilibili', 'B站'), ('xiaohongshu', '小红书')):
        platform_box, controls = card(name + _tr('账号与填写'), _tr('先登录或复用已有会话。'))
        status = label(_tr('登录完成后请关闭登录窗口，再打开平台填写；已有会话会继续使用。'), 'muted')
        status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tab.account_status[platform] = status
        controls.addWidget(status)
        actions = {
            'login': button(_tr('登录 / 更换账号'),
                            lambda checked=False, p=platform: tab.launch_browser(p, login_only=True)),
            'resume': button(_tr('继续填写当前页面'),
                             lambda checked=False, p=platform: tab.resume_browser(p)),
            'stop': button(_tr('关闭平台任务'),
                           lambda checked=False, p=platform: tab.stop_browser(p), 'danger'),
        }
        tab.account_actions[platform] = actions
        for control in actions.values():
            controls.addWidget(control)
        platform_box.setMinimumHeight(260)
        accounts_controls.addWidget(platform_box)
    accounts_controls.addStretch()
    body.addWidget(AdaptiveColumns(summary_card, accounts_card,
                                   breakpoint=1080, ratio=(3, 2)), 1)

    tab.prepare_button = button(_tr('生成发布资料'), tab.prepare_bundle, 'primary')
    tab.prepare_button.setObjectName('prepare-publication')
    tab.prepare_button.setMinimumHeight(54)
    tab.prepare_button.setMinimumWidth(190)
    tab.upload_button = button(_tr('打开平台填写'), tab.upload_selected)
    tab.upload_button.setObjectName('upload-publication')
    tab.upload_button.setMinimumHeight(54)
    tab.upload_button.setMinimumWidth(190)
    tab.upload_button.setToolTip(_tr('内容一致并生成发布资料后可用。'))
    tab.workspace.actions.addStretch()
    tab.workspace.actions.addWidget(tab.prepare_button, 1)
    tab.workspace.actions.addWidget(tab.upload_button, 1)
    tab.workspace.actions.addStretch()
    tab.prepare_button.hide()
    tab.upload_button.hide()

    tab.status = label(_tr('先添加成品与来源资料'), 'muted')
    tab.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
    outer.addWidget(tab.status)
    tab.log = QTextEdit()
    tab.log.setReadOnly(True)
    tab.log.setAcceptRichText(False)
    tab.log.setFixedHeight(140)
    tab.log.document().setMaximumBlockCount(1800)
    tab.log_toggle = fold(_tr('发布日志'), tab.log, outer)

    for field in (tab.job, tab.video, tab.cover_file):
        field.textChanged.connect(tab.refresh_action)
    tab.cover_mode.currentIndexChanged.connect(tab.refresh_action)
    tab.cover_seconds.valueChanged.connect(tab.refresh_action)
    tab.workspace.refresh()
