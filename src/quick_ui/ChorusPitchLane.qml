import QtQuick
import QtQuick.Controls

Rectangle {
    id: lane
    objectName: "chorusPitchLane"
    required property QtObject theme
    required property QtObject controller
    required property var view
    required property int headerWidth
    property var curve: ({first: 0, step: .01, midi: [], target: [], notes: [], scoreNotes: []})
    readonly property string tool: view.pitchTool || (view.pitchDraw ? "draw" : "note")
    readonly property bool drawing: tool === "draw" && canDraw
    readonly property bool splitting: tool === "split" && hasNotes
    readonly property bool hasNotes: (curve.notes || []).length > 0
    readonly property bool hasScoreNotes: (curve.scoreNotes || []).length > 0
    readonly property bool canDraw: (curve.midi || []).some(value => value >= 0) && view.take.role === "vocal"
    property real lowNote: 48
    property real highNote: 72
    property bool manualPitchRange: false
    property string lastTrackId: ""
    property var stroke: []
    property var selectedKeys: []
    property bool draggingNote: false
    property real dragDeltaSemitones: 0
    property real dragAnchorMidi: 60
    property real dragMouseX: 0
    property real dragMouseY: 0
    property bool marqueeActive: false
    property real marqueeX1: 0
    property real marqueeY1: 0
    property real marqueeX2: 0
    property real marqueeY2: 0
    property real hoverX: -1
    property real hoverY: -1
    property var hoveredNote: null
    property var contextNote: null
    property real contextTime: 0

    color: theme.base
    border.color: theme.line
    radius: Math.round(7 * theme.scale)
    clip: true
    implicitHeight: Math.round(210 * theme.scale)

    function t(text) { return quickTranslation.text(text) }

    function noteName(midi) {
        const keys = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
        const nearest = Math.round(midi)
        const cents = Math.round((midi - nearest) * 100)
        const label = keys[((nearest % 12) + 12) % 12] + (Math.floor(nearest / 12) - 1)
        if (cents === 0)
            return label
        return label + " " + (cents > 0 ? "+" : "") + cents + "¢"
    }

    function isSelected(note) {
        return selectedKeys.indexOf(note.id) >= 0
    }

    function selectedSpans() {
        const list = []
        const notes = curve.notes || []
        for (let i = 0; i < notes.length; i++) {
            if (isSelected(notes[i]))
                list.push({takeStart: notes[i].takeStart, takeEnd: notes[i].takeEnd})
        }
        return list
    }

    function plotWidth() {
        return Math.max(1, plot.width - lane.headerWidth)
    }

    function rowHeight() {
        return plot.height / Math.max(1, highNote - lowNote + 1)
    }

    function timeToPlotX(time) {
        const span = Math.max(.01, lane.view.viewSpan)
        return (time - lane.view.viewFirst) * plotWidth() / span
    }

    function plotXToTime(px) {
        const w = plotWidth()
        return lane.view.viewFirst + Math.max(0, Math.min(1, px / w)) * lane.view.viewSpan
    }

    function midiToY(midi) {
        return (highNote - midi + .5) * rowHeight()
    }

    function noteBox(note) {
        const rh = rowHeight()
        const x1 = timeToPlotX(note.start)
        const x2 = timeToPlotX(note.end)
        const activeDelta = draggingNote && isSelected(note) ? dragDeltaSemitones : 0
        const centerY = midiToY(note.targetMidi + activeDelta)
        const h = Math.max(Math.round(11 * theme.scale), Math.min(rh * .86, Math.round(22 * theme.scale)))
        return {
            x: x1,
            y: centerY - h / 2,
            w: Math.max(5, x2 - x1),
            h: h,
            centerY: centerY,
        }
    }

    function noteAt(px, py) {
        const notes = curve.notes || []
        for (let i = notes.length - 1; i >= 0; i--) {
            const box = noteBox(notes[i])
            if (px >= box.x - 2 && px <= box.x + box.w + 2 && py >= box.y - 3 && py <= box.y + box.h + 3)
                return notes[i]
        }
        return null
    }

    function noteShortName(midi) {
        const keys = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
        const nearest = Math.round(midi)
        return keys[((nearest % 12) + 12) % 12] + (Math.floor(nearest / 12) - 1)
    }

    function scrollPitch(semitones) {
        const span = highNote - lowNote
        const nextLow = Math.max(24, Math.min(96 - span, lowNote + semitones))
        lowNote = Math.round(nextLow)
        highNote = lowNote + span
        manualPitchRange = true
        plot.requestPaint()
    }

    function zoomPitch(factor, anchorMidi) {
        const oldSpan = Math.max(8, highNote - lowNote)
        const nextSpan = Math.max(8, Math.min(48, Math.round(oldSpan * factor)))
        if (nextSpan === oldSpan)
            return
        const ratio = Math.max(0, Math.min(1, (anchorMidi - lowNote) / oldSpan))
        const nextLow = Math.max(24, Math.min(96 - nextSpan, Math.round(anchorMidi - ratio * nextSpan)))
        lowNote = nextLow
        highNote = nextLow + nextSpan
        manualPitchRange = true
        plot.requestPaint()
    }

    function resetPitchRange() {
        manualPitchRange = false
        rebuild()
    }

    function rebuild() {
        if (!lane.visible)
            return
        if (lastTrackId !== lane.view.selected) {
            lastTrackId = lane.view.selected || ""
            manualPitchRange = false
        }
        curve = lane.controller.curve(lane.view.selected)
        const notes = curve.notes || []
        const synced = []
        for (let i = 0; i < notes.length; i++) {
            if (notes[i].selected)
                synced.push(notes[i].id)
        }
        if (!draggingNote)
            selectedKeys = synced

        let low = 128, high = 0
        for (const line of [curve.midi || [], curve.target || []]) {
            for (const val of line) {
                if (val < 0)
                    continue
                low = Math.min(low, val)
                high = Math.max(high, val)
            }
        }
        for (const sn of (curve.scoreNotes || [])) {
            low = Math.min(low, sn.pitch)
            high = Math.max(high, sn.pitch)
        }
        if (low <= high && stroke.length === 0 && !draggingNote && !manualPitchRange) {
            lowNote = Math.max(0, Math.floor(low) - 2)
            highNote = Math.max(lowNote + 8, Math.ceil(high) + 2)
        }
        plot.requestPaint()
    }

    onVisibleChanged: rebuild()
    onStrokeChanged: plot.requestPaint()
    onDragDeltaSemitonesChanged: plot.requestPaint()
    onDraggingNoteChanged: plot.requestPaint()
    onMarqueeActiveChanged: plot.requestPaint()
    onHoverXChanged: if (splitting) plot.requestPaint()
    onToolChanged: plot.requestPaint()
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
            const source = lane.curve.midi || []
            const target = lane.curve.target || []
            const notes = lane.curve.notes || []
            const scoreNotes = lane.curve.scoreNotes || []
            const low = lane.lowNote
            const high = lane.highNote
            const rh = height / Math.max(1, high - low + 1)
            const keys = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]

            // 1. Piano key gutter and pitch grid
            c.fillStyle = lane.theme.raised
            c.fillRect(0, 0, lane.headerWidth, height)
            c.font = Math.round(11 * lane.theme.scale) + "px sans-serif"
            c.textBaseline = "middle"
            const activeDragRow = lane.draggingNote ? Math.round(lane.dragAnchorMidi + lane.dragDeltaSemitones) : -999
            for (let note = low; note <= high; note++) {
                const y = (high - note) * rh
                if (note === activeDragRow) {
                    c.fillStyle = lane.theme.accent
                    c.globalAlpha = .15
                    c.fillRect(0, y, width, rh)
                    c.globalAlpha = 1
                } else if ([1, 3, 6, 8, 10].includes((note + 120) % 12)) {
                    c.fillStyle = lane.theme.surface
                    c.fillRect(0, y, width, rh)
                }
                c.fillStyle = lane.theme.line
                c.fillRect(0, y, width, 1)
                if (rh > 12 * lane.theme.scale || note % 12 === 0 || note === activeDragRow) {
                    c.fillStyle = note === activeDragRow ? lane.theme.accent : lane.theme.muted
                    c.fillText(keys[(note + 120) % 12] + (Math.floor(note / 12) - 1),
                               10, y + rh / 2)
                }
            }
            c.fillStyle = lane.theme.line
            c.fillRect(lane.headerWidth, 0, 1, height)

            c.save()
            c.beginPath()
            c.rect(lane.headerWidth, 0, widthOfPlot, height)
            c.clip()

            function tx(t) { return lane.headerWidth + (t - lane.view.viewFirst) * widthOfPlot / span }
            function py(m) { return (high - m + .5) * rh }

            // 1b. Selection / loop range highlight
            const selA = tx(lane.view.rangeFirst)
            const selB = tx(lane.view.rangeLast)
            if (selB - selA > 1 && selB - selA < widthOfPlot - 1) {
                c.fillStyle = lane.theme.accent
                c.globalAlpha = .08
                c.fillRect(selA, 0, selB - selA, height)
                c.globalAlpha = .35
                c.fillRect(selA, 0, 1, height)
                c.fillRect(selB, 0, 1, height)
                c.globalAlpha = 1
            }

            // 2. Bound score lane target notes (outline blocks behind vocal notes)
            if (scoreNotes.length > 0) {
                c.font = Math.round(10 * lane.theme.scale) + "px sans-serif"
                for (let i = 0; i < scoreNotes.length; i++) {
                    const sn = scoreNotes[i]
                    const sx = tx(sn.start)
                    const sw = Math.max(4, tx(sn.end) - sx)
                    const sh = Math.max(8, rh * .78)
                    const sy = py(sn.pitch) - sh / 2
                    c.fillStyle = lane.theme.success
                    c.globalAlpha = .13
                    c.fillRect(sx, sy, sw, sh)
                    c.globalAlpha = .72
                    c.strokeStyle = lane.theme.success
                    c.lineWidth = 1.2
                    c.setLineDash([4, 3])
                    c.strokeRect(sx + .5, sy + .5, Math.max(1, sw - 1), Math.max(1, sh - 1))
                    c.setLineDash([])
                    if (sn.lyric && sw > 16 * lane.theme.scale) {
                        c.fillStyle = lane.theme.success
                        c.globalAlpha = .92
                        c.fillText(sn.lyric, sx + 4, sy + sh / 2)
                    }
                }
                c.globalAlpha = 1
            }

            // 3. Editable vocal note blocks
            const blockH = Math.max(Math.round(11 * lane.theme.scale), Math.min(rh * .86, Math.round(22 * lane.theme.scale)))
            for (let i = 0; i < notes.length; i++) {
                const n = notes[i]
                const sel = lane.isSelected(n)
                const activeDelta = lane.draggingNote && sel ? lane.dragDeltaSemitones : 0
                const x1 = tx(n.start)
                const x2 = tx(n.end)
                const bw = Math.max(5, x2 - x1)
                const origY = py(n.centerMidi)
                const targetY = py(n.targetMidi + activeDelta)

                // Ghost shadow at original pitch center when shifted
                if (Math.abs((n.targetMidi + activeDelta) - n.centerMidi) > .04) {
                    c.fillStyle = lane.theme.muted
                    c.globalAlpha = .16
                    c.fillRect(x1, origY - blockH * .38, bw, blockH * .76)
                    c.strokeStyle = lane.theme.muted
                    c.globalAlpha = .35
                    c.lineWidth = 1
                    c.beginPath()
                    c.moveTo(x1 + bw / 2, origY)
                    c.lineTo(x1 + bw / 2, targetY)
                    c.stroke()
                }

                // Main vocal note block
                const by = targetY - blockH / 2
                c.fillStyle = sel ? lane.theme.accent : n.locked ? lane.theme.raised : n.manual ? lane.theme.accent : lane.theme.selected
                c.globalAlpha = sel ? .34 : n.locked ? .55 : n.manual ? .22 : .42
                c.fillRect(x1, by, bw, blockH)

                c.globalAlpha = sel ? 1.0 : .78
                c.strokeStyle = sel ? lane.theme.accent : n.locked ? lane.theme.muted : lane.theme.selectedLine
                c.lineWidth = sel ? 2.0 : 1.1
                c.setLineDash(n.locked ? [3, 2] : [])
                c.strokeRect(x1 + .5, by + .5, Math.max(1, bw - 1), Math.max(1, blockH - 1))
                c.setLineDash([])

                // Continuous legato link tick at right edge
                if (n.linkedNext) {
                    c.fillStyle = lane.theme.accent
                    c.globalAlpha = .55
                    c.fillRect(x2 - 1, targetY - 3, 2, 6)
                }

                // Inline pitch / cent readout on wider note blocks
                if (bw >= Math.round(28 * lane.theme.scale)) {
                    const shownMidi = n.targetMidi + activeDelta
                    const shownOffset = Math.round(n.offsetCents + activeDelta * 100)
                    let noteText = lane.noteShortName(shownMidi)
                    if (bw >= Math.round(64 * lane.theme.scale) && Math.abs(shownOffset) >= 1)
                        noteText += " " + (shownOffset > 0 ? "+" : "") + shownOffset + "¢"
                    c.fillStyle = lane.theme.text
                    c.globalAlpha = sel ? .96 : .82
                    c.font = Math.round(10 * lane.theme.scale) + "px sans-serif"
                    c.fillText(noteText, x1 + 4, targetY)
                }

                // Match-review warning dot
                if (n.matchReview && n.scorePitch >= 0) {
                    c.fillStyle = lane.theme.attention
                    c.globalAlpha = .95
                    c.beginPath()
                    c.arc(x1 + bw - 5, by + 5, 3, 0, Math.PI * 2)
                    c.fill()
                }
            }
            c.globalAlpha = 1

            // 4. Raw vocal F0 (dashed) and compiled rendered pitch curve (solid)
            function drawCurve(values, color, thick, dashed, applyDrag) {
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
                    let val = values[i]
                    if (applyDrag && lane.draggingNote && Math.abs(lane.dragDeltaSemitones) > 1e-4) {
                        for (let j = 0; j < notes.length; j++) {
                            const n = notes[j]
                            if (lane.isSelected(n) && at >= n.start && at <= n.end) {
                                val += lane.dragDeltaSemitones
                                break
                            }
                        }
                    }
                    const x = tx(at)
                    const y = py(val)
                    if (pending)
                        c.moveTo(x, y)
                    else
                        c.lineTo(x, y)
                    pending = false
                }
                c.stroke()
                c.setLineDash([])
            }
            drawCurve(source, lane.theme.muted, 1.1, true, false)
            drawCurve(target, lane.theme.accent, 1.9, false, true)

            // 5. Split tool hover indicator
            if (lane.splitting && lane.hoverX >= 0) {
                const hit = lane.noteAt(lane.hoverX, lane.hoverY)
                if (hit) {
                    const gx = lane.headerWidth + lane.hoverX
                    c.strokeStyle = lane.theme.attention
                    c.lineWidth = 1.6
                    c.setLineDash([4, 3])
                    c.beginPath()
                    c.moveTo(gx, 0)
                    c.lineTo(gx, height)
                    c.stroke()
                    c.setLineDash([])
                }
            }

            // 6. Marquee selection box
            if (lane.marqueeActive) {
                const mx = lane.headerWidth + Math.min(lane.marqueeX1, lane.marqueeX2)
                const my = Math.min(lane.marqueeY1, lane.marqueeY2)
                const mw = Math.abs(lane.marqueeX2 - lane.marqueeX1)
                const mh = Math.abs(lane.marqueeY2 - lane.marqueeY1)
                c.fillStyle = lane.theme.accent
                c.globalAlpha = .14
                c.fillRect(mx, my, mw, mh)
                c.globalAlpha = .8
                c.strokeStyle = lane.theme.accent
                c.lineWidth = 1
                c.strokeRect(mx + .5, my + .5, mw, mh)
                c.globalAlpha = 1
            }

            // 7. Hand-drawn pitch stroke preview
            if (lane.stroke.length) {
                c.strokeStyle = lane.theme.success
                c.lineWidth = 2.4
                c.beginPath()
                for (let i = 0; i < lane.stroke.length; i++) {
                    const dot = lane.stroke[i]
                    const x = tx(dot.time)
                    const y = py(dot.note)
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
        visible: !lane.canDraw && !lane.hasScoreNotes
        color: lane.theme.muted
        font.pixelSize: Math.round(13 * lane.theme.scale)
        text: lane.t("分析修音后显示此轨的实测音高")
    }

    // Subtle status pill for hovered/selected vocal note or score match reason
    Rectangle {
        visible: !lane.draggingNote && !!lane.hoveredNote
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 6
        radius: Math.round(5 * lane.theme.scale)
        color: lane.theme.surface
        border.color: lane.theme.line
        implicitWidth: infoLabel.implicitWidth + 14
        implicitHeight: infoLabel.implicitHeight + 6
        Text {
            id: infoLabel
            anchors.centerIn: parent
            color: lane.theme.text
            font.pixelSize: Math.round(11 * lane.theme.scale)
            text: {
                const n = lane.hoveredNote
                if (!n) return ""
                let msg = lane.noteName(n.targetMidi)
                if (Math.abs(n.offsetCents) >= 0.5)
                    msg += "  (" + (n.offsetCents > 0 ? "+" : "") + Math.round(n.offsetCents) + "¢)"
                if (n.scorePitch >= 0)
                    msg += "  →  " + lane.noteName(n.scorePitch) + (n.scoreLyric ? " " + n.scoreLyric : "")
                if (n.matchReason)
                    msg += "  ·  " + lane.t(n.matchReason)
                return msg
            }
        }
    }

    // Live vertical-drag pitch HUD badge
    Rectangle {
        visible: lane.draggingNote
        x: Math.max(lane.headerWidth + 8, Math.min(lane.width - width - 8, lane.headerWidth + lane.dragMouseX + 12))
        y: Math.max(6, Math.min(lane.height - height - 6, lane.dragMouseY - height - 8))
        radius: Math.round(5 * lane.theme.scale)
        color: lane.theme.surface
        border.color: lane.theme.accent
        implicitWidth: dragLabel.implicitWidth + 16
        implicitHeight: dragLabel.implicitHeight + 8
        Text {
            id: dragLabel
            anchors.centerIn: parent
            color: lane.theme.text
            font.pixelSize: Math.round(11 * lane.theme.scale)
            font.weight: Font.DemiBold
            text: {
                const targetM = lane.dragAnchorMidi + lane.dragDeltaSemitones
                const deltaCents = Math.round(lane.dragDeltaSemitones * 100)
                return lane.noteName(targetM) + "  (" + (deltaCents >= 0 ? "+" : "") + deltaCents + "¢)"
            }
        }
    }

    MouseArea {
        id: pianoGutterArea
        x: 1
        y: 1
        width: lane.headerWidth
        height: Math.max(1, parent.height - 2)
        cursorShape: Qt.SizeVerCursor
        property real pressY: 0
        property real startLow: 48
        onPressed: mouse => {
            pressY = mouse.y
            startLow = lane.lowNote
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return
            const deltaSemis = Math.round((mouse.y - pressY) / Math.max(6, lane.rowHeight()))
            const span = lane.highNote - lane.lowNote
            const nextLow = Math.max(24, Math.min(96 - span, startLow + deltaSemis))
            if (nextLow !== lane.lowNote) {
                lane.lowNote = nextLow
                lane.highNote = nextLow + span
                lane.manualPitchRange = true
                plot.requestPaint()
            }
        }
        onDoubleClicked: lane.resetPitchRange()
    }

    Item {
        x: lane.headerWidth + 1
        y: 1
        width: Math.max(1, parent.width - x - 1)
        height: Math.max(1, parent.height - 2)
        clip: true
        Item {
            objectName: "chorusPitchPlayhead"
            readonly property real px: Math.round((lane.controller.position - lane.view.viewFirst)
                * parent.width / Math.max(.01, lane.view.viewSpan))
            x: px
            y: 0
            width: 1
            height: parent.height
            visible: px >= -1 && px <= parent.width + 1
            Rectangle {
                x: 0
                y: 0
                width: 1
                height: parent.height
                color: lane.theme.accent
            }
            Rectangle {
                x: -Math.floor(width / 2)
                y: 0
                width: Math.round(7 * lane.theme.scale)
                height: Math.round(5 * lane.theme.scale)
                radius: 2
                color: lane.theme.accent
            }
        }
        MouseArea {
            id: inputArea
            objectName: "chorusPitchDrawArea"
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            hoverEnabled: true
            cursorShape: panning ? Qt.ClosedHandCursor
                : lane.drawing ? Qt.CrossCursor
                : lane.splitting ? Qt.SplitHCursor
                : lane.draggingNote ? Qt.SizeVerCursor
                : (lane.hoveredNote && lane.tool === "note") ? Qt.OpenHandCursor
                : Qt.ArrowCursor
            preventStealing: true
            property real startTime: 0
            property real pressX: 0
            property real pressY: 0
            property var pressedNote: null
            property bool dragged: false
            property bool panning: false
            property real panLastX: 0
            property real panStartY: 0
            property real panStartLow: 48

            function at(x) { return lane.view.viewFirst + Math.max(0, Math.min(1, x / Math.max(1, width))) * lane.view.viewSpan }
            function dot(x, y) {
                return {time: at(x), note: lane.highNote + .5 - Math.max(0, Math.min(1, y / Math.max(1, height)))
                    * (lane.highNote - lane.lowNote + 1)}
            }
            function commitSelection(keys) {
                lane.selectedKeys = keys
                lane.controller.selectPitchNotes(lane.selectedSpans())
                plot.requestPaint()
            }

            onEntered: { lane.hoverX = mouseX; lane.hoverY = mouseY }
            onExited: { lane.hoverX = -1; lane.hoverY = -1; lane.hoveredNote = null; plot.requestPaint() }

            onWheel: wheel => {
                const dy = wheel.angleDelta.y
                const dx = wheel.angleDelta.x
                if (wheel.modifiers & Qt.ControlModifier) {
                    if (dy !== 0)
                        lane.controller.zoomViewportAt(dy > 0 ? .8 : 1.25, at(wheel.x))
                    wheel.accepted = true
                    return
                }
                if ((wheel.modifiers & Qt.ShiftModifier) || (Math.abs(dx) > Math.abs(dy) && dx !== 0)) {
                    const delta = Math.abs(dx) > Math.abs(dy) ? dx : dy
                    if (delta !== 0)
                        lane.controller.panViewport((-delta / 120) * lane.view.viewSpan * .14)
                    wheel.accepted = true
                    return
                }
                if (wheel.modifiers & Qt.AltModifier) {
                    if (dy !== 0)
                        lane.zoomPitch(dy > 0 ? .85 : 1.18, dot(wheel.x, wheel.y).note)
                    wheel.accepted = true
                    return
                }
                if (dy !== 0) {
                    lane.scrollPitch(dy > 0 ? 2 : -2)
                    wheel.accepted = true
                }
            }

            onPressed: mouse => {
                pressX = mouse.x
                pressY = mouse.y
                startTime = at(mouse.x)
                dragged = false
                pressedNote = null
                panning = false

                if (mouse.button === Qt.MiddleButton) {
                    panning = true
                    panLastX = mouse.x
                    panStartY = mouse.y
                    panStartLow = lane.lowNote
                    lane.controller.setFollowHold(true)
                    return
                }

                if (mouse.button === Qt.RightButton) {
                    const hit = lane.noteAt(mouse.x, mouse.y)
                    lane.contextNote = hit
                    lane.contextTime = startTime
                    if (hit && lane.view.take.role === "vocal" && !lane.isSelected(hit))
                        commitSelection([hit.id])
                    noteContextMenu.popup(mouse.x, mouse.y)
                    return
                }

                lane.controller.setFollowHold(true)
                if (lane.drawing) {
                    lane.stroke = [dot(mouse.x, mouse.y)]
                    return
                }
                if (lane.splitting) {
                    const hit = lane.noteAt(mouse.x, mouse.y)
                    if (hit)
                        lane.controller.splitPitchNote(hit.takeStart, hit.takeEnd, startTime)
                    return
                }
                const hit = lane.noteAt(mouse.x, mouse.y)
                if (hit && lane.view.take.role === "vocal") {
                    pressedNote = hit
                    const multi = !!(mouse.modifiers & (Qt.ShiftModifier | Qt.ControlModifier))
                    let next = lane.selectedKeys.slice()
                    const idx = next.indexOf(hit.id)
                    if (multi) {
                        if (idx >= 0) {
                            next.splice(idx, 1)
                            pressedNote = null
                        } else {
                            next.push(hit.id)
                        }
                    } else if (idx < 0) {
                        next = [hit.id]
                    }
                    commitSelection(next)
                    if (pressedNote) {
                        lane.dragAnchorMidi = hit.targetMidi
                        lane.dragDeltaSemitones = 0
                        lane.dragMouseX = mouse.x
                        lane.dragMouseY = mouse.y
                    }
                } else {
                    lane.marqueeX1 = mouse.x
                    lane.marqueeY1 = mouse.y
                    lane.marqueeX2 = mouse.x
                    lane.marqueeY2 = mouse.y
                }
            }

            onPositionChanged: mouse => {
                lane.hoverX = mouse.x
                lane.hoverY = mouse.y
                if (!pressed) {
                    lane.hoveredNote = lane.noteAt(mouse.x, mouse.y)
                    return
                }
                if (panning) {
                    const dxPx = mouse.x - panLastX
                    panLastX = mouse.x
                    if (Math.abs(dxPx) >= 1)
                        lane.controller.panViewport(-dxPx * lane.view.viewSpan / Math.max(1, width))
                    const deltaSemis = Math.round((mouse.y - panStartY) / Math.max(6, lane.rowHeight()))
                    const span = lane.highNote - lane.lowNote
                    const nextLow = Math.max(24, Math.min(96 - span, panStartLow + deltaSemis))
                    if (nextLow !== lane.lowNote) {
                        lane.lowNote = nextLow
                        lane.highNote = nextLow + span
                        lane.manualPitchRange = true
                        plot.requestPaint()
                    }
                    return
                }
                if (lane.drawing) {
                    lane.stroke = lane.stroke.concat([dot(mouse.x, mouse.y)])
                    return
                }
                if (lane.splitting)
                    return
                if (pressedNote) {
                    if (!dragged && Math.abs(mouse.y - pressY) >= 4)
                        dragged = true
                    if (dragged) {
                        lane.draggingNote = true
                        lane.dragMouseX = mouse.x
                        lane.dragMouseY = mouse.y
                        const rawDelta = (pressY - mouse.y) / Math.max(1, lane.rowHeight())
                        const snap = lane.view.pitchSnap && !(mouse.modifiers & Qt.AltModifier)
                        lane.dragDeltaSemitones = snap
                            ? (Math.round(lane.dragAnchorMidi + rawDelta) - lane.dragAnchorMidi)
                            : rawDelta
                    }
                } else {
                    if (!dragged && (Math.abs(mouse.x - pressX) >= 5 || Math.abs(mouse.y - pressY) >= 5))
                        dragged = true
                    if (dragged) {
                        lane.marqueeActive = true
                        lane.marqueeX2 = mouse.x
                        lane.marqueeY2 = mouse.y
                        plot.requestPaint()
                    }
                }
            }

            onReleased: mouse => {
                lane.controller.setFollowHold(false)
                if (panning) {
                    panning = false
                    return
                }
                if (mouse.button === Qt.RightButton)
                    return
                const end = at(mouse.x)
                if (lane.drawing) {
                    const captured = lane.stroke.concat([dot(mouse.x, mouse.y)])
                    lane.stroke = []
                    lane.controller.drawPitch(captured)
                    return
                }
                if (lane.splitting)
                    return
                if (pressedNote) {
                    const wasDragging = lane.draggingNote
                    const deltaCents = lane.dragDeltaSemitones * 100
                    lane.draggingNote = false
                    lane.dragDeltaSemitones = 0
                    pressedNote = null
                    if (wasDragging && Math.abs(deltaCents) >= 0.5) {
                        lane.controller.shiftPitchNotes(lane.selectedSpans(), deltaCents)
                    } else if (!wasDragging && !(mouse.modifiers & (Qt.ShiftModifier | Qt.ControlModifier))) {
                        lane.controller.seek(end)
                    }
                    return
                }
                if (lane.marqueeActive) {
                    const xMin = Math.min(lane.marqueeX1, lane.marqueeX2)
                    const xMax = Math.max(lane.marqueeX1, lane.marqueeX2)
                    const yMin = Math.min(lane.marqueeY1, lane.marqueeY2)
                    const yMax = Math.max(lane.marqueeY1, lane.marqueeY2)
                    lane.marqueeActive = false
                    const picked = []
                    const notes = lane.curve.notes || []
                    for (let i = 0; i < notes.length; i++) {
                        const b = lane.noteBox(notes[i])
                        if (b.x + b.w >= xMin && b.x <= xMax && b.y + b.h >= yMin && b.y <= yMax)
                            picked.push(notes[i].id)
                    }
                    if (picked.length > 0)
                        commitSelection(picked)
                    else
                        lane.controller.selectRange(startTime, end)
                } else {
                    if (lane.selectedKeys.length > 0)
                        commitSelection([])
                    lane.controller.seek(end)
                }
            }

            onDoubleClicked: mouse => {
                if (mouse.button !== Qt.LeftButton || lane.tool !== "note" || lane.view.take.role !== "vocal")
                    return
                const hit = lane.noteAt(mouse.x, mouse.y)
                if (hit)
                    lane.controller.snapPitchNotesToSemitone([{takeStart: hit.takeStart, takeEnd: hit.takeEnd}])
            }

            onCanceled: {
                lane.controller.setFollowHold(false)
                lane.stroke = []
                lane.draggingNote = false
                lane.dragDeltaSemitones = 0
                lane.marqueeActive = false
                panning = false
                pressedNote = null
            }

            Menu {
                id: noteContextMenu
                MenuItem {
                    text: lane.t("对齐到最近半音 (Q)")
                    enabled: lane.selectedKeys.length > 0 && lane.view.take.role === "vocal"
                    onTriggered: lane.controller.snapPitchNotesToSemitone([])
                }
                MenuItem {
                    text: lane.t("在此处拆分音符")
                    enabled: !!lane.contextNote && lane.view.take.role === "vocal"
                    onTriggered: {
                        if (lane.contextNote)
                            lane.controller.splitPitchNote(lane.contextNote.takeStart, lane.contextNote.takeEnd, lane.contextTime)
                    }
                }
                MenuItem {
                    text: lane.t("合并所选音符 (M)")
                    enabled: lane.view.canMergeNotes && lane.view.take.role === "vocal"
                    onTriggered: lane.controller.mergePitchNotes([])
                }
                MenuItem {
                    text: lane.t("循环试听选中音符")
                    enabled: lane.selectedKeys.length > 0
                    onTriggered: lane.controller.loopSelectedNotes()
                }
                MenuSeparator {}
                MenuItem {
                    text: lane.t("还原所选音符 (Del)")
                    enabled: lane.selectedKeys.length > 0 && lane.view.take.role === "vocal"
                    onTriggered: lane.controller.resetPitchNotes([], false)
                }
                MenuItem {
                    text: lane.t("保留原音（锁定）")
                    enabled: lane.selectedKeys.length > 0 && lane.view.take.role === "vocal"
                    onTriggered: lane.controller.resetPitchNotes([], true)
                }
                MenuSeparator { visible: lane.manualPitchRange }
                MenuItem {
                    visible: lane.manualPitchRange
                    text: lane.t("自动适配纵轴音域")
                    onTriggered: lane.resetPitchRange()
                }
            }
        }
    }
}
