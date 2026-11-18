import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: lane
    objectName: "tuningParameterLane"
    required property QtObject theme
    required property QtObject controller
    required property Item roll
    required property var view
    readonly property real uiScale: theme.scale || 1
    readonly property var plot: visible ? controller.parameterPlot : ({})
    readonly property var manual: visible ? roll.manualCurves : []
    readonly property color originalColor: theme.muted
    readonly property color styleColor: theme.accent
    readonly property color manualColor: roll.handColor
    readonly property real leftInset: roll.x + roll.plotLeft
    readonly property real rightInset: roll.x + roll.plotRight
    readonly property bool canEdit: !view.busy && !String(view.pitchEditReason || "")
    readonly property real extent: plotExtent()
    readonly property real inspectionTime: visible && isFinite(pointerTime) ? pointerTime : Number.NaN
    property bool showOriginal: true
    property bool showStyle: true
    property bool showManual: true
    property string tool: ""
    property var gesture: null
    property var pointer: null
    property real pointerTime: Number.NaN

    color: theme.base
    radius: Math.round(6 * uiScale)
    border.color: theme.line
    border.width: 1
    implicitHeight: Math.round(126 * uiScale)
    clip: true

    function label(text) { return roll.vwaTr(text) }

    function plotExtent() {
        let peak = Number(view.pitchMaxCents || 200)
        const layers = [showOriginal ? plot.original || [] : [], showStyle ? plot.style || [] : []]
        for (const rows of layers)
            for (const row of rows)
                for (const point of row.points)
                    if (point && isFinite(point[1])) peak = Math.max(peak, Math.abs(point[1]))
        return Math.ceil(peak / 100) * 100
    }

    function xForTime(seconds) { return roll.x + roll.xForTime(seconds) }
    function timeForX(x) {
        return roll.timeForPlotX(x - leftInset)
    }

    function yForValue(cents) {
        const pad = 10 * uiScale
        return pad + (extent - cents) / (2 * extent) * Math.max(1, graph.height - 2 * pad)
    }

    function noteAt(seconds) {
        const candidates = roll.visibleNotes.filter(
            note => note.start <= seconds && seconds < note.end)
        return candidates.length === 1 ? candidates[0] : null
    }

    function pointAt(x, y, modifiers, note) {
        const raw = timeForX(x)
        const pad = 10 * uiScale
        const limit = Number(view.pitchMaxCents || 200)
        const snap = view.snapEnabled && !(modifiers & Qt.AltModifier)
        const step = snap ? 10 : 1
        const candidate = snap ? controller.snapBeat(raw) : raw
        const seconds = note && candidate >= note.start && candidate <= note.end ? candidate : raw
        const cents = extent * (1 - 2 * (y - pad) / Math.max(1, graph.height - 2 * pad))
        return [seconds, Math.max(-limit, Math.min(limit, Math.round(cents / step) * step))]
    }

    function nodeAt(x, y) {
        let best = null
        let threshold = Math.pow(8 * uiScale, 2)
        for (const row of manual) {
            for (let i = 0; i < row.offsets.length; ++i) {
                const pair = row.offsets[i]
                const dx = xForTime(pair[0]) - x
                const dy = yForValue(pair[1]) - y
                if (dx * dx + dy * dy <= threshold) {
                    threshold = dx * dx + dy * dy
                    best = {row: row, index: i}
                }
            }
        }
        return best
    }

    function resetPointer() {
        gesture = null
        pointer = null
        pointerTime = Number.NaN
        graph.requestPaint()
    }

    function switchTool(value) {
        resetPointer()
        tool = tool === value ? "" : value
        if (tool.length)
            showManual = true
    }

    function startEdit(mouse) {
        const raw = timeForX(mouse.x)
        const hit = tool === "nodes" ? nodeAt(mouse.x, mouse.y) : null
        const note = hit ? roll.visibleNotes.find(entry => entry.id === hit.row.noteId) : noteAt(raw)
        if (!note || !tool.length || !canEdit) {
            if (note)
                roll.selectNote(note)
            return
        }
        const pair = pointAt(mouse.x, mouse.y, mouse.modifiers, note)
        if (tool === "line")
            gesture = {note: note, first: pair}
        else {
            const row = hit ? hit.row : manual.find(entry => entry.noteId === note.id
                && entry.offsets[0][0] < raw && raw < entry.offsets[entry.offsets.length - 1][0])
            if (row)
                gesture = {note: note, row: row, index: hit ? hit.index : -1}
        }
        pointer = pair
        graph.requestPaint()
    }

    function finishEdit(mouse) {
        const current = gesture
        gesture = null
        if (!current)
            return
        let pair = pointAt(mouse.x, mouse.y, mouse.modifiers, current.note)
        if (current.row) {
            if (current.index < 0 &&
                    !(current.row.offsets[0][0] < pair[0] &&
                      pair[0] < current.row.offsets[current.row.offsets.length - 1][0]))
                pair[0] = timeForX(mouse.x)
            controller.editPitchNode(current.note.id, current.row.spanStart, current.index, pair[0], pair[1])
        } else {
            controller.drawPitchLine(current.note.id, current.first[0], current.first[1], pair[0], pair[1])
        }
        graph.requestPaint()
    }

    onPlotChanged: graph.requestPaint()
    onManualChanged: graph.requestPaint()
    onShowOriginalChanged: graph.requestPaint()
    onShowStyleChanged: graph.requestPaint()
    onShowManualChanged: graph.requestPaint()
    onExtentChanged: graph.requestPaint()
    onToolChanged: graph.requestPaint()
    onCanEditChanged: if (!canEdit) resetPointer()
    onVisibleChanged: if (!visible) resetPointer()

    Connections {
        target: lane.roll
        function onSourceKeyChanged() { lane.resetPointer() }
        function onActiveTrackChanged() { lane.resetPointer() }
        function onVisibleStartChanged() { lane.resetPointer() }
        function onVisibleSpanChanged() { lane.resetPointer() }
        function onSelectionStartChanged() { graph.requestPaint() }
        function onSelectionEndChanged() { graph.requestPaint() }
        function onBeatMarksChanged() { graph.requestPaint() }
        function onCursorTimeChanged() { graph.requestPaint() }
    }

    component LaneButton: UiButton {
        theme: lane.theme
        implicitHeight: Math.round(25 * lane.uiScale)
        leftPadding: Math.round(6 * lane.uiScale)
        rightPadding: leftPadding
        topPadding: 0
        bottomPadding: 0
        quiet: true
    }

    component CurveToggle: LaneButton {
        id: toggle
        required property color tint
        Accessible.checkable: true
        Accessible.checked: selected
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            height: Math.max(1, Math.round(lane.uiScale))
            width: Math.round(16 * lane.uiScale)
            color: toggle.tint
            opacity: toggle.selected ? 1 : 0.25
        }
    }

    ColumnLayout {
        id: controls
        anchors.fill: parent
        anchors.margins: Math.round(5 * lane.uiScale)
        spacing: Math.round(3 * lane.uiScale)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(4 * lane.uiScale)

            Text {
                text: lane.label("音高")
                color: lane.theme.text
                font.pixelSize: Math.round(12 * lane.uiScale)
                font.weight: Font.DemiBold
            }
            LaneButton {
                objectName: "tuningParameterLine"
                text: lane.label("直线")
                enabled: lane.canEdit
                selected: lane.tool === "line"
                ToolTip.visible: hovered || visualFocus
                ToolTip.text: lane.label("在一个音符内拖动，画出两点之间的直线。")
                onClicked: lane.switchTool("line")
            }
            LaneButton {
                objectName: "tuningParameterNodes"
                text: lane.label("节点")
                enabled: lane.canEdit && lane.manual.length > 0
                selected: lane.tool === "nodes"
                ToolTip.visible: hovered || visualFocus
                ToolTip.text: lane.label("单击手绘线插入节点，拖动已有节点。")
                onClicked: lane.switchTool("nodes")
            }
            UiComboBox {
                objectName: "tuningBeatDivision"
                theme: lane.theme
                implicitHeight: Math.round(25 * lane.uiScale)
                Layout.preferredWidth: Math.round(126 * lane.uiScale)
                leftPadding: Math.round(6 * lane.uiScale)
                rightPadding: Math.round(17 * lane.uiScale)
                font.pixelSize: Math.round(12 * lane.uiScale)
                readonly property var divisions: [0, 4, 8, 16]
                model: [
                    {label: lane.label("拍点关"), value: 0},
                    {label: "1/4", value: 4},
                    {label: "1/8", value: 8},
                    {label: "1/16", value: 16}
                ]
                currentIndex: Math.max(0, divisions.indexOf(Number(lane.view.beatDivision || 0)))
                enabled: !!lane.view.canUseBeatGrid
                fieldLabel: lane.label("音乐网格")
                onActivated: index => lane.controller.setBeatDivision(divisions[index])
            }
            Item { Layout.fillWidth: true }
            CurveToggle {
                objectName: "tuningParameterOriginal"
                text: lane.label("原曲线")
                selected: lane.showOriginal
                tint: lane.originalColor
                onClicked: lane.showOriginal = !lane.showOriginal
            }
            CurveToggle {
                objectName: "tuningParameterStyle"
                text: lane.label("风格线")
                selected: lane.showStyle
                tint: lane.styleColor
                ToolTip.visible: hovered || visualFocus
                ToolTip.text: lane.label("风格线随预览更新。")
                onClicked: lane.showStyle = !lane.showStyle
            }
            CurveToggle {
                objectName: "tuningParameterManual"
                text: lane.label("手绘")
                selected: lane.showManual
                tint: lane.manualColor
                onClicked: lane.showManual = !lane.showManual
            }
            LaneButton {
                objectName: "tuningParameterPreview"
                text: lane.label("预览")
                enabled: !!lane.view.canFreeze
                ToolTip.visible: hovered || visualFocus
                ToolTip.text: lane.label("核对当前参数并更新风格线。")
                onClicked: lane.controller.freeze()
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Canvas {
                id: graph
                objectName: "tuningParameterGraph"
                // The control lane and the piano roll share their physical x origin.
                x: -parent.x - controls.x
                width: lane.width
                height: parent.height

                function stroke(ctx, points, color, nodes) {
                    ctx.strokeStyle = color
                    ctx.lineWidth = 1.5 * lane.uiScale
                    ctx.beginPath()
                    let started = false
                    for (const p of points) {
                        if (!p) {
                            started = false
                            continue
                        }
                        const x = lane.xForTime(p[0]), y = lane.yForValue(p[1])
                        if (started) ctx.lineTo(x, y)
                        else ctx.moveTo(x, y)
                        started = true
                    }
                    ctx.stroke()
                    if (!nodes)
                        return
                    ctx.fillStyle = color
                    for (const p of points) {
                        const x = lane.xForTime(p[0])
                        if (x < lane.leftInset || x > lane.rightInset)
                            continue
                        ctx.beginPath()
                        ctx.arc(x, lane.yForValue(p[1]), 2.7 * lane.uiScale, 0, 2 * Math.PI)
                        ctx.fill()
                    }
                }

                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.clearRect(0, 0, width, height)
                    ctx.font = Math.round(10 * lane.uiScale) + "px sans-serif"
                    ctx.textBaseline = "middle"
                    for (const value of [lane.extent, 0, -lane.extent]) {
                        const y = lane.yForValue(value)
                        ctx.fillStyle = lane.theme.line
                        ctx.fillRect(lane.leftInset, y, lane.rightInset - lane.leftInset, 1)
                        ctx.fillStyle = lane.theme.muted
                        ctx.fillText(value > 0 ? "+" + value : String(value), 7 * lane.uiScale, y)
                    }
                    ctx.save()
                    ctx.beginPath()
                    ctx.rect(lane.leftInset, 0, lane.rightInset - lane.leftInset, height)
                    ctx.clip()
                    if (lane.roll.selectionEnd > lane.roll.selectionStart) {
                        const x = lane.xForTime(lane.roll.selectionStart)
                        ctx.fillStyle = lane.theme.accent
                        ctx.globalAlpha = 0.1
                        ctx.fillRect(x, 0, lane.xForTime(lane.roll.selectionEnd) - x, height)
                        ctx.globalAlpha = 1
                    }
                    ctx.fillStyle = lane.theme.line
                    for (const mark of lane.roll.beatMarks) {
                        ctx.globalAlpha = mark[1] ? 0.85 : 0.42
                        ctx.fillRect(Math.round(lane.xForTime(mark[0])), 0, 1, height)
                    }
                    ctx.globalAlpha = 1
                    if (lane.showOriginal)
                        for (const row of lane.plot.original || []) stroke(ctx, row.points, lane.originalColor, false)
                    if (lane.showStyle)
                        for (const row of lane.plot.style || []) stroke(ctx, row.points, lane.styleColor, false)
                    if (lane.showManual)
                        for (const row of lane.manual)
                            stroke(ctx, row.offsets, lane.manualColor, lane.tool === "nodes")
                    if (lane.gesture && lane.pointer) {
                        const g = lane.gesture
                        if (g.first)
                            stroke(ctx, [g.first, lane.pointer], lane.manualColor, true)
                        else if (g.index >= 0) {
                            const points = g.row.offsets.slice()
                            points[g.index] = lane.pointer
                            stroke(ctx, points, lane.manualColor, true)
                        }
                    }
                    if (isFinite(lane.roll.cursorTime)) {
                        ctx.fillStyle = lane.theme.accent
                        ctx.fillRect(lane.xForTime(lane.roll.cursorTime), 0, 1, height)
                    }
                    ctx.restore()
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: lane.tool.length && lane.canEdit ? Qt.CrossCursor : Qt.ArrowCursor
                    onPressed: mouse => {
                        if (mouse.x >= lane.leftInset && mouse.x <= lane.rightInset)
                            lane.startEdit(mouse)
                    }
                    onPositionChanged: mouse => {
                        lane.pointerTime = lane.timeForX(mouse.x)
                        lane.pointer = lane.pointAt(mouse.x, mouse.y, mouse.modifiers,
                            lane.gesture ? lane.gesture.note : null)
                        graph.requestPaint()
                    }
                    onReleased: mouse => lane.finishEdit(mouse)
                    onCanceled: lane.resetPointer()
                    onExited: if (!pressed) lane.resetPointer()
                    onWheel: wheel => {
                        const amount = wheel.angleDelta.y || wheel.pixelDelta.y
                        if (!amount || pressed) return
                        if (wheel.modifiers & Qt.ShiftModifier)
                            lane.roll.panBy(-amount / 120 * lane.roll.visibleSpan * 0.15)
                        else
                            lane.roll.zoomAt(amount > 0 ? 0.82 : 1.22, lane.timeForX(wheel.x))
                        wheel.accepted = true
                    }
                }
            }
        }
    }
}
