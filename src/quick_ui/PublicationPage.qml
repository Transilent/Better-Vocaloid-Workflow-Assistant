import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    objectName: "publicationPage"
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
    readonly property bool canHandoff: verified && bundleText.length > 0 && !bundleStale && !busy
    readonly property string stateName: page.textOf(view.state).toLowerCase()
    readonly property string messageText: page.textOf(view.message)
    readonly property color stateColor: page.colorForState()
    readonly property string stateLabel: page.labelForState()
    readonly property var actionItems: page.actionsFrom(view.actions)
    readonly property string coverPreviewUrl: page.textOf(view.coverPreview)
    readonly property int coverSeconds: Math.max(0, Math.floor(page.number(view.coverSeconds, 20)))

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
        return normalized === "frame" || normalized === "custom" ? normalized : "original";
    }

    function coverModeIndex() {
        const mode = page.coverModeValue(page.view.coverMode);
        return mode === "frame" ? 1 : mode === "custom" ? 2 : 0;
    }

    function coverModeName() {
        const mode = page.coverModeValue(page.view.coverMode);
        if (mode === "frame")
            return page.controller.text("成品视频截帧");
        if (mode === "custom")
            return page.controller.text("自选图片");
        return page.controller.text("本家原始封面");
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
        if (page.jobText.length > 0)
            parts.push(page.controller.text("任务") + " · " + page.fileName(page.jobText));
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
                        onClicked: page.controller.cancel()
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
                                    text: index < page.step ? "?" : String(index + 1)
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

        ScrollView {
            id: contentScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            ColumnLayout {
                width: contentScroll.availableWidth
                spacing: 0

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.margins: page.pageMargin
                    Layout.bottomMargin: Math.round(10 * page.theme.scale)
                    visible: page.step === 0
                    spacing: Math.round(10 * page.theme.scale)

                    SurfaceCard {
                        Layout.fillWidth: true
                        theme: page.theme
                        heading: page.controller.text("素材与文案")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: Math.round(4 * page.theme.scale)
                                HelperText {
                                    theme: page.theme
                                    text: page.controller.text("任务")
                                }
                                Text {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: page.jobText.length > 0 ? page.fileName(page.jobText) : page.controller.text("未关联任务")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(12 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: Math.round(4 * page.theme.scale)
                                HelperText {
                                    theme: page.theme
                                    text: page.controller.text("成品视频")
                                }
                                Text {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: page.videoText.length > 0 ? page.fileName(page.videoText) : page.controller.text("未关联成品")
                                    color: page.videoText.length > 0 ? page.theme.text : page.theme.error
                                    font.pixelSize: Math.round(12 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.compact ? 1 : 2
                        columnSpacing: Math.round(8 * page.theme.scale)
                        rowSpacing: Math.round(8 * page.theme.scale)

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("平台")

                            UiComboBox {
                                id: platformCombo
                                objectName: "publicationPlatform"
                                Layout.fillWidth: true
                                theme: page.theme
                                model: [
                                    { label: page.controller.text("B站"), value: "bilibili" },
                                    { label: page.controller.text("小红书"), value: "xiaohongshu" }
                                ]
                                currentIndex: page.platformIndex()
                                enabled: !page.busy
                                Accessible.name: page.controller.text("发布平台")
                                onActivated: page.controller.setField("platform", currentValue)
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.platform === "xiaohongshu"
                                    ? page.controller.text("小红书仅生成手动投稿资料，不自动登录或发布。")
                                    : page.controller.text("B站资料会生成 4:3 与 16:9 补边封面，最终仍在网页确认。")
                            }
                        }

                        SurfaceCard {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignTop
                            theme: page.theme
                            heading: page.controller.text("标题")

                            ThemedTextField {
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
                                ]
                                currentIndex: page.coverModeIndex()
                                enabled: !page.busy
                                Accessible.name: page.controller.text("封面来源")
                                onActivated: page.controller.setField("coverMode", currentValue)
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                visible: page.coverModeValue(page.view.coverMode) === "frame"
                                spacing: Math.round(8 * page.theme.scale)
                                LabelText {
                                    Layout.alignment: Qt.AlignVCenter
                                    theme: page.theme
                                    text: page.controller.text("截图时间")
                                }
                                SpinBox {
                                    id: coverSecondsInput
                                    Layout.fillWidth: true
                                    from: 0
                                    to: 86400
                                    stepSize: 1
                                    editable: true
                                    value: page.coverSeconds
                                    enabled: !page.busy
                                    Accessible.name: page.controller.text("封面截图时间（秒）")
                                    onValueModified: page.controller.setField("coverSeconds", value)
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
                                        ? page.controller.text("从来源任务原视频截取画面；没有原视频时会由控制器拒绝。")
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

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.round((page.dense ? 148 : 230) * page.theme.scale)
                                color: page.theme.base
                                radius: Math.round(7 * page.theme.scale)
                                border.width: 1
                                border.color: page.theme.line
                                clip: true

                                Image {
                                    anchors.fill: parent
                                    anchors.margins: Math.round(5 * page.theme.scale)
                                    source: page.coverPreviewUrl
                                    visible: page.coverPreviewUrl.length > 0 && status === Image.Ready
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                    cache: false
                                }

                                ColumnLayout {
                                    anchors.centerIn: parent
                                    width: Math.min(parent.width - Math.round(24 * page.theme.scale), Math.round(260 * page.theme.scale))
                                    visible: page.coverPreviewUrl.length === 0 || coverImage.status !== Image.Ready
                                    spacing: Math.round(4 * page.theme.scale)

                                    Text {
                                        Layout.fillWidth: true
                                        text: page.coverPreviewUrl.length === 0
                                            ? page.controller.text("尚未生成预览")
                                            : coverImage.status === Image.Error
                                                ? page.controller.text("预览无法读取") : page.controller.text("正在读取预览")
                                        color: page.coverPreviewUrl.length > 0 && coverImage.status === Image.Error
                                            ? page.theme.error : page.theme.muted
                                        font.pixelSize: Math.round(12 * page.theme.scale)
                                        horizontalAlignment: Text.AlignHCenter
                                        wrapMode: Text.Wrap
                                        textFormat: Text.PlainText
                                    }

                                    HelperText {
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.controller.text("控制器返回本地 URL 后在此显示；不会上传素材。")
                                        horizontalAlignment: Text.AlignHCenter
                                    }
                                }

                                Image {
                                    id: coverImage
                                    source: page.coverPreviewUrl
                                    visible: false
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.platform === "bilibili"
                                    ? page.controller.text("生成发布包时，B站 4:3 与 16:9 封面会按比例补边，完整保留原图。")
                                    : page.controller.text("小红书使用同一张封面上传；平台尺寸与裁切请在发布页亲自检查。")
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
                                value: page.platformName()
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
                            heading: page.controller.text("本地文件与复制")
                            explanation: page.controller.text("只操作本机资料；复制内容由你粘贴到官方平台。")

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
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制标题")
                                    enabled: page.titleText.length > 0
                                    onClicked: page.controller.copyField("title")
                                }
                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制简介")
                                    enabled: page.descriptionText.length > 0
                                    onClicked: page.controller.copyField("description")
                                }
                                UiButton {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("复制标签")
                                    enabled: page.tagsText.length > 0
                                    onClicked: page.controller.copyField("tags")
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
                        theme: page.theme
                        heading: page.platform === "xiaohongshu"
                            ? page.controller.text("小红书手动投稿")
                            : page.controller.text("到平台人工确认")

                        Text {
                            Layout.fillWidth: true
                            text: page.platform === "xiaohongshu"
                                ? page.controller.text("请在官方网页或 App 中手动上传成品和封面，粘贴标题、正文与标签，并亲自设置 AI 内容声明、原创/转载和授权后，再决定是否发布。")
                                : page.controller.text("请在上传页亲自核对封面比例、分区、标签、AI 与其他声明、原创/转载和授权后，再决定是否发布。助手不会提交最终投稿。")
                            color: page.theme.text
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            lineHeight: 1.2
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: disclosureText.implicitHeight + Math.round(12 * page.theme.scale)
                            color: Qt.rgba(page.theme.attention.r, page.theme.attention.g, page.theme.attention.b, 0.1)
                            radius: Math.round(6 * page.theme.scale)

                            Text {
                                id: disclosureText
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: Math.round(9 * page.theme.scale)
                                anchors.rightMargin: Math.round(9 * page.theme.scale)
                                text: page.aiIndex() === 1
                                    ? page.aiLabel() + page.controller.text("。本地记录不会同步平台，请在发布页实际选择 AI 声明。")
                                    : page.aiIndex() === 2
                                        ? page.controller.text("本地记录为不含 AI 内容；若素材或文案有变，请在平台重新核对声明。")
                                        : page.controller.text("尚未确认是否含 AI 内容。请先核对素材与文案，再在平台设置实际声明。")
                                color: page.theme.attention
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        UiButton {
                            objectName: "publicationCreator"
                            Layout.fillWidth: true
                            theme: page.theme
                            primary: page.canHandoff
                            text: page.platform === "xiaohongshu"
                                ? page.controller.text("打开小红书创作中心，手动投稿")
                                : page.controller.text("打开 B站创作中心，手动核对")
                            enabled: page.canHandoff
                            onClicked: page.controller.openCreator()
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.canHandoff
                                ? page.controller.text("本地文件已核对。打开入口后仍由你人工填写并确认发布。")
                                : page.bundleStale
                                    ? page.controller.text("输入已变化，请重新生成并核对发布资料。")
                                    : page.verified
                                        ? page.controller.text("请先打开并核验当前发布包。")
                                        : page.controller.text("生成发布资料并通过本地核对后，才能打开平台入口。")
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
                    enabled: !page.busy
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
