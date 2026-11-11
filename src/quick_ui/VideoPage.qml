import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "videoPage"
    required property QtObject theme
    required property QtObject controller

    readonly property var view: controller.view || ({})
    readonly property string jobText: page.textOf(view.job)
    readonly property string sourceText: page.textOf(view.sourceVideo)
    readonly property string mixdownText: page.textOf(view.mixdown)
    readonly property string outputText: page.textOf(view.output)
    readonly property string previewUrl: page.textOf(view.preview)
    readonly property string alignmentText: page.textOf(view.alignmentSummary)
    readonly property string stateKey: page.textOf(view.state).toLowerCase()
    readonly property string stateLabel: page.stateLabelFor(page.stateKey)
    readonly property bool busy: page.boolValue(view.busy, false)
    readonly property bool autoAlign: page.boolValue(view.autoAlign, true)
    readonly property bool normalize: page.boolValue(view.normalize, false)
    readonly property bool resultStale: page.boolValue(view.resultStale, false)
    readonly property bool canAnalyze: page.boolValue(view.canAnalyze, false)
    readonly property bool canAdoptSuggestion: page.boolValue(view.canAdoptSuggestion, false)
    readonly property bool canRender: page.boolValue(view.canRender, false)
    readonly property bool canRegister: page.boolValue(view.canRegister, false)
    readonly property bool canPublish: page.boolValue(view.canPublish, false)
    readonly property bool canCancel: page.boolValue(view.canCancel, false)
    readonly property bool taskScanBusy: page.boolValue(view.taskScanBusy, false)
    readonly property bool canRefreshTasks: page.boolValue(view.canRefreshTasks, false)
    readonly property bool hasResult: page.resultValue()
    readonly property string taskTitleText: page.textOf(view.taskTitle)
    readonly property string restoredSourceBv: page.textOf(view.sourceBv)
    readonly property string restoreMessageText: page.textOf(view.restoreMessage)
    readonly property bool sideBySide: availableWidth >= Math.round(960 * Math.min(1.2, theme.scale))
    readonly property bool narrow: availableWidth < Math.round(700 * theme.scale)
    readonly property int pageMargin: Math.round((page.narrow ? 14 : 24) * theme.scale)
    readonly property int cardPadding: Math.round((page.narrow ? 14 : 18) * theme.scale)
    readonly property string primaryAction: page.choosePrimaryAction()
    readonly property string statusLine: page.textOf(view.message).length > 0
        ? page.textOf(view.message) : page.defaultStatus()
    property string restoreJobId: ""

    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AsNeeded

    function tr(source) {
        if (controller && typeof controller.text === "function")
            return controller.text(source)
        return source
    }

    function textOf(value) {
        if (value === undefined || value === null)
            return ""
        if (typeof value === "string")
            return value
        if (typeof value === "number" || typeof value === "boolean")
            return String(value)
        if (typeof value === "object") {
            const keys = ["output", "path", "file", "summary", "message", "text", "label"]
            for (let index = 0; index < keys.length; ++index) {
                const candidate = value[keys[index]]
                if (candidate !== undefined && candidate !== null && String(candidate).length > 0)
                    return String(candidate)
            }
        }
        return ""
    }

    function boolValue(value, fallback) {
        if (value === undefined || value === null)
            return fallback
        if (typeof value === "string") {
            const normalized = value.toLowerCase()
            return normalized !== "" && normalized !== "0" && normalized !== "false"
                && normalized !== "off" && normalized !== "no"
        }
        return Boolean(value)
    }

    function resultValue() {
        const result = view.result
        if (typeof result === "string")
            return result.trim().length > 0
        if (result === undefined || result === null)
            return false
        if (typeof result === "boolean")
            return result
        if (typeof result === "object")
            return Object.keys(result).length > 0
        return Boolean(result)
    }

    function resultPathText() {
        const result = view.result
        if (typeof result === "string")
            return result
        if (result && typeof result === "object")
            return page.textOf(result.output || result.path || result.file)
        return ""
    }

    function loudnessValueText(label, value, digits) {
        if (value === undefined || value === null || value === "")
            return ""
        const number = Number(value)
        return isFinite(number) ? label + " " + number.toFixed(digits) : label + " " + String(value)
    }

    function loudnessReportText() {
        const receipt = page.view.receipt
        if (!receipt || typeof receipt !== "object")
            return ""
        const parts = []
        parts.push(receipt.normalized === true
            ? page.tr("响度处理：已应用") : page.tr("响度处理：未启用"))
        const measurement = receipt.loudness_measurement
        if (measurement && typeof measurement === "object") {
            const fields = [
                { key: "input_i", label: page.tr("输入 LUFS"), digits: 2 },
                { key: "output_i", label: page.tr("输出 LUFS"), digits: 2 },
                { key: "input_tp", label: page.tr("峰值 dBTP"), digits: 2 },
                { key: "output_tp", label: page.tr("输出 dBTP"), digits: 2 },
                { key: "input_lra", label: page.tr("LRA"), digits: 1 },
                { key: "target_offset", label: page.tr("目标修正 dB"), digits: 2 }
            ]
            for (let index = 0; index < fields.length; ++index) {
                const field = fields[index]
                const line = page.loudnessValueText(field.label, measurement[field.key], field.digits)
                if (line.length > 0)
                    parts.push(line)
            }
        }
        return parts.join(" · ")
    }

    function offsetText() {
        const value = view.offset
        if (value === undefined || value === null || value === "")
            return "0.000"
        const number = Number(value)
        return isFinite(number) ? number.toFixed(3) : String(value)
    }

    function commitOffset(field) {
        const value = field.text.trim()
        if (value.length === 0) {
            field.text = page.offsetText()
            return
        }
        const number = Number(value)
        if (!isFinite(number)) {
            field.text = page.offsetText()
            return
        }
        controller.setOption("offset", number)
    }

    function stateLabelFor(value) {
        switch (value) {
        case "idle":
        case "ready": return page.tr("就绪")
        case "analyzed": return page.tr("对齐已分析")
        case "queued": return page.tr("等待开始")
        case "analyzing":
        case "aligning": return page.tr("正在分析对齐")
        case "adopting": return page.tr("正在采用建议偏移")
        case "previewing": return page.tr("正在生成预览")
        case "rendering": return page.tr("正在合成")
        case "running": return page.tr("正在处理")
        case "cancelling": return page.tr("正在取消")
        case "succeeded":
        case "done":
        case "completed": return page.tr("已完成")
        case "registered": return page.tr("已登记")
        case "needs_manual": return page.tr("需手动偏移")
        case "partial": return page.tr("部分完成")
        case "failed":
        case "error": return page.tr("失败")
        case "cancelled": return page.tr("已取消")
        case "interrupted": return page.tr("已中断")
        case "stale": return page.tr("需要重新处理")
        default: return value.length > 0 ? value.replace(/_/g, " ") : page.tr("就绪")
        }
    }

    function stateColor() {
        switch (page.stateKey) {
        case "failed":
        case "error":
        case "cancelled":
        case "interrupted": return page.theme.error
        case "succeeded":
        case "done":
        case "completed":
        case "registered": return page.theme.success
        case "needs_manual": return page.theme.attention
        case "analyzing":
        case "aligning":
        case "previewing":
        case "rendering":
        case "running":
        case "queued":
        case "cancelling": return page.theme.accent
        default: return page.theme.muted
        }
    }

    function defaultStatus() {
        if (page.busy)
            return page.tr("视频任务正在后台处理，完成后会按控制器状态恢复操作。")
        if (page.resultStale)
            return page.tr("当前候选已过期，请检查素材后重新合成。")
        if (page.hasResult)
            return page.tr("成品已生成，可检查文件后登记或带入发布。")
        if (page.sourceText.length === 0)
            return page.tr("先选择原视频，再补充 Mixdown 音轨。")
        if (page.mixdownText.length === 0)
            return page.tr("再选择 Mixdown 音轨，以便检查对齐并合成。")
        if (page.alignmentText.length === 0 && page.canAnalyze)
            return page.tr("可先分析音频对齐，再生成首帧预览。")
        return page.tr("检查首帧与参数后合成 MP4。")
    }

    function choosePrimaryAction() {
        if (page.busy)
            return ""
        if (page.sourceText.length === 0)
            return "source"
        if (page.mixdownText.length === 0)
            return "mixdown"
        if (page.hasResult && !page.resultStale) {
            if (page.canRegister)
                return "register"
            if (page.canPublish)
                return "publish"
            return "openResult"
        }
        if (page.canAnalyze && page.alignmentText.length === 0)
            return "analyze"
        if (page.previewUrl.length === 0)
            return "preview"
        if (page.outputText.length === 0
                || (page.resultStale && page.canAnalyze && !page.canRender))
            return "output"
        if (page.canRender)
            return "render"
        if (page.canPublish)
            return "publish"
        return ""
    }

    function isPrimary(action) {
        return page.primaryAction === action
    }

    function restoreChoices() {
        const result = [{ label: page.tr("选择已登记任务…"), value: "", sourceIdentity: "" }]
        const rows = page.view.taskChoices
        if (rows && rows.length) {
            for (let index = 0; index < rows.length; ++index) {
                const row = rows[index]
                if (!row || String(row.value || "").length === 0)
                    continue
                result.push({
                    label: String(row.label || row.title || row.value),
                    value: String(row.value),
                    sourceIdentity: String(row.sourceIdentity || "")
                })
            }
        }
        return result
    }

    function chooseRestoreTask(jobId) {
        page.restoreJobId = String(jobId || "")
        const rows = page.restoreChoices()
        let identity = ""
        for (let index = 0; index < rows.length; ++index) {
            if (rows[index].value === page.restoreJobId) {
                identity = rows[index].sourceIdentity || ""
                break
            }
        }
        sourceIdentityField.text = identity
    }

    component StepCard: Rectangle {
        id: card
        required property QtObject theme
        property string stepNumber
        property string title
        property string description
        default property alias contentData: cardBody.data

        implicitHeight: cardLayout.implicitHeight + Math.round(34 * card.theme.scale)
        color: card.theme.surface
        radius: Math.round(11 * card.theme.scale)
        border.width: 1
        border.color: card.theme.line

        ColumnLayout {
            id: cardLayout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Math.round(17 * card.theme.scale)
            spacing: Math.round(13 * card.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(11 * card.theme.scale)

                Rectangle {
                    Layout.alignment: Qt.AlignTop
                    Layout.preferredWidth: Math.round(34 * card.theme.scale)
                    Layout.preferredHeight: Layout.preferredWidth
                    radius: width / 2
                    color: card.theme.selected
                    border.color: card.theme.selectedLine
                    Text {
                        anchors.centerIn: parent
                        text: card.stepNumber
                        color: card.theme.accent
                        font.pixelSize: Math.round(12 * card.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(3 * card.theme.scale)
                    Text {
                        Layout.fillWidth: true
                        text: card.title
                        color: card.theme.text
                        font.pixelSize: Math.round(18 * card.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: card.description
                        color: card.theme.muted
                        font.pixelSize: Math.round(12 * card.theme.scale)
                        lineHeight: 1.15
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: card.theme.line
            }

            ColumnLayout {
                id: cardBody
                Layout.fillWidth: true
                spacing: Math.round(11 * card.theme.scale)
            }
        }
    }

    component PathWell: Rectangle {
        id: well
        required property QtObject theme
        property string value
        property string placeholder
        property string textObjectName
        property bool highlighted

        implicitHeight: Math.round(40 * well.theme.scale)
        color: well.highlighted ? well.theme.selected : well.theme.base
        radius: Math.round(7 * well.theme.scale)
        border.width: 1
        border.color: well.highlighted ? well.theme.selectedLine : well.theme.line

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Math.round(10 * well.theme.scale)
            anchors.rightMargin: Math.round(9 * well.theme.scale)
            spacing: Math.round(7 * well.theme.scale)

            Text {
                objectName: well.textObjectName
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: well.value.length > 0 ? well.value : well.placeholder
                color: well.value.length > 0 ? well.theme.text : well.theme.muted
                font.pixelSize: Math.round(11.5 * well.theme.scale)
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
                ToolTip.visible: pathHover.hovered && well.value.length > 0
                ToolTip.text: well.value
                HoverHandler { id: pathHover }
            }

            Rectangle {
                visible: well.highlighted
                implicitWidth: selectedLabel.implicitWidth + Math.round(12 * well.theme.scale)
                implicitHeight: Math.round(20 * well.theme.scale)
                radius: height / 2
                color: well.theme.surface
                Text {
                    id: selectedLabel
                    anchors.centerIn: parent
                    text: page.tr("已选")
                    color: well.theme.accent
                    font.pixelSize: Math.round(10 * well.theme.scale)
                    textFormat: Text.PlainText
                }
            }
        }
    }

    component MaterialRow: ColumnLayout {
        id: row
        required property QtObject theme
        property string title
        property string hint
        property string value
        property string placeholder
        property string fieldObjectName
        property string buttonObjectName
        property string buttonText
        property bool blocked
        property bool actionPrimary
        signal chooseRequested()

        spacing: Math.round(7 * row.theme.scale)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(8 * row.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 1
                Text {
                    Layout.fillWidth: true
                    text: row.title
                    color: row.theme.text
                    font.pixelSize: Math.round(12.5 * row.theme.scale)
                    font.weight: Font.DemiBold
                    textFormat: Text.PlainText
                }
                Text {
                    Layout.fillWidth: true
                    visible: row.hint.length > 0
                    text: row.hint
                    color: row.theme.muted
                    font.pixelSize: Math.round(10.5 * row.theme.scale)
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                }
            }

            UiButton {
                objectName: row.buttonObjectName
                theme: row.theme
                text: row.buttonText
                primary: row.actionPrimary
                enabled: !row.blocked
                Accessible.name: row.buttonText
                onClicked: row.chooseRequested()
            }
        }

        PathWell {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            theme: row.theme
            value: row.value
            placeholder: row.placeholder
            textObjectName: row.fieldObjectName
            highlighted: row.value.length > 0
        }
    }

    ColumnLayout {
        width: Math.max(0, Math.min(page.availableWidth - page.pageMargin * 2,
                                     Math.round(1120 * page.theme.scale)))
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Math.round(13 * page.theme.scale)

        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: Math.round(22 * page.theme.scale)
            spacing: Math.round(5 * page.theme.scale)

            Text {
                Layout.fillWidth: true
                text: page.tr("视频工作台")
                color: page.theme.accent
                font.pixelSize: Math.round(11 * page.theme.scale)
                font.weight: Font.DemiBold
                font.letterSpacing: 1.2
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("合成视频")
                color: page.theme.text
                font.pixelSize: Math.round(26 * page.theme.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("保留原视频画面，将 Mixdown 配回视频。按素材、对齐预览、导出的顺序完成。")
                color: page.theme.muted
                font.pixelSize: Math.round(12.5 * page.theme.scale)
                lineHeight: 1.18
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
        }

        Rectangle {
            objectName: "videoStatusPanel"
            Layout.fillWidth: true
            implicitHeight: statusLayout.implicitHeight + page.cardPadding * 2
            color: page.theme.surface
            radius: Math.round(9 * page.theme.scale)
            border.width: 1
            border.color: page.busy ? page.theme.selectedLine : page.theme.line

            ColumnLayout {
                id: statusLayout
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: page.cardPadding
                spacing: Math.round(8 * page.theme.scale)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(9 * page.theme.scale)

                    Rectangle {
                        implicitWidth: statusBadge.implicitWidth + Math.round(18 * page.theme.scale)
                        implicitHeight: Math.round(25 * page.theme.scale)
                        radius: height / 2
                        color: Qt.rgba(page.stateColor().r, page.stateColor().g, page.stateColor().b, 0.13)
                        border.color: page.stateColor()
                        Text {
                            id: statusBadge
                            anchors.centerIn: parent
                            text: page.stateLabel
                            color: page.stateColor()
                            font.pixelSize: Math.round(10.5 * page.theme.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: page.statusLine
                        color: page.stateKey === "failed" || page.stateKey === "error"
                            ? page.theme.error : page.theme.text
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        lineHeight: 1.15
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }

                    UiButton {
                        objectName: "videoCancel"
                        visible: page.busy
                        theme: page.theme
                        text: page.stateKey === "cancelling"
                            ? page.tr("正在取消…") : page.tr("取消操作")
                        enabled: page.busy && page.canCancel && page.stateKey !== "cancelling"
                        Accessible.name: text
                        onClicked: page.controller.cancel()
                    }
                }

                ProgressBar {
                    objectName: "videoProgress"
                    Layout.fillWidth: true
                    visible: page.busy && !page.theme.reduceMotion
                    indeterminate: true
                    value: 0
                    palette.dark: page.theme.accent
                    palette.midlight: page.theme.raised
                    Accessible.name: page.tr("视频任务进度")
                }
            }
        }

        StepCard {
            Layout.fillWidth: true
            theme: page.theme
            stepNumber: "01"
            title: page.tr("素材")
            description: page.tr("原视频提供画面，Mixdown 提供新音轨；文件可来自任务，也可独立选择。")

            RowLayout {
                Layout.fillWidth: true
                spacing: 7 * page.theme.scale
                TextField {
                    id: sourceIdentityField
                    objectName: "videoSourceIdentity"
                    Layout.fillWidth: true
                    Layout.preferredWidth: Math.round(270 * page.theme.scale)
                    Layout.minimumWidth: 0
                    placeholderText: page.tr("BV 号或链接（可选）")
                    enabled: !page.busy
                    selectByMouse: true
                    font.pixelSize: 13 * page.theme.scale
                    onAccepted: {
                        page.restoreJobId = ""
                        page.controller.refreshTaskChoices(text)
                    }
                }
                UiButton {
                    objectName: "videoFindTask"
                    theme: page.theme
                    text: page.tr(page.taskScanBusy ? "查找中…" : "查找任务")
                    enabled: page.canRefreshTasks
                    onClicked: {
                        page.restoreJobId = ""
                        page.controller.refreshTaskChoices(sourceIdentityField.text)
                    }
                }
                UiComboBox {
                    objectName: "videoTaskPicker"
                    theme: page.theme
                    Layout.fillWidth: true
                    Layout.preferredWidth: Math.round(290 * page.theme.scale)
                    Layout.minimumWidth: 0
                    model: page.restoreChoices()
                    enabled: !page.busy && model.length > 1
                    currentIndex: Math.max(0, model.findIndex(row => row.value === page.restoreJobId))
                    onActivated: page.chooseRestoreTask(currentValue)
                }
                UiButton {
                    objectName: "videoRestoreTask"
                    theme: page.theme
                    text: page.tr("载入素材")
                    enabled: !page.busy && !!page.restoreJobId
                    onClicked: page.controller.restoreTask(page.restoreJobId, sourceIdentityField.text)
                }
            }
            Text {
                Layout.fillWidth: true
                visible: text.length > 0
                text: page.textOf(page.view.restoreMessage)
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            Rectangle {
                Layout.fillWidth: true
                visible: page.jobText.length > 0
                implicitHeight: jobLayout.implicitHeight + Math.round(20 * page.theme.scale)
                color: page.theme.selected
                radius: Math.round(8 * page.theme.scale)
                border.color: page.theme.selectedLine

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: Math.round(3 * page.theme.scale)
                    color: page.theme.accent
                    radius: parent.radius
                }

                RowLayout {
                    id: jobLayout
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Math.round(13 * page.theme.scale)
                    anchors.rightMargin: Math.round(12 * page.theme.scale)
                    spacing: Math.round(8 * page.theme.scale)

                    Text {
                        text: page.tr("来源任务")
                        color: page.theme.accent
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: (page.taskTitleText || page.jobText)
                            + (page.restoredSourceBv ? " · " + page.restoredSourceBv : "")
                        color: page.theme.text
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: jobHover.hovered && truncated
                        ToolTip.text: page.jobText
                        HoverHandler { id: jobHover }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: page.sideBySide ? 2 : 1
                columnSpacing: Math.round(16 * page.theme.scale)
                rowSpacing: Math.round(11 * page.theme.scale)

                MaterialRow {
                    objectName: "videoSource"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.alignment: Qt.AlignTop
                    theme: page.theme
                    title: page.tr("原视频")
                    hint: page.tr("保留原始画面与时长")
                    value: page.sourceText
                    placeholder: page.tr("尚未选择原视频")
                    fieldObjectName: "videoSourcePath"
                    buttonObjectName: "videoChooseSource"
                    buttonText: page.sourceText.length > 0 ? page.tr("更换") : page.tr("选择")
                    blocked: page.busy
                    actionPrimary: page.isPrimary("source")
                    onChooseRequested: videoDialog.open()
                }

                MaterialRow {
                    objectName: "videoMixdown"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.alignment: Qt.AlignTop
                    theme: page.theme
                    title: page.tr("Mixdown")
                    hint: page.tr("用作成品的音频轨道")
                    value: page.mixdownText
                    placeholder: page.tr("尚未选择 Mixdown")
                    fieldObjectName: "videoMixdownPath"
                    buttonObjectName: "videoChooseMixdown"
                    buttonText: page.mixdownText.length > 0 ? page.tr("更换") : page.tr("选择")
                    blocked: page.busy
                    actionPrimary: page.isPrimary("mixdown")
                    onChooseRequested: mixdownDialog.open()
                }
            }

            Flow {
                Layout.fillWidth: true
                spacing: Math.round(8 * page.theme.scale)

                UiButton {
                    objectName: "videoOpenSource"
                    visible: page.sourceText.length > 0
                    theme: page.theme
                    text: page.tr("打开原视频")
                    enabled: !page.busy
                    Accessible.name: text
                    onClicked: page.controller.openSourceVideo()
                }

                UiButton {
                    objectName: "videoOpenMixdown"
                    visible: page.mixdownText.length > 0
                    theme: page.theme
                    text: page.tr("打开 Mixdown")
                    enabled: !page.busy
                    Accessible.name: text
                    onClicked: page.controller.openMixdown()
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.tr("选择后即可分别检查画面和音轨；成品位置在导出步骤填写。")
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
        }

        StepCard {
            Layout.fillWidth: true
            theme: page.theme
            stepNumber: "02"
            title: page.tr("对齐与预览")
            description: page.tr("使用控制器提供的对齐结果与真实首帧；这里不推断进度或对齐质量。")

            GridLayout {
                Layout.fillWidth: true
                columns: page.sideBySide ? 2 : 1
                columnSpacing: Math.round(16 * page.theme.scale)
                rowSpacing: Math.round(11 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(6 * page.theme.scale)

                    Text {
                        Layout.fillWidth: true
                        text: page.tr("音频对齐")
                        color: page.theme.text
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }

                    UiComboBox {
                        objectName: "videoAutoAlign"
                        Layout.fillWidth: true
                        theme: page.theme
                        enabled: !page.busy
                        model: [
                            { label: page.tr("自动匹配"), value: true },
                            { label: page.tr("手动偏移"), value: false }
                        ]
                        currentIndex: page.autoAlign ? 0 : 1
                        Accessible.name: page.tr("音频对齐方式")
                        onActivated: page.controller.setOption("autoAlign", Boolean(currentValue))
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.autoAlign
                            ? page.tr("控制器会在合成前使用有效分析；不可靠结果不会自动套用。")
                            : page.tr("手动调整 Mixdown 相对原视频的时间。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(6 * page.theme.scale)

                    Text {
                        Layout.fillWidth: true
                        text: page.tr("手动偏移（秒）")
                        color: page.theme.text
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }

                    TextField {
                        id: offsetField
                        objectName: "videoOffset"
                        Layout.fillWidth: true
                        text: page.offsetText()
                        placeholderText: "0.000"
                        readOnly: page.autoAlign || page.busy
                        enabled: !page.autoAlign && !page.busy
                        selectByMouse: true
                        focusPolicy: Qt.StrongFocus
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        color: enabled ? page.theme.text : page.theme.muted
                        placeholderTextColor: page.theme.muted
                        Accessible.name: page.tr("手动偏移秒数")
                        validator: DoubleValidator {
                            bottom: -3600
                            top: 3600
                            decimals: 3
                            notation: DoubleValidator.StandardNotation
                        }
                        onEditingFinished: page.commitOffset(offsetField)
                        background: Rectangle {
                            implicitHeight: Math.round(38 * page.theme.scale)
                            radius: Math.round(7 * page.theme.scale)
                            color: offsetField.enabled ? page.theme.surface : page.theme.base
                            border.width: 1
                            border.color: offsetField.activeFocus ? page.theme.accent : page.theme.line
                            opacity: offsetField.enabled ? 1 : 0.62
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.autoAlign
                            ? page.tr("自动对齐开启时不可手填偏移。")
                            : page.tr("正数延后，负数提前。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.columnSpan: page.sideBySide ? 2 : 1
                    spacing: Math.round(9 * page.theme.scale)

                    UiButton {
                        objectName: "videoNormalize"
                        theme: page.theme
                        text: page.normalize
                            ? page.tr("响度归一化：-14 LUFS") : page.tr("保持原响度")
                        selected: page.normalize
                        enabled: !page.busy
                        Accessible.name: page.tr("切换响度归一化")
                        onClicked: page.controller.setOption("normalize", !page.normalize)
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: page.tr("双遍测量由现有后端完成；可在合成结果中复核。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: page.theme.line
            }

            GridLayout {
                Layout.fillWidth: true
                columns: page.sideBySide ? 2 : 1
                columnSpacing: Math.round(16 * page.theme.scale)
                rowSpacing: Math.round(11 * page.theme.scale)

                Rectangle {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: alignmentLayout.implicitHeight + Math.round(24 * page.theme.scale)
                    color: page.alignmentText.length > 0 ? page.theme.selected : page.theme.base
                    radius: Math.round(9 * page.theme.scale)
                    border.width: 1
                    border.color: page.alignmentText.length > 0
                        ? page.theme.selectedLine : page.theme.line

                    ColumnLayout {
                        id: alignmentLayout
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Math.round(12 * page.theme.scale)
                        spacing: Math.round(8 * page.theme.scale)

                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: page.tr("对齐状态")
                                color: page.theme.text
                                font.pixelSize: Math.round(12.5 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                            Text {
                                visible: page.alignmentText.length > 0
                                text: page.tr("已获得")
                                color: page.theme.accent
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: page.alignmentText.length > 0
                                ? page.alignmentText
                                : page.tr("尚未获得对齐摘要。可由控制器分析后再决定自动或手动偏移。")
                            color: page.alignmentText.length > 0 ? page.theme.text : page.theme.muted
                            font.pixelSize: Math.round(11.5 * page.theme.scale)
                            lineHeight: 1.15
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            UiButton {
                                objectName: "videoAnalyze"
                                theme: page.theme
                                text: page.busy ? page.tr("正在处理…")
                                    : page.alignmentText.length > 0 ? page.tr("重新分析")
                                    : page.tr("分析音频对齐")
                                primary: page.isPrimary("analyze")
                                enabled: !page.busy && page.canAnalyze
                                Accessible.name: text
                                onClicked: page.controller.analyze()
                            }

                            UiButton {
                                objectName: "videoAdoptSuggestion"
                                visible: page.autoAlign && page.alignmentText.length > 0
                                theme: page.theme
                                text: page.tr("采用建议偏移")
                                enabled: page.canAdoptSuggestion
                                Accessible.name: text
                                onClicked: page.controller.adoptSuggestion()
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: previewLayout.implicitHeight + Math.round(24 * page.theme.scale)
                    color: page.theme.base
                    radius: Math.round(9 * page.theme.scale)
                    border.width: 1
                    border.color: page.previewUrl.length > 0
                        ? page.theme.selectedLine : page.theme.line

                    ColumnLayout {
                        id: previewLayout
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Math.round(12 * page.theme.scale)
                        spacing: Math.round(8 * page.theme.scale)

                        Text {
                            Layout.fillWidth: true
                            text: page.tr("首帧画面")
                            color: page.theme.text
                            font.pixelSize: Math.round(12.5 * page.theme.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Layout.preferredHeight: Math.round(210 * page.theme.scale)
                            color: page.theme.surface
                            radius: Math.round(7 * page.theme.scale)
                            border.color: page.theme.line
                            clip: true

                            Image {
                                id: previewImage
                                objectName: "videoPreview"
                                anchors.fill: parent
                                anchors.margins: 1
                                visible: page.previewUrl.length > 0
                                source: page.previewUrl
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                                cache: false
                                Accessible.name: page.tr("原视频首帧预览")
                            }

                            Column {
                                anchors.centerIn: parent
                                width: Math.max(0, parent.width - Math.round(32 * page.theme.scale))
                                spacing: Math.round(5 * page.theme.scale)
                                visible: page.previewUrl.length === 0
                                    || previewImage.status === Image.Loading
                                    || previewImage.status === Image.Error

                                Text {
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    text: page.previewUrl.length === 0
                                        ? page.tr("选择原视频后可生成首帧")
                                        : previewImage.status === Image.Error
                                            ? page.tr("预览图片无法读取，请重新生成")
                                            : page.tr("正在载入预览…")
                                    color: previewImage.status === Image.Error
                                        ? page.theme.error : page.theme.muted
                                    font.pixelSize: Math.round(11.5 * page.theme.scale)
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }
                                Text {
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    visible: page.previewUrl.length === 0
                                    text: page.tr("预览仅显示静态首帧，不启动媒体播放。")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: page.previewUrl.length > 0
                                    ? page.tr("已由后端生成真实首帧。") : page.tr("尚未生成预览。")
                                color: page.theme.muted
                                font.pixelSize: Math.round(10.5 * page.theme.scale)
                                textFormat: Text.PlainText
                                wrapMode: Text.Wrap
                            }

                            UiButton {
                                objectName: "videoGeneratePreview"
                                theme: page.theme
                                text: page.previewUrl.length > 0
                                    ? page.tr("刷新首帧") : page.tr("生成首帧")
                                primary: page.isPrimary("preview")
                                enabled: !page.busy && page.sourceText.length > 0
                                Accessible.name: text
                                onClicked: page.controller.previewFrame(0.0)
                            }
                        }
                    }
                }
            }
        }

        StepCard {
            Layout.fillWidth: true
            theme: page.theme
            stepNumber: "03"
            title: page.tr("导出与下一步")
            description: page.tr("确认成品位置并合成；结果仍由控制器核验，过期候选不会被当作当前结果。")

            MaterialRow {
                objectName: "videoOutput"
                Layout.fillWidth: true
                theme: page.theme
                title: page.tr("成品位置")
                hint: page.tr("输出 MP4；未填写时沿用控制器给出的默认位置")
                value: page.outputText
                placeholder: page.tr("尚未选择成品位置")
                fieldObjectName: "videoOutputPath"
                buttonObjectName: "videoChooseOutput"
                buttonText: page.resultStale && !page.canRender
                    ? page.tr("选择新位置")
                    : page.outputText.length > 0 ? page.tr("更换") : page.tr("选择")
                blocked: page.busy
                actionPrimary: page.isPrimary("output")
                onChooseRequested: outputDialog.open()
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: resultLayout.implicitHeight + Math.round(24 * page.theme.scale)
                color: page.resultStale
                    ? Qt.rgba(page.theme.attention.r, page.theme.attention.g, page.theme.attention.b, 0.12)
                    : page.hasResult ? page.theme.selected : page.theme.base
                radius: Math.round(9 * page.theme.scale)
                border.width: page.resultStale ? 2 : 1
                border.color: page.resultStale
                    ? page.theme.attention
                    : page.hasResult ? page.theme.selectedLine : page.theme.line

                ColumnLayout {
                    id: resultLayout
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(12 * page.theme.scale)
                    spacing: Math.round(7 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.theme.scale)

                        Rectangle {
                            Layout.alignment: Qt.AlignVCenter
                            implicitWidth: Math.round(8 * page.theme.scale)
                            implicitHeight: implicitWidth
                            radius: width / 2
                            color: page.resultStale
                                ? page.theme.attention
                                : page.hasResult ? page.theme.success : page.theme.muted
                        }

                        Text {
                            Layout.fillWidth: true
                            text: page.resultStale
                                ? page.tr("当前候选已过期")
                                : page.hasResult ? page.tr("当前候选") : page.tr("尚无合成结果")
                            color: page.resultStale
                                ? page.theme.attention
                                : page.hasResult ? page.theme.text : page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: page.resultStale
                            text: page.tr("请重新合成")
                            color: page.theme.attention
                            font.pixelSize: Math.round(10 * page.theme.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.resultStale
                            ? page.tr("素材或参数已变化；旧成品仍可定位，但登记和发布入口会保持禁用。")
                            : page.hasResult
                                ? page.tr("成品已生成。打开检查后再决定登记到结果或带入发布。")
                                : page.tr("合成完成后显示成品路径、候选状态与后续入口。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        lineHeight: 1.15
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }

                    Text {
                        objectName: "videoResultPath"
                        Layout.fillWidth: true
                        visible: page.hasResult && page.resultPathText().length > 0
                        text: page.resultPathText()
                        color: page.theme.text
                        font.family: "Consolas"
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: resultHover.hovered && truncated
                        ToolTip.text: page.resultPathText()
                        HoverHandler { id: resultHover }
                    }

                    Text {
                        objectName: "videoLoudnessReport"
                        Layout.fillWidth: true
                        visible: page.hasResult && page.loudnessReportText().length > 0
                        text: page.loudnessReportText()
                        color: page.theme.text
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        lineHeight: 1.15
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }
            }

            Flow {
                Layout.fillWidth: true
                spacing: Math.round(8 * page.theme.scale)

                UiButton {
                    objectName: "videoRender"
                    theme: page.theme
                    text: page.busy ? page.tr("正在处理…")
                        : page.resultStale ? page.tr("重新合成") : page.tr("合成视频")
                    primary: page.isPrimary("render")
                    enabled: !page.busy && page.canRender
                    Accessible.name: text
                    onClicked: page.controller.render()
                }

                UiButton {
                    objectName: "videoOpenResult"
                    visible: page.hasResult
                    theme: page.theme
                    text: page.tr("打开成品")
                    primary: page.isPrimary("openResult")
                    enabled: page.hasResult && !page.busy
                    Accessible.name: text
                    onClicked: page.controller.openResult()
                }

                UiButton {
                    objectName: "videoRegister"
                    theme: page.theme
                    text: page.tr("登记到结果")
                    primary: page.isPrimary("register")
                    enabled: page.hasResult && !page.resultStale && !page.busy && page.canRegister
                    Accessible.name: text
                    onClicked: page.controller.registerResult()
                }

                UiButton {
                    objectName: "videoPublish"
                    theme: page.theme
                    text: page.tr("带入发布")
                    primary: page.isPrimary("publish")
                    enabled: !page.resultStale && !page.busy && page.canPublish
                    Accessible.name: text
                    onClicked: page.controller.publishResult()
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.busy
                    ? page.tr("当前任务由后台控制；完成后按钮会按最新状态恢复。")
                    : page.resultStale
                        ? page.tr("请重新合成后再登记或发布当前版本。")
                        : page.hasResult
                            ? page.tr("登记与发布入口只在控制器核验允许时启用。")
                            : page.tr("合成完成后可打开、登记或带入发布。")
                color: page.theme.muted
                font.pixelSize: Math.round(10.5 * page.theme.scale)
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(24 * page.theme.scale)
        }
    }

    FileDialog {
        id: videoDialog
        title: page.tr("选择原视频")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.tr("视频文件") + " (*.mp4 *.mov *.mkv *.webm *.avi)",
            page.tr("所有文件") + " (*)"
        ]
        onAccepted: page.controller.chooseFile("sourceVideo", selectedFile)
    }

    FileDialog {
        id: mixdownDialog
        title: page.tr("选择 Mixdown")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.tr("音频文件") + " (*.wav *.flac *.mp3 *.m4a *.aac *.ogg)",
            page.tr("所有文件") + " (*)"
        ]
        onAccepted: page.controller.chooseFile("mixdown", selectedFile)
    }

    FileDialog {
        id: outputDialog
        title: page.tr("选择成品位置")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "mp4"
        nameFilters: [
            page.tr("MP4 视频") + " (*.mp4)",
            page.tr("所有文件") + " (*)"
        ]
        onAccepted: page.controller.chooseFile("output", selectedFile)
    }
}
