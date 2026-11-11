import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "voiceCalibrationDialog"
    required property QtObject theme
    required property QtObject controller
    signal bindingRequested()

    readonly property var view: controller.view
    readonly property bool hasSession: !!view.session
    readonly property bool busy: !!view.busy
    readonly property var voices: [
        {value: "lead", label: "主唱"}, {value: "backing", label: "和声"}, {value: "vocals", label: "人声"}
    ]
    readonly property var channels: [
        {value: "standard", label: "普通（气声：无）"},
        {value: "sv-extra", label: "SV 追加气声"},
        {value: "sv-isolated", label: "SV 隔离气声"}
    ]

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    title: "声库校准"
    width: Math.min(parent.width - 40, Math.round(750 * theme.scale))
    height: Math.min(parent.height - 48, Math.round(650 * theme.scale))
    padding: Math.round(16 * theme.scale)
    closePolicy: Popup.CloseOnEscape
    background: Rectangle {
        color: dialog.theme.surface
        radius: 10 * dialog.theme.scale
        border.color: dialog.theme.line
    }

    function indexOf(rows, value) {
        return Math.max(0, rows.findIndex(row => row.value === value))
    }

    component Copy: Text {
        Layout.fillWidth: true
        color: dialog.theme.muted
        font.pixelSize: Math.round(13 * dialog.theme.scale)
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component PathLabel: Text {
        Layout.fillWidth: true
        color: dialog.theme.text
        font.pixelSize: Math.round(13 * dialog.theme.scale)
        elide: Text.ElideMiddle
        textFormat: Text.PlainText
        HoverHandler { id: pathHover }
        ToolTip.visible: pathHover.hovered && truncated
        ToolTip.text: text
    }

    contentItem: ColumnLayout {
        spacing: 9 * dialog.theme.scale
        RowLayout {
            Layout.fillWidth: true
            Copy {
                text: dialog.hasSession ? "2 · 分轨导出与拟合" : "1 · 选择工程和参考演唱"
                color: dialog.theme.accent
                font.weight: Font.DemiBold
            }
            UiButton {
                objectName: "calibrationResume"
                theme: dialog.theme
                quiet: true
                text: "继续已有会话…"
                enabled: !dialog.busy
                onClicked: sessionFile.open()
            }
            UiButton {
                objectName: "calibrationNewSession"
                visible: dialog.hasSession
                theme: dialog.theme
                quiet: true
                text: "新建会话"
                enabled: !dialog.busy
                onClicked: dialog.controller.newSession()
            }
        }
        ScrollView {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: body.availableWidth
                spacing: 10 * dialog.theme.scale

                Copy {
                    text: dialog.hasSession
                        ? "在 Synthesizer V 打开七轨试音工程。用同一声库从零秒分轨导出，保留轨名，关闭混合导出。拟合只处理所选演唱轨。"
                        : "工程需要已指定声库，音符在主音符组。参考演唱应从工程零秒开始，并覆盖所选轨的全部音符。"
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 3
                    rowSpacing: 6 * dialog.theme.scale
                    columnSpacing: 10 * dialog.theme.scale
                    Copy { text: "SVP 工程"; Layout.fillWidth: false }
                    PathLabel { text: dialog.view.project || "未选择" }
                    RowLayout {
                        UiButton {
                            objectName: "calibrationUseTuningTrack"
                            theme: dialog.theme
                            text: "用当前调音轨"
                            visible: !dialog.hasSession && dialog.view.canUseTrack
                            enabled: !dialog.busy
                            onClicked: dialog.controller.useCurrentTrack()
                        }
                        UiButton {
                            theme: dialog.theme
                            text: "选择…"
                            visible: !dialog.hasSession
                            enabled: !dialog.busy
                            onClicked: projectFile.open()
                        }
                    }
                    Copy { text: "演唱轨"; Layout.fillWidth: false }
                    UiComboBox {
                        objectName: "calibrationTrack"
                        theme: dialog.theme
                        Layout.fillWidth: true
                        textRole: "name"
                        valueRole: "index"
                        model: dialog.view.tracks
                        currentIndex: model.length ? dialog.view.track : -1
                        enabled: !dialog.busy && !dialog.hasSession && model.length > 0
                        onActivated: index => dialog.controller.selectTrack(index)
                    }
                    UiComboBox {
                        theme: dialog.theme
                        model: dialog.voices
                        currentIndex: dialog.indexOf(model, dialog.view.voice)
                        enabled: !dialog.busy && !dialog.hasSession
                        onActivated: dialog.controller.selectVoice(currentValue)
                    }
                    Copy { text: "参考演唱"; Layout.fillWidth: false }
                    PathLabel { text: dialog.view.reference || "未选择 WAV" }
                    RowLayout {
                        UiButton {
                            theme: dialog.theme
                            text: "选择…"
                            visible: !dialog.hasSession
                            enabled: !dialog.busy
                            onClicked: referenceFile.open()
                        }
                    }
                    Copy { text: dialog.hasSession ? "会话位置" : "保存位置"; Layout.fillWidth: false }
                    PathLabel { text: dialog.view.folder || "未选择文件夹" }
                    UiButton {
                        theme: dialog.theme
                        text: dialog.hasSession ? "打开文件夹" : "选择…"
                        enabled: dialog.hasSession || !dialog.busy
                        onClicked: dialog.hasSession ? dialog.controller.openFile("folder") : outputFolder.open()
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Copy { text: "拟合强度"; Layout.fillWidth: false }
                    Slider {
                        objectName: "calibrationStrength"
                        Layout.preferredWidth: Math.round(170 * dialog.theme.scale)
                        from: 0.01
                        to: 1
                        stepSize: 0.01
                        value: dialog.view.strength
                        enabled: !dialog.busy
                        Accessible.name: "拟合强度"
                        onMoved: dialog.controller.setStrength(value)
                    }
                    Copy {
                        text: Math.round(dialog.view.strength * 100) + "%"
                        Layout.fillWidth: false
                    }
                    Item { Layout.fillWidth: true }
                    UiComboBox {
                        objectName: "calibrationChannels"
                        visible: dialog.hasSession
                        theme: dialog.theme
                        model: dialog.channels
                        currentIndex: dialog.indexOf(model, dialog.view.channels)
                        enabled: !dialog.busy
                        onActivated: dialog.controller.selectChannels(currentValue)
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: !dialog.hasSession && !!dialog.view.review.issues
                    spacing: 5
                    Copy {
                        text: (dialog.view.review.bank ? "声库：" + dialog.view.review.bank + " · " : "")
                            + (dialog.view.review.notes || 0) + " 个音符"
                    }
                    Repeater {
                        model: dialog.view.review.issues || []
                        delegate: Copy {
                            required property var modelData
                            text: (modelData.level === "error" ? "需要处理 · " : "复核 · ") + modelData.message
                            color: modelData.level === "error" ? dialog.theme.error : dialog.theme.attention
                        }
                    }
                    Copy {
                        visible: dialog.view.review.fit_ready === false
                        color: dialog.theme.attention
                        text: "拟合需要 WORLD 分析器，可在“模型与组件”安装。可以先准备试音工程。"
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: dialog.hasSession
                    spacing: 6 * dialog.theme.scale
                    RowLayout {
                        Layout.fillWidth: true
                        UiButton {
                            objectName: "calibrationOpenTrial"
                            theme: dialog.theme
                            text: "打开七轨试音"
                            onClicked: dialog.controller.openFile("trial")
                        }
                        UiButton {
                            theme: dialog.theme
                            text: "打开导出目录"
                            onClicked: dialog.controller.openFile("renders")
                        }
                        UiButton {
                            theme: dialog.theme
                            text: "更换导出目录…"
                            enabled: !dialog.busy
                            onClicked: renderFolder.open()
                        }
                    }
                    PathLabel { text: dialog.view.renders }
                    ProgressBar {
                        objectName: "calibrationTrialProgress"
                        Layout.fillWidth: true
                        from: 0
                        to: 7
                        value: dialog.view.stable
                        indeterminate: dialog.busy && dialog.view.phase === "fitting"
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6 * dialog.theme.scale
                        Repeater {
                            model: dialog.view.trials
                            delegate: Rectangle {
                                required property var modelData
                                implicitWidth: trialLabel.implicitWidth + 14 * dialog.theme.scale
                                implicitHeight: trialLabel.implicitHeight + 8 * dialog.theme.scale
                                color: dialog.theme.raised
                                border.color: dialog.theme.line
                                radius: 5 * dialog.theme.scale
                                Text {
                                    id: trialLabel
                                    anchors.centerIn: parent
                                    text: modelData.name + " · "
                                        + ({missing:"未收到", writing:"写入中", stable:"已收到", validated:"已核验"}[modelData.state] || "")
                                    color: modelData.state === "validated" || modelData.state === "stable"
                                        ? dialog.theme.success : dialog.theme.muted
                                    font.pixelSize: 12 * dialog.theme.scale
                                }
                            }
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: !!dialog.view.result
                    spacing: 6 * dialog.theme.scale
                    Copy {
                        color: dialog.view.measured ? dialog.theme.success : dialog.theme.attention
                        text: dialog.view.measured ? "拟合工程已核验" : "未取得可用的声库拟合"
                    }
                    PathLabel { text: dialog.view.result }
                    RowLayout {
                        Layout.fillWidth: true
                        UiButton {
                            theme: dialog.theme
                            text: "打开校准工程"
                            enabled: !dialog.busy
                            onClicked: dialog.controller.openFile("output")
                        }
                        UiButton {
                            theme: dialog.theme
                            text: "打开基准工程"
                            enabled: !dialog.busy && !!dialog.view.neutral
                            onClicked: dialog.controller.openFile("neutral")
                        }
                        UiButton {
                            theme: dialog.theme
                            text: "查看回执"
                            enabled: !dialog.busy
                            onClicked: dialog.controller.openFile("report_path")
                        }
                        UiButton {
                            objectName: "calibrationBindResult"
                            theme: dialog.theme
                            text: "绑定到当前调音轨"
                            enabled: !dialog.busy && dialog.view.canBind
                            onClicked: {
                                dialog.controller.bindResult()
                                dialog.close()
                                dialog.bindingRequested()
                            }
                        }
                    }
                    Copy {
                        visible: dialog.view.measured && !dialog.view.canBind
                        text: "要绑定此配置，请在调音中选中同一工程的校准轨，使用单轨精修，并先解除已有预设绑定。"
                    }
                }
            }
        }
        Copy {
            objectName: "calibrationMessage"
            visible: !!dialog.view.message
            color: dialog.theme.text
            text: dialog.view.message
        }
        Copy {
            visible: !!dialog.view.hint
            color: dialog.theme.attention
            text: dialog.view.hint
        }
        RowLayout {
            Layout.fillWidth: true
            UiButton {
                objectName: "calibrationClose"
                theme: dialog.theme
                text: dialog.busy ? "转到后台" : "关闭"
                onClicked: dialog.close()
            }
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "calibrationStop"
                theme: dialog.theme
                visible: dialog.busy
                text: "停止"
                onClicked: dialog.controller.cancel()
            }
            UiButton {
                objectName: "calibrationInspect"
                visible: !dialog.hasSession
                theme: dialog.theme
                text: "检查输入"
                enabled: !dialog.busy && dialog.view.canInspect
                onClicked: dialog.controller.checkInputs(false)
            }
            UiButton {
                objectName: "calibrationPrepare"
                visible: !dialog.hasSession
                theme: dialog.theme
                text: "准备七轨试音"
                primary: true
                enabled: !dialog.busy && dialog.view.canInspect && !!dialog.view.folder
                onClicked: dialog.controller.checkInputs(true)
            }
            UiButton {
                objectName: "calibrationFit"
                visible: dialog.hasSession
                theme: dialog.theme
                text: dialog.view.result ? "再次拟合" : "等待导出并拟合"
                primary: true
                enabled: !dialog.busy
                onClicked: dialog.controller.fit()
            }
        }
    }

    FileDialog {
        id: projectFile
        title: "选择指定了声库的 SVP 工程"
        nameFilters: ["Synthesizer V 工程 (*.svp)"]
        onAccepted: dialog.controller.chooseProject(selectedFile)
    }
    FileDialog {
        id: referenceFile
        title: "选择参考演唱 WAV"
        nameFilters: ["WAV 音频 (*.wav)"]
        onAccepted: dialog.controller.chooseReference(selectedFile)
    }
    FileDialog {
        id: sessionFile
        title: "选择校准目录中的 session.json"
        nameFilters: ["校准会话 (session.json)"]
        onAccepted: dialog.controller.resumeSession(selectedFile)
    }
    FolderDialog {
        id: outputFolder
        title: "选择校准保存位置"
        onAccepted: dialog.controller.chooseFolder(selectedFolder)
    }
    FolderDialog {
        id: renderFolder
        title: "选择七份试音的导出目录"
        onAccepted: dialog.controller.chooseRenders(selectedFolder)
    }
}
