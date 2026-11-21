import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "tuningStyleAuthorDialog"
    required property QtObject theme
    required property QtObject controller
    readonly property real uiScale: theme.scale || 1
    readonly property var author: controller.styleAuthor || ({})
    readonly property var status: controller.view || ({})
    readonly property var reference: author.reference || ({})
    readonly property var sourceTracks: reference.tracks || []
    readonly property var points: author.points || []
    readonly property var actions: author.gestures || []
    readonly property var report: author.report || ({})
    readonly property var excluded: report.excluded || ({})
    readonly property int excludedCount: Object.values(excluded).reduce((a, b) => a + b, 0)
    readonly property bool busy: !!status.busy
    readonly property string inputKey: JSON.stringify([reference.sha256, sourceTrack.currentIndex, startTime.text,
        endTime.text, String(audioUrl), offsetField.text, measurePitch.checked])
    readonly property bool ready: !!report.source && analysisKey === inputKey
    readonly property bool validRecipe: ready && nameField.text.trim().length > 0
        && ((gainCheck.checked && points.length > 0) || (pitchCheck.checked && actions.length > 0))
    property url audioUrl: ""
    property string audioName: ""
    property string sourceKey: ""
    property string analysisKey: ""
    property int selectedPoint: 0
    property bool trialCurrent: false

    function label(value) { return controller.text(value) }
    function fileName(value) { return String(value || "").split(/[\\/]/).pop() }
    function showRange(row, selectedRange) {
        if (!row)
            return
        startTime.text = Number(selectedRange ? selectedRange[0] : row.start).toFixed(3)
        endTime.text = Number(selectedRange ? selectedRange[1] : row.end).toFixed(3)
    }
    function syncReference() {
        const identity = String(reference.sha256 || "") + String(reference.path || "")
        if (!sourceTrack || sourceTracks.length === 0 || identity === sourceKey)
            return
        sourceKey = identity
        const match = sourceTracks.findIndex(row => row.index === reference.selected)
        sourceTrack.currentIndex = Math.max(0, match)
        showRange(sourceTracks[sourceTrack.currentIndex], match >= 0 ? reference.range : null)
    }
    function pitchCents(kind) {
        const row = actions.find(item => item.kind === kind)
        return row ? Number(row.cents) : 0
    }
    function invalidateTrial() { trialCurrent = false }

    component PanelLabel: Text {
        required property QtObject theme
        color: theme.text
        font.pixelSize: Math.round(13 * (theme.scale || 1))
        textFormat: Text.PlainText
    }
    component HelperText: Text {
        required property QtObject theme
        color: theme.muted
        font.pixelSize: Math.round(12 * (theme.scale || 1))
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component CheckOption: CheckBox {
        required property QtObject theme
        font.pixelSize: Math.round(13 * (theme.scale || 1))
        palette.windowText: theme.text
    }

    title: label("从参考制作风格")
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, Math.round(780 * uiScale))
    height: Math.min(parent.height - 54, Math.round(735 * uiScale))
    closePolicy: Popup.CloseOnEscape
    onOpened: {
        controller.openStyleAuthor()
        syncReference()
    }
    onReferenceChanged: syncReference()
    onPointsChanged: {
        if (selectedPoint >= points.length)
            selectedPoint = 0
        if (chart)
            chart.requestPaint()
    }

    FileDialog {
        id: referenceDialog
        title: dialog.label("选择参考工程")
        nameFilters: ["VWA supported projects (*.svp *.ustx *.vpr *.vsqx *.acep *.ufdata)"]
        onAccepted: {
            dialog.invalidateTrial()
            dialog.controller.loadStyleReference(selectedFile)
        }
    }
    FileDialog {
        id: audioDialog
        title: dialog.label("选择对齐干声")
        nameFilters: ["WAV (*.wav)"]
        onAccepted: {
            dialog.audioUrl = selectedFile
            dialog.audioName = dialog.fileName(decodeURIComponent(String(selectedFile)))
            dialog.invalidateTrial()
        }
    }

    contentItem: ColumnLayout {
        spacing: Math.round(8 * dialog.uiScale)
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: scroll.availableWidth
                spacing: Math.round(9 * dialog.uiScale)
                RowLayout {
                    Layout.fillWidth: true
                    UiButton {
                        objectName: "styleAuthorReferenceFile"
                        theme: dialog.theme
                        text: dialog.label("选择参考工程")
                        enabled: !dialog.busy
                        onClicked: referenceDialog.open()
                    }
                    HelperText {
                        Layout.fillWidth: true
                        theme: dialog.theme
                        text: dialog.fileName(dialog.reference.path)
                    }
                    UiComboBox {
                        id: sourceTrack
                        objectName: "styleAuthorTrack"
                        Layout.preferredWidth: Math.round(205 * dialog.uiScale)
                        theme: dialog.theme
                        model: dialog.sourceTracks
                        textRole: "name"
                        enabled: !dialog.busy && dialog.sourceTracks.length > 0
                        onActivated: dialog.showRange(dialog.sourceTracks[currentIndex], null)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    PanelLabel { theme: dialog.theme; text: dialog.label("参考唱段（秒）") }
                    TextField {
                        id: startTime
                        objectName: "styleAuthorFrom"
                        Layout.preferredWidth: Math.round(95 * dialog.uiScale)
                        enabled: !dialog.busy
                        selectByMouse: true
                        Accessible.name: dialog.label("参考开始时间")
                        validator: DoubleValidator { decimals: 3; locale: "C"; notation: DoubleValidator.StandardNotation }
                    }
                    TextField {
                        id: endTime
                        objectName: "styleAuthorTo"
                        Layout.preferredWidth: Math.round(95 * dialog.uiScale)
                        enabled: !dialog.busy
                        selectByMouse: true
                        Accessible.name: dialog.label("参考结束时间")
                        validator: DoubleValidator { decimals: 3; locale: "C"; notation: DoubleValidator.StandardNotation }
                    }
                    Item { Layout.fillWidth: true }
                    UiButton {
                        objectName: "styleAuthorAnalyze"
                        theme: dialog.theme
                        primary: true
                        text: dialog.label("提取风格")
                        enabled: !dialog.busy && sourceTrack.currentIndex >= 0 && dialog.sourceTracks.length > 0
                            && startTime.acceptableInput && endTime.acceptableInput
                            && Number(endTime.text) > Number(startTime.text)
                            && (!dialog.audioName || offsetField.acceptableInput)
                        onClicked: {
                            dialog.invalidateTrial()
                            dialog.analysisKey = dialog.inputKey
                            dialog.controller.analyzeStyleReference(dialog.sourceTracks[sourceTrack.currentIndex].index,
                                Number(startTime.text), Number(endTime.text), dialog.audioUrl,
                                Number(offsetField.text), measurePitch.checked)
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    UiButton {
                        theme: dialog.theme
                        text: dialog.label("选择对齐干声")
                        enabled: !dialog.busy
                        onClicked: audioDialog.open()
                    }
                    HelperText {
                        Layout.fillWidth: true
                        theme: dialog.theme
                        text: dialog.audioName || dialog.label("未选干声时读取参考工程中已有的曲线。")
                    }
                    UiButton {
                        visible: !!dialog.audioName
                        enabled: !dialog.busy
                        theme: dialog.theme
                        text: dialog.label("清空")
                        onClicked: {
                            dialog.audioUrl = ""
                            dialog.audioName = ""
                            dialog.invalidateTrial()
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: !!dialog.audioName
                    PanelLabel { theme: dialog.theme; text: dialog.label("干声 0 秒对应的工程时间") }
                    TextField {
                        id: offsetField
                        objectName: "styleAuthorAudioZero"
                        Layout.preferredWidth: Math.round(95 * dialog.uiScale)
                        text: "0"
                        enabled: !dialog.busy
                        selectByMouse: true
                        Accessible.name: dialog.label("干声 0 秒对应的工程时间")
                        validator: DoubleValidator { decimals: 3; locale: "C"; notation: DoubleValidator.StandardNotation }
                    }
                    Item { Layout.fillWidth: true }
                }
                CheckOption {
                    id: measurePitch
                    objectName: "styleAuthorMeasurePitch"
                    theme: dialog.theme
                    text: dialog.label("一并提取起音与收尾音高")
                    enabled: !dialog.busy
                }
                HelperText {
                    Layout.fillWidth: true
                    theme: dialog.theme
                    visible: dialog.ready
                    text: dialog.label("分析 {0} 个音符 · 力度 {1} 个 · 音高 {2} 个 · 排除 {3} 个")
                        .replace("{0}", dialog.report.notes).replace("{1}", dialog.report.loudness_notes)
                        .replace("{2}", dialog.report.pitch_notes)
                        .replace("{3}", dialog.excludedCount)
                }
                HelperText {
                    Layout.fillWidth: true
                    theme: dialog.theme
                    visible: dialog.ready && dialog.excludedCount > 0
                    text: dialog.label("排除明细 · 重叠 {0} · 过短 {1} · 非演唱 {2} · 被选区切断 {3}")
                        .replace("{0}", dialog.excluded.overlap || 0).replace("{1}", dialog.excluded.short || 0)
                        .replace("{2}", dialog.excluded.non_vocal || 0).replace("{3}", dialog.excluded.cut || 0)
                }
                HelperText {
                    Layout.fillWidth: true
                    theme: dialog.theme
                    visible: dialog.ready && !!dialog.report.alignment
                    text: dialog.report.alignment
                        ? dialog.label("音符内干声活跃率 {0}% · {1} 个可用 · {2} 个低重合")
                            .replace("{0}", Math.round(100 * dialog.report.alignment.in_note_signal_fraction))
                            .replace("{1}", dialog.report.alignment.accepted_notes)
                            .replace("{2}", dialog.report.alignment.excluded_notes) : ""
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: dialog.ready
                    CheckOption {
                        id: gainCheck
                        theme: dialog.theme
                        text: dialog.label("保留力度")
                        checked: true
                        enabled: dialog.points.length > 0 && !dialog.busy
                        onToggled: dialog.invalidateTrial()
                    }
                    CheckOption {
                        id: pitchCheck
                        theme: dialog.theme
                        text: dialog.label("保留音高动作")
                        checked: true
                        enabled: !dialog.busy
                        onToggled: dialog.invalidateTrial()
                    }
                    Item { Layout.fillWidth: true }
                    HelperText {
                        visible: dialog.points.length > 0
                        theme: dialog.theme
                        text: dialog.label("拖动控制点调整力度")
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(118 * dialog.uiScale)
                    visible: dialog.ready && dialog.points.length > 0
                    radius: 6
                    color: dialog.theme.surface
                    border.color: dialog.theme.line
                    Canvas {
                        id: chart
                        objectName: "styleAuthorEnvelope"
                        anchors.fill: parent
                        anchors.margins: 9
                        onPaint: {
                            const ctx = getContext("2d")
                            ctx.clearRect(0, 0, width, height)
                            ctx.strokeStyle = dialog.theme.line
                            ctx.lineWidth = 1
                            ctx.beginPath()
                            ctx.moveTo(0, height / 2)
                            ctx.lineTo(width, height / 2)
                            ctx.stroke()
                            ctx.lineWidth = 2
                            ctx.strokeStyle = dialog.theme.accent
                            ctx.beginPath()
                            dialog.points.forEach((p, i) => {
                                const x = 6 + p[0] * (width - 12)
                                const y = 5 + (12 - p[1]) / 24 * (height - 10)
                                if (i === 0) ctx.moveTo(x, y)
                                else ctx.lineTo(x, y)
                            })
                            ctx.stroke()
                            dialog.points.forEach((p, i) => {
                                ctx.fillStyle = i === dialog.selectedPoint ? dialog.theme.accent : dialog.theme.text
                                ctx.beginPath()
                                ctx.arc(6 + p[0] * (width - 12), 5 + (12 - p[1]) / 24 * (height - 10),
                                        i === dialog.selectedPoint ? 5 : 3, 0, 2 * Math.PI)
                                ctx.fill()
                            })
                        }
                        MouseArea {
                            anchors.fill: parent
                            enabled: dialog.points.length > 0 && gainCheck.checked && !dialog.busy
                            function update(y) {
                                dialog.controller.setStyleAuthorPoint(dialog.selectedPoint, 12 - 24 * (y - 5) / (height - 10))
                                dialog.invalidateTrial()
                            }
                            onPressed: mouse => {
                                const phase = Math.max(0, Math.min(1, (mouse.x - 6) / (width - 12)))
                                dialog.selectedPoint = Math.round(phase * (dialog.points.length - 1))
                                update(mouse.y)
                            }
                            onPositionChanged: mouse => { if (pressed) update(mouse.y) }
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: dialog.ready && dialog.points.length > 0
                    UiComboBox {
                        id: pointPick
                        theme: dialog.theme
                        model: dialog.points.map(point => (Number(point[0]) * 100).toFixed(1) + "%")
                        currentIndex: dialog.selectedPoint
                        Accessible.name: dialog.label("力度相位")
                        onActivated: {
                            dialog.selectedPoint = currentIndex
                            chart.requestPaint()
                        }
                    }
                    Slider {
                        Layout.fillWidth: true
                        from: -12
                        to: 12
                        stepSize: .1
                        value: dialog.points.length > dialog.selectedPoint ? dialog.points[dialog.selectedPoint][1] : 0
                        enabled: gainCheck.checked && !dialog.busy
                        Accessible.name: dialog.label("力度")
                        onMoved: {
                            dialog.controller.setStyleAuthorPoint(dialog.selectedPoint, value)
                            dialog.invalidateTrial()
                        }
                    }
                    PanelLabel {
                        theme: dialog.theme
                        text: (dialog.points.length > dialog.selectedPoint
                               ? Number(dialog.points[dialog.selectedPoint][1]).toFixed(1) : "0.0") + " dB"
                    }
                }
                Repeater {
                    model: dialog.ready ? [{kind: "onset", label: "起音音高"}, {kind: "release", label: "收尾音高"}] : []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        PanelLabel { theme: dialog.theme; text: dialog.label(modelData.label) }
                        Slider {
                            Layout.fillWidth: true
                            from: -120
                            to: 120
                            stepSize: 1
                            value: dialog.pitchCents(modelData.kind)
                            enabled: pitchCheck.checked && !dialog.busy
                            Accessible.name: dialog.label(modelData.label)
                            onMoved: {
                                dialog.controller.setStyleAuthorPitch(modelData.kind, value)
                                dialog.invalidateTrial()
                            }
                        }
                        PanelLabel { theme: dialog.theme; text: dialog.pitchCents(modelData.kind).toFixed(1) + " ¢" }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: dialog.ready
            TextField {
                id: nameField
                objectName: "styleAuthorName"
                Layout.fillWidth: true
                placeholderText: dialog.label("风格名称")
                maximumLength: 80
                selectByMouse: true
                enabled: !dialog.busy
                onTextChanged: dialog.invalidateTrial()
            }
            PanelLabel { theme: dialog.theme; text: dialog.label("风格强度") }
            Slider {
                id: strengthSlider
                Layout.preferredWidth: Math.round(104 * dialog.uiScale)
                from: 0
                to: 1
                value: .6
                stepSize: .01
                enabled: !dialog.busy
                Accessible.name: dialog.label("风格强度")
                onMoved: dialog.invalidateTrial()
            }
            PanelLabel { theme: dialog.theme; text: Math.round(strengthSlider.value * 100) + "%" }
        }
        HelperText {
            Layout.fillWidth: true
            theme: dialog.theme
            visible: dialog.ready
            text: dialog.author.targetReady
                ? dialog.label("试套目标 {0} · {1}–{2} 秒").replace("{0}", dialog.author.targetTrack)
                    .replace("{1}", Number(dialog.author.targetStart).toFixed(2))
                    .replace("{2}", Number(dialog.author.targetEnd).toFixed(2))
                : dialog.label("可先关闭制作窗口，在调音的单轨视图选好目标唱段，再打开试套。")
        }
        HelperText {
            Layout.fillWidth: true
            theme: dialog.theme
            text: dialog.trialCurrent && dialog.ready && dialog.author.trial
                ? dialog.author.trial
                : !!dialog.report.source && !dialog.ready
                    ? dialog.label("参考输入已变化，请重新提取。") : String(dialog.status.message || "")
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            UiButton {
                theme: dialog.theme
                text: dialog.busy ? dialog.label("取消") : dialog.label("关闭")
                onClicked: dialog.busy ? dialog.controller.cancel() : dialog.close()
            }
            UiButton {
                objectName: "styleAuthorTrial"
                theme: dialog.theme
                text: dialog.label("试套当前选区")
                enabled: dialog.validRecipe && dialog.author.targetReady && !dialog.busy
                onClicked: {
                    dialog.trialCurrent = true
                    dialog.controller.trialStyleAuthor(nameField.text, strengthSlider.value, gainCheck.checked, pitchCheck.checked)
                }
            }
            UiButton {
                objectName: "styleAuthorApply"
                theme: dialog.theme
                text: dialog.label("应用到时间选区")
                enabled: dialog.validRecipe && dialog.trialCurrent && !!dialog.author.trial && !dialog.busy
                onClicked: {
                    if (dialog.controller.applyStyleAuthor(nameField.text, strengthSlider.value, gainCheck.checked, pitchCheck.checked))
                        dialog.close()
                }
            }
            UiButton {
                objectName: "styleAuthorSave"
                theme: dialog.theme
                text: dialog.label("保存配方")
                primary: true
                enabled: dialog.validRecipe && !dialog.busy
                onClicked: dialog.controller.saveStyleAuthor(nameField.text, strengthSlider.value, gainCheck.checked, pitchCheck.checked)
            }
        }
    }
}
