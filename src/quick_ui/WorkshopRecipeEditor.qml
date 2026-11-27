import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: editor
    required property QtObject theme
    required property QtObject controller
    required property var view
    readonly property var design: view.design || ({})
    readonly property var draft: design.draft || ({})
    readonly property var preview: design.preview || ({})
    readonly property real unit: theme.scale
    readonly property bool narrow: width < Math.round(740 * unit)
    readonly property string channel: design.channel || "loudness"
    readonly property bool gain: channel === "loudness"
    readonly property bool pitchPanel: design.panel === "pitch"
    readonly property var channelLabels: ({
        loudness: "力度", tension: "张力", breathiness: "气声", voicing: "发声强度"
    })
    readonly property var noteRows: [
        {key: "onset", label: "起音"}, {key: "sustain", label: "持续"},
        {key: "release", label: "收尾"}, {key: "arc", label: "长音展开"}
    ]
    readonly property var phraseRows: [
        {key: "opening", label: "句首"}, {key: "body", label: "句中"}, {key: "closing", label: "句尾"}
    ]
    readonly property var pitchGroups: [
        {kind: "onset", title: "句首倚滑", rows: [
            {key: "cents", label: "偏移", unit: "¢"}, {key: "seconds", label: "最长时间", unit: "ms"},
            {key: "peak", label: "峰值位置", unit: "%"}
        ]},
        {kind: "release", title: "句尾收腔", rows: [
            {key: "cents", label: "偏移", unit: "¢"}, {key: "seconds", label: "最长时间", unit: "ms"},
            {key: "peak", label: "峰值位置", unit: "%"}
        ]},
        {kind: "connection", title: "音间衔接", rows: [
            {key: "cents_per_semitone", label: "每半音偏移", unit: "¢"}, {key: "limit", label: "偏移上限", unit: "¢"},
            {key: "seconds", label: "最长时间", unit: "ms"}, {key: "repeat_cents", label: "同音衔接", unit: "¢"}
        ]},
        {kind: "vibrato", title: "长音颤音", rows: [
            {key: "depth", label: "颤音半幅", unit: "¢"}, {key: "rate", label: "频率", unit: "Hz"},
            {key: "delay", label: "进入延迟", unit: "ms"}, {key: "minimum_note", label: "最短音符", unit: "ms"}
        ]}
    ]
    readonly property var connectionTypes: [
        {value: "connection", label: label("通用衔接")},
        {value: "repeated", label: label("同音衔接")},
        {value: "rising", label: label("上行衔接")},
        {value: "falling", label: label("下行衔接")}
    ]
    readonly property var positionParameters: [
        {key: "cents", label: "偏移", unit: "¢"}, {key: "seconds", label: "最长时间", unit: "ms"}
    ]

    function label(text) { return typeof quickTranslation !== "undefined" && quickTranslation ? quickTranslation.text(text) : text }
    function parameter(section, key) {
        if (section === "timing")
            return (draft.timing || {})[key] || 0
        if ((draft.pitch || {})[section])
            return draft.pitch[section][key]
        const parts = section.split(".")
        return (((draft.controls || {})[parts[0]] || {})[parts[1]] || {})[key] || 0
    }
    function limits(section, key) {
        const bounds = controller.designBounds[section]
        if (bounds && bounds[key])
            return bounds[key]
        const limit = gain ? 12 : 1
        return [-limit, limit]
    }

    component Heading: Text {
        color: editor.theme.text
        font.pixelSize: Math.round(14 * editor.unit)
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
    }

    component Caption: Text {
        color: editor.theme.muted
        font.pixelSize: Math.round(12 * editor.unit)
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    component Parameter: RowLayout {
        id: control
        required property string section
        required property string parameterKey
        required property string title
        property string symbol: ""
        property int precision: (symbol === "ms" || symbol === "%") ? 0 : symbol === "" ? 3 : 1
        readonly property var bounds: editor.limits(section, parameterKey)
        readonly property real factor: symbol === "ms" ? 1000 : symbol === "%" ? 100 : 1
        readonly property real current: Number(editor.parameter(section, parameterKey)) * factor
        Layout.fillWidth: true
        spacing: Math.round(7 * editor.unit)
        Caption {
            Layout.preferredWidth: Math.round(92 * editor.unit)
            text: control.title
        }
        WorkshopSlider {
            objectName: "designSlider_" + control.section + "_" + control.parameterKey
            theme: editor.theme
            Layout.fillWidth: true
            Accessible.name: control.title
            enabled: !editor.view.busy
            from: Number(control.bounds[0]) * control.factor
            to: Number(control.bounds[1]) * control.factor
            stepSize: Math.pow(10, -control.precision)
            value: control.current
            onMoved: editor.controller.editDesign(control.section, control.parameterKey, value / control.factor)
        }
        TextField {
            id: numeric
            objectName: "designValue_" + control.section + "_" + control.parameterKey
            Layout.preferredWidth: Math.round(61 * editor.unit)
            topPadding: Math.round(4 * editor.unit)
            bottomPadding: topPadding
            horizontalAlignment: TextInput.AlignHCenter
            selectByMouse: true
            font.pixelSize: Math.round(12 * editor.unit)
            color: editor.theme.text
            enabled: !editor.view.busy
            Accessible.name: control.title
            text: control.current.toFixed(control.precision)
            onEditingFinished: {
                const value = Number(text.replace(",", "."))
                if (text.trim() && Number.isFinite(value))
                    editor.controller.editDesign(control.section, control.parameterKey, value / control.factor)
                text = Qt.binding(() => control.current.toFixed(control.precision))
            }
            background: Rectangle {
                color: editor.theme.base
                border.color: numeric.activeFocus ? editor.theme.accent : editor.theme.line
                radius: Math.round(5 * editor.unit)
            }
        }
        Caption {
            Layout.preferredWidth: Math.round(21 * editor.unit)
            visible: !!control.symbol
            text: control.symbol
        }
    }

    GridLayout {
        anchors.fill: parent
        columns: editor.narrow ? 1 : 2
        columnSpacing: Math.round(14 * editor.unit)
        rowSpacing: Math.round(10 * editor.unit)
        Rectangle {
            Layout.fillHeight: !editor.narrow
            Layout.fillWidth: editor.narrow
            Layout.minimumWidth: 0
            Layout.preferredWidth: editor.narrow ? -1 : Math.round(232 * editor.unit)
            Layout.preferredHeight: editor.narrow ? sidebarColumn.implicitHeight + Math.round(20 * editor.unit) : -1
            color: editor.theme.surface
            border.color: editor.theme.line
            radius: Math.round(9 * editor.unit)
            ColumnLayout {
                id: sidebarColumn
                anchors.fill: parent
                anchors.margins: Math.round(10 * editor.unit)
                spacing: Math.round(6 * editor.unit)
                Heading { text: editor.label("起始配方") }
                Flow {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(6 * editor.unit)
                    Repeater {
                        model: editor.design.templates || []
                        delegate: Item {
                            id: entry
                            required property var modelData
                            width: editor.narrow ? Math.min(parent.width, templateButton.implicitWidth + Math.round(10 * editor.unit)) : parent.width
                            implicitHeight: templateButton.implicitHeight
                            height: implicitHeight
                            UiButton {
                                id: templateButton
                                objectName: "designTemplate_" + entry.modelData.id
                                anchors.fill: parent
                                anchors.leftMargin: Math.round(8 * editor.unit)
                                theme: editor.theme
                                text: editor.label(entry.modelData.label)
                                selected: editor.design.template === entry.modelData.id
                                quiet: !selected
                                leftAligned: !editor.narrow
                                enabled: !editor.view.busy
                                onClicked: editor.controller.selectTemplate(entry.modelData.id)
                            }
                            Rectangle {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.round(3 * editor.unit)
                                height: Math.round(21 * editor.unit)
                                radius: width / 2
                                visible: !!entry.modelData.color
                                color: entry.modelData.color || editor.theme.line
                            }
                        }
                    }
                }
                Caption {
                    Layout.fillWidth: true
                    Layout.topMargin: editor.narrow ? 0 : Math.round(4 * editor.unit)
                    text: {
                        const item = (editor.design.templates || []).find(row => row.id === editor.design.template)
                        return item ? editor.label(item.description) : ""
                    }
                }
                Caption {
                    Layout.fillWidth: true
                    visible: !!editor.design.hasLocalDefault || !!editor.design.invalidLocalDefault
                    text: editor.label(editor.design.invalidLocalDefault ? "本机默认版本不可用"
                        : editor.design.localDefaultEdited ? "当前调整尚未设为默认" : "已保存本机默认版本")
                    color: editor.design.invalidLocalDefault || editor.design.localDefaultEdited
                        ? editor.theme.attention : editor.theme.success
                }
                Flow {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(6 * editor.unit)
                    UiButton {
                        objectName: "designSaveDefault"
                        width: editor.narrow ? implicitWidth : parent.width
                        visible: !!editor.design.isFactoryTemplate
                        theme: editor.theme
                        text: editor.label("保存为本机默认")
                        leftAligned: !editor.narrow
                        enabled: !!editor.view.validRecipe && !editor.view.busy
                        onClicked: editor.controller.saveStyleDefault()
                    }
                    UiButton {
                        objectName: "designUseDefault"
                        width: editor.narrow ? implicitWidth : parent.width
                        visible: !!editor.design.hasLocalDefault
                        theme: editor.theme
                        text: editor.label("选用本机默认")
                        leftAligned: !editor.narrow
                        enabled: !!editor.design.canUseLocalDefault && !editor.view.busy
                        onClicked: editor.controller.useStyleDefault()
                    }
                    UiButton {
                        objectName: "designRestoreDefault"
                        width: editor.narrow ? implicitWidth : parent.width
                        visible: !!editor.design.isFactoryTemplate
                        theme: editor.theme
                        text: editor.label("恢复内置参数")
                        quiet: true
                        leftAligned: !editor.narrow
                        enabled: !editor.view.busy
                        onClicked: editor.controller.restoreStyleDefault()
                    }
                }
                Item { Layout.fillHeight: !editor.narrow }
            }
        }

        Rectangle {
            id: rightPane
            Layout.fillHeight: true
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            readonly property bool twoCol: width >= Math.round(560 * editor.unit)
            color: editor.theme.surface
            border.color: editor.theme.line
            radius: Math.round(9 * editor.unit)

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Math.round(14 * editor.unit)
                spacing: Math.round(10 * editor.unit)
                Flow {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: Math.round(6 * editor.unit)
                    UiButton {
                        objectName: "designPanelExpression"
                        theme: editor.theme
                        text: editor.label("表达包络")
                        selected: !editor.pitchPanel
                        quiet: !selected
                        onClicked: editor.controller.selectDesignPanel("expression")
                    }
                    UiButton {
                        objectName: "designPanelPitch"
                        theme: editor.theme
                        text: editor.label("唱腔动作")
                        selected: editor.pitchPanel
                        quiet: !selected
                        onClicked: editor.controller.selectDesignPanel("pitch")
                    }
                    Repeater {
                        model: Object.keys(editor.channelLabels)
                        delegate: UiButton {
                            required property string modelData
                            objectName: "designChannel_" + modelData
                            visible: !editor.pitchPanel
                            theme: editor.theme
                            text: editor.label(editor.channelLabels[modelData])
                            quiet: true
                            selected: editor.channel === modelData
                            onClicked: editor.controller.selectDesignChannel(modelData)
                        }
                    }
                }

                ScrollView {
                    id: expressionScroll
                    visible: !editor.pitchPanel
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: availableWidth
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: expressionScroll.availableWidth
                        spacing: Math.round(8 * editor.unit)

                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(10 * editor.unit)
                            Caption {
                                color: editor.theme.accent
                                text: editor.label("单音示例 · {0} 秒").replace("{0}", Number(editor.preview.note_seconds || 1.2).toFixed(2))
                            }
                            Caption { color: editor.theme.success; text: editor.label("乐句示例 · 3 秒") }
                            Caption {
                                visible: !!editor.preview.guide
                                color: editor.theme.attention
                                text: editor.label("参考力度")
                            }
                            Caption { text: editor.label("当前强度下的增量") + (editor.gain ? " · dB" : "") }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.round(120 * editor.unit)
                            Layout.minimumHeight: Math.round(99 * editor.unit)
                            color: editor.theme.base
                            border.color: editor.theme.line
                            radius: Math.round(7 * editor.unit)
                            Canvas {
                                id: chart
                                objectName: "designEnvelope"
                                anchors.fill: parent
                                readonly property var curves: editor.preview
                                readonly property color noteInk: editor.theme.accent
                                readonly property color phraseInk: editor.theme.success
                                readonly property color guideInk: editor.theme.attention
                                readonly property color gridInk: editor.theme.line
                                readonly property color labelInk: editor.theme.muted
                                onCurvesChanged: requestPaint()
                                onNoteInkChanged: requestPaint()
                                onPhraseInkChanged: requestPaint()
                                onGuideInkChanged: requestPaint()
                                onGridInkChanged: requestPaint()
                                onLabelInkChanged: requestPaint()
                                onWidthChanged: requestPaint()
                                onHeightChanged: requestPaint()
                                onPaint: {
                                    const ctx = getContext("2d")
                                    ctx.reset()
                                    const trace = [curves.note || [], curves.phrase || [], curves.guide || []]
                                    const values = trace[0].concat(trace[1], trace[2])
                                    const range = Math.max(editor.gain ? 1 : .10, ...values.map(y => Math.abs(y))) * 1.08
                                    const left = 49 * editor.unit, right = width - 13 * editor.unit
                                    const top = 13 * editor.unit, bottom = height - 22 * editor.unit
                                    ctx.strokeStyle = gridInk
                                    ctx.fillStyle = labelInk
                                    ctx.font = Math.round(10 * editor.unit) + "px sans-serif"
                                    for (let i = 0; i < 5; ++i) {
                                        const x = left + (right - left) * i / 4
                                        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke()
                                        ctx.textAlign = i === 0 ? "left" : i === 4 ? "right" : "center"
                                        ctx.fillText(String(i * 25) + "%", x, bottom + 14 * editor.unit)
                                    }
                                    ctx.textAlign = "left"
                                    for (let i = 0; i < 3; ++i) {
                                        const y = top + (bottom - top) * i / 2
                                        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke()
                                        const value = (1 - i) * range
                                        ctx.fillText((value > 0 ? "+" : "") + value.toFixed(editor.gain ? 1 : 2), 8 * editor.unit, y + 4)
                                    }
                                    for (let series = 0; series < trace.length; ++series) {
                                        const points = trace[series]
                                        if (!points.length) continue
                                        ctx.strokeStyle = [noteInk, phraseInk, guideInk][series]
                                        ctx.lineWidth = 1.7 * editor.unit
                                        ctx.setLineDash(series === 0 ? [] : series === 1
                                            ? [5 * editor.unit, 4 * editor.unit] : [2 * editor.unit, 4 * editor.unit])
                                        ctx.beginPath()
                                        for (let i = 0; i < points.length; ++i) {
                                            const x = left + (right - left) * i / (points.length - 1)
                                            const y = top + (bottom - top) * (1 - points[i] / range) / 2
                                            if (i) ctx.lineTo(x, y)
                                            else ctx.moveTo(x, y)
                                        }
                                        ctx.stroke()
                                    }
                                }
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: rightPane.twoCol ? 2 : 1
                            columnSpacing: Math.round(16 * editor.unit)
                            rowSpacing: Math.round(10 * editor.unit)
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Heading { text: editor.label("单音包络") }
                                Repeater {
                                    model: editor.noteRows
                                    delegate: Parameter {
                                        required property var modelData
                                        section: editor.channel + ".note"
                                        parameterKey: modelData.key
                                        title: editor.label(modelData.label)
                                        symbol: editor.gain ? "dB" : ""
                                    }
                                }
                            }
                            ColumnLayout {
                                Layout.alignment: Qt.AlignTop
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Heading { text: editor.label("乐句包络") }
                                Repeater {
                                    model: editor.phraseRows
                                    delegate: Parameter {
                                        required property var modelData
                                        section: editor.channel + ".phrase"
                                        parameterKey: modelData.key
                                        title: editor.label(modelData.label)
                                        symbol: editor.gain ? "dB" : ""
                                    }
                                }
                            }
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: rightPane.twoCol ? 2 : 1
                            columnSpacing: Math.round(16 * editor.unit)
                            rowSpacing: Math.round(8 * editor.unit)
                            Parameter {
                                section: "timing"
                                parameterKey: "onset"
                                title: editor.label("起音时窗")
                                symbol: "ms"
                            }
                            Parameter {
                                section: "timing"
                                parameterKey: "release"
                                title: editor.label("收尾时窗")
                                symbol: "ms"
                            }
                        }
                    }
                }

                ScrollView {
                    id: rules
                    visible: editor.pitchPanel
                    Layout.fillHeight: true
                    Layout.fillWidth: true
                    contentWidth: availableWidth
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: rules.availableWidth
                        spacing: Math.round(13 * editor.unit)
                        Caption {
                            Layout.fillWidth: true
                            text: editor.label("按唱段位置叠加。原有手绘、滑音和颤音由目标工程的保护规则处理。")
                        }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: rules.availableWidth >= Math.round(520 * editor.unit) ? 2 : 1
                            columnSpacing: Math.round(22 * editor.unit)
                            rowSpacing: Math.round(17 * editor.unit)
                            Repeater {
                                model: editor.pitchGroups
                                delegate: ColumnLayout {
                                    id: action
                                    required property var modelData
                                    readonly property bool connection: modelData.kind === "connection"
                                    readonly property string kind: connection ? (editor.design.connectionAction || "connection") : modelData.kind
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    Layout.alignment: Qt.AlignTop
                                    Heading {
                                        text: editor.label(action.modelData.title)
                                        color: (editor.design.activeActions || []).includes(action.kind)
                                            ? editor.theme.text : editor.theme.muted
                                    }
                                    UiComboBox {
                                        objectName: "designConnectionKind"
                                        visible: action.connection
                                        Layout.fillWidth: true
                                        theme: editor.theme
                                        fieldLabel: editor.label("衔接位置")
                                        enabled: !editor.view.busy
                                        model: editor.connectionTypes
                                        currentIndex: Math.max(0, editor.connectionTypes.findIndex(row => row.value === action.kind))
                                        onActivated: editor.controller.selectConnectionAction(currentValue)
                                    }
                                    Repeater {
                                        model: action.connection && action.kind !== "connection" ? editor.positionParameters : action.modelData.rows
                                        delegate: Parameter {
                                            required property var modelData
                                            section: action.kind
                                            parameterKey: modelData.key
                                            title: editor.label(modelData.label)
                                            symbol: modelData.unit
                                        }
                                    }
                                    UiComboBox {
                                        visible: action.kind === "connection"
                                        Layout.fillWidth: true
                                        theme: editor.theme
                                        fieldLabel: editor.label("衔接方向")
                                        enabled: !editor.view.busy
                                        model: [{value: "settle", label: editor.label("延迟落稳")},
                                                {value: "follow", label: editor.label("沿音程方向")}]
                                        currentIndex: editor.parameter("connection", "direction") === "follow" ? 1 : 0
                                        onActivated: editor.controller.editDesign("connection", "direction", currentValue)
                                    }
                                    UiButton {
                                        visible: action.kind === "connection"
                                        theme: editor.theme
                                        text: editor.label("大下行提前衔接")
                                        selected: editor.parameter("connection", "anticipate_descent") === true
                                        enabled: !editor.view.busy
                                        onClicked: editor.controller.editDesign("connection", "anticipate_descent", !selected)
                                    }
                                    Caption {
                                        Layout.fillWidth: true
                                        visible: action.connection && action.kind !== "connection"
                                        text: editor.label("此位置的偏移设为 0 时，使用通用衔接。")
                                    }
                                    Caption {
                                        Layout.fillWidth: true
                                        visible: action.kind === "vibrato"
                                        text: editor.label("有颤音或无法确认颤音状态的音符会保留。半幅设为 0 可关闭。")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
