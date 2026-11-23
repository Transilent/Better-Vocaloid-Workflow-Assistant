import QtQuick

Rectangle {
    id: lane
    objectName: "chorusPitchLane"
    required property QtObject theme
    required property QtObject controller
    required property var view
    required property int headerWidth
    property var curve: ({first: 0, step: .01, midi: [], target: []})
    readonly property bool drawing: view.pitchDraw && canDraw
    property real lowNote: 48
    property real highNote: 72
    property var stroke: []
    readonly property bool canDraw: curve.midi.some(value => value >= 0) && view.take.role === "vocal"
    color: theme.base
    border.color: theme.line
    radius: Math.round(7 * theme.scale)
    clip: true
    implicitHeight: Math.round(178 * theme.scale)

    function rebuild() {
        if (!lane.visible)
            return
        curve = lane.controller.curve(lane.view.selected)
        let low = 128, high = 0
        for (const line of [curve.midi, curve.target]) {
            for (const note of line) {
                if (note < 0)
                    continue
                low = Math.min(low, note)
                high = Math.max(high, note)
            }
        }
        if (low <= high && stroke.length === 0) {
            lowNote = Math.max(0, Math.floor(low) - 2)
            highNote = Math.max(lowNote + 7, Math.ceil(high) + 2)
        }
        plot.requestPaint()
    }

    onVisibleChanged: rebuild()
    onStrokeChanged: plot.requestPaint()
    Connections {
        target: lane.controller
        function onChanged() { lane.rebuild() }
    }

    Canvas {
        id: plot
        anchors.fill: parent
        anchors.margins: 1
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const c = getContext("2d")
            c.reset()
            const widthOfPlot = Math.max(1, width - lane.headerWidth)
            const span = Math.max(.01, lane.view.viewSpan)
            const source = lane.curve.midi
            const target = lane.curve.target
            const low = lane.lowNote
            const high = lane.highNote
            const rowHeight = height / (high - low + 1)
            const keys = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
            c.fillStyle = lane.theme.raised
            c.fillRect(0, 0, lane.headerWidth, height)
            c.font = Math.round(11 * lane.theme.scale) + "px sans-serif"
            c.textBaseline = "middle"
            for (let note = low; note <= high; note++) {
                const y = (high - note) * rowHeight
                if ([1, 3, 6, 8, 10].includes((note + 120) % 12)) {
                    c.fillStyle = lane.theme.surface
                    c.fillRect(0, y, width, rowHeight)
                }
                c.fillStyle = lane.theme.line
                c.fillRect(0, y, width, 1)
                if (rowHeight > 12 * lane.theme.scale || note % 12 === 0) {
                    c.fillStyle = lane.theme.muted
                    c.fillText(keys[(note + 120) % 12] + (Math.floor(note / 12) - 1),
                               10, y + rowHeight / 2)
                }
            }
            c.fillStyle = lane.theme.line
            c.fillRect(lane.headerWidth, 0, 1, height)
            c.save()
            c.beginPath()
            c.rect(lane.headerWidth, 0, widthOfPlot, height)
            c.clip()
            function stroke(values, color, thick, dashed) {
                c.strokeStyle = color
                c.lineWidth = thick
                c.setLineDash(dashed ? [4, 4] : [])
                c.beginPath()
                let pending = true
                for (let i = 0; i < values.length; i++) {
                    if (values[i] < 0) {
                        pending = true
                        continue
                    }
                    const at = lane.curve.first + i * lane.curve.step
                    const x = lane.headerWidth + (at - lane.view.viewFirst) * widthOfPlot / span
                    const y = (high - values[i] + .5) * rowHeight
                    if (pending)
                        c.moveTo(x, y)
                    else
                        c.lineTo(x, y)
                    pending = false
                }
                c.stroke()
            }
            stroke(source, lane.theme.muted, 1.1, true)
            stroke(target, lane.theme.accent, 1.8, false)
            if (lane.stroke.length) {
                c.strokeStyle = lane.theme.success
                c.lineWidth = 2.4
                c.beginPath()
                for (let i = 0; i < lane.stroke.length; i++) {
                    const dot = lane.stroke[i]
                    const x = lane.headerWidth + (dot.time - lane.view.viewFirst) * widthOfPlot / span
                    const y = (high - dot.note + .5) * rowHeight
                    if (i)
                        c.lineTo(x, y)
                    else
                        c.moveTo(x, y)
                }
                c.stroke()
            }
            c.restore()
        }
    }
    Text {
        anchors.centerIn: parent
        visible: !lane.curve.midi.some(value => value >= 0)
        color: lane.theme.muted
        font.pixelSize: Math.round(13 * lane.theme.scale)
        text: quickTranslation.text("分析修音后显示此轨的实测音高")
    }
    Item {
        x: lane.headerWidth + 1
        width: Math.max(1, parent.width - x - 1)
        height: parent.height
        clip: true
        Rectangle {
            x: (lane.controller.position - lane.view.viewFirst) * parent.width / Math.max(.01, lane.view.viewSpan)
            width: 1
            height: parent.height
            color: lane.theme.accent
        }
        MouseArea {
            objectName: "chorusPitchDrawArea"
            anchors.fill: parent
            cursorShape: lane.drawing ? Qt.CrossCursor : Qt.ArrowCursor
            preventStealing: true
            property real startTime
            property real pressX
            function at(x) { return lane.view.viewFirst + Math.max(0, Math.min(1, x / width)) * lane.view.viewSpan }
            function dot(x, y) {
                return {time: at(x), note: lane.highNote + .5 - Math.max(0, Math.min(1, y / height))
                    * (lane.highNote - lane.lowNote + 1)}
            }
            onPressed: mouse => {
                pressX = mouse.x
                startTime = at(mouse.x)
                if (lane.drawing)
                    lane.stroke = [dot(mouse.x, mouse.y)]
            }
            onPositionChanged: mouse => {
                if (pressed && lane.drawing)
                    lane.stroke = lane.stroke.concat([dot(mouse.x, mouse.y)])
            }
            onReleased: mouse => {
                const end = at(mouse.x)
                if (lane.drawing) {
                    const captured = lane.stroke.concat([dot(mouse.x, mouse.y)])
                    lane.stroke = []
                    lane.controller.drawPitch(captured)
                } else if (Math.abs(mouse.x - pressX) >= 4)
                    lane.controller.selectRange(startTime, end)
                else
                    lane.controller.seek(end)
            }
            onCanceled: lane.stroke = []
        }
    }
}
