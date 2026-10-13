"""Four full-area publication steps with visible draft and account controls."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLineEdit,QTextEdit,QCheckBox,QComboBox,QDoubleSpinBox
from desktop_theme import button,card,fold,label
from workflow_widgets import StepWorkspace,CoverPreview


def build(tab):
    outer=QVBoxLayout(tab)
    outer.setContentsMargins(28,24,28,20)
    outer.setSpacing(16)
    outer.addWidget(label('发布工作流','heading'))
    outer.addWidget(label('逐步检查成品、封面和文案，上传后在平台页面确认发布。','muted'))
    tab.workspace=StepWorkspace(('成品','封面','文案','上传'))
    tab.form_panel=tab.workspace.stack
    outer.addWidget(tab.workspace,1)
    tab.workspace.changed.connect(tab.step_changed)
    tab.workspace.next.clicked.disconnect()
    tab.workspace.next.clicked.connect(tab.next_step)
    source,body=tab.workspace.add_step('01 · 成品与来源','选择调音后的成品视频，再关联本家与 STAFF 资料。')
    body.addWidget(label('成品视频','muted'))
    tab.video=QLineEdit();tab.video.setPlaceholderText('选择调音与剪辑完成的视频')
    tab.row(body,'视频',tab.video,'选择视频',tab.choose_video)
    body.addWidget(label('来源任务','muted'))
    tab.job=QLineEdit();tab.job.setReadOnly(True);tab.job.setPlaceholderText('选择本首歌对应的来源任务')
    tab.row(body,'任务',tab.job,'选择任务',tab.choose_job)
    body.addWidget(button('使用当前调音任务',tab.current_job))
    body.addWidget(label('通过 BV 读取本家','muted'))
    tab.bv=QLineEdit();tab.bv.setPlaceholderText('粘贴本家 BV 号或 B站链接')
    row=QHBoxLayout();row.addWidget(tab.bv,1)
    tab.fetch=button('读取本家资料',tab.fetch_source);row.addWidget(tab.fetch);body.addLayout(row)
    tab.source_info=label('来源任务将提供本家、STAFF 和原始封面。','muted')
    tab.source_info.setTextInteractionFlags(Qt.TextSelectableByMouse);body.addWidget(tab.source_info)
    row=QHBoxLayout();row.addWidget(button('载入已有发布包',tab.choose_bundle));row.addWidget(button('打开发布包目录',tab.open_bundle));row.addStretch();body.addLayout(row)
    body.addStretch()

    cover,body=tab.workspace.add_step('02 · 视频封面','使用本家原始封面，或选择自己的图片／视频画面。')
    columns=QHBoxLayout();columns.setSpacing(26)
    settings=QWidget();settings.setMinimumWidth(250);settings.setMaximumWidth(360)
    controls=QVBoxLayout(settings);controls.setContentsMargins(0,0,0,0);controls.setSpacing(18)
    controls.addWidget(label('封面来源','card-title'))
    tab.cover_mode=QComboBox()
    for title,value in [('本家原始封面','original'),('视频画面截图','frame'),('自选图片','custom')]: tab.cover_mode.addItem(title,value)
    controls.addWidget(tab.cover_mode)
    tab.frame_panel=QWidget();frame=QVBoxLayout(tab.frame_panel);frame.setContentsMargins(0,0,0,0)
    frame.addWidget(label('原视频截图时间','muted'))
    tab.cover_seconds=QDoubleSpinBox();tab.cover_seconds.setRange(0,86400);tab.cover_seconds.setDecimals(2);tab.cover_seconds.setValue(20);tab.cover_seconds.setSuffix(' 秒')
    frame.addWidget(tab.cover_seconds);controls.addWidget(tab.frame_panel)
    tab.custom_cover_panel=QWidget();custom=QVBoxLayout(tab.custom_cover_panel);custom.setContentsMargins(0,0,0,0)
    tab.cover_file=QLineEdit();tab.cover_file.setPlaceholderText('JPG / PNG / WebP 图片');custom.addWidget(tab.cover_file);custom.addWidget(button('选择图片',tab.choose_cover));controls.addWidget(tab.custom_cover_panel)
    tab.cover_button=button('获取 / 刷新预览',tab.preview_cover,'primary');controls.addWidget(tab.cover_button)
    controls.addWidget(label('上传时自动生成 4:3 与 16:9 版本，上下或左右补边，保留完整原图。','muted'))
    controls.addStretch();columns.addWidget(settings)
    tab.cover_preview=CoverPreview();columns.addWidget(tab.cover_preview,1);body.addLayout(columns,1)
    tab.cover_detail=label('预览按原图比例显示。','muted');body.addWidget(tab.cover_detail)
    tab.cover_mode.currentIndexChanged.connect(tab.update_cover_options)
    tab.cover_file.textChanged.connect(tab.invalidate_cover_preview)
    tab.job.textChanged.connect(tab.invalidate_cover_preview)
    tab.cover_seconds.valueChanged.connect(tab.invalidate_cover_preview)
    tab.update_cover_options()

    draft,body=tab.workspace.add_step('03 · 标题与简介','填写本次翻唱信息，并分别检查两个平台的文案。')
    tab.song,tab.voice,tab.credit=QLineEdit(),QLineEdit(),QLineEdit()
    for field,text in [(tab.song,'歌名'),(tab.voice,'翻唱歌姬（可留空）'),(tab.credit,'调音署名（可留空）')]: field.setPlaceholderText(text)
    row=QHBoxLayout()
    for title,field in [('歌名',tab.song),('歌姬',tab.voice),('署名',tab.credit)]: row.addWidget(label(title));row.addWidget(field,1)
    body.addLayout(row)
    def template(): tab.apply_title();tab.append_credit()
    body.addWidget(button('应用标题格式与署名',template))
    grid=QGridLayout();grid.setSpacing(24)
    for column,(platform,name) in enumerate([('bilibili','B站'),('xiaohongshu','小红书')]):
        panel,layout=card(name)
        enabled=QCheckBox('上传到'+name);enabled.setChecked(True);layout.addWidget(enabled)
        layout.addWidget(label('标题','muted'))
        title=QLineEdit();title.setPlaceholderText('【歌姬翻唱】歌名，也可自行编辑');layout.addWidget(title)
        layout.addWidget(label('简介与 STAFF','muted'))
        description=QTextEdit();description.setAcceptRichText(False);description.setPlaceholderText('本家、歌姬与 STAFF 列表');description.setMinimumHeight(280);layout.addWidget(description,1)
        counter=label('','muted');layout.addWidget(counter)
        tab.fields[platform]={'enabled':enabled,'title':title,'description':description,'counter':counter}
        for signal in (title.textChanged,description.textChanged): signal.connect(tab.count_text);signal.connect(tab.refresh_action)
        enabled.toggled.connect(tab.refresh_action)
        grid.addWidget(panel,0,column);grid.setColumnStretch(column,1)
    body.addLayout(grid,1)

    upload,body=tab.workspace.add_step('04 · 账号与上传','生成发布资料并上传；验证码与最终发布在平台网页完成。')
    tab.review=label('添加成品视频与来源后，可在这里检查上传资料。','muted');tab.review.setTextInteractionFlags(Qt.TextSelectableByMouse);body.addWidget(tab.review)
    tab.account_status={}
    tab.account_actions={}
    accounts=QGridLayout();accounts.setSpacing(24)
    for column,(platform,name) in enumerate([('bilibili','B站'),('xiaohongshu','小红书')]):
        panel,controls=card(name+'账号与上传')
        status=label('登录后关闭登录窗口，再开始上传。已有登录会话会自动复用。','muted');tab.account_status[platform]=status;controls.addWidget(status)
        actions={
            'login':button('登录 / 更换账号',lambda checked=False,p=platform: tab.launch_browser(p,login_only=True)),
            'resume':button('继续填写当前上传',lambda checked=False,p=platform: tab.resume_browser(p)),
            'stop':button('关闭平台任务',lambda checked=False,p=platform: tab.stop_browser(p),'danger')}
        tab.account_actions[platform]=actions
        for control in actions.values(): controls.addWidget(control)
        accounts.addWidget(panel,0,column);accounts.setColumnStretch(column,1)
    body.addLayout(accounts)
    row=QHBoxLayout();row.addWidget(button('打开发布包目录',tab.open_bundle));row.addWidget(button('载入已有发布包',tab.choose_bundle));row.addStretch()
    body.addLayout(row);body.addStretch()
    tab.prepare_button=button('生成发布资料',tab.primary_action,'primary');tab.prepare_button.setMinimumWidth(180)
    tab.workspace.navigation.addWidget(tab.prepare_button)
    tab.prepare_button.hide()
    tab.status=label('准备就绪 · 添加成品视频与来源资料');outer.addWidget(tab.status)
    tab.log=QTextEdit();tab.log.setReadOnly(True);tab.log.setAcceptRichText(False);tab.log.setFixedHeight(140)
    tab.log_toggle=fold('发布日志',tab.log,outer)
    for field in (tab.job,tab.video,tab.cover_file): field.textChanged.connect(tab.refresh_action)
    tab.cover_mode.currentIndexChanged.connect(tab.refresh_action)
    tab.cover_seconds.valueChanged.connect(tab.refresh_action)
    tab.workspace.refresh()
