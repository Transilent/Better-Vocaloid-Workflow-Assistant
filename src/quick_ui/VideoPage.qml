import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
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
    readonly property bool sourceAvailable: page.boolValue(
        view.sourceAvailable, page.sourceText.length > 0)
    readonly property bool mixdownAvailable: page.boolValue(
        view.mixdownAvailable, page.mixdownText.length > 0)
    readonly property bool autoNeedsManual: page.boolValue(view.autoNeedsManual, false)
    readonly property bool hasResult: page.resultValue()
    readonly property string taskTitleText: page.textOf(view.taskTitle)
    readonly property string restoredSourceBv: page.textOf(view.sourceBv)
    readonly property string restoreMessageText: page.textOf(view.restoreMessage)
    readonly property bool sideBySide: page.width >= Math.round(980 * Math.min(1.2, page.theme.scale))
    readonly property bool narrow: page.width < Math.round(700 * page.theme.scale)
    readonly property int pageMargin: Math.round((page.narrow ? 14 : 22) * page.theme.scale)
    readonly property int cardPadding: Math.round((page.narrow ? 13 : 16) * page.theme.scale)
    readonly property int contentAvailableWidth: Math.max(0, page.width - page.pageMargin * 2)
    readonly property int contentMaxWidth: Math.round(1360 * page.theme.scale)
    readonly property int workspaceWidth: Math.min(page.contentAvailableWidth, page.contentMaxWidth)
    readonly property int inspectorWidth: Math.min(
        Math.round(410 * page.theme.scale),
        Math.max(Math.round(326 * page.theme.scale), Math.round(page.workspaceWidth * 0.30)))
    readonly property int footerHeight: Math.round(72 * page.theme.scale)
    readonly property string primaryAction: page.choosePrimaryAction()
    readonly property string statusLine: page.textOf(view.message).length > 0
        ? page.textOf(view.message) : page.defaultStatus()
    property string restoreJobId: ""
    property bool outputOptionsExpanded: false
    property bool taskInputsExpanded: false
    onJobTextChanged: if (jobText.length) taskInputsExpanded = false

    function tr(source) {
        if (page.controller && typeof page.controller.text === "function")
            return page.controller.text(source)
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
        const result = page.view.result
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
        const result = page.view.result
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
        if (!isFinite(number))
            return ""
        return label + " " + number.toFixed(digits)
    }

    function loudnessReportText() {
        const receipt = page.view.receipt
        if (!receipt || typeof receipt !== "object")
            return ""
        const parts = [receipt.normalized === true
            ? page.tr("响度处理：已应用") : page.tr("响度处理：未启用")]
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
        const value = page.view.offset
        if (value === undefined || value === null || value === "")
            return "0.000"
        const number = Number(value)
        return isFinite(number) ? number.toFixed(3) : String(value)
    }

    function signedSeconds(value) {
        if (typeof value !== "number" || !isFinite(value))
            return ""
        return (value > 0 ? "+" : "") + value.toFixed(3) + " " + page.tr("秒")
    }

    function previewTimeText() {
        const value = page.view.previewTime
        if (value === undefined || value === null || value === "")
            return page.tr("未取帧")
        const seconds = Number(value)
        if (!isFinite(seconds) || seconds < 0)
            return page.tr("时间未知")
        const minutes = Math.floor(seconds / 60)
        const remainder = seconds - minutes * 60
        const minuteText = (minutes < 10 ? "0" : "") + String(minutes)
        const secondText = (remainder < 10 ? "0" : "") + remainder.toFixed(3)
        return minuteText + ":" + secondText
    }

    function adoptedOffsetText() {
        if (!page.autoAlign)
            return page.signedSeconds(page.view.offset)
        if (page.autoNeedsManual)
            return page.tr("需要检查或手动偏移")
        const receipt = page.view.receipt
        if (page.hasResult && !page.resultStale && receipt && receipt.alignment) {
            const applied = page.signedSeconds(receipt.alignment.offset_seconds)
            if (applied.length)
                return applied + " · " + page.tr("自动")
        }
        return page.tr("自动 · 合成时确认")
    }

    function commitOffset(field) {
        if (!field.edited)
            return
        field.edited = false
        const number = Number(field.text.trim())
        if (field.acceptableInput && field.text.trim() !== "" && isFinite(number))
            page.controller.setManualOffset(number)
        field.text = Qt.binding(() => page.offsetText())
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
            return page.tr("当前候选已过期；旧成品仍可外部打开，但不可登记或发布。")
        if (page.hasResult)
            return page.tr("成品已生成，可用外部播放器检查。")
        if (!page.sourceAvailable)
            return page.tr("先选择可用原视频；没有 Mixdown 时仍可检查静帧。")
        if (page.autoNeedsManual && page.autoAlign)
            return page.tr("自动对齐候选不可靠，请改为手动偏移后合成。")
        if (!page.mixdownAvailable)
            return page.tr("补充 Mixdown 以检查声音和对齐。")
        return page.tr("检查画面和对齐后合成 MP4。")
    }

    function choosePrimaryAction() {
        if (page.busy)
            return ""
        if (!page.sourceAvailable)
            return "source"
        if (!page.mixdownAvailable)
            return "mixdown"
        if (page.hasResult && !page.resultStale) {
            if (page.canRegister)
                return "register"
            if (page.canPublish)
                return "publish"
            return "openResult"
        }
        if (page.autoNeedsManual)
            return "manualOffset"
        if (page.autoAlign && page.canAnalyze && page.alignmentText.length === 0)
            return "analyze"
        if (page.canRender)
            return "render"
        return "output"
    }

    function isPrimary(action) {
        return page.primaryAction === action
    }

    function primaryActionLabel() {
        switch (page.primaryAction) {
        case "source": return page.tr("选择原视频")
        case "mixdown": return page.tr("选择 Mixdown")
        case "manualOffset": return page.tr("改为手动偏移")
        case "analyze": return page.tr("分析音频对齐")
        case "output": return page.tr("选择成品位置")
        case "render": return page.resultStale || page.hasResult
            ? page.tr("重新合成") : page.tr("合成视频")
        case "register": return page.tr("登记到结果")
        case "publish": return page.tr("带入发布")
        case "openResult": return page.tr("打开成品")
        default: return page.busy ? page.tr("正在处理…") : page.tr("等待可用输入")
        }
    }

    function primaryActionEnabled() {
        switch (page.primaryAction) {
        case "source":
        case "mixdown":
        case "output":
        case "openResult": return !page.busy
        case "manualOffset": return !page.busy && page.autoAlign && page.autoNeedsManual
        case "analyze": return !page.busy && page.canAnalyze
        case "render": return !page.busy && page.canRender
        case "register": return page.hasResult && !page.resultStale && !page.busy
            && page.canRegister
        case "publish": return !page.resultStale && !page.busy && page.canPublish
        default: return false
        }
    }

    function runPrimaryAction() {
        switch (page.primaryAction) {
        case "source": videoDialog.open(); break
        case "mixdown": mixdownDialog.open(); break
        case "manualOffset":
            page.controller.setOption("autoAlign", false)
            offsetField.forceActiveFocus()
            break
        case "analyze": page.controller.analyze(); break
        case "output": outputDialog.open(); break
        case "render": page.controller.render(); break
        case "register": page.controller.registerResult(); break
        case "publish": page.controller.publishResult(); break
        case "openResult": page.controller.openResult(); break
        }
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

    component MaterialRow: RowLayout {
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

        spacing: Math.round(9 * row.theme.scale)

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: Math.round(3 * row.theme.scale)
            Text {
                Layout.fillWidth: true
                text: row.title
                color: row.theme.text
                font.pixelSize: Math.round(12 * row.theme.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            Text {
                objectName: row.fieldObjectName
                Layout.fillWidth: true
                text: row.value.length ? row.value.substring(Math.max(row.value.lastIndexOf("/"), row.value.lastIndexOf("\\")) + 1) : row.placeholder
                color: row.theme.muted
                font.pixelSize: Math.round(11 * row.theme.scale)
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
                ToolTip.visible: fileHover.hovered
                ToolTip.text: row.value || row.hint
                HoverHandler { id: fileHover }
            }
        }

        UiButton {
            objectName: row.buttonObjectName
            theme: row.theme
            text: row.buttonText
            selected: row.actionPrimary
            enabled: !row.blocked
            Accessible.name: row.title + " · " + text
            onClicked: row.chooseRequested()
        }
    }

    ScrollView {
        id: contentScroll
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: contentColumn
            width: page.workspaceWidth
            height: Math.max(contentScroll.availableHeight, implicitHeight)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Math.round(12 * page.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Math.round(16 * page.theme.scale)
                spacing: Math.round(14 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(3 * page.theme.scale)

                    Text {
                        Layout.fillWidth: true
                        text: page.tr("视频工作台")
                        color: page.theme.accent
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.2
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("合成视频")
                        color: page.theme.text
                        font.pixelSize: Math.round(24 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("检查原片、声音与对齐，再合成新的 MP4。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11.5 * page.theme.scale)
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                    }
                }

                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: headerState.implicitWidth + Math.round(18 * page.theme.scale)
                    implicitHeight: Math.round(25 * page.theme.scale)
                    radius: height / 2
                    color: Qt.rgba(page.stateColor().r, page.stateColor().g,
                                   page.stateColor().b, 0.13)
                    border.color: page.stateColor()
                    Text {
                        id: headerState
                        anchors.centerIn: parent
                        text: page.stateLabel
                        color: page.stateColor()
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: Math.round(510 * page.theme.scale)
                spacing: Math.round(13 * page.theme.scale)

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: Math.round(520 * page.theme.scale)
                    Layout.minimumHeight: Math.round(500 * page.theme.scale)
                    color: page.theme.surface
                    radius: Math.round(11 * page.theme.scale)
                    border.width: 1
                    border.color: page.previewUrl.length > 0
                        ? page.theme.selectedLine : page.theme.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: page.cardPadding
                        spacing: Math.round(10 * page.theme.scale)

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: 1
                                Text {
                                    Layout.fillWidth: true
                                    text: page.tr("完整静帧")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(14 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.tr("保留原视频比例")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10.5 * page.theme.scale)
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }
                            }

                            Text {
                                text: page.previewUrl.length > 0
                                    ? page.tr("真实首帧") : page.tr("尚未取帧")
                                color: page.previewUrl.length > 0
                                    ? page.theme.accent : page.theme.muted
                                font.pixelSize: Math.round(10.5 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: Math.round(350 * page.theme.scale)
                            color: page.theme.base
                            radius: Math.round(8 * page.theme.scale)
                            border.width: 1
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
                                smooth: true
                                mipmap: true
                                Accessible.name: page.tr("原视频首帧完整预览")
                            }

                            Column {
                                anchors.centerIn: parent
                                width: Math.max(0, parent.width - Math.round(64 * page.theme.scale))
                                spacing: Math.round(6 * page.theme.scale)
                                visible: page.previewUrl.length === 0
                                    || previewImage.status === Image.Loading
                                    || previewImage.status === Image.Error

                                Text {
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    text: page.previewUrl.length === 0
                                        ? (page.sourceAvailable ? page.tr("原视频已就绪") : page.tr("请选择原视频"))
                                        : previewImage.status === Image.Error
                                            ? page.tr("预览图片无法读取，请重新生成")
                                            : page.tr("正在载入预览…")
                                    color: previewImage.status === Image.Error
                                        ? page.theme.error : page.theme.muted
                                    font.pixelSize: Math.round(13 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }
                                Text {
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    visible: page.previewUrl.length === 0
                                    text: page.tr("取帧后会显示原片的完整画面。")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10.5 * page.theme.scale)
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }
                            }

                            Rectangle {
                                visible: page.previewUrl.length > 0
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.margins: Math.round(10 * page.theme.scale)
                                implicitWidth: frameStamp.implicitWidth + Math.round(14 * page.theme.scale)
                                implicitHeight: Math.round(23 * page.theme.scale)
                                radius: height / 2
                                color: Qt.rgba(page.theme.surface.r, page.theme.surface.g,
                                               page.theme.surface.b, 0.9)
                                border.color: page.theme.line
                                Text {
                                    id: frameStamp
                                    anchors.centerIn: parent
                                    text: page.previewTimeText()
                                    color: page.theme.text
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    textFormat: Text.PlainText
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: 1
                                Text {
                                    objectName: "videoPreviewTime"
                                    Layout.fillWidth: true
                                    text: page.tr("抽帧时间 ") + page.previewTimeText()
                                    color: page.theme.text
                                    font.pixelSize: Math.round(10.5 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.previewUrl.length > 0
                                        ? page.tr("原视频 · 静帧")
                                        : page.tr("可单独检查原片")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }
                            }

                            UiButton {
                                objectName: "videoGeneratePreview"
                                theme: page.theme
                                text: page.previewUrl.length > 0
                                    ? page.tr("刷新首帧") : page.tr("生成首帧")
                                primary: false
                                quiet: page.previewUrl.length > 0
                                enabled: !page.busy && page.sourceAvailable
                                Accessible.name: text
                                onClicked: page.controller.previewFrame(0.0)
                            }

                            UiButton {
                                objectName: "videoOpenSource"
                                visible: page.sourceText.length > 0
                                theme: page.theme
                                text: page.tr("用外部播放器检查")
                                quiet: true
                                enabled: !page.busy && page.sourceAvailable
                                Accessible.name: text
                                onClicked: page.controller.openSourceVideo()
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: page.inspectorWidth
                    Layout.minimumWidth: Math.round(304 * page.theme.scale)
                    Layout.fillHeight: true
                    color: page.theme.surface
                    radius: Math.round(11 * page.theme.scale)
                    border.width: 1
                    border.color: page.theme.line

                    ScrollView {
                        id: inspectorScroll
                        anchors.fill: parent
                        anchors.margins: 1
                        contentWidth: availableWidth
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical.policy: ScrollBar.AsNeeded

                        ColumnLayout {
                            width: inspectorScroll.availableWidth
                            spacing: Math.round(10 * page.theme.scale)

                            Item { Layout.preferredHeight: Math.round(12 * page.theme.scale) }

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(14 * page.theme.scale)
                                Layout.rightMargin: Math.round(14 * page.theme.scale)
                                Text {
                                    Layout.fillWidth: true
                                    text: page.tr("输入来源")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(14 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    text: page.sourceAvailable && page.mixdownAvailable
                                        ? page.tr("素材可用") : page.tr("独立 / 任务")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(9.5 * page.theme.scale)
                                    textFormat: Text.PlainText
                                }
                            }

                            MaterialRow {
                                objectName: "videoSource"
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                theme: page.theme
                                title: page.tr("原视频")
                                hint: page.tr("保留原始画面与时长")
                                value: page.sourceText
                                placeholder: page.tr("尚未选择原视频")
                                fieldObjectName: "videoSourcePath"
                                buttonObjectName: "videoChooseSource"
                                buttonText: page.sourceText.length > 0
                                    ? page.tr("更换") : page.tr("选择")
                                blocked: page.busy
                                actionPrimary: page.isPrimary("source")
                                onChooseRequested: videoDialog.open()
                            }

                            MaterialRow {
                                objectName: "videoMixdown"
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                theme: page.theme
                                title: page.tr("Mixdown")
                                hint: page.tr("用作成品的音频轨道")
                                value: page.mixdownText
                                placeholder: page.tr("尚未选择 Mixdown")
                                fieldObjectName: "videoMixdownPath"
                                buttonObjectName: "videoChooseMixdown"
                                buttonText: page.mixdownText.length > 0
                                    ? page.tr("更换") : page.tr("选择")
                                blocked: page.busy
                                actionPrimary: page.isPrimary("mixdown")
                                onChooseRequested: mixdownDialog.open()
                            }

                            UiButton {
                                objectName: "videoOpenMixdown"
                                visible: page.mixdownText.length > 0
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                theme: page.theme
                                text: page.tr("外部检查 Mixdown")
                                quiet: true
                                enabled: !page.busy && page.mixdownAvailable
                                Accessible.name: text
                                onClicked: page.controller.openMixdown()
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                Layout.preferredHeight: 1
                                color: page.theme.line
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                spacing: Math.round(7 * page.theme.scale)

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.jobText.length
                                            ? page.tr("任务") + " · " + (page.taskTitleText || page.jobText)
                                              + (page.restoredSourceBv ? " · " + page.restoredSourceBv : "")
                                            : page.tr("独立合成")
                                        color: page.jobText.length ? page.theme.accent : page.theme.muted
                                        font.pixelSize: Math.round(11 * page.theme.scale)
                                        elide: Text.ElideMiddle
                                        textFormat: Text.PlainText
                                        ToolTip.visible: taskHover.hovered && page.jobText.length > 0
                                        ToolTip.text: page.jobText
                                        HoverHandler { id: taskHover }
                                    }
                                    UiButton {
                                        objectName: "videoTaskInputsToggle"
                                        theme: page.theme
                                        quiet: true
                                        text: page.taskInputsExpanded ? page.tr("收起任务") : page.tr("从任务带入")
                                        onClicked: page.taskInputsExpanded = !page.taskInputsExpanded
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    visible: page.taskInputsExpanded
                                    spacing: Math.round(6 * page.theme.scale)
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Math.round(7 * page.theme.scale)
                                        TextField {
                                            id: sourceIdentityField
                                            objectName: "videoSourceIdentity"
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            placeholderText: page.tr("BV 号或链接（可选）")
                                            enabled: !page.busy
                                            selectByMouse: true
                                            font.pixelSize: Math.round(12 * page.theme.scale)
                                            onAccepted: {
                                                page.restoreJobId = ""
                                                page.controller.refreshTaskChoices(text)
                                            }
                                        }
                                        UiButton {
                                            objectName: "videoFindTask"
                                            theme: page.theme
                                            text: page.taskScanBusy
                                                ? page.tr("查找中…") : page.tr("查找")
                                            enabled: page.canRefreshTasks
                                            Accessible.name: text
                                            onClicked: {
                                                page.restoreJobId = ""
                                                page.controller.refreshTaskChoices(sourceIdentityField.text)
                                            }
                                        }
                                    }

                                    UiComboBox {
                                        objectName: "videoTaskPicker"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        model: page.restoreChoices()
                                        enabled: !page.busy && model.length > 1
                                        currentIndex: Math.max(0, model.findIndex(
                                            row => row.value === page.restoreJobId))
                                        onActivated: page.chooseRestoreTask(currentValue)
                                    }

                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: Math.round(7 * page.theme.scale)

                                        UiButton {
                                            objectName: "videoRestoreTask"
                                            theme: page.theme
                                            text: page.tr("载入原片 + Mixdown")
                                            enabled: !page.busy && !!page.restoreJobId
                                            Accessible.name: text
                                            onClicked: page.controller.restoreTask(
                                                page.restoreJobId, sourceIdentityField.text)
                                        }

                                        UiButton {
                                            objectName: "videoRestoreTaskSource"
                                            theme: page.theme
                                            quiet: true
                                            text: page.tr("仅载入任务原视频…")
                                            enabled: !page.busy && !!page.restoreJobId
                                            Accessible.name: text
                                            onClicked: sourceOnlyDialog.open()
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        visible: page.restoreMessageText.length > 0
                                        text: page.restoreMessageText
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        wrapMode: Text.Wrap
                                        textFormat: Text.PlainText
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                Layout.preferredHeight: 1
                                color: page.theme.line
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                spacing: Math.round(8 * page.theme.scale)

                                Text {
                                    Layout.fillWidth: true
                                    text: page.tr("声音与对齐")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(14 * page.theme.scale)
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
                                    onActivated: page.controller.setOption(
                                        "autoAlign", Boolean(currentValue))
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: adoptedLayout.implicitHeight
                                        + Math.round(20 * page.theme.scale)
                                    color: page.theme.base
                                    radius: Math.round(8 * page.theme.scale)
                                    border.color: page.autoNeedsManual
                                        ? page.theme.attention : page.theme.line

                                    ColumnLayout {
                                        id: adoptedLayout
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: Math.round(10 * page.theme.scale)
                                        spacing: Math.round(4 * page.theme.scale)

                                        Text {
                                            Layout.fillWidth: true
                                            text: page.tr("本次合成实际采用")
                                            color: page.theme.muted
                                            font.pixelSize: Math.round(9.5 * page.theme.scale)
                                            font.weight: Font.DemiBold
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            objectName: "videoAdoptedOffset"
                                            Layout.fillWidth: true
                                            text: page.adoptedOffsetText()
                                            color: page.autoNeedsManual && page.autoAlign
                                                ? page.theme.attention : page.theme.text
                                            font.pixelSize: Math.round(12.5 * page.theme.scale)
                                            font.weight: Font.DemiBold
                                            textFormat: Text.PlainText
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: page.tr("手动偏移（秒）")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(9.5 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    textFormat: Text.PlainText
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(7 * page.theme.scale)
                                    TextField {
                                        id: offsetField
                                        objectName: "videoOffset"
                                        property bool edited: false
                                        Layout.fillWidth: true
                                        text: page.offsetText()
                                        placeholderText: "0.000"
                                        readOnly: page.busy
                                        enabled: !page.busy
                                        selectByMouse: true
                                        focusPolicy: Qt.StrongFocus
                                        font.pixelSize: Math.round(13 * page.theme.scale)
                                        color: page.theme.text
                                        placeholderTextColor: page.theme.muted
                                        Accessible.name: page.tr("手动偏移秒数")
                                        validator: DoubleValidator {
                                            bottom: -3600
                                            top: 3600
                                            decimals: 3
                                            locale: "C"
                                            notation: DoubleValidator.StandardNotation
                                        }
                                        onTextEdited: edited = true
                                        onEditingFinished: page.commitOffset(offsetField)
                                        onActiveFocusChanged: if (!activeFocus) page.commitOffset(offsetField)
                                        background: Rectangle {
                                            implicitHeight: Math.round(38 * page.theme.scale)
                                            radius: Math.round(7 * page.theme.scale)
                                            color: offsetField.enabled
                                                ? page.theme.surface : page.theme.base
                                            border.width: 1
                                            border.color: offsetField.activeFocus
                                                ? page.theme.accent : page.theme.line
                                            opacity: offsetField.enabled ? 1 : 0.62
                                        }
                                    }
                                    Text {
                                        text: page.tr("秒")
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                                        textFormat: Text.PlainText
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.autoAlign
                                        ? page.tr("直接编辑会切换为手动偏移；正数延后，负数提前。")
                                        : page.tr("当前使用这个手动偏移；正数延后，负数提前。")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(9.5 * page.theme.scale)
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: candidateLayout.implicitHeight
                                        + Math.round(20 * page.theme.scale)
                                    color: page.alignmentText.length > 0
                                        ? page.theme.selected : page.theme.base
                                    radius: Math.round(8 * page.theme.scale)
                                    border.width: 1
                                    border.color: page.alignmentText.length > 0
                                        ? page.theme.selectedLine : page.theme.line

                                    ColumnLayout {
                                        id: candidateLayout
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: Math.round(10 * page.theme.scale)
                                        spacing: Math.round(6 * page.theme.scale)

                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text {
                                                Layout.fillWidth: true
                                                text: page.tr("分析建议")
                                                color: page.theme.text
                                                font.pixelSize: Math.round(10.5 * page.theme.scale)
                                                font.weight: Font.DemiBold
                                                textFormat: Text.PlainText
                                            }
                                            Text {
                                                text: page.signedSeconds(page.view.alignmentOffset).length > 0
                                                    ? page.signedSeconds(page.view.alignmentOffset)
                                                    : page.autoNeedsManual
                                                        ? page.tr("不可靠")
                                                        : page.alignmentText.length > 0
                                                            ? page.tr("未给数值") : page.tr("等待分析")
                                                color: page.signedSeconds(page.view.alignmentOffset).length > 0
                                                    ? page.theme.accent : page.theme.muted
                                                font.pixelSize: Math.round(10.5 * page.theme.scale)
                                                font.weight: Font.DemiBold
                                                textFormat: Text.PlainText
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: page.alignmentText.length > 0
                                                ? page.alignmentText
                                                : page.tr("分析后在此显示建议与可信度；采用前不会改写当前值。")
                                            color: page.alignmentText.length > 0
                                                ? page.theme.text : page.theme.muted
                                            font.pixelSize: Math.round(10 * page.theme.scale)
                                            lineHeight: 1.12
                                            textFormat: Text.PlainText
                                            wrapMode: Text.Wrap
                                        }

                                        Flow {
                                            Layout.fillWidth: true
                                            spacing: Math.round(7 * page.theme.scale)

                                            UiButton {
                                                objectName: "videoAnalyze"
                                                theme: page.theme
                                                text: page.busy ? page.tr("正在处理…")
                                                    : page.alignmentText.length > 0
                                                        ? page.tr("重新分析")
                                                        : page.tr("分析音频对齐")
                                                primary: false
                                                enabled: !page.busy && page.canAnalyze
                                                Accessible.name: text
                                                onClicked: page.controller.analyze()
                                            }

                                            UiButton {
                                                objectName: "videoAdoptSuggestion"
                                                visible: page.autoAlign
                                                    && page.alignmentText.length > 0
                                                theme: page.theme
                                                text: page.tr("采用建议偏移")
                                                quiet: true
                                                enabled: page.canAdoptSuggestion
                                                Accessible.name: text
                                                onClicked: page.controller.adoptSuggestion()
                                            }
                                        }
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(8 * page.theme.scale)

                                    UiButton {
                                        objectName: "videoNormalize"
                                        theme: page.theme
                                        text: page.normalize
                                            ? page.tr("响度归一化：-14 LUFS")
                                            : page.tr("保持原响度")
                                        selected: page.normalize
                                        enabled: !page.busy
                                        Accessible.name: page.tr("切换响度归一化")
                                        onClicked: page.controller.setOption(
                                            "normalize", !page.normalize)
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        text: page.tr("实测在合成结果中显示。")
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(9.5 * page.theme.scale)
                                        textFormat: Text.PlainText
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                Layout.preferredHeight: 1
                                color: page.theme.line
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: Math.round(13 * page.theme.scale)
                                Layout.rightMargin: Math.round(13 * page.theme.scale)
                                spacing: Math.round(8 * page.theme.scale)

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.tr("输出、登记与发布")
                                        color: page.theme.text
                                        font.pixelSize: Math.round(14 * page.theme.scale)
                                        font.weight: Font.DemiBold
                                        textFormat: Text.PlainText
                                    }
                                    UiButton {
                                        objectName: "videoOutputOptionsToggle"
                                        theme: page.theme
                                        text: page.outputOptionsExpanded
                                            ? page.tr("收起输出") : page.tr("输出选项")
                                        quiet: true
                                        Accessible.name: text
                                        onClicked: page.outputOptionsExpanded
                                            = !page.outputOptionsExpanded
                                    }
                                }

                                Text {
                                    objectName: "videoOutputSummary"
                                    Layout.fillWidth: true
                                    text: page.outputText.length > 0
                                        ? page.outputText
                                        : page.tr("未填写时沿用控制器给出的默认成品位置")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    elide: Text.ElideMiddle
                                    textFormat: Text.PlainText
                                    ToolTip.visible: outputHover.hovered && truncated
                                    ToolTip.text: page.outputText
                                    HoverHandler { id: outputHover }
                                }

                                MaterialRow {
                                    objectName: "videoOutput"
                                    visible: page.outputOptionsExpanded
                                        || page.isPrimary("output")
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    title: page.tr("成品位置")
                                    hint: page.tr("输出 MP4；不改变输入文件")
                                    value: page.outputText
                                    placeholder: page.tr("尚未选择成品位置")
                                    fieldObjectName: "videoOutputPath"
                                    buttonObjectName: "videoChooseOutput"
                                    buttonText: page.resultStale && !page.canRender
                                        ? page.tr("选择新位置")
                                        : page.outputText.length > 0
                                            ? page.tr("更换") : page.tr("选择")
                                    blocked: page.busy
                                    actionPrimary: page.isPrimary("output")
                                    onChooseRequested: outputDialog.open()
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    visible: page.hasResult || page.resultStale
                                    implicitHeight: resultLayout.implicitHeight
                                        + Math.round(20 * page.theme.scale)
                                    color: page.resultStale
                                        ? Qt.rgba(page.theme.attention.r,
                                                   page.theme.attention.g,
                                                   page.theme.attention.b, 0.12)
                                        : page.hasResult
                                            ? page.theme.selected : page.theme.base
                                    radius: Math.round(8 * page.theme.scale)
                                    border.width: page.resultStale ? 2 : 1
                                    border.color: page.resultStale
                                        ? page.theme.attention
                                        : page.hasResult
                                            ? page.theme.selectedLine : page.theme.line

                                    ColumnLayout {
                                        id: resultLayout
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.top: parent.top
                                        anchors.margins: Math.round(10 * page.theme.scale)
                                        spacing: Math.round(6 * page.theme.scale)

                                        RowLayout {
                                            Layout.fillWidth: true
                                            Rectangle {
                                                implicitWidth: Math.round(7 * page.theme.scale)
                                                implicitHeight: implicitWidth
                                                radius: width / 2
                                                color: page.resultStale
                                                    ? page.theme.attention
                                                    : page.hasResult
                                                        ? page.theme.success
                                                        : page.theme.muted
                                            }
                                            Text {
                                                objectName: "videoResultState"
                                                Layout.fillWidth: true
                                                text: page.resultStale
                                                    ? page.tr("当前候选已过期")
                                                    : page.canPublish
                                                        ? page.tr("已登记")
                                                        : page.canRegister
                                                            ? page.tr("当前候选")
                                                            : page.tr("已完成")
                                                color: page.resultStale
                                                    ? page.theme.attention
                                                    : page.hasResult
                                                        ? page.theme.text : page.theme.muted
                                                font.pixelSize: Math.round(11 * page.theme.scale)
                                                font.weight: Font.DemiBold
                                                textFormat: Text.PlainText
                                            }
                                            Text {
                                                visible: page.resultStale
                                                text: page.tr("旧片仍可外部打开")
                                                color: page.theme.attention
                                                font.pixelSize: Math.round(9 * page.theme.scale)
                                                textFormat: Text.PlainText
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            visible: page.resultStale || page.canRegister
                                            text: page.resultStale
                                                ? page.tr("素材或参数已变化；旧 MP4 保留，但登记和发布保持禁用。")
                                                : page.tr("打开检查后再决定登记到结果或带入发布。")
                                            color: page.theme.muted
                                            font.pixelSize: Math.round(9.5 * page.theme.scale)
                                            lineHeight: 1.12
                                            textFormat: Text.PlainText
                                            wrapMode: Text.Wrap
                                        }

                                        Text {
                                            objectName: "videoResultPath"
                                            Layout.fillWidth: true
                                            visible: page.hasResult
                                                && page.resultPathText().length > 0
                                            text: page.resultPathText()
                                            color: page.theme.text
                                            font.family: "Consolas"
                                            font.pixelSize: Math.round(9.5 * page.theme.scale)
                                            elide: Text.ElideMiddle
                                            textFormat: Text.PlainText
                                            ToolTip.visible: resultHover.hovered && truncated
                                            ToolTip.text: page.resultPathText()
                                            HoverHandler { id: resultHover }
                                        }

                                        Text {
                                            objectName: "videoLoudnessReport"
                                            Layout.fillWidth: true
                                            visible: page.hasResult
                                                && page.loudnessReportText().length > 0
                                            text: page.loudnessReportText()
                                            color: page.theme.text
                                            font.pixelSize: Math.round(9.5 * page.theme.scale)
                                            lineHeight: 1.12
                                            textFormat: Text.PlainText
                                            wrapMode: Text.Wrap
                                        }

                                    }
                                }

                            }

                            Item { Layout.preferredHeight: Math.round(14 * page.theme.scale) }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: Math.round(6 * page.theme.scale) }
        }
    }

    Rectangle {
        id: footer
        objectName: "videoStatusPanel"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: page.footerHeight
        color: page.theme.surface
        border.width: 1
        border.color: page.theme.line

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 1
            color: page.busy ? page.theme.selectedLine : page.theme.line
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: page.pageMargin
            anchors.rightMargin: page.pageMargin
            spacing: Math.round(11 * page.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: Math.round(4 * page.theme.scale)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(8 * page.theme.scale)

                    Rectangle {
                        implicitWidth: footerBadge.implicitWidth
                            + Math.round(16 * page.theme.scale)
                        implicitHeight: Math.round(23 * page.theme.scale)
                        radius: height / 2
                        color: Qt.rgba(page.stateColor().r, page.stateColor().g,
                                       page.stateColor().b, 0.13)
                        border.color: page.stateColor()
                        Text {
                            id: footerBadge
                            anchors.centerIn: parent
                            text: page.stateLabel
                            color: page.stateColor()
                            font.pixelSize: Math.round(9.5 * page.theme.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: page.statusLine
                        color: page.stateKey === "failed"
                            || page.stateKey === "error"
                            ? page.theme.error : page.theme.text
                        font.pixelSize: Math.round(10.5 * page.theme.scale)
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                        ToolTip.visible: footerStatusHover.hovered && truncated
                        ToolTip.text: page.statusLine
                        HoverHandler { id: footerStatusHover }
                    }
                }

                ProgressBar {
                    objectName: "videoProgress"
                    Layout.fillWidth: true
                    visible: page.busy
                    indeterminate: page.busy && !page.theme.reduceMotion
                    value: 0
                    implicitHeight: visible ? Math.round(5 * page.theme.scale) : 0
                    palette.dark: page.theme.accent
                    palette.midlight: page.theme.raised
                    Accessible.name: page.tr("视频任务进度")
                }
            }

            UiButton {
                objectName: "videoCancel"
                visible: page.busy
                theme: page.theme
                text: page.stateKey === "cancelling"
                    ? page.tr("正在取消…") : page.tr("取消操作")
                enabled: page.busy && page.canCancel
                    && page.stateKey !== "cancelling"
                Accessible.name: text
                onClicked: page.controller.cancel()
            }

            UiButton {
                objectName: "videoOpenResult"
                visible: page.hasResult && page.primaryAction !== "openResult"
                theme: page.theme
                text: page.resultStale ? page.tr("外部打开旧成品") : page.tr("打开成品")
                quiet: true
                enabled: !page.busy
                onClicked: page.controller.openResult()
            }

            UiButton {
                objectName: "videoFooterPrimary"
                theme: page.theme
                text: page.primaryActionLabel()
                primary: true
                enabled: page.primaryActionEnabled()
                Accessible.name: text
                onClicked: page.runPrimaryAction()
            }
        }
    }

    Dialog {
        id: sourceOnlyDialog
        objectName: "videoRestoreSourceConfirm"
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: page.tr("只载入任务原视频？")
        width: Math.min(parent.width - 36, Math.round(500 * page.theme.scale))
        standardButtons: Dialog.Cancel | Dialog.Ok
        onAccepted: page.controller.restoreTaskSource(
            page.restoreJobId, sourceIdentityField.text)

        contentItem: Text {
            text: page.tr("将只冻结任务中已核验的 video/source.mp4，并清空已绑定 Mixdown。请手动选择与任务对应的音轨；登记成品前仍会重新核验任务当前绑定。")
            color: page.theme.text
            font.pixelSize: Math.round(12 * page.theme.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
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
