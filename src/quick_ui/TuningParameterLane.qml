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
    readonly property bool hasStrokes: Number(view.pitchStrokeCount || 0) > 0
    readonly property bool rangeTool: tool === "freehand" || tool === "erase"
    readonly property var brushWindows: rangeTool && canEdit
        ? roll.visibleNotes.filter(note => !note.drawReason) : []
    readonly property real pitchLimit: Number(view.pitchMaxCents || 200)
    readonly property real extent: plotExtent()
    readonly property real inspectionTime: visible && isFinite(pointerTime) ? pointerTime : Number.NaN
    property bool showOriginal: true
    property bool showStyle: true
    property bool showManual: true
    property string tool: "line"
    property var gesture: null
    property var pointer: null
    property real pointerTime: Number.NaN
    property var hoverNote: null
    property var hoverNode: null
    property var menuNode: null

    color: theme.base
    radius: Math.round(6 * uiScale)
    border.color: theme.line
    border.width: 1
    implicitHeight: Math.round(150 * uiScale)
    clip: true

    function label(text) { return roll.vwaTr(text) }

    function plotExtent() {
        let peak = pitchLimit
        if (tool !== "inspect" && canEdit)
            return peak
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
        return candidates.length === 1 ? candidates[0]
            : candidates.find(note => note.id === (view.selectedNote || {}).id) || null
    }

    function pointAt(x, y, modifiers, note) {
        const raw = timeForX(roll.clamp(x, leftInset, rightInset))
        const pad = 10 * uiScale
        const snap = view.snapEnabled && !(modifiers & Qt.AltModifier)
        const step = snap ? 10 : 1
        const edge = Number(view.pitchEdgeSeconds || .018)
        const low = note ? note.start + edge : roll.origin
        const high = note ? note.end - edge : roll.fullEnd
        const seconds = roll.clamp(raw, low, high)
        const cents = extent * (1 - 2 * (y - pad) / Math.max(1, graph.height - 2 * pad))
        return [seconds, roll.clamp(Math.round(cents / step) * step, -pitchLimit, pitchLimit)]
    }

    function nodeAt(x, y) {
        if (!showManual)
            return null
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

    function strokeAt(x, y) {
        const raw = timeForX(x)
        for (const row of manual) {
            const points = row.offsets
            if (raw <= points[0][0] || raw >= points[points.length - 1][0])
                continue
            const next = points.findIndex(pair => pair[0] > raw)
            const a = points[next - 1], b = points[next]
            const cents = a[1] + (b[1] - a[1]) * (raw - a[0]) / (b[0] - a[0])
            if (Math.abs(yForValue(cents) - y) <= 7 * uiScale)
                return {row: row, index: -1, pair: [raw, cents]}
        }
        return null
    }

    function resetPointer() {
        gesture = null
        pointer = null
        pointerTime = Number.NaN
        hoverNote = hoverNode = null
        graph.requestPaint()
    }

    function switchTool(value) {
        resetPointer()
        tool = value
        if (value !== "inspect")
            showManual = true
    }

    function startEdit(mouse) {
        graph.forceActiveFocus(Qt.MouseFocusReason)
        if (mouse.button === Qt.MiddleButton) {
            gesture = {pan: roll.beginPan(mouse.x, mouse.y)}
            return
        }
        if (mouse.x < leftInset || mouse.x > rightInset)
            return
        const raw = timeForX(mouse.x)
        const hit = tool === "nodes" ? nodeAt(mouse.x, mouse.y) || strokeAt(mouse.x, mouse.y) : null
        const note = hit ? roll.visibleNotes.find(entry => entry.id === hit.row.noteId) : noteAt(raw)
        if (mouse.button === Qt.RightButton) {
            if (canEdit && hit && hit.index >= 0 && note && !note.drawReason) {
                menuNode = hit
                nodeMenu.x = mouse.x
                nodeMenu.y = graph.y + controls.y + mouse.y
                nodeMenu.open()
            }
            return
        }
        if (note) roll.selectNote(note)
        if (!canEdit || tool === "inspect" || (!rangeTool && (!note || note.drawReason)))
            return
        const pair = pointAt(mouse.x, mouse.y, mouse.modifiers, rangeTool ? null : note)
        if (tool === "nodes") {
            if (hit) gesture = {note: note, kind: tool, hit: hit, x: mouse.x, y: mouse.y,
                initial: hit.index >= 0 ? hit.row.offsets[hit.index] : hit.pair}
        } else {
            gesture = {note: rangeTool ? null : note, kind: tool, track: roll.activeTrack,
                x: mouse.x, y: mouse.y, first: pair, points: [pair]}
        }
        pointer = gesture && gesture.initial ? gesture.initial : pair
        graph.requestPaint()
    }

    function dragPoint(current, mouse) {
        if (current.kind !== "nodes" || current.hit.index < 0)
            return pointAt(mouse.x, mouse.y, mouse.modifiers, current.note)
        const idx = current.hit.index
        const points = current.hit.row.offsets
        const x = xForTime(current.initial[0]) + mouse.x - current.x
        const y = yForValue(current.initial[1]) + mouse.y - current.y
        const pair = pointAt(x, y, mouse.modifiers, current.note)
        const low = idx === 0 ? current.note.start : points[idx - 1][0] + .000001
        const high = idx === points.length - 1 ? current.note.end : points[idx + 1][0] - .000001
        if ((mouse.modifiers & Qt.ShiftModifier) || Math.abs(mouse.x - current.x) < 2 * uiScale)
            pair[0] = current.initial[0]
        if (Math.abs(mouse.y - current.y) < 2 * uiScale) pair[1] = current.initial[1]
        pair[0] = roll.clamp(pair[0], low, high)
        return pair
    }

    function trackPointer(mouse) {
        if (gesture && gesture.pan) {
            roll.movePan(gesture.pan, mouse.x, mouse.y)
            return
        }
        if (!gesture && (mouse.x < leftInset || mouse.x > rightInset)) {
            resetPointer()
            return
        }
        pointerTime = timeForX(mouse.x)
        if (gesture) {
            pointer = dragPoint(gesture, mouse)
            if (gesture.kind === "freehand") {
                const points = gesture.points
                const last = points[points.length - 1]
                if (Math.abs(xForTime(pointer[0]) - xForTime(last[0])) >= 2 * uiScale
                        || Math.abs(pointer[1] - last[1]) >= 4) {
                    if (points.length < 2047) points.push(pointer)
                    else points[points.length - 1] = pointer
                }
            }
        } else {
            hoverNote = noteAt(pointerTime)
            hoverNode = tool === "nodes" ? nodeAt(mouse.x, mouse.y) : null
        }
        graph.requestPaint()
    }

    function finishEdit(mouse) {
        const current = gesture
        gesture = null
        if (!current)
            return
        if (current.pan) {
            roll.movePan(current.pan, mouse.x, mouse.y)
            return
        }
        const moved = Math.hypot(mouse.x - current.x, mouse.y - current.y) >= 3 * uiScale
        const pair = dragPoint(current, mouse)
        if (current.kind === "nodes") {
            const hit = current.hit
            if ((hit.index < 0 && !moved) || (hit.index >= 0 && moved)) {
                const value = hit.index < 0 ? hit.pair : pair
                controller.editPitchNode(current.note.id, hit.row.spanStart, hit.index, value[0], value[1])
            }
        } else if (current.kind === "erase") {
            controller.brushPitch(current.track, "erase", moved ? [current.first, pair] : [current.first])
        } else if (moved) {
            const samples = current.kind === "line" ? [current.first, pair] : [...current.points, pair]
            const times = samples.map(point => point[0])
            if (Math.max(...times) - Math.min(...times) >= Number(view.pitchStrokeMinimum || .012)) {
                if (current.kind === "line")
                    controller.drawPitchLine(current.note.id, current.first[0], current.first[1], pair[0], pair[1])
                else
                    controller.brushPitch(current.track, "draw", samples)
            }
        }
        graph.requestPaint()
    }

    onPlotChanged: graph.requestPaint()
    onManualChanged: graph.requestPaint()
    onHasStrokesChanged: if (!hasStrokes && (tool === "nodes" || tool === "erase")) switchTool("line")
    onShowOriginalChanged: graph.requestPaint()
    onShowStyleChanged: graph.requestPaint()
    onShowManualChanged: {
        if (!showManual) switchTool("inspect")
        graph.requestPaint()
    }
    onExtentChanged: graph.requestPaint()
    onToolChanged: graph.requestPaint()
    onCanEditChanged: if (!canEdit) resetPointer()
    onVisibleChanged: if (!visible) resetPointer()

    Connections {
        target: lane.roll
        function onSourceKeyChanged() { lane.switchTool("line") }
        function onActiveTrackChanged() { lane.resetPointer() }
        function onVisibleStartChanged() { if (!lane.gesture || !lane.gesture.pan) lane.resetPointer() }
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

    UiMenu {
        id: nodeMenu
        theme: lane.theme
        MenuItem {
            objectName: "tuningDeletePitchNode"
            text: lane.label("删除节点")
            onTriggered: {
                const node = lane.menuNode
                if (node) lane.controller.removePitchNode(node.row.noteId, node.row.spanStart, node.index)
            }
        }
        onClosed: lane.menuNode = null
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
            Repeater {
                model: [
                    {id: "Inspect", tool: "inspect", title: "查看", hint: "查看曲线全幅。"},
                    {id: "Freehand", tool: "freehand", title: "曲线笔", hint: "拖动绘制平滑音高曲线，可一笔跨多个音符。按 Alt 细调。"},
                    {id: "Line", tool: "line", title: "直线", hint: "在一个音符内拖动，画出两点之间的直线。"},
                    {id: "Nodes", tool: "nodes", title: "节点", hint: "点击线条插入节点。拖动调整，按 Shift 锁定时间，右键删除。"},
                    {id: "Erase", tool: "erase", title: "橡皮", hint: "单击擦除整段手绘，拖动按时间擦除，可跨音符。"}
                ]
                LaneButton {
                    required property var modelData
                    objectName: "tuningParameter" + modelData.id
                    text: lane.label(modelData.title)
                    enabled: modelData.tool === "inspect" || (lane.canEdit
                        && ((modelData.tool !== "nodes" && modelData.tool !== "erase") || lane.hasStrokes))
                    selected: lane.tool === modelData.tool
                    Accessible.checkable: true
                    Accessible.checked: selected
                    ToolTip.visible: hovered || visualFocus
                    ToolTip.text: lane.label(modelData.hint)
                    onClicked: lane.switchTool(modelData.tool)
                }
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
                text: lane.label("工程线")
                selected: lane.showOriginal
                tint: lane.originalColor
                ToolTip.visible: hovered || visualFocus
                ToolTip.text: lane.label("查看工程音高偏移、音符弯音和当前微调与颤音。")
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
                activeFocusOnTab: true
                Accessible.name: lane.label("音高参数轨")

                function stroke(ctx, points, color, nodes, anchorsOnly) {
                    ctx.strokeStyle = color
                    ctx.lineWidth = 1.5 * lane.uiScale
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
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
                    if (!anchorsOnly)
                        ctx.stroke()
                    if (!nodes)
                        return
                    ctx.fillStyle = color
                    for (const p of points) {
                        if (!p)
                            continue
                        const x = lane.xForTime(p[0])
                        if (x < lane.leftInset || x > lane.rightInset)
                            continue
                        ctx.beginPath()
                        ctx.arc(x, lane.yForValue(p[1]), 2.7 * lane.uiScale, 0, 2 * Math.PI)
                        ctx.fill()
                    }
                }

                function brushPreview(ctx, points) {
                    ctx.save()
                    ctx.globalAlpha = .25
                    stroke(ctx, points, lane.manualColor, false)
                    ctx.globalAlpha = 1
                    ctx.beginPath()
                    const guard = Number(lane.view.pitchEdgeSeconds || .018)
                    for (const note of lane.brushWindows) {
                        const left = lane.xForTime(note.start + guard)
                        const right = lane.xForTime(note.end - guard)
                        if (right > left && right >= lane.leftInset && left <= lane.rightInset)
                            ctx.rect(left, 0, right - left, height)
                    }
                    ctx.clip()
                    stroke(ctx, points, lane.manualColor, false)
                    ctx.restore()
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
                        ctx.globalAlpha = mark[1] === 2 ? 0.85 : mark[1] === 1 ? 0.6 : 0.32
                        ctx.fillRect(Math.round(lane.xForTime(mark[0])), 0, 1, height)
                    }
                    ctx.globalAlpha = 1
                    if (lane.showOriginal)
                        for (const row of lane.plot.original || [])
                            stroke(ctx, row.points, lane.originalColor, !!row.anchors, !!row.anchors)
                    if (lane.showStyle)
                        for (const row of lane.plot.style || [])
                            stroke(ctx, row.points, lane.styleColor, !!row.anchors, !!row.anchors)
                    const g = lane.gesture
                    if (g && g.note) {
                        ctx.fillStyle = lane.theme.accent
                        ctx.globalAlpha = .09
                        ctx.fillRect(lane.xForTime(g.note.start), 0,
                            lane.xForTime(g.note.end) - lane.xForTime(g.note.start), height)
                        ctx.globalAlpha = 1
                    }
                    if (lane.showManual) {
                        for (const row of lane.manual) {
                            const moving = g && g.hit && g.hit.index >= 0 && g.hit.row === row
                            const points = moving ? row.offsets.slice() : row.offsets
                            if (moving) points[g.hit.index] = lane.pointer || g.initial
                            stroke(ctx, points, lane.manualColor, lane.tool === "nodes")
                        }
                    }
                    if (g && lane.pointer && g.kind === "line")
                        stroke(ctx, [g.first, lane.pointer], lane.manualColor, true)
                    if (g && lane.pointer && g.kind === "freehand")
                        brushPreview(ctx, [...g.points, lane.pointer])
                    if (g && lane.pointer && g.kind === "erase") {
                        const first = lane.xForTime(g.first[0]), last = lane.xForTime(lane.pointer[0])
                        ctx.fillStyle = lane.theme.error
                        ctx.globalAlpha = .28
                        ctx.fillRect(Math.min(first, last), 0, Math.max(2, Math.abs(first - last)), height)
                        ctx.globalAlpha = 1
                    }
                    if (isFinite(lane.roll.cursorTime)) {
                        ctx.fillStyle = lane.theme.accent
                        ctx.fillRect(lane.xForTime(lane.roll.cursorTime), 0, 1, height)
                    }
                    ctx.restore()
                }

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                    preventStealing: true
                    hoverEnabled: true
                    cursorShape: lane.gesture && lane.gesture.pan ? Qt.ClosedHandCursor
                        : lane.hoverNode ? Qt.SizeAllCursor
                        : !lane.canEdit || lane.tool === "inspect" ? Qt.ArrowCursor
                        : lane.hoverNote && lane.hoverNote.drawReason ? Qt.ForbiddenCursor : Qt.CrossCursor
                    ToolTip.visible: containsMouse && !pressed && (!!lane.view.pitchEditReason
                        || (lane.hoverNote && !!lane.hoverNote.drawReason))
                    ToolTip.delay: 600
                    ToolTip.text: lane.label(lane.view.pitchEditReason || (lane.hoverNote ? lane.hoverNote.drawReason : ""))
                    onPressed: mouse => lane.startEdit(mouse)
                    onPositionChanged: mouse => lane.trackPointer(mouse)
                    onReleased: mouse => lane.finishEdit(mouse)
                    onCanceled: lane.resetPointer()
                    onExited: if (!pressed) lane.resetPointer()
                    onWheel: wheel => wheel.accepted = pressed || lane.roll.viewportWheel(wheel, wheel.x - lane.leftInset)
                }
            }
        }
    }
}
