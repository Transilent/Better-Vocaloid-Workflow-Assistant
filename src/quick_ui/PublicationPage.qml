import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    objectName: "publicationPage"
    signal taskVideoRequested()
    required property QtObject theme
    required property QtObject controller

    readonly property var view: controller.view || ({})
    readonly property int step: page.clamp(page.number(view.step, 0), 0, 2)
    readonly property bool busy: !!view.busy
    readonly property bool dense: width < Math.round(760 * theme.scale) || height < Math.round(500 * theme.scale)
    readonly property bool compact: width < Math.round(900 * theme.scale)
    readonly property int pageMargin: Math.round((dense ? 13 : 20) * theme.scale)
    readonly property string platform: page.platformValue(view.platform)
    readonly property string titleText: page.textOf(view.title)
    readonly property string descriptionText: page.textOf(view.description)
    readonly property string tagsText: page.textOf(view.tags)
    readonly property string jobText: page.textOf(view.job)
    readonly property string videoText: page.textOf(view.video)
    readonly property string bundleText: page.textOf(view.bundle)
    readonly property bool bundleStale: !!view.bundleStale
    readonly property bool verified: !!view.verified
    readonly property bool canPrepare: !!view.canPrepare && !busy
    readonly property bool canCheck: !!view.canCheck && !busy && bundleText.length > 0 && !bundleStale
    readonly property bool canHandoff: !busy && page.handoffReady(page.platform)
    readonly property string stateName: page.textOf(view.state).toLowerCase()
    readonly property string messageText: page.textOf(view.message)
    readonly property color stateColor: page.colorForState()
    readonly property string stateLabel: page.labelForState()
    readonly property var actionItems: page.actionsFrom(view.actions)
    readonly property string coverPreviewUrl: page.textOf(view.coverPreview)
    readonly property real coverSeconds: Math.max(0, page.number(view.coverSeconds, 20))
    readonly property var selectedPlatforms: page.platformList(view.selectedPlatforms)
    readonly property bool bilibiliSelected: page.platformSelected("bilibili")
    readonly property bool xiaohongshuSelected: page.platformSelected("xiaohongshu")
    readonly property var handoffs: page.objectOf(view.handoffs)
    readonly property var browserSession: page.objectOf(view.browserSession)
    readonly property var browserChecks: page.objectOf(page.browserSession.checks)
    readonly property string coverRatio: page.textOf(view.coverRatio || "16:9") === "4:3" ? "4:3" : "16:9"
    readonly property var coverVariants: page.arrayOf(view.coverVariants)
    readonly property var selectedCoverVariant: page.coverVariantFor(page.coverRatio)
    readonly property string resolvedCoverPreview: page.platform === "bilibili"
        && page.selectedCoverVariant.preview
        ? page.textOf(page.selectedCoverVariant.preview) : page.coverPreviewUrl
    readonly property real coverAspect: page.coverRatio === "4:3" ? 4 / 3 : 16 / 9
    readonly property int coverCanvasHeight: Math.round((page.dense ? 150 : 226) * page.theme.scale)
    readonly property var taskChoices: page.arrayOf(view.taskChoices)
    property string taskSelection: ""

    property bool sourceOptionsExpanded: false
    readonly property var review: page.objectOf(view.review)
    readonly property bool hasVideo: !!review.videoReady
    readonly property bool showDraftEditors: hasVideo || view.bundleOnly === true
    readonly property var platformReview: page.objectOf(page.objectOf(review.platforms)[page.platform])

    onVideoTextChanged: if (videoText.length) sourceOptionsExpanded = false

    function reviewItems() {
        const cover = review.coverState
        const bundle = review.packageState
        return [
            { key: "video", step: 0, label: controller.text("成品视频"),
              status: hasVideo && !review.videoProblem ? controller.text("已关联") : controller.text("待检查"),
              problem: review.videoProblem || "" },
            { key: "cover", step: 1, label: controller.text("封面"),
              status: cover === "previewed" ? controller.text("已预览")
                  : cover === "configured" ? controller.text("待预览") : controller.text("待选择"),
              problem: review.coverProblem || "" },
            { key: "copy", step: 0, label: platformLabel(platform) + " " + controller.text("文稿"),
              status: hasVideo && !platformReview.copyProblem ? controller.text("已填写") : controller.text("待核对"),
              problem: platformReview.copyProblem || "" },
            { key: "declarations", step: 0, label: controller.text("声明"),
              status: hasVideo && !platformReview.declarationProblem ? controller.text("已填写") : controller.text("待填写"),
              problem: platformReview.declarationProblem || "" },
            { key: "package", step: 2, label: controller.text("发布资料"),
              status: bundle === "verified" ? controller.text("本地已核验")
                  : bundle === "stale" ? controller.text("已过期")
                  : bundle === "unchecked" ? controller.text("待核验") : controller.text("未生成"),
              problem: "" }
        ]
    }

    function openReviewItem(item) {
        controller.reviewStep(item.step)
        Qt.callLater(() => {
            if (page.step !== item.step)
                return
            if (item.key === "copy" && publicationTitleInput.visible)
                publicationTitleInput.forceActiveFocus()
            else if (item.key === "declarations" && aiCombo.visible)
                aiCombo.forceActiveFocus()
            const flick = contentScroll.contentItem
            const focus = item.key === "copy" ? publicationTitleInput
                : item.key === "declarations" ? aiCombo : null
            flick.contentY = focus && focus.visible
                ? Math.max(0, Math.min(focus.mapToItem(publicationContent, 0, 0).y - page.pageMargin,
                    flick.contentHeight - contentScroll.availableHeight)) : 0
        })
    }

    function number(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback;
        const parsed = Number(value);
        return isFinite(parsed) ? parsed : fallback;
    }

    function clamp(value, low, high) {
        return Math.max(low, Math.min(high, value));
    }

    function textOf(value) {
        return value === undefined || value === null ? "" : String(value);
    }

    function fileName(value) {
        let text = page.textOf(value).replace(/[\\/]+$/, "");
        if (!text)
            return "";
        const parts = text.split(/[\\/]/);
        return parts[parts.length - 1] || text;
    }

    function platformValue(value) {
        const normalized = page.textOf(value).toLowerCase();
        return normalized === "xiaohongshu" ? "xiaohongshu" : "bilibili";
    }

    function platformIndex() {
        return page.platform === "xiaohongshu" ? 1 : 0;
    }

    function platformName() {
        return page.platform === "xiaohongshu"
            ? page.controller.text("小红书") : page.controller.text("B站");
    }

    function coverModeValue(value) {
        const normalized = page.textOf(value).toLowerCase();
        return normalized === "frame" || normalized === "custom" || normalized === "original-frame" ? normalized : "original";
    }

    function coverModeIndex() {
        const mode = page.coverModeValue(page.view.coverMode);
        return mode === "frame" ? 1 : mode === "custom" ? 2 : mode === "original-frame" ? 3 : 0;
    }

    function coverModeName() {
        const mode = page.coverModeValue(page.view.coverMode);
        if (mode === "frame")
            return page.controller.text("成品视频截帧");
        if (mode === "original-frame")
            return page.controller.text("本家原视频截帧");
        if (mode === "custom")
            return page.controller.text("自选图片");
        return page.controller.text("本家原始封面");
    }

    Dialog {
        id: creditDialog
        objectName: "publicationCreditDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(parent.width - 32, 490 * page.theme.scale)
        modal: true
        title: page.controller.text("生成标题和署名")
        ColumnLayout {
            anchors.fill: parent
            ThemedTextField {
                id: creditSong
                objectName: "publicationCreditSong"
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: page.controller.text("歌名")
                maximumLength: 80
            }
            ThemedTextField {
                id: creditVoice
                objectName: "publicationCreditVoice"
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: page.controller.text("本次歌姬")
                maximumLength: 80
            }
            ThemedTextField {
                id: creditName
                objectName: "publicationCreditName"
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: page.controller.text("本次调音署名")
                maximumLength: 80
            }
            CheckBox {
                id: creditAll
                visible: page.selectedPlatforms.length > 1
                text: page.controller.text("同时应用到已选平台")
            }
            RowLayout {
                Layout.fillWidth: true
                UiButton {
                    theme: page.theme
                    text: page.controller.text("仅添加署名")
                    enabled: !!(creditVoice.text.trim() || creditName.text.trim())
                    onClicked: {
                        if (page.controller.applyTitleCredits("", creditVoice.text, creditName.text, false, creditAll.checked))
                            creditDialog.close()
                    }
                }
                UiButton {
                    objectName: "publicationApplyCredits"
                    theme: page.theme
                    primary: true
                    text: page.controller.text("应用标题和署名")
                    enabled: !!creditSong.text.trim()
                    onClicked: {
                        if (page.controller.applyTitleCredits(creditSong.text, creditVoice.text, creditName.text, true, creditAll.checked))
                            creditDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: browserLog
        objectName: "publicationBrowserLogDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(parent.width - 36, 760 * page.theme.scale)
        height: Math.min(parent.height - 40, 450 * page.theme.scale)
        modal: true
        title: page.controller.text("B站填写日志")
        standardButtons: Dialog.Close
        onAboutToShow: logText.text = page.controller.browserLogTail()
        ScrollView {
            anchors.fill: parent
            TextArea {
                id: logText
                objectName: "publicationBrowserLogText"
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                color: page.theme.text
            }
        }
    }

    function aiIndex() {
        const value = page.view.ai;
        if (value === true)
            return 1;
        if (value === false)
            return 2;
        const normalized = page.textOf(value).toLowerCase();
        if (normalized === "true" || normalized === "yes" || normalized === "contains")
            return 1;
        if (normalized === "false" || normalized === "no" || normalized === "none")
            return 2;
        return 0;
    }

    function aiValueAt(index) {
        if (index === 1)
            return true;
        if (index === 2)
            return false;
        return null;
    }

    function taskModel() {
        const rows = [{ label: page.controller.text("选择来源任务…"), value: "" }]
        for (let index = 0; index < page.taskChoices.length; ++index) {
            const row = page.taskChoices[index]
            if (row && String(row.value || "").length > 0)
                rows.push({ label: String(row.label || row.value), value: String(row.value) })
        }
        return rows
    }

    function taskLabel() {
        if (page.jobText.length === 0)
            return page.controller.text("尚未选择来源任务")
        return page.fileName(page.jobText)
    }

    function aiLabel() {
        const index = page.aiIndex();
        if (index === 1)
            return page.controller.text("含 AI 生成或合成内容");
        if (index === 2)
            return page.controller.text("不含 AI 生成或合成内容");
        return page.controller.text("尚未确认 AI 内容声明");
    }

    function rightsText() {
        const value = page.view.rights;
        if (typeof value === "boolean")
            return value ? page.controller.text("已核对原创、转载、署名与授权") : "";
        return page.textOf(value);
    }

    function descriptionPreview() {
        const text = page.descriptionText;
        return text.length > 420 ? text.slice(0, 420) + "…" : text;
    }

    function contextLine() {
        const parts = [];
        if (page.jobText.length > 0) {
            parts.push(page.controller.text("任务") + " · " + page.fileName(page.jobText));
            parts.push(page.selectedPlatformSummary() + " · " + page.platformName() + page.controller.text("文稿"));
        }
        if (page.titleText.length > 0)
            parts.push(page.titleText);
        if (page.videoText.length > 0)
            parts.push(page.controller.text("视频") + " · " + page.fileName(page.videoText));
        return parts.join("  ·  ");
    }

    function colorForState() {
        const state = page.stateName;
        if (page.bundleStale || state === "failed" || state === "error" || state === "stale")
            return theme.error;
        if (page.verified || state === "verified" || state === "ready")
            return theme.success;
        if (page.busy || state === "preparing" || state === "checking" || state === "running")
            return theme.accent;
        return theme.muted;
    }

    function labelForState() {
        if (page.busy)
            return page.controller.text("正在更新发布资料…");
        if (page.bundleStale)
            return page.controller.text("输入已变化，旧发布包已失效");
        if (page.verified)
            return page.controller.text("本地发布资料已核对");
        if (page.bundleText.length > 0)
            return page.controller.text("发布资料已生成，等待核对");
        return page.controller.text("尚未生成发布资料");
    }

    function actionsFrom(source) {
        const actions = [];
        if (!source)
            return actions;
        if (Array.isArray(source)) {
            for (let index = 0; index < source.length; ++index) {
                const item = source[index];
                if (item === undefined || item === null || item === false)
                    continue;
                if (typeof item === "string") {
                    actions.push({ id: item, label: page.actionLabel(item) });
                } else {
                    const id = page.textOf(item.id || item.action || item.key);
                    if (id.length === 0)
                        continue;
                    actions.push({ id: id, label: page.textOf(item.label || item.title) || page.actionLabel(id) });
                }
            }
            return actions;
        }
        if (typeof source === "object") {
            const keys = Object.keys(source);
            for (let index = 0; index < keys.length; ++index) {
                const key = keys[index];
                const item = source[key];
                if (item === false || item === null || item === undefined)
                    continue;
                let label = page.actionLabel(key);
                if (item === true) {
                    // Keep the controller-provided action identifier.
                } else if (typeof item === "object") {
                    label = page.textOf(item.label || item.title) || label;
                } else {
                    label = page.textOf(item) || label;
                }
                actions.push({ id: key, label: label });
            }
        }
        return actions;
    }

    function actionLabel(id) {
        const normalized = page.textOf(id).toLowerCase();
        if (normalized === "video" || normalized === "成品")
            return page.controller.text("打开成品视频");
        if (normalized === "cover" || normalized === "封面")
            return page.controller.text("打开封面");
        if (normalized === "bundle" || normalized === "folder" || normalized === "发布包")
            return page.controller.text("打开发布包");
        if (normalized === "checklist" || normalized === "核对")
            return page.controller.text("打开核对清单");
        return page.textOf(id);
    }

    function summaryValue(value, fallback) {
        const text = page.textOf(value);
        return text.length > 0 ? text : page.controller.text(fallback);
    }

    function arrayOf(value) {
        return value && typeof value.length === "number" ? value : [];
    }

    function objectOf(value) {
        return value && typeof value === "object" && typeof value.length !== "number" ? value : ({});
    }

    function platformList(value) {
        const items = page.arrayOf(value);
        return items.length > 0 ? items : ["bilibili"];
    }

    function platformSelected(id) {
        return page.selectedPlatforms.indexOf(id) >= 0;
    }

    function platformLabel(id) {
        return id === "xiaohongshu" ? page.controller.text("小红书") : page.controller.text("B站");
    }

    function selectedPlatformSummary() {
        const labels = [];
        for (let index = 0; index < page.selectedPlatforms.length; ++index)
            labels.push(page.platformLabel(page.selectedPlatforms[index]));
        return labels.join(page.controller.text("、"));
    }

    function handoffFor(id) {
        const value = page.handoffs[id];
        return value && typeof value === "object" ? value : ({});
    }

    function handoffReady(id) {
        const value = page.handoffFor(id);
        return page.verified && !page.bundleStale && value.ready === true;
    }

    function coverVariantFor(ratio) {
        for (let index = 0; index < page.coverVariants.length; ++index) {
            const item = page.coverVariants[index];
            if (item && page.textOf(item.ratio) === ratio)
                return item;
        }
        return ({});
    }

    function coverRatioSize() {
        return page.coverRatio === "4:3" ? "1440 × 1080" : "1920 × 1080";
    }

    function browserCheck(key, label) {
        return {
            key: key,
            label: page.controller.text(label),
            ready: page.browserChecks[key] === true
        };
    }

    function browserChecksModel() {
        return [
            page.browserCheck("video_selected", "已选择视频"),
            page.browserCheck("upload_complete", "上传完成"),
            page.browserCheck("title_filled", "标题已填写"),
            page.browserCheck("description_filled", "简介已填写"),
            page.browserCheck("cover_filled", "封面已填写")
        ];
    }

    function handoffAiLabel(id) {
        const fields = page.objectOf(page.handoffFor(id).fields);
        if (fields.ai_generated === true)
            return page.controller.text("含 AI 生成或合成内容");
        if (fields.ai_generated === false)
            return page.controller.text("不含 AI 生成或合成内容");
        return page.controller.text("未记录 AI 声明");
    }

    function browserStateLabel() {
        const state = page.textOf(page.browserSession.state).toLowerCase();
        if (state === "waiting_login") return page.controller.text("等待网页登录");
        if (state === "login_window_closed") return page.controller.text("登录窗口已关闭");
        if (state === "opening_browser" || state === "starting") return page.controller.text("正在打开会话");
        if (state === "opening_editor") return page.controller.text("正在载入填写页");
        if (state === "uploading") return page.controller.text("正在上传视频");
        if (state === "needs_manual") return page.controller.text("需要人工处理");
        if (state === "cancelled") return page.controller.text("填写已停止");
        if (state === "failed") return page.controller.text("会话失败");
        if (state === "stopping") return page.controller.text("正在停止");
        if (page.browserSession.active) return page.controller.text("会话进行中");
        if (state === "closed") return page.controller.text("会话已关闭");
        return page.controller.text("尚未启动");
    }

    function browserStateColor() {
        const state = page.textOf(page.browserSession.state).toLowerCase();
        if (state === "failed" || state === "needs_manual") return page.theme.error;
        if (state === "cancelled" || state === "closed" || state === "login_window_closed") return page.theme.muted;
        if (page.browserSession.active || state === "starting" || state === "stopping") return page.theme.accent;
        return page.theme.muted;
    }

    FileDialog {
        id: publicationVideoFileDialog
        objectName: "publicationVideoFileDialog"
        title: page.controller.text("选择成品视频")
        nameFilters: [
            page.controller.text("视频 (*.mp4 *.mov *.mkv *.webm)"),
            page.controller.text("所有文件 (*)")
        ]
        fileMode: FileDialog.OpenFile
        onAccepted: page.controller.chooseVideo(selectedFile)
    }

    FolderDialog {
        id: publicationBundleFolderDialog
        objectName: "publicationBundleFolderDialog"
        title: page.controller.text("选择发布资料文件夹")
        onAccepted: page.controller.importBundle(selectedFolder)
    }

    FileDialog {
        id: coverFileDialog
        title: page.controller.text("选择封面图片")
        nameFilters: [page.controller.text("图片 (*.jpg *.jpeg *.png *.webp)"), page.controller.text("所有文件 (*)")]
        fileMode: FileDialog.OpenFile
        onAccepted: page.controller.chooseCover(selectedFile)
    }

    component LabelText: Text {
        id: label
        required property QtObject theme
        color: label.theme.text
        font.pixelSize: Math.round(12 * label.theme.scale)
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    component HelperText: Text {
        id: helper
        required property QtObject theme
        color: helper.theme.muted
        font.pixelSize: Math.round(11 * helper.theme.scale)
        lineHeight: 1.18
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    component ThemedTextField: TextField {
        id: input
        required property QtObject theme

        implicitHeight: Math.round(36 * input.theme.scale)
        leftPadding: Math.round(10 * input.theme.scale)
        rightPadding: leftPadding
        topPadding: Math.round(7 * input.theme.scale)
        bottomPadding: topPadding
        color: input.theme.text
        placeholderTextColor: input.theme.muted
        font.pixelSize: Math.round(12 * input.theme.scale)
        selectByMouse: true
        persistentSelection: true
        focusPolicy: Qt.StrongFocus
        selectionColor: input.theme.accent
        selectedTextColor: input.theme.base
        Accessible.name: placeholderText
        background: Rectangle {
            radius: Math.round(7 * input.theme.scale)
            color: input.enabled ? input.theme.surface : input.theme.base
            border.width: 1
            border.color: input.activeFocus ? input.theme.accent : input.theme.line
            opacity: input.enabled ? 1 : 0.58
        }
    }

    component ThemedTextArea: TextArea {
        id: area
        required property QtObject theme

        color: area.theme.text
        placeholderTextColor: area.theme.muted
        font.pixelSize: Math.round(12 * area.theme.scale)
        selectByMouse: true
        persistentSelection: true
        wrapMode: TextEdit.Wrap
        textFormat: TextEdit.PlainText
        focusPolicy: Qt.StrongFocus
        selectionColor: area.theme.accent
        selectedTextColor: area.theme.base
        topPadding: Math.round(9 * area.theme.scale)
        bottomPadding: topPadding
        leftPadding: Math.round(10 * area.theme.scale)
        rightPadding: leftPadding
        Accessible.name: placeholderText
        background: Rectangle {
            radius: Math.round(7 * area.theme.scale)
            color: area.enabled ? area.theme.surface : area.theme.base
            border.width: 1
            border.color: area.activeFocus ? area.theme.accent : area.theme.line
            opacity: area.enabled ? 1 : 0.58
        }
    }

    component SurfaceCard: Rectangle {
        id: card
        required property QtObject theme
        property string heading: ""
        property string explanation: ""
        readonly property int cardPadding: Math.round(13 * theme.scale)
        default property alias contentData: cardBody.data

        implicitHeight: cardBody.implicitHeight + cardPadding * 2
        color: card.theme.surface
        radius: Math.round(9 * card.theme.scale)
        border.width: 1
        border.color: card.theme.line

        ColumnLayout {
            id: cardBody
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: card.cardPadding
            spacing: Math.round(7 * card.theme.scale)

            Text {
                Layout.fillWidth: true
                visible: card.heading.length > 0
                text: card.heading
                color: card.theme.text
                font.pixelSize: Math.round(14 * card.theme.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }

            Text {
                Layout.fillWidth: true
                visible: card.explanation.length > 0
                text: card.explanation
                color: card.theme.muted
                font.pixelSize: Math.round(11 * card.theme.scale)
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
        }
    }

    component SummaryRow: RowLayout {
        id: summary
        required property QtObject theme
        property string caption: ""
        property string value: ""
        property bool pathValue: false
        property color valueColor: theme.text

        Layout.fillWidth: true
        spacing: Math.round(10 * theme.scale)

        Text {
            Layout.preferredWidth: Math.round(72 * summary.theme.scale)
            Layout.alignment: Qt.AlignTop
            text: summary.caption
            color: summary.theme.muted
            font.pixelSize: Math.round(11 * summary.theme.scale)
            textFormat: Text.PlainText
        }

        Text {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: summary.value
            color: summary.valueColor
            font.pixelSize: Math.round(11 * summary.theme.scale)
            wrapMode: summary.pathValue ? Text.WrapAnywhere : Text.Wrap
            textFormat: Text.PlainText
        }
    }

    Rectangle {
        anchors.fill: parent
        color: page.theme.base
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: headerColumn.implicitHeight + Math.round(18 * page.theme.scale)
            color: page.theme.base

            ColumnLayout {
                id: headerColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: page.pageMargin
                anchors.rightMargin: page.pageMargin
                anchors.topMargin: Math.round(9 * page.theme.scale)
                spacing: Math.round(3 * page.theme.scale)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(10 * page.theme.scale)

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: Math.round(2 * page.theme.scale)

                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("发布")
                            color: page.theme.text
                            font.pixelSize: Math.round(22 * page.theme.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }

                        Text {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: page.contextLine().length > 0
                                ? page.contextLine()
                                : page.controller.text("先关联已完成任务和成品视频")
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                            ToolTip.visible: contextHover.hovered && truncated
                            ToolTip.text: text
                            HoverHandler { id: contextHover }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: !page.dense
                            text: page.controller.text("核对素材、封面和文案，再按平台手动交接；这里不会代替你提交发布。")
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                    }

                    BusyIndicator {
                        Layout.preferredWidth: Math.round(24 * page.theme.scale)
                        Layout.preferredHeight: Layout.preferredWidth
                        running: page.busy
                        visible: page.busy
                        Accessible.name: page.controller.text("发布资料正在处理")
                    }

                    UiButton {
                        objectName: "publicationCancel"
                        theme: page.theme
                        text: page.busy ? page.controller.text("取消操作") : page.controller.text("退出引导")
                        quiet: true
                        onClicked: page.controller.requestExit()
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    visible: page.bundleText.length > 0 && page.bundleStale
                    Layout.preferredHeight: staleHeader.implicitHeight + Math.round(10 * page.theme.scale)
                    color: Qt.rgba(page.theme.attention.r, page.theme.attention.g, page.theme.attention.b, 0.12)
                    radius: Math.round(6 * page.theme.scale)
                    border.width: 1
                    border.color: page.theme.attention

                    Text {
                        id: staleHeader
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Math.round(9 * page.theme.scale)
                        anchors.rightMargin: Math.round(9 * page.theme.scale)
                        text: page.controller.text("旧发布包与当前输入不一致，请重新生成后再核对或交接。")
                        color: page.theme.attention
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: page.messageText.length > 0
                    text: page.messageText
                    color: page.stateColor
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
            }
        }

        Rectangle {
            objectName: "publicationStepper"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(43 * page.theme.scale)
            color: page.theme.sidebar
            Rectangle { width: parent.width; height: 1; color: page.theme.line }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: page.pageMargin
                anchors.rightMargin: page.pageMargin
                spacing: Math.round(6 * page.theme.scale)

                Repeater {
                    model: [
                        page.controller.text("素材与文案"),
                        page.controller.text("封面"),
                        page.controller.text("核对与交接")
                    ]
                    delegate: Rectangle {
                        id: stepCard
                        objectName: "publicationStep_" + index
                        required property string modelData
                        required property int index

                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        Layout.preferredHeight: Math.round(31 * page.theme.scale)
                        radius: Math.round(7 * page.theme.scale)
                        color: index === page.step ? page.theme.selected
                            : index < page.step ? Qt.rgba(page.theme.success.r, page.theme.success.g, page.theme.success.b, 0.1)
                            : "transparent"
                        border.width: index === page.step ? 1 : 0
                        border.color: page.theme.selectedLine

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Math.round(7 * page.theme.scale)
                            anchors.rightMargin: Math.round(7 * page.theme.scale)
                            spacing: Math.round(6 * page.theme.scale)

                            Rectangle {
                                Layout.preferredWidth: Math.round(18 * page.theme.scale)
                                Layout.preferredHeight: Layout.preferredWidth
                                radius: width / 2
                                color: index === page.step ? page.theme.accent
                                    : index < page.step ? page.theme.success : page.theme.raised

                                Text {
                                    anchors.centerIn: parent
                                    text: index < page.step ? "✓" : String(index + 1)
                                    color: index === page.step ? page.theme.onAccent
                                        : index < page.step ? page.theme.base : page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: modelData
                                color: index <= page.step ? page.theme.text : page.theme.muted
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                font.weight: index === page.step ? Font.DemiBold : Font.Normal
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            objectName: "publicationReviewStrip"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            visible: page.hasVideo || page.bundleText.length > 0
            spacing: Math.round(4 * page.theme.scale)

            HelperText {
                theme: page.theme
                text: page.controller.text("交付检查")
            }

            Repeater {
                model: page.reviewItems()
                delegate: UiButton {
                    id: reviewButton
                    required property var modelData
                    objectName: "publicationReview_" + modelData.key
                    theme: page.theme
                    text: modelData.label + " · " + modelData.status
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    implicitHeight: Math.round(29 * page.theme.scale)
                    enabled: !page.busy
                    quiet: true
                    selected: modelData.key === "package" && page.review.packageState === "stale"
                    ToolTip.visible: (hovered || visualFocus) && modelData.problem.length > 0
                    ToolTip.text: modelData.problem
                    onClicked: page.openReviewItem(modelData)
                }
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
        }

        ScrollView {
            id: contentScroll
            objectName: "publicationContentScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            ColumnLayout {
                id: publicationContent
                width: contentScroll.availableWidth
                spacing: 0

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.margins: page.pageMargin
                    Layout.bottomMargin: Math.round(10 * page.theme.scale)
                    visible: page.step === 0
                    spacing: Math.round(10 * page.theme.scale)

                    SurfaceCard {
                        objectName: "publicationInputEntry"
                        Layout.fillWidth: true
                        theme: page.theme
                        heading: page.hasVideo ? page.fileName(page.videoText) : page.controller.text("先带入要发布的成品")
                        explanation: page.hasVideo
                            ? (page.jobText.length ? page.controller.text("来源任务") + " · " + page.fileName(page.jobText)
                                                   : page.controller.text("独立发布资料"))
                            : page.controller.text("从任务带入核验过的视频，或选择本地视频、导入已有资料。")

                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            UiButton {
                                objectName: "publicationUseTaskVideo"
                                theme: page.theme
                                primary: !page.hasVideo
                                text: page.controller.text("从任务带入成品")
                                enabled: !page.busy
                                onClicked: page.taskVideoRequested()
                            }

                            UiButton {
                                objectName: "publicationLocalOptions"
                                theme: page.theme
                                selected: page.sourceOptionsExpanded
                                text: page.controller.text(page.sourceOptionsExpanded ? "收起本地素材设置"
                                    : page.hasVideo ? "更换来源或视频…" : "选择本地视频…")
                                enabled: !page.busy
                                onClicked: page.sourceOptionsExpanded = !page.sourceOptionsExpanded
                            }

                            UiButton {
                                objectName: "publicationBundleImport"
                                theme: page.theme
                                quiet: true
                                text: page.controller.text("导入已有发布资料…")
                                enabled: !page.busy
                                onClicked: publicationBundleFolderDialog.open()
                            }
                        }
                    }

                    SurfaceCard {
                        Layout.fillWidth: true
                        visible: page.sourceOptionsExpanded
                        theme: page.theme
                        heading: page.controller.text("来源任务与本地视频")
                        explanation: page.controller.text("先载入来源任务或本家资料，再选择本地成品。")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(7 * page.theme.scale)

                            TextField {
                                id: publicationTaskFilter
                                objectName: "publicationTaskFilter"
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                placeholderText: page.controller.text("搜索任务名或 BV")
                                selectByMouse: true
                                enabled: !page.busy
                                onAccepted: page.controller.refreshTaskChoices(text)
                                font.pixelSize: 12 * page.theme.scale
                            }

                            UiButton {
                                objectName: "publicationTaskRefresh"
                                theme: page.theme
                                text: page.controller.text(page.view.taskScanBusy ? "读取中…" : "刷新任务")
                                enabled: !page.busy
                                onClicked: page.controller.refreshTaskChoices(publicationTaskFilter.text)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(7 * page.theme.scale)

                            UiComboBox {
                                id: publicationTaskPicker
                                objectName: "publicationTaskPicker"
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                theme: page.theme
                                model: page.taskModel()
                                enabled: !page.busy && model.length > 1
                                onActivated: page.taskSelection = String(currentValue || "")
                            }

                            UiButton {
                                objectName: "publicationTaskLoad"
                                theme: page.theme
                                text: page.controller.text("载入任务字段")
                                enabled: !page.busy && page.taskSelection.length > 0
                                onClicked: page.controller.chooseTask(page.taskSelection)
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.textOf(page.view.taskMessage)
                                || page.controller.text("可先刷新任务，再单独选择成品视频或导入已有发布资料。")
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            ThemedTextField {
                                id: publicationBv
                                objectName: "publicationBv"
                                theme: page.theme
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                placeholderText: page.controller.text("BV 号或本家视频链接")
                                enabled: !page.busy
                                onAccepted: if (text.trim()) page.controller.fetchSourceMetadata(text)
                            }
                            UiButton {
                                objectName: "publicationFetchBv"
                                theme: page.theme
                                text: page.controller.text("读取本家资料")
                                enabled: !page.busy && !!publicationBv.text.trim()
                                onClicked: page.controller.fetchSourceMetadata(publicationBv.text)
                            }
                        }

                        SummaryRow {
                            theme: page.theme
                            caption: page.controller.text("任务")
                            value: page.jobText.length > 0 ? page.jobText : page.controller.text("未关联任务")
                            pathValue: true
                            valueColor: page.jobText.length > 0 ? page.theme.text : page.theme.muted
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(7 * page.theme.scale)

                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: page.videoText.length > 0
                                    ? page.controller.text("成品视频") + " · " + page.fileName(page.videoText)
                                    : page.controller.text("尚未单独选择成品视频")
                                color: page.videoText.length > 0 ? page.theme.text : page.theme.muted
                                font.pixelSize: Math.round(11.5 * page.theme.scale)
                                elide: Text.ElideMiddle
                                textFormat: Text.PlainText
                            }

                            UiButton {
                                objectName: "publicationVideoChoose"
                                theme: page.theme
                                text: page.videoText.length > 0
                                    ? page.controller.text("更换视频…") : page.controller.text("选择视频…")
                                enabled: !page.busy && page.jobText.length > 0
                                onClicked: publicationVideoFileDialog.open()
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.view.bundleOnly === true
                                ? page.controller.text("当前是独立导入资料：可核对和手动交接，生成新包前仍需选择来源任务与成品。")
                                : page.controller.text("从任务载入和单独选择成品互不代替；导入既有资料不会关联或改写来源任务。")
                        }
                    }

                    GridLayout {
                        objectName: "publicationDraftEditors"
                        Layout.fillWidth: true
                        visible: page.showDraftEditors
                        columns: page.compact ? 1 : 2
                        columnSpacing: Math.round(8 * page.theme.scale)
                        rowSpacing: Math.round(8 * page.theme.scale)

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("平台选择")
                            explanation: page.controller.text("选中的平台共用这一份冻结成品与封面，但各自保留独立文稿和交接状态。")

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("加入同一个发布包")
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(7 * page.theme.scale)

                                UiButton {
                                    objectName: "publicationPlatformBilibili"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("B站")
                                    selected: page.bilibiliSelected
                                    enabled: !page.busy
                                    Accessible.checkable: true
                                    Accessible.checked: page.bilibiliSelected
                                    onClicked: page.controller.togglePlatform("bilibili", !page.bilibiliSelected)
                                }

                                UiButton {
                                    objectName: "publicationPlatformXiaohongshu"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("小红书")
                                    selected: page.xiaohongshuSelected
                                    enabled: !page.busy
                                    Accessible.checkable: true
                                    Accessible.checked: page.xiaohongshuSelected
                                    onClicked: page.controller.togglePlatform("xiaohongshu", !page.xiaohongshuSelected)
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("当前包：") + page.selectedPlatformSummary()
                                    + (page.selectedPlatforms.length > 1
                                        ? page.controller.text("。两段文稿会分别核对。")
                                        : page.controller.text("。可再加入另一平台。"))
                            }

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("正在编辑的独立文稿")
                            }

                            UiComboBox {
                                id: platformCombo
                                objectName: "publicationPlatform"
                                Layout.fillWidth: true
                                theme: page.theme
                                model: [
                                    { label: page.controller.text("B站文稿"), value: "bilibili" },
                                    { label: page.controller.text("小红书文稿"), value: "xiaohongshu" }
                                ]
                                currentIndex: page.platformIndex()
                                enabled: !page.busy
                                Accessible.name: page.controller.text("当前编辑平台")
                                onActivated: page.controller.editPlatform(currentValue)
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.platform === "xiaohongshu"
                                    ? page.controller.text("小红书继续使用复制文稿、本地素材和官方创作中心手动投稿。")
                                    : page.controller.text("B站会生成 4:3 与 16:9 补边封面；网页登录、继续填写和最终发布均由你操作。")
                            }
                        }

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("标题")

                            UiButton {
                                objectName: "publicationTitleHelper"
                                theme: page.theme
                                quiet: true
                                text: page.controller.text("标题与署名…")
                                enabled: !page.busy && !page.view.bundleOnly
                                onClicked: creditDialog.open()
                            }

                            ThemedTextField {
                                id: publicationTitleInput
                                objectName: "publicationTitle"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.titleText
                                placeholderText: page.controller.text("发布标题")
                                Accessible.name: page.controller.text("发布标题")
                                enabled: !page.busy
                                onTextEdited: page.controller.setField("title", text)
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("标题字数上限由所选平台的本地检查规则验证。")
                            }
                        }
                    }

                    SurfaceCard {
                        Layout.fillWidth: true
                        visible: page.showDraftEditors
                        theme: page.theme
                        heading: page.controller.text("简介与 STAFF")

                        ScrollView {
                            id: descriptionScroll
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.round((page.dense ? 112 : 154) * page.theme.scale)
                            clip: true
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                            ScrollBar.vertical.policy: ScrollBar.AsNeeded

                            ThemedTextArea {
                                objectName: "publicationDescription"
                                width: descriptionScroll.availableWidth
                                height: Math.max(implicitHeight, descriptionScroll.availableHeight)
                                theme: page.theme
                                text: page.descriptionText
                                placeholderText: page.controller.text("填写简介，并保留本家、歌姬与 STAFF 信息")
                                Accessible.name: page.controller.text("发布简介")
                                enabled: !page.busy
                                onTextEdited: page.controller.setField("description", text)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)
                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("简介会原样进入交接，不会截断或替你补全署名。")
                            }
                            HelperText {
                                Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                theme: page.theme
                                text: String(page.descriptionText.length)
                            }
                        }
                    }

                    SurfaceCard {
                        Layout.fillWidth: true
                        visible: page.showDraftEditors
                        theme: page.theme
                        heading: page.controller.text("标签与声明")
                        explanation: page.controller.text("这些记录进入本地交接清单；平台上的实际声明仍需你本人选择。")

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.compact ? 1 : 2
                            columnSpacing: Math.round(8 * page.theme.scale)
                            rowSpacing: Math.round(7 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: Math.round(4 * page.theme.scale)
                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("标签")
                                }
                                ThemedTextField {
                                    objectName: "publicationTags"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.tagsText
                                    placeholderText: page.controller.text("以空格或逗号分隔")
                                    Accessible.name: page.controller.text("发布标签")
                                    enabled: !page.busy
                                    onTextEdited: page.controller.setField("tags", text)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: Math.round(4 * page.theme.scale)
                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("AI 内容")
                                }
                                UiComboBox {
                                    id: aiCombo
                                    objectName: "publicationAI"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        { label: page.controller.text("请选择（不确定时先核对素材）"), value: null },
                                        { label: page.controller.text("含 AI 生成或合成内容"), value: true },
                                        { label: page.controller.text("不含 AI 生成或合成内容"), value: false }
                                    ]
                                    currentIndex: page.aiIndex()
                                    enabled: !page.busy
                                    Accessible.name: page.controller.text("AI 内容声明")
                                    onActivated: page.controller.setField("ai", page.aiValueAt(currentIndex))
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            spacing: Math.round(4 * page.theme.scale)
                            LabelText {
                                theme: page.theme
                                text: page.controller.text("原创 / 转载、署名与授权")
                            }
                            ThemedTextField {
                                objectName: "publicationRights"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.rightsText()
                                placeholderText: page.controller.text("记录本次核对结论或授权说明")
                                Accessible.name: page.controller.text("原创、转载、署名与授权")
                                enabled: !page.busy
                                onTextEdited: page.controller.setField("rights", text)
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.platform === "xiaohongshu"
                                ? page.controller.text("小红书：发布时请亲自设置“笔记内容声明”、原创/转载与授权；本页不会代填。")
                                : page.controller.text("B站：请在上传页亲自核对分区、标签、原创/转载与其他必填声明；本页不会代填。")
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.margins: page.pageMargin
                    Layout.bottomMargin: Math.round(10 * page.theme.scale)
                    visible: page.step === 1
                    spacing: Math.round(10 * page.theme.scale)

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.compact ? 1 : 2
                        columnSpacing: Math.round(10 * page.theme.scale)
                        rowSpacing: Math.round(10 * page.theme.scale)

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("封面来源")
                            explanation: page.controller.text("发布包会固定当前选择；修改后请重新生成。")

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("来源")
                            }

                            UiComboBox {
                                id: coverModeCombo
                                Layout.fillWidth: true
                                theme: page.theme
                                model: [
                                    { label: page.controller.text("本家原始封面"), value: "original" },
                                    { label: page.controller.text("成品视频截帧"), value: "frame" },
                                    { label: page.controller.text("自选图片"), value: "custom" }
                                ].concat(page.view.canFetchOriginalCover || page.coverModeIndex() === 3
                                    ? [{ label: page.controller.text("本家原视频截帧"), value: "original-frame" }] : [])
                                currentIndex: page.coverModeIndex()
                                enabled: !page.busy
                                Accessible.name: page.controller.text("封面来源")
                                onActivated: page.controller.setField("coverMode", currentValue)
                            }

                            UiButton {
                                objectName: "publicationFetchOriginalCover"
                                theme: page.theme
                                quiet: true
                                visible: !!page.view.canFetchOriginalCover
                                text: page.controller.text("获取或刷新本家封面")
                                enabled: !page.busy
                                onClicked: page.controller.refreshOriginalCover()
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: ["frame", "original-frame"].indexOf(page.coverModeValue(page.view.coverMode)) >= 0
                                spacing: Math.round(8 * page.theme.scale)
                                LabelText {
                                    Layout.alignment: Qt.AlignVCenter
                                    theme: page.theme
                                    text: page.controller.text("截图时间")
                                }
                                ThemedTextField {
                                    id: coverSecondsInput
                                    objectName: "publicationCoverSeconds"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.coverSeconds.toFixed(2)
                                    validator: DoubleValidator { bottom: 0; top: 86399.99; decimals: 2; notation: DoubleValidator.StandardNotation }
                                    enabled: !page.busy
                                    Accessible.name: page.controller.text("封面截图时间（秒）")
                                    onEditingFinished: {
                                        const seconds = Number.fromLocaleString(Qt.locale(), text)
                                        if (acceptableInput && isFinite(seconds))
                                            page.controller.setField("coverSeconds", seconds)
                                        text = page.coverSeconds.toFixed(2)
                                    }
                                }
                                Text {
                                    Layout.alignment: Qt.AlignVCenter
                                    text: page.controller.text("秒")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(11 * page.theme.scale)
                                    textFormat: Text.PlainText
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                visible: page.coverModeValue(page.view.coverMode) === "custom"
                                spacing: Math.round(5 * page.theme.scale)
                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("图片文件")
                                }
                                ThemedTextField {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.textOf(page.view.coverFile)
                                    placeholderText: "JPG / PNG / WebP"
                                    readOnly: true
                                    Accessible.name: page.controller.text("自选封面文件")
                                }
                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.textOf(page.view.coverFile).length > 0
                                        ? page.controller.text("更换封面图片…") : page.controller.text("选择封面图片…")
                                    enabled: !page.busy
                                    onClicked: coverFileDialog.open()
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.coverModeValue(page.view.coverMode) === "original"
                                    ? page.controller.text("使用来源任务登记的本家封面；本地任务没有原封面时会由控制器拒绝。")
                                    : page.coverModeValue(page.view.coverMode) === "frame"
                                        ? page.controller.text("从当前已核验成品视频截取画面；成品不可读取时会由控制器拒绝。")
                                        : page.coverModeValue(page.view.coverMode) === "original-frame"
                                        ? page.controller.text("从本家原视频截帧。缺少原视频时，预览或生成会先补齐。")
                                        : page.controller.text("支持 JPG、PNG 或 WebP；生成发布包时会统一为可用封面。")
                            }

                            UiButton {
                                Layout.fillWidth: true
                                theme: page.theme
                                primary: page.coverPreviewUrl.length === 0
                                text: page.busy ? page.controller.text("正在准备预览…")
                                    : page.coverPreviewUrl.length > 0 ? page.controller.text("刷新封面预览") : page.controller.text("查看封面预览")
                                enabled: !page.busy
                                onClicked: page.controller.previewCover()
                            }
                        }

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("封面预览")
                            explanation: page.coverModeName()

                            RowLayout {
                                Layout.fillWidth: true
                                visible: page.bilibiliSelected
                                spacing: Math.round(7 * page.theme.scale)

                                LabelText {
                                    Layout.alignment: Qt.AlignVCenter
                                    theme: page.theme
                                    text: page.controller.text("B站比例")
                                }

                                UiButton {
                                    objectName: "publicationBilibiliCover43"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: "4:3"
                                    selected: page.coverRatio === "4:3"
                                    enabled: !page.busy
                                    Accessible.checkable: true
                                    Accessible.checked: page.coverRatio === "4:3"
                                    onClicked: page.controller.setCoverRatio("4:3")
                                }

                                UiButton {
                                    objectName: "publicationBilibiliCover169"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: "16:9"
                                    selected: page.coverRatio === "16:9"
                                    enabled: !page.busy
                                    Accessible.checkable: true
                                    Accessible.checked: page.coverRatio === "16:9"
                                    onClicked: page.controller.setCoverRatio("16:9")
                                }
                            }

                            Rectangle {
                                id: coverPreviewPanel
                                Layout.fillWidth: true
                                Layout.preferredHeight: page.coverCanvasHeight
                                color: page.theme.base
                                radius: Math.round(7 * page.theme.scale)
                                border.width: 1
                                border.color: page.theme.line
                                clip: true

                                Rectangle {
                                    id: coverPreviewCanvas
                                    objectName: "publicationCoverCanvas"
                                    width: Math.min(coverPreviewPanel.width - Math.round(10 * page.theme.scale),
                                                    (coverPreviewPanel.height - Math.round(10 * page.theme.scale)) * page.coverAspect)
                                    height: width / page.coverAspect
                                    anchors.centerIn: parent
                                    radius: Math.round(4 * page.theme.scale)
                                    color: "#050506"
                                    clip: true

                                    Image {
                                        anchors.fill: parent
                                        source: page.resolvedCoverPreview
                                        visible: page.resolvedCoverPreview.length > 0 && status === Image.Ready
                                        fillMode: Image.PreserveAspectFit
                                        asynchronous: true
                                        cache: false
                                    }

                                    ColumnLayout {
                                        anchors.centerIn: parent
                                        width: Math.min(parent.width - Math.round(20 * page.theme.scale), Math.round(260 * page.theme.scale))
                                        visible: page.resolvedCoverPreview.length === 0 || coverImage.status !== Image.Ready
                                        spacing: Math.round(4 * page.theme.scale)

                                        Text {
                                            Layout.fillWidth: true
                                            text: page.resolvedCoverPreview.length === 0
                                                ? page.controller.text("尚未生成预览")
                                                : coverImage.status === Image.Error
                                                    ? page.controller.text("预览无法读取") : page.controller.text("正在读取预览")
                                            color: page.resolvedCoverPreview.length > 0 && coverImage.status === Image.Error
                                                ? page.theme.error : page.theme.muted
                                            font.pixelSize: Math.round(12 * page.theme.scale)
                                            horizontalAlignment: Text.AlignHCenter
                                            wrapMode: Text.Wrap
                                            textFormat: Text.PlainText
                                        }

                                        HelperText {
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: page.controller.text("本地预览；不会上传素材。")
                                            horizontalAlignment: Text.AlignHCenter
                                        }
                                    }
                                }

                                Image {
                                    id: coverImage
                                    source: page.resolvedCoverPreview
                                    visible: false
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.bilibiliSelected
                                    ? page.controller.text("当前画布为 B站 ") + page.coverRatio
                                        + page.controller.text("，生成文件 ") + page.coverRatioSize()
                                        + page.controller.text("；比例不同时补黑边，完整保留原图。")
                                    : page.controller.text("小红书使用同一张封面上传；平台尺寸与裁切请在发布页亲自检查。")
                            }

                            HelperText {
                                Layout.fillWidth: true
                                visible: page.bilibiliSelected && page.coverVariants.length > 0
                                theme: page.theme
                                text: (page.selectedCoverVariant.path
                                    ? page.controller.text("当前显示发布包内的 ") + page.coverRatio
                                        + page.controller.text(" 适配文件：") + page.fileName(page.selectedCoverVariant.file)
                                    : page.controller.text("发布包缺少当前比例适配文件；请在生成后重新核对。"))
                            }
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.margins: page.pageMargin
                    Layout.bottomMargin: Math.round(10 * page.theme.scale)
                    visible: page.step === 2
                    spacing: Math.round(10 * page.theme.scale)

                    SurfaceCard {
                        Layout.fillWidth: true
                        theme: page.theme
                        heading: page.controller.text("发布资料状态")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)
                            Rectangle {
                                Layout.preferredWidth: Math.round(8 * page.theme.scale)
                                Layout.preferredHeight: Layout.preferredWidth
                                radius: width / 2
                                color: page.stateColor
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: page.stateLabel
                                color: page.stateColor
                                font.pixelSize: Math.round(12 * page.theme.scale)
                                font.weight: Font.DemiBold
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: page.messageText.length > 0
                            text: page.messageText
                            color: page.stateName === "failed" || page.stateName === "error" ? page.theme.error : page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            visible: page.bundleText.length > 0 && page.bundleStale
                            Layout.preferredHeight: staleReview.implicitHeight + Math.round(12 * page.theme.scale)
                            color: Qt.rgba(page.theme.error.r, page.theme.error.g, page.theme.error.b, 0.1)
                            radius: Math.round(6 * page.theme.scale)
                            border.width: 1
                            border.color: page.theme.error

                            Text {
                                id: staleReview
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: Math.round(9 * page.theme.scale)
                                anchors.rightMargin: Math.round(9 * page.theme.scale)
                                text: page.controller.text("当前任务、视频、平台、文案、声明或封面已变化。请重新生成发布资料，旧包不能再用于核对或交接。")
                                color: page.theme.error
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: page.bundleText.length > 0
                                ? page.bundleText
                                : page.controller.text("尚未生成发布包")
                            color: page.bundleText.length > 0 ? page.theme.text : page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.WrapAnywhere
                            textFormat: Text.PlainText
                        }

                        ProgressBar {
                            Layout.fillWidth: true
                            visible: page.busy
                            indeterminate: !page.theme.reduceMotion
                            Accessible.name: page.controller.text("发布资料处理进度")
                        }
                    }

                    SurfaceCard {
                        Layout.fillWidth: true
                        theme: page.theme
                        heading: page.controller.text("平台交接")
                        explanation: page.controller.text("同一冻结发布包内，各平台分别核验、分别交接；一个平台的操作不会提交另一个平台。")

                        Repeater {
                            model: page.selectedPlatforms
                            delegate: Rectangle {
                                id: handoffPlate
                                required property string modelData
                                readonly property var handoff: page.handoffFor(modelData)
                                readonly property bool ready: page.handoffReady(modelData)

                                Layout.fillWidth: true
                                implicitHeight: handoffColumn.implicitHeight + Math.round(20 * page.theme.scale)
                                radius: Math.round(7 * page.theme.scale)
                                color: page.platform === modelData ? page.theme.selected : page.theme.raised
                                border.width: 1
                                border.color: page.platform === modelData ? page.theme.selectedLine : page.theme.line

                                ColumnLayout {
                                    id: handoffColumn
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: Math.round(10 * page.theme.scale)
                                    spacing: Math.round(6 * page.theme.scale)

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Math.round(7 * page.theme.scale)

                                        Text {
                                            Layout.fillWidth: true
                                            text: page.platformLabel(modelData)
                                            color: page.theme.text
                                            font.pixelSize: Math.round(13 * page.theme.scale)
                                            font.weight: Font.DemiBold
                                            textFormat: Text.PlainText
                                        }

                                        HelperText {
                                            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                            theme: page.theme
                                            text: page.bundleStale
                                                ? page.controller.text("输入已变化")
                                                : handoffPlate.ready
                                                    ? page.controller.text("已独立核验")
                                                    : page.verified
                                                        ? page.controller.text("交接不可用")
                                                        : page.controller.text("等待本地核对")
                                            color: handoffPlate.ready ? page.theme.success
                                                : page.bundleStale ? page.theme.error : page.theme.muted
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: page.textOf(handoffPlate.handoff.fields
                                            ? handoffPlate.handoff.fields.title : "")
                                            || page.textOf(handoffPlate.handoff.error)
                                            || page.controller.text("核验后显示该平台冻结文稿")
                                        color: page.theme.text
                                        font.pixelSize: Math.round(11 * page.theme.scale)
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Math.round(6 * page.theme.scale)

                                        UiButton {
                                            objectName: "publicationHandoff_" + modelData
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: page.platform === modelData
                                                ? page.controller.text("正在查看")
                                                : page.controller.text("查看该平台")
                                            selected: page.platform === modelData
                                            enabled: !page.busy
                                            onClicked: page.controller.editPlatform(modelData)
                                        }

                                        UiButton {
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: page.controller.text("复制该平台全部")
                                            enabled: !page.busy && handoffPlate.ready
                                            onClicked: page.controller.copyFieldFor(modelData, "all")
                                        }

                                        UiButton {
                                            Layout.fillWidth: true
                                            visible: modelData === "xiaohongshu"
                                            theme: page.theme
                                            text: page.controller.text("打开创作中心")
                                            enabled: !page.busy && handoffPlate.ready
                                            onClicked: page.controller.openCreatorFor(modelData)
                                        }
                                    }
                                }
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.compact ? 1 : 2
                        columnSpacing: Math.round(10 * page.theme.scale)
                        rowSpacing: Math.round(10 * page.theme.scale)

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("交接摘要")

                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("任务")
                                value: page.summaryValue(page.fileName(page.jobText), "未关联任务")
                                pathValue: true
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("视频")
                                value: page.summaryValue(page.fileName(page.videoText), "未关联成品")
                                pathValue: true
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("平台")
                                value: page.selectedPlatformSummary()
                                    + page.controller.text("；当前编辑 ") + page.platformName()
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("标题")
                                value: page.summaryValue(page.titleText, "未填写发布标题")
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("简介")
                                value: page.summaryValue(page.descriptionPreview(), "未填写发布简介")
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("标签")
                                value: page.summaryValue(page.tagsText, "未填写")
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("AI")
                                value: page.aiLabel()
                                valueColor: page.aiIndex() === 0 ? page.theme.attention : page.theme.text
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("授权")
                                value: page.summaryValue(page.rightsText(), "尚未记录")
                                valueColor: page.rightsText().length > 0 ? page.theme.text : page.theme.attention
                            }
                            SummaryRow {
                                theme: page.theme
                                caption: page.controller.text("封面")
                                value: page.coverModeName()
                            }
                        }

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("当前平台本地文件与复制")
                            explanation: page.controller.text("当前编辑：") + page.platformName()
                                + page.controller.text("。只操作本机资料；每个平台的交接按钮仍独立可用。")

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("打开")
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: Math.round(6 * page.theme.scale)
                                rowSpacing: Math.round(6 * page.theme.scale)

                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("成品视频")
                                    enabled: page.videoText.length > 0
                                    onClicked: page.controller.openMaterial("video")
                                }
                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("封面")
                                    enabled: page.bundleText.length > 0 || page.textOf(page.view.coverFile).length > 0
                                    onClicked: page.controller.openMaterial("cover")
                                }
                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("发布包")
                                    enabled: page.bundleText.length > 0
                                    onClicked: page.controller.openMaterial("bundle")
                                }
                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("核对清单")
                                    enabled: page.bundleText.length > 0
                                    onClicked: page.controller.openMaterial("checklist")
                                }
                            }

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("复制")
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: Math.round(6 * page.theme.scale)
                                rowSpacing: Math.round(6 * page.theme.scale)

                                UiButton {
                                    objectName: "publicationCopyTitle"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制标题")
                                    enabled: page.canHandoff
                                    onClicked: page.controller.copyField("title")
                                }
                                UiButton {
                                    objectName: "publicationCopyDescription"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制简介")
                                    enabled: page.canHandoff
                                    onClicked: page.controller.copyField("description")
                                }
                                UiButton {
                                    objectName: "publicationCopyTags"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制标签")
                                    enabled: page.canHandoff
                                    onClicked: page.controller.copyField("tags")
                                }
                                UiButton {
                                    objectName: "publicationCopyAll"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制全部文稿")
                                    enabled: page.canHandoff
                                    onClicked: page.controller.copyField("all")
                                }
                                UiButton {
                                    objectName: "publicationCopyVideoPath"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制成品路径")
                                    enabled: page.canHandoff
                                    onClicked: page.controller.copyField("video")
                                }
                                UiButton {
                                    objectName: "publicationCopyCoverPath"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制封面路径")
                                    enabled: page.canHandoff
                                    onClicked: page.controller.copyField("cover")
                                }
                            }

                            Repeater {
                                model: page.actionItems
                                delegate: UiButton {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.textOf(modelData.label)
                                    enabled: !page.busy
                                    onClicked: page.controller.openMaterial(page.textOf(modelData.id))
                                }
                            }
                        }
                    }

                    SurfaceCard {
                        Layout.fillWidth: true
                        visible: page.bilibiliSelected
                        theme: page.theme
                        heading: page.controller.text("B站填写会话")
                        explanation: page.controller.text("只在你点击后启动现有隔离填写服务。页面登录、验证码、字段核对与最终投稿都由你完成。")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            Rectangle {
                                Layout.preferredWidth: Math.round(8 * page.theme.scale)
                                Layout.preferredHeight: Layout.preferredWidth
                                radius: width / 2
                                color: page.browserStateColor()
                            }

                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: page.browserStateLabel()
                                color: page.browserStateColor()
                                font.pixelSize: Math.round(12 * page.theme.scale)
                                font.weight: Font.DemiBold
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }

                            HelperText {
                                Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                theme: page.theme
                                text: page.browserSession.active && page.browserSession.pid > 0
                                    ? "PID " + String(page.browserSession.pid) : ""
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: page.textOf(page.browserSession.message)
                                || page.controller.text("尚未启动。可先打开隔离登录窗口，或核对资料后直接打开填写。")
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.theme.scale)

                            Repeater {
                                model: page.browserChecksModel()
                                delegate: Rectangle {
                                    required property var modelData
                                    implicitWidth: checkText.implicitWidth + Math.round(12 * page.theme.scale)
                                    implicitHeight: Math.round(24 * page.theme.scale)
                                    radius: height / 2
                                    color: modelData.ready
                                        ? Qt.rgba(page.theme.success.r, page.theme.success.g, page.theme.success.b, 0.12)
                                        : page.theme.raised
                                    border.width: 1
                                    border.color: modelData.ready ? page.theme.success : page.theme.line

                                    Text {
                                        id: checkText
                                        anchors.centerIn: parent
                                        text: (modelData.ready ? page.controller.text("已 ") : page.controller.text("未 ")) + modelData.label
                                        color: modelData.ready ? page.theme.success : page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.compact ? 1 : 2
                            columnSpacing: Math.round(6 * page.theme.scale)
                            rowSpacing: Math.round(6 * page.theme.scale)

                            UiButton {
                                objectName: "publicationBilibiliLogin"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("打开隔离登录会话")
                                enabled: !page.busy && page.browserSession.canLogin === true
                                onClicked: page.controller.loginBilibili()
                            }

                            UiButton {
                                objectName: "publicationCreator"
                                Layout.fillWidth: true
                                theme: page.theme
                                primary: page.handoffReady("bilibili")
                                    && page.browserSession.canOpen === true
                                text: page.controller.text("打开 B站并填写")
                                enabled: !page.busy && page.handoffReady("bilibili")
                                    && page.browserSession.canOpen === true
                                onClicked: page.controller.openCreatorFor("bilibili")
                            }

                            UiButton {
                                objectName: "publicationBilibiliContinue"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("继续填写")
                                enabled: !page.busy && page.browserSession.canContinue === true
                                onClicked: page.controller.continueFill()
                            }

                            UiButton {
                                objectName: "publicationBilibiliStop"
                                Layout.fillWidth: true
                                theme: page.theme
                                quiet: true
                                text: page.browserSession.mode === "login"
                                    ? page.controller.text("停止登录会话")
                                    : page.controller.text("停止填写")
                                enabled: !page.busy && page.browserSession.canStop === true
                                onClicked: page.controller.stopFill()
                            }
                            UiButton {
                                objectName: "publicationOpenBrowserLog"
                                theme: page.theme
                                quiet: true
                                text: page.controller.text("查看填写日志")
                                enabled: page.browserSession.hasLog === true
                                onClicked: browserLog.open()
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.browserSession.active
                                ? page.controller.text("“继续填写”只更新标题、简介与封面；必须先在网页选择视频。停止操作不会保存草稿或投稿。")
                                : page.handoffReady("bilibili")
                                    ? page.controller.text("资料已独立核验。启动会话后仍需人工登录、选择视频、核对声明并确认发布。")
                                    : page.controller.text("生成并核对 B站交接后，才能启动填写会话。")
                        }
                    }

                    SurfaceCard {
                        Layout.fillWidth: true
                        visible: page.xiaohongshuSelected
                        theme: page.theme
                        heading: page.controller.text("小红书手动投稿")
                        explanation: page.controller.text("小红书始终使用复制与本地文件交接，不启动自动登录或自动填写。")

                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("请在官方网页或 App 中手动上传成品和封面，粘贴标题、正文与标签，并亲自设置 AI 内容声明、原创/转载和授权后，再决定是否发布。")
                            color: page.theme.text
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            lineHeight: 1.2
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: xhsDisclosure.implicitHeight + Math.round(12 * page.theme.scale)
                            color: Qt.rgba(page.theme.attention.r, page.theme.attention.g, page.theme.attention.b, 0.1)
                            radius: Math.round(6 * page.theme.scale)

                            Text {
                                id: xhsDisclosure
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: Math.round(9 * page.theme.scale)
                                anchors.rightMargin: Math.round(9 * page.theme.scale)
                                text: page.handoffAiLabel("xiaohongshu")
                                    + page.controller.text("。本地记录不会同步平台，请在发布页实际选择声明。")
                                color: page.theme.attention
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.compact ? 1 : 2
                            columnSpacing: Math.round(6 * page.theme.scale)
                            rowSpacing: Math.round(6 * page.theme.scale)

                            UiButton {
                                objectName: "publicationXiaohongshuCopyAll"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("复制小红书全部文稿")
                                enabled: !page.busy && page.handoffReady("xiaohongshu")
                                onClicked: page.controller.copyFieldFor("xiaohongshu", "all")
                            }

                            UiButton {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("复制成品路径")
                                enabled: !page.busy && page.handoffReady("xiaohongshu")
                                onClicked: page.controller.copyFieldFor("xiaohongshu", "video")
                            }

                            UiButton {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("复制封面路径")
                                enabled: !page.busy && page.handoffReady("xiaohongshu")
                                onClicked: page.controller.copyFieldFor("xiaohongshu", "cover")
                            }

                            UiButton {
                                objectName: "publicationXiaohongshuCreator"
                                Layout.fillWidth: true
                                theme: page.theme
                                primary: page.handoffReady("xiaohongshu")
                                text: page.controller.text("打开小红书创作中心")
                                enabled: !page.busy && page.handoffReady("xiaohongshu")
                                onClicked: page.controller.openCreatorFor("xiaohongshu")
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.handoffReady("xiaohongshu")
                                ? page.controller.text("小红书文稿、成品和封面已独立核验。最终上传、声明与发布仍由你手动完成。")
                                : page.controller.text("生成并核对小红书交接后，复制与官方入口才会启用。")
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: footerRow.implicitHeight + Math.round(14 * page.theme.scale)
            color: page.theme.surface
            Rectangle { width: parent.width; height: 1; color: page.theme.line }

            RowLayout {
                id: footerRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: page.pageMargin
                anchors.rightMargin: page.pageMargin
                spacing: Math.round(7 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: 1
                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text("第 ") + String(page.step + 1) + page.controller.text(" / 3 步")
                        color: page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: page.stateLabel
                        color: page.stateColor
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                }

                UiButton {
                    objectName: "publicationPrevious"
                    visible: page.step > 0
                    theme: page.theme
                    text: page.controller.text("上一步")
                    enabled: !page.busy
                    onClicked: page.controller.previousStep()
                }

                UiButton {
                    objectName: "publicationNext"
                    visible: page.step < 2
                    theme: page.theme
                    primary: true
                    text: page.step === 0 ? page.controller.text("检查封面") : page.controller.text("检查文案与声明")
                    enabled: !page.busy && (page.hasVideo || page.view.bundleOnly === true)
                    onClicked: page.controller.nextStep()
                }

                UiButton {
                    objectName: "publicationPrepare"
                    visible: page.step === 2
                    theme: page.theme
                    primary: !page.bundleStale || page.bundleText.length === 0
                    text: page.busy ? page.controller.text("正在生成…")
                        : page.bundleText.length > 0 && page.bundleStale ? page.controller.text("重新生成")
                        : page.bundleText.length > 0 ? page.controller.text("重新生成资料") : page.controller.text("生成发布资料")
                    enabled: page.canPrepare
                    onClicked: page.controller.prepareBundle()
                }

                UiButton {
                    objectName: "publicationVerify"
                    visible: page.step === 2
                    theme: page.theme
                    primary: page.bundleText.length > 0 && !page.bundleStale && !page.verified
                    text: page.busy ? page.controller.text("正在核对…")
                        : page.verified ? page.controller.text("重新核对") : page.controller.text("核对资料")
                    enabled: page.canCheck
                    onClicked: page.controller.checkBundle()
                }
            }
        }
    }
}
