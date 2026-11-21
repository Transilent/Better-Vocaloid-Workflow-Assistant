import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: pane
    objectName: "workshopStylePane"
    required property QtObject theme
    required property QtObject controller
    signal targetRequested()
    readonly property real unit: theme.scale
    readonly property var authorView: controller ? controller.view : ({})
    readonly property var fields: authorView.fields || ({})
    readonly property var reference: authorView.reference || ({})
    readonly property var evidence: authorView.report || ({})
    readonly property var trackRows: reference.tracks || []
    readonly property var points: authorView.points || []
    readonly property var target: authorView.target || ({})
    readonly property bool audioMode: fields.mode === "audio"
    readonly property int excluded: Object.values(evidence.excluded || {}).reduce((sum, value) => sum + value, 0)

    function label(text) { return typeof quickTranslation !== "undefined" && quickTranslation ? quickTranslation.text(text) : text }
    function leaf(path) { return (path || "").split(/[\\/]/).pop() }
    function cents(kind) {
        const row = (authorView.gestures || []).find(value => value.kind === kind)
        return row ? row.cents : 0
    }

    component Heading: Text {
        font.pixelSize: Math.round(15 * pane.unit)
        font.weight: Font.DemiBold
        color: pane.theme.text
        textFormat: Text.PlainText
    }
    component Label: Text {
        color: pane.theme.text
        font.pixelSize: Math.round(13 * pane.unit)
        textFormat: Text.PlainText
    }
    component Hint: Text {
        Layout.fillWidth: true
        color: pane.theme.muted
        font.pixelSize: Math.round(12 * pane.unit)
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
    component Field: TextField {
        color: pane.theme.text
        placeholderTextColor: pane.theme.muted
        selectByMouse: true
        font.pixelSize: Math.round(13 * pane.unit)
        background: Rectangle {
            radius: Math.round(6 * pane.unit)
            color: pane.theme.base
            border.color: parent.activeFocus ? pane.theme.accent : pane.theme.line
        }
    }
    component Option: CheckBox {
        id: option
        font.pixelSize: Math.round(12 * pane.unit)
        palette.windowText: pane.theme.text
        indicator: Rectangle {
            x: option.leftPadding
            y: Math.round((option.height - height) / 2)
            width: Math.round(16 * pane.unit)
            height: width
            radius: Math.round(3 * pane.unit)
            color: option.checked ? pane.theme.accent : pane.theme.base
            border.color: option.visualFocus ? pane.theme.accent : pane.theme.line
            opacity: option.enabled ? 1 : .48
            UiIcon {
                anchors.centerIn: parent
                glyph: "check"
                tint: pane.theme.onAccent
                width: parent.width - 2
                height: width
                visible: option.checked
            }
        }
    }

    FileDialog {
        id: sourceFile
        title: pane.label("选择参考工程")
        nameFilters: ["VWA (*.svp *.ustx *.vpr *.vsqx *.acep *.ufdata)"]
        onAccepted: pane.controller.loadReference(selectedFile)
    }
    FileDialog {
        id: audioFile
        title: pane.label("选择对齐干声")
        nameFilters: ["WAV (*.wav)"]
        onAccepted: pane.controller.chooseDryVocal(selectedFile)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Math.round(14 * pane.unit)

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Math.round(14 * pane.unit)

            Rectangle {
                color: pane.theme.surface
                border.color: pane.theme.line
                radius: Math.round(9 * pane.unit)
                Layout.fillHeight: true
                Layout.preferredWidth: Math.round(Math.max(242, Math.min(pane.width * .30 / pane.unit, 328)) * pane.unit)

                ScrollView {
                    id: sources
                    anchors.fill: parent
                    anchors.margins: Math.round(15 * pane.unit)
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: sources.availableWidth
                        spacing: Math.round(11 * pane.unit)
                        Heading { text: pane.label("参考素材") }
                        RowLayout {
                            Layout.fillWidth: true
                            UiButton {
                                theme: pane.theme
                                Layout.fillWidth: true
                                text: pane.label("参考工程")
                                selected: !pane.audioMode
                                enabled: !pane.authorView.busy
                                onClicked: pane.controller.edit("mode", "project")
                            }
                            UiButton {
                                theme: pane.theme
                                Layout.fillWidth: true
                                text: pane.label("对齐干声")
                                selected: pane.audioMode
                                enabled: !pane.authorView.busy
                                onClicked: pane.controller.edit("mode", "audio")
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                text: pane.leaf(pane.reference.path) || pane.label("未选择参考工程")
                            }
                            UiButton {
                                objectName: "styleAuthorReferenceFile"
                                theme: pane.theme
                                glyph: "folder"
                                text: pane.label("选择参考工程")
                                enabled: !pane.authorView.busy
                                onClicked: sourceFile.open()
                            }
                        }
                        UiButton {
                            theme: pane.theme
                            quiet: true
                            text: pane.label("使用当前调音工程")
                            visible: !!pane.authorView.canUseCurrent
                            enabled: !pane.authorView.busy
                            onClicked: pane.controller.useCurrentReference()
                        }
                        UiComboBox {
                            objectName: "styleAuthorTrack"
                            theme: pane.theme
                            Layout.fillWidth: true
                            model: pane.trackRows
                            textRole: "name"
                            currentIndex: pane.trackRows.findIndex(row => row.index === pane.fields.track)
                            enabled: !pane.authorView.busy && pane.trackRows.length > 0
                            Accessible.name: pane.label("参考演唱轨")
                            onActivated: pane.controller.selectReferenceTrack(pane.trackRows[currentIndex].index)
                        }
                        Label { text: pane.label("参考唱段（秒）") }
                        RowLayout {
                            Layout.fillWidth: true
                            Field {
                                objectName: "styleAuthorFrom"
                                Layout.fillWidth: true
                                text: pane.fields.start || ""
                                enabled: !!pane.reference.path && !pane.authorView.busy
                                validator: DoubleValidator { decimals: 3; locale: "C"; notation: DoubleValidator.StandardNotation }
                                Accessible.name: pane.label("参考开始时间")
                                onTextEdited: pane.controller.edit("start", text)
                            }
                            Label { text: "–" }
                            Field {
                                objectName: "styleAuthorTo"
                                Layout.fillWidth: true
                                text: pane.fields.end || ""
                                enabled: !!pane.reference.path && !pane.authorView.busy
                                validator: DoubleValidator { decimals: 3; locale: "C"; notation: DoubleValidator.StandardNotation }
                                Accessible.name: pane.label("参考结束时间")
                                onTextEdited: pane.controller.edit("end", text)
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: pane.audioMode
                            RowLayout {
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true
                                    elide: Text.ElideMiddle
                                    text: pane.leaf(pane.fields.audio) || pane.label("未选择对齐干声")
                                }
                                UiButton {
                                    objectName: "styleAuthorDryFile"
                                    theme: pane.theme
                                    glyph: "folder"
                                    text: pane.label("选择对齐干声")
                                    enabled: !pane.authorView.busy
                                    onClicked: audioFile.open()
                                }
                            }
                            Label { text: pane.label("干声 0 秒对应的工程时间") }
                            Field {
                                objectName: "styleAuthorAudioZero"
                                Layout.fillWidth: true
                                text: pane.fields.offset || "0"
                                enabled: !pane.authorView.busy
                                validator: DoubleValidator { decimals: 3; locale: "C"; notation: DoubleValidator.StandardNotation }
                                onTextEdited: pane.controller.edit("offset", text)
                            }
                        }
                        Option {
                            objectName: "styleAuthorMeasurePitch"
                            text: pane.label("一并提取起音与收尾音高")
                            checked: !!pane.fields.measurePitch
                            enabled: !pane.authorView.busy
                            onToggled: pane.controller.edit("measurePitch", checked)
                        }
                        UiButton {
                            objectName: "styleAuthorAnalyze"
                            Layout.fillWidth: true
                            theme: pane.theme
                            text: pane.label("提取风格")
                            primary: !pane.authorView.ready
                            enabled: !!pane.authorView.canAnalyze && !pane.authorView.busy
                            onClicked: pane.controller.analyze()
                        }
                        Hint {
                            visible: !!pane.authorView.ready
                            text: pane.label("分析 {0} 个音符 · 力度 {1} 个 · 音高 {2} 个 · 排除 {3} 个")
                                .replace("{0}", pane.evidence.notes).replace("{1}", pane.evidence.loudness_notes)
                                .replace("{2}", pane.evidence.pitch_notes).replace("{3}", pane.excluded)
                        }
                        Hint {
                            visible: !!pane.authorView.ready && pane.excluded > 0
                            text: pane.label("排除明细 · 重叠 {0} · 过短 {1} · 非演唱 {2} · 被选区切断 {3}")
                                .replace("{0}", (pane.evidence.excluded || {}).overlap || 0)
                                .replace("{1}", (pane.evidence.excluded || {}).short || 0)
                                .replace("{2}", (pane.evidence.excluded || {}).non_vocal || 0)
                                .replace("{3}", (pane.evidence.excluded || {}).cut || 0)
                        }
                        Hint {
                            visible: !!pane.authorView.ready && !!pane.evidence.alignment
                            text: pane.evidence.alignment
                                ? pane.label("音符内干声活跃率 {0}% · {1} 个可用 · {2} 个低重合")
                                    .replace("{0}", Math.round(100 * pane.evidence.alignment.in_note_signal_fraction))
                                    .replace("{1}", pane.evidence.alignment.accepted_notes)
                                    .replace("{2}", pane.evidence.alignment.excluded_notes) : ""
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: pane.theme.surface
                border.color: pane.theme.line
                radius: Math.round(9 * pane.unit)

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(15 * pane.unit)
                    spacing: Math.round(10 * pane.unit)
                    RowLayout {
                        Layout.fillWidth: true
                        Heading { text: pane.label("力度包络") }
                        Item { Layout.fillWidth: true }
                        Option {
                            text: pane.label("保留力度")
                            checked: !!pane.fields.useGain
                            enabled: !!pane.authorView.ready && pane.points.length > 0 && !pane.authorView.busy
                            onToggled: pane.controller.edit("useGain", checked)
                        }
                        Option {
                            text: pane.label("保留音高动作")
                            checked: !!pane.fields.usePitch
                            enabled: !!pane.authorView.ready && !pane.authorView.busy
                            onToggled: pane.controller.edit("usePitch", checked)
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 160 * pane.unit
                        color: pane.theme.base
                        border.color: pane.theme.line
                        radius: Math.round(7 * pane.unit)

                        Canvas {
                            id: graph
                            objectName: "styleAuthorEnvelope"
                            anchors.fill: parent
                            readonly property real plotLeft: 47 * pane.unit
                            readonly property real plotTop: 15 * pane.unit
                            readonly property real plotWidth: Math.max(1, width - plotLeft - 22 * pane.unit)
                            readonly property real plotHeight: Math.max(1, height - plotTop - 28 * pane.unit)
                            readonly property var plotPoints: pane.authorView.ready ? pane.points : []
                            readonly property int pointIndex: Number(pane.authorView.selectedPoint || 0)
                            property int dragIndex: -1
                            property real dragValue: 0
                            function levelY(value) { return plotTop + (12 - value) / 24 * plotHeight }
                            function level(value) { return Math.max(-12, Math.min(12, 12 - (value - plotTop) * 24 / plotHeight)) }
                            onPlotPointsChanged: requestPaint()
                            onPointIndexChanged: requestPaint()
                            onDragValueChanged: requestPaint()
                            onDragIndexChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onHeightChanged: requestPaint()
                            Connections {
                                target: pane.theme
                                function onDarkChanged() { graph.requestPaint() }
                            }
                            onPaint: {
                                const ctx = getContext("2d")
                                ctx.clearRect(0, 0, width, height)
                                ctx.font = Math.round(10 * pane.unit) + "px sans-serif"
                                ctx.fillStyle = pane.theme.muted
                                ctx.lineWidth = 1
                                for (let i = 0; i <= 4; ++i) {
                                    const x = plotLeft + i * plotWidth / 4
                                    const db = 12 - i * 6
                                    const yy = levelY(db)
                                    ctx.strokeStyle = pane.theme.line
                                    ctx.beginPath()
                                    ctx.moveTo(x, plotTop)
                                    ctx.lineTo(x, plotTop + plotHeight)
                                    ctx.moveTo(plotLeft, yy)
                                    ctx.lineTo(plotLeft + plotWidth, yy)
                                    ctx.stroke()
                                    ctx.textAlign = "right"
                                    ctx.fillText((db > 0 ? "+" : "") + db, plotLeft - 8, yy + 3)
                                    ctx.textAlign = "center"
                                    ctx.fillText(String(i * 25) + "%", x, plotTop + plotHeight + 17 * pane.unit)
                                }
                                if (!plotPoints.length)
                                    return
                                const coords = plotPoints.map((point, index) => [plotLeft + point[0] * plotWidth,
                                    levelY(index === dragIndex ? dragValue : point[1])])
                                ctx.strokeStyle = pane.theme.accent
                                ctx.lineWidth = 2 * pane.unit
                                ctx.beginPath()
                                coords.forEach((point, index) => index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1]))
                                ctx.stroke()
                                coords.forEach((point, index) => {
                                    ctx.fillStyle = index === pointIndex || dragIndex === index ? pane.theme.accent : pane.theme.text
                                    ctx.beginPath()
                                    ctx.arc(point[0], point[1], (index === pointIndex ? 4.5 : 3) * pane.unit, 0, Math.PI * 2)
                                    ctx.fill()
                                })
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: graph.plotPoints.length > 0 && pane.fields.useGain && !pane.authorView.busy
                                cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                                onPressed: event => {
                                    const t = Math.max(0, Math.min(1, (event.x - graph.plotLeft) / graph.plotWidth))
                                    graph.dragIndex = Math.round(t * (graph.plotPoints.length - 1))
                                    graph.dragValue = graph.level(event.y)
                                }
                                onPositionChanged: event => { if (pressed) graph.dragValue = graph.level(event.y) }
                                onReleased: {
                                    pane.controller.setPoint(graph.dragIndex, graph.dragValue)
                                    graph.dragIndex = -1
                                }
                                onCanceled: graph.dragIndex = -1
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            width: parent.width - 112 * pane.unit
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            visible: !pane.authorView.ready || !pane.points.length
                            color: pane.theme.muted
                            font.pixelSize: Math.round(14 * pane.unit)
                            text: pane.authorView.ready ? pane.label("本次提取包含音高动作")
                                : pane.authorView.hasAnalysis ? pane.label("参考输入已变化，请重新提取。")
                                : pane.label("选择参考素材并提取，查看和调整力度包络。")
                            textFormat: Text.PlainText
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!pane.authorView.ready && pane.points.length > 0
                        UiComboBox {
                            theme: pane.theme
                            Accessible.name: pane.label("力度相位")
                            model: pane.points.map((point, index) => ({label: (100 * point[0]).toFixed(1) + "%", value: index}))
                            currentIndex: pane.authorView.selectedPoint || 0
                            onActivated: pane.controller.selectPoint(currentIndex)
                        }
                        WorkshopSlider {
                            theme: pane.theme
                            Layout.fillWidth: true
                            Accessible.name: pane.label("力度")
                            enabled: pane.fields.useGain && !pane.authorView.busy
                            from: -12
                            to: 12
                            stepSize: .1
                            value: pane.points.length ? pane.points[pane.authorView.selectedPoint || 0][1] : 0
                            onMoved: pane.controller.setPoint(pane.authorView.selectedPoint || 0, value)
                        }
                        Label { text: (pane.points.length ? pane.points[pane.authorView.selectedPoint || 0][1].toFixed(1) : "0") + " dB" }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!pane.authorView.ready
                        spacing: Math.round(20 * pane.unit)
                        Repeater {
                            model: [{kind: "onset", text: "起音音高"}, {kind: "release", text: "收尾音高"}]
                            RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Label { text: pane.label(modelData.text) }
                                WorkshopSlider {
                                    theme: pane.theme
                                    Layout.fillWidth: true
                                    Accessible.name: pane.label(modelData.text)
                                    enabled: pane.fields.usePitch && !pane.authorView.busy
                                    from: -120
                                    to: 120
                                    stepSize: 1
                                    value: pane.cents(modelData.kind)
                                    onMoved: pane.controller.setPitch(modelData.kind, value)
                                }
                                Label { text: pane.cents(modelData.kind).toFixed(0) + " ¢" }
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Hint {
                text: pane.target.ready
                    ? pane.target.title + "  ·  " + pane.label("试套目标 {0} · {1}–{2} 秒")
                        .replace("{0}", pane.target.track).replace("{1}", Number(pane.target.start).toFixed(2))
                        .replace("{2}", Number(pane.target.end).toFixed(2))
                    : (pane.target.reason || pane.label("在调音工作流中选中目标唱段后，可以试套。"))
                color: pane.target.ready ? pane.theme.text : pane.theme.muted
            }
            UiButton {
                visible: !pane.target.ready
                theme: pane.theme
                quiet: true
                text: pane.label("去调音选唱段")
                onClicked: pane.targetRequested()
            }
        }
        Hint {
            visible: !!text
            text: pane.authorView.trial || pane.authorView.message || ""
        }
        RowLayout {
            Layout.fillWidth: true
            Field {
                objectName: "styleAuthorName"
                Layout.fillWidth: true
                Layout.minimumWidth: 100 * pane.unit
                placeholderText: pane.label("风格名称")
                text: pane.fields.name || ""
                maximumLength: 80
                enabled: !pane.authorView.busy && !!pane.authorView.ready
                onTextEdited: pane.controller.edit("name", text)
            }
            Label { text: pane.label("风格强度") }
            WorkshopSlider {
                theme: pane.theme
                Layout.preferredWidth: 91 * pane.unit
                Accessible.name: pane.label("风格强度")
                enabled: !pane.authorView.busy && !!pane.authorView.ready
                from: 0
                to: 1
                stepSize: .01
                value: Number(pane.fields.strength || 0)
                onMoved: pane.controller.edit("strength", value)
            }
            Label { text: Math.round((pane.fields.strength || 0) * 100) + "%" }
            UiButton {
                visible: !!pane.authorView.running
                theme: pane.theme
                quiet: true
                text: pane.label("取消")
                onClicked: pane.controller.cancel()
            }
            UiButton {
                objectName: "styleAuthorTrial"
                theme: pane.theme
                text: pane.label("试套当前选区")
                enabled: !!pane.authorView.validRecipe && !!pane.target.ready && !pane.authorView.busy
                onClicked: pane.controller.trialStyle()
            }
            UiButton {
                objectName: "styleAuthorApply"
                theme: pane.theme
                text: pane.label("应用到时间选区")
                enabled: !!pane.authorView.trial && !pane.authorView.busy
                onClicked: pane.controller.applyStyle()
            }
            UiButton {
                objectName: "styleAuthorSave"
                theme: pane.theme
                primary: true
                text: pane.label("保存配方")
                enabled: !!pane.authorView.validRecipe && !pane.authorView.busy
                onClicked: pane.controller.saveStyle()
            }
        }
    }
}
