import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: pane
    objectName: "workshopCalibrationPane"
    required property QtObject theme
    required property QtObject controller
    signal bindingRequested()
    signal presetsRequested()
    signal componentsRequested()
    readonly property real unit: theme.scale
    readonly property var fit: controller ? controller.view : ({})
    readonly property bool busy: !!fit.busy
    readonly property bool hasSession: !!fit.session
    readonly property int step: fit.result ? 2 : hasSession ? 1 : 0
    property bool advanced: false
    property bool history: false

    function label(text) { return typeof quickTranslation !== "undefined" && quickTranslation ? quickTranslation.text(text) : text }
    function indexOf(rows, value) { return Math.max(0, rows.findIndex(row => row.value === value)) }
    function leaf(path) { return (path || "").split(/[\\/]/).pop() }

    component Heading: Text {
        color: pane.theme.text
        font.pixelSize: Math.round(15 * pane.unit)
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
    }
    component Copy: Text {
        Layout.fillWidth: true
        color: pane.theme.muted
        font.pixelSize: Math.round(12.5 * pane.unit)
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component PathLabel: Text {
        property string fullPath: text
        Layout.fillWidth: true
        color: pane.theme.text
        font.pixelSize: Math.round(13 * pane.unit)
        elide: Text.ElideMiddle
        textFormat: Text.PlainText
        HoverHandler { id: hover }
        ToolTip.visible: hover.hovered && (truncated || fullPath !== text)
        ToolTip.text: fullPath
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Math.round(12 * pane.unit)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(15 * pane.unit)
            Repeater {
                model: ["准备素材", "七轨试音", "拟合结果"]
                delegate: RowLayout {
                    required property string modelData
                    required property int index
                    Rectangle {
                        Layout.preferredWidth: 23 * pane.unit
                        Layout.preferredHeight: 23 * pane.unit
                        radius: width / 2
                        color: index === pane.step ? pane.theme.accent : pane.theme.raised
                        Text {
                            anchors.centerIn: parent
                            text: index < pane.step ? "✓" : String(index + 1)
                            color: index === pane.step ? pane.theme.accentInk : index < pane.step ? pane.theme.success : pane.theme.muted
                            font.pixelSize: Math.round(12 * pane.unit)
                            font.weight: Font.DemiBold
                        }
                    }
                    Text {
                        text: pane.label(modelData)
                        font.pixelSize: Math.round(13 * pane.unit)
                        font.weight: index === pane.step ? Font.DemiBold : Font.Normal
                        color: index === pane.step ? pane.theme.text : pane.theme.muted
                    }
                }
            }
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "calibrationResume"
                theme: pane.theme
                quiet: true
                text: pane.label("继续已有会话…")
                enabled: !pane.busy
                onClicked: sessionFile.open()
            }
            UiButton {
                objectName: "calibrationSessionHistory"
                theme: pane.theme
                quiet: true
                text: pane.label("校准记录")
                disclosure: pane.history ? 1 : 0
                enabled: !pane.busy
                onClicked: pane.history = !pane.history
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: pane.history
            UiButton {
                objectName: "calibrationFindTaskSessions"
                theme: pane.theme
                quiet: true
                text: pane.label("从任务查找")
                enabled: !pane.busy
                onClicked: pane.controller.findSessions("")
            }
            UiButton {
                theme: pane.theme
                quiet: true
                text: pane.label("查找文件夹…")
                enabled: !pane.busy
                onClicked: sessionFolder.open()
            }
            UiComboBox {
                id: sessions
                objectName: "calibrationSessionPicker"
                Layout.fillWidth: true
                theme: pane.theme
                model: pane.fit.sessionChoices || []
                textRole: "label"
                valueRole: "path"
                enabled: !pane.busy && count > 0
                Accessible.name: pane.label("校准记录")
            }
            UiButton {
                theme: pane.theme
                text: pane.label("继续")
                enabled: !pane.busy && sessions.currentIndex >= 0
                onClicked: {
                    pane.controller.resumeListedSession(sessions.currentValue)
                    pane.history = false
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Math.round(14 * pane.unit)

            Rectangle {
                color: pane.theme.surface
                border.color: pane.theme.line
                radius: Math.round(9 * pane.unit)
                Layout.fillHeight: true
                Layout.preferredWidth: Math.round(Math.max(252, Math.min(pane.width * .32 / pane.unit, 344)) * pane.unit)

                ScrollView {
                    id: inputScroll
                    anchors.fill: parent
                    anchors.margins: Math.round(15 * pane.unit)
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: inputScroll.availableWidth
                        spacing: Math.round(9 * pane.unit)
                        Heading { text: pane.label("工程与声库") }
                        RowLayout {
                            Layout.fillWidth: true
                            PathLabel { text: pane.leaf(pane.fit.project) || pane.label("未选择 SVP 工程") }
                            UiButton {
                                theme: pane.theme
                                glyph: pane.hasSession ? "external" : "folder"
                                text: pane.label(pane.hasSession ? "在 SV 打开源工程" : "选择 SVP 工程")
                                enabled: !pane.busy || pane.hasSession
                                onClicked: pane.hasSession ? pane.controller.openFile("source") : projectFile.open()
                            }
                        }
                        UiButton {
                            objectName: "calibrationUseTuningTrack"
                            theme: pane.theme
                            quiet: true
                            text: pane.label("用当前调音轨")
                            visible: !pane.hasSession && !!pane.fit.canUseTrack
                            enabled: !pane.busy
                            onClicked: pane.controller.useCurrentTrack()
                        }
                        UiComboBox {
                            objectName: "calibrationTrack"
                            Layout.fillWidth: true
                            theme: pane.theme
                            textRole: "name"
                            model: pane.fit.tracks || []
                            currentIndex: count ? pane.fit.track : -1
                            enabled: !pane.busy && !pane.hasSession && count > 0
                            Accessible.name: pane.label("演唱轨")
                            onActivated: pane.controller.selectTrack(currentIndex)
                        }
                        Copy {
                            visible: !!pane.fit.project
                            text: pane.fit.bank ? pane.label("声库") + "  " + pane.fit.bank : pane.label("工程需要指定声库")
                            color: pane.fit.bank ? pane.theme.success : pane.theme.attention
                        }
                        Heading { text: pane.label("参考演唱") }
                        RowLayout {
                            Layout.fillWidth: true
                            PathLabel { text: pane.leaf(pane.fit.reference) || pane.label("未选择 WAV") }
                            UiButton {
                                theme: pane.theme
                                glyph: "folder"
                                text: pane.label("选择参考演唱 WAV")
                                visible: !pane.hasSession
                                enabled: !pane.busy
                                onClicked: referenceFile.open()
                            }
                        }
                        UiComboBox {
                            theme: pane.theme
                            Layout.fillWidth: true
                            model: [{value: "lead", label: pane.label("主唱")}, {value: "backing", label: pane.label("和声")},
                                    {value: "vocals", label: pane.label("人声")}]
                            currentIndex: pane.indexOf(model, pane.fit.voice)
                            enabled: !pane.busy && !pane.hasSession
                            Accessible.name: pane.label("参考演唱声部")
                            onActivated: pane.controller.selectVoice(currentValue)
                        }
                        Heading { text: pane.label(pane.hasSession ? "会话位置" : "保存位置") }
                        RowLayout {
                            Layout.fillWidth: true
                            PathLabel { text: pane.fit.folder || pane.label("未选择文件夹") }
                            UiButton {
                                theme: pane.theme
                                glyph: "folder"
                                text: pane.label(pane.hasSession ? "打开文件夹" : "选择校准保存位置")
                                enabled: pane.hasSession || !pane.busy
                                onClicked: pane.hasSession ? pane.controller.openFile("folder") : outputFolder.open()
                            }
                        }
                        UiButton {
                            theme: pane.theme
                            quiet: true
                            text: pane.label("更多拟合设置")
                            disclosure: pane.advanced ? 1 : 0
                            onClicked: pane.advanced = !pane.advanced
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: pane.advanced
                            Copy { text: pane.label("拟合强度") + "  " + Math.round(100 * (pane.fit.strength || 0)) + "%" }
                            WorkshopSlider {
                                theme: pane.theme
                                objectName: "calibrationStrength"
                                Layout.fillWidth: true
                                from: .01
                                to: 1
                                stepSize: .01
                                value: pane.fit.strength || .6
                                enabled: !pane.busy
                                Accessible.name: pane.label("拟合强度")
                                onMoved: pane.controller.setStrength(value)
                            }
                            UiComboBox {
                                objectName: "calibrationChannels"
                                Layout.fillWidth: true
                                theme: pane.theme
                                model: [{value: "standard", label: pane.label("普通（气声：无）")},
                                        {value: "sv-extra", label: pane.label("SV 追加气声")},
                                        {value: "sv-isolated", label: pane.label("SV 隔离气声")}]
                                currentIndex: pane.indexOf(model, pane.fit.channels)
                                enabled: !pane.busy
                                onActivated: pane.controller.selectChannels(currentValue)
                            }
                            UiButton {
                                objectName: "calibrationCustomRenderFolder"
                                theme: pane.theme
                                text: pane.label("更换导出目录…")
                                visible: !pane.hasSession
                                enabled: !pane.busy
                                onClicked: renderFolder.open()
                            }
                            PathLabel { visible: !pane.hasSession && !!pane.fit.renders; text: pane.fit.renders || "" }
                            UiButton {
                                objectName: "calibrationCopyBank"
                                theme: pane.theme
                                text: pane.label("从 SVP 复制声库设置…")
                                visible: !pane.hasSession
                                enabled: !pane.busy && pane.fit.canInspect && !!pane.fit.folder
                                onClicked: templateFile.open()
                            }
                            UiButton {
                                objectName: "calibrationRepairPitch"
                                theme: pane.theme
                                text: pane.label("另存并重建连续音高")
                                visible: !pane.hasSession
                                enabled: !pane.busy && pane.fit.canInspect && !!pane.fit.folder
                                onClicked: pane.controller.prepareInputs("", true)
                            }
                        }
                    }
                }
            }

            Rectangle {
                color: pane.theme.surface
                border.color: pane.theme.line
                radius: Math.round(9 * pane.unit)
                Layout.fillWidth: true
                Layout.fillHeight: true

                ScrollView {
                    id: detailsScroll
                    anchors.fill: parent
                    anchors.margins: Math.round(17 * pane.unit)
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: detailsScroll.availableWidth
                        spacing: Math.round(12 * pane.unit)

                        Heading { text: pane.label(pane.fit.result ? "拟合结果" : pane.hasSession ? "七轨试音" : "准备试音") }
                        Copy {
                            visible: !pane.hasSession
                            text: pane.label("选用已指定声库的 SVP 和从工程零点对齐的参考干声。生成七轨工程后，在 Synthesizer V 按轨导出 WAV。")
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: !pane.hasSession
                            spacing: Math.round(8 * pane.unit)
                            Repeater {
                                model: [{label: "工程与声库", value: pane.fit.bank || "", ready: !!pane.fit.bank},
                                        {label: "参考演唱", value: pane.leaf(pane.fit.reference), ready: !!pane.fit.reference},
                                        {label: "保存位置", value: pane.fit.folder || "", ready: !!pane.fit.folder}]
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Rectangle {
                                        Layout.preferredWidth: 7 * pane.unit
                                        Layout.preferredHeight: 7 * pane.unit
                                        radius: width / 2
                                        color: parent.modelData.ready ? pane.theme.success : pane.theme.attention
                                    }
                                    Text {
                                        Layout.preferredWidth: 91 * pane.unit
                                        color: pane.theme.text
                                        font.pixelSize: Math.round(13 * pane.unit)
                                        text: pane.label(parent.modelData.label)
                                    }
                                    PathLabel { text: parent.modelData.value || pane.label("待准备") }
                                }
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: !pane.hasSession && !!(pane.fit.review || {}).issues
                            readonly property var review: pane.fit.review || ({})
                            Copy {
                                text: (parent.review.notes || 0) + pane.label(" 个音符")
                                    + (parent.review.minimum_duration
                                       ? pane.label(" · 参考演唱需覆盖至 ") + parent.review.minimum_duration.toFixed(2) + pane.label(" 秒") : "")
                            }
                            Repeater {
                                model: (pane.fit.review || {}).issues || []
                                Copy {
                                    required property var modelData
                                    text: pane.label(modelData.message)
                                    color: modelData.level === "error" ? pane.theme.error : pane.theme.attention
                                }
                            }
                            Copy {
                                visible: parent.review.fit_ready === false
                                color: pane.theme.attention
                                text: pane.label("拟合需要 WORLD 分析器，可在“模型与组件”安装。可以先准备试音工程。")
                            }
                            Copy {
                                visible: !!parent.review.lyrics && parent.review.lyrics.status === "different_script"
                                text: pane.label("歌词文字体系不同，未做逐字比较。")
                            }
                            Repeater {
                                model: ((pane.fit.review || {}).lyrics || {}).candidates || []
                                Copy {
                                    required property var modelData
                                    text: modelData.start.toFixed(2) + "–" + modelData.end.toFixed(2)
                                        + pane.label(" 秒 · 参考「") + (modelData.expected || pane.label("无"))
                                        + pane.label("」／乐谱「") + (modelData.actual || pane.label("无")) + "」"
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: pane.hasSession
                            Flow {
                                Layout.fillWidth: true
                                spacing: 7 * pane.unit
                                UiButton {
                                    objectName: "calibrationOpenTrial"
                                    theme: pane.theme
                                    text: pane.label("打开七轨试音")
                                    onClicked: pane.controller.openFile("trial")
                                }
                                UiButton {
                                    theme: pane.theme
                                    glyph: "folder"
                                    text: pane.label("打开导出目录")
                                    onClicked: pane.controller.openFile("renders")
                                }
                                UiButton {
                                    objectName: "calibrationCopyRenderPath"
                                    theme: pane.theme
                                    quiet: true
                                    text: pane.label("复制导出目录")
                                    onClicked: pane.controller.copyRenderFolder()
                                }
                                UiButton {
                                    theme: pane.theme
                                    quiet: true
                                    text: pane.label("更换导出目录…")
                                    enabled: !pane.busy
                                    onClicked: renderFolder.open()
                                }
                            }
                            PathLabel { text: pane.leaf(pane.fit.renders); fullPath: pane.fit.renders || "" }
                            UiProgressBar {
                                objectName: "calibrationTrialProgress"
                                theme: pane.theme
                                Layout.fillWidth: true
                                from: 0
                                to: 7
                                value: pane.fit.stable || 0
                                indeterminate: pane.busy && pane.fit.phase === "fitting" && !pane.theme.reduceMotion
                                Accessible.name: pane.label("七轨试音")
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: Math.round(21 * pane.unit)
                                rowSpacing: Math.round(5 * pane.unit)
                                Repeater {
                                    model: pane.fit.trials || []
                                    RowLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        Rectangle {
                                            Layout.preferredWidth: 6 * pane.unit
                                            Layout.preferredHeight: 6 * pane.unit
                                            radius: width / 2
                                            color: parent.modelData.state === "validated" || parent.modelData.state === "stable"
                                                ? pane.theme.success : parent.modelData.state === "writing" ? pane.theme.attention : pane.theme.line
                                        }
                                        PathLabel { text: parent.modelData.name }
                                        Text {
                                            text: pane.label({missing: "未收到", writing: "写入中", stable: "已收到", validated: "已核验"}[parent.modelData.state] || "")
                                            color: pane.theme.muted
                                            font.pixelSize: Math.round(12 * pane.unit)
                                        }
                                    }
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: !!pane.fit.result
                            Copy {
                                color: pane.fit.measured ? pane.theme.success : pane.theme.attention
                                text: pane.label(pane.fit.measured ? "拟合工程已核验" : "未取得可用的声库拟合")
                            }
                            Copy {
                                visible: !!pane.fit.measured
                                text: ((pane.fit.writtenControls || []).join(" · ")) + "   |   "
                                    + pane.label("拟合强度") + " " + Math.round(100 * (pane.fit.fitStrength || 0)) + "%"
                            }
                            PathLabel { text: pane.leaf(pane.fit.result); fullPath: pane.fit.result || "" }
                            Flow {
                                Layout.fillWidth: true
                                spacing: Math.round(7 * pane.unit)
                                UiButton {
                                    objectName: "calibrationOpenFit"
                                    theme: pane.theme
                                    text: pane.label("打开拟合工程")
                                    enabled: !pane.busy
                                    onClicked: pane.controller.openFile("output")
                                }
                                UiButton {
                                    theme: pane.theme
                                    quiet: true
                                    text: pane.label("打开基准工程")
                                    enabled: !pane.busy && !!pane.fit.neutral
                                    onClicked: pane.controller.openFile("neutral")
                                }
                                UiButton {
                                    theme: pane.theme
                                    quiet: true
                                    text: pane.label("查看核验报告")
                                    enabled: !pane.busy
                                    onClicked: pane.controller.openFile("report_path")
                                }
                                UiButton {
                                    objectName: "calibrationRegisterTask"
                                    theme: pane.theme
                                    quiet: true
                                    text: pane.label("登记到任务")
                                    visible: !!pane.fit.canRegisterTask
                                    enabled: !pane.busy
                                    onClicked: pane.controller.registerResult()
                                }
                            }
                        }
                    }
                }
            }
        }

        Copy { visible: !!text; text: pane.fit.message || ""; color: pane.theme.text }
        Copy { visible: !!text; text: pane.fit.hint || ""; color: pane.theme.attention }

        RowLayout {
            Layout.fillWidth: true
            UiButton {
                objectName: "calibrationNewSession"
                theme: pane.theme
                quiet: true
                text: pane.label("新建校准")
                visible: pane.hasSession
                enabled: !pane.busy
                onClicked: pane.controller.newSession()
            }
            UiButton {
                theme: pane.theme
                quiet: true
                text: pane.label("模型与组件")
                visible: (pane.fit.review || {}).fit_ready === false || (pane.fit.hint || "").includes("WORLD")
                onClicked: pane.componentsRequested()
            }
            TextField {
                id: presetName
                objectName: "calibrationPresetName"
                Layout.fillWidth: true
                placeholderText: pane.label("校准预设名称")
                placeholderTextColor: pane.theme.muted
                text: pane.fit.presetName || ""
                maximumLength: 80
                visible: !!pane.fit.measured
                enabled: !pane.busy
                font.pixelSize: Math.round(13 * pane.unit)
                background: Rectangle {
                    radius: Math.round(7 * pane.unit)
                    color: pane.theme.surface
                    border.color: presetName.activeFocus ? pane.theme.accent : pane.theme.line
                }
                onTextEdited: pane.controller.setPresetName(text)
            }
            Item { Layout.fillWidth: true; visible: !pane.fit.measured }
            UiButton {
                objectName: "calibrationStop"
                theme: pane.theme
                text: pane.label("停止")
                visible: pane.busy
                onClicked: pane.controller.cancel()
            }
            UiButton {
                objectName: "calibrationInspect"
                theme: pane.theme
                text: pane.label("检查输入")
                visible: !pane.hasSession
                enabled: !pane.busy && !!pane.fit.canInspect
                onClicked: pane.controller.checkInputs(false)
            }
            UiButton {
                objectName: "calibrationPrepare"
                theme: pane.theme
                primary: true
                text: pane.label("准备七轨试音")
                visible: !pane.hasSession
                enabled: !pane.busy && pane.fit.canInspect && !!pane.fit.folder
                onClicked: pane.controller.checkInputs(true)
            }
            UiButton {
                objectName: "calibrationFit"
                theme: pane.theme
                primary: !pane.fit.measured
                text: pane.label(pane.fit.result ? "再次拟合" : "等待导出并拟合")
                visible: pane.hasSession
                enabled: !pane.busy
                onClicked: pane.controller.fit()
            }
            UiButton {
                objectName: "calibrationBindResult"
                theme: pane.theme
                quiet: true
                text: pane.label("绑定到当前调音轨")
                visible: !!pane.fit.canBind
                enabled: !pane.busy
                onClicked: {
                    pane.controller.bindResult()
                    pane.bindingRequested()
                }
            }
            UiButton {
                objectName: "calibrationSavePreset"
                theme: pane.theme
                primary: true
                text: pane.label("保存校准预设")
                visible: !!pane.fit.measured
                enabled: !pane.busy && (pane.fit.presetName || "").trim().length > 0
                onClicked: pane.controller.savePreset()
            }
            UiButton {
                theme: pane.theme
                quiet: true
                text: pane.label("查看预设")
                visible: !!(pane.fit.savedPreset || {}).id
                onClicked: pane.presetsRequested()
            }
        }
    }

    FileDialog {
        id: projectFile
        title: pane.label("选择指定了声库的 SVP 工程")
        nameFilters: ["SVP (*.svp)"]
        onAccepted: pane.controller.chooseProject(selectedFile)
    }
    FileDialog {
        id: templateFile
        title: pane.label("选择含目标声库的 SVP")
        nameFilters: ["SVP (*.svp)"]
        onAccepted: pane.controller.prepareInputs(selectedFile, false)
    }
    FileDialog {
        id: referenceFile
        title: pane.label("选择参考演唱 WAV")
        nameFilters: ["WAV (*.wav)"]
        onAccepted: pane.controller.chooseReference(selectedFile)
    }
    FileDialog {
        id: sessionFile
        title: pane.label("选择校准目录中的 session.json")
        nameFilters: ["session.json (session.json)"]
        onAccepted: pane.controller.resumeSession(selectedFile)
    }
    FolderDialog {
        id: outputFolder
        title: pane.label("选择校准保存位置")
        onAccepted: pane.controller.chooseFolder(selectedFolder)
    }
    FolderDialog {
        id: renderFolder
        title: pane.label("选择七份试音的导出目录")
        onAccepted: pane.controller.chooseRenders(selectedFolder)
    }
    FolderDialog {
        id: sessionFolder
        title: pane.label("查找本地校准记录")
        onAccepted: pane.controller.findSessions(selectedFolder)
    }
}
