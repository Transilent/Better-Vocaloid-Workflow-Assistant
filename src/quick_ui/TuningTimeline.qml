import QtQuick
Item {
    id: timeline
    objectName: "tuningTimelineCanvas"
    required property QtObject theme
    required property QtObject controller
    required property var view
    required property var tracks
    required property var notes
    required property var regions

    readonly property real scale: theme.scale || 1
    readonly property real origin: numberOr(view.timelineStart, 0)
    readonly property real fullSpan: Math.max(0.001, numberOr(view.duration, 1))
    readonly property real fullEnd: origin + fullSpan
    readonly property real visibleStart: clamp(numberOr(view.viewportStart, origin),
        origin, Math.max(origin, fullEnd - 0.001))
    readonly property real visibleSpan: clamp(numberOr(view.viewportSpan, fullSpan),
        Math.max(0.001, Math.min(fullSpan, 0.5)), fullSpan)
    readonly property real visibleEnd: Math.min(fullEnd, visibleStart + visibleSpan)
    readonly property real gutter: Math.min(width * 0.34,
        Math.round((width < Math.round(520 * scale) ? 70 : 104) * scale))
    readonly property real plotLeft: gutter
    readonly property real plotRight: Math.max(plotLeft, width - Math.round(10 * scale))
    readonly property real plotTop: Math.round(28 * scale)
    readonly property real plotBottom: Math.max(plotTop, height - Math.round(28 * scale))
    readonly property real plotWidth: Math.max(1, plotRight - plotLeft)
    readonly property real plotHeight: Math.max(1, plotBottom - plotTop)
    readonly property var laneTracks: resolvedLaneTracks()
    readonly property int visibleNoteCount: countVisibleNotes()
    readonly property string emptyText: emptyMessage()

    property real pressX: -1
    property real pressTime: Number.NaN
    property real dragTime: Number.NaN

    function numberOr(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback
        const parsed = Number(value)
        return isFinite(parsed) ? parsed : fallback
    }

    function clamp(value, low, high) {
        if (high < low)
            return low
        return Math.max(low, Math.min(high, value))
    }

    function listContains(values, value) {
        if (!values || values.length === undefined)
            return false
        for (let index = 0; index < values.length; ++index) {
            if (String(values[index]) === String(value))
                return true
        }
        return false
    }

    function targetTrackIds() {
        const result = []
        if (String(view.mode || "single") === "multi") {
            const selected = view.selectedTracks || []
            for (let index = 0; index < selected.length; ++index)
                result.push(String(selected[index]))
        } else if (view.activeTrack !== undefined && view.activeTrack !== null
                   && String(view.activeTrack).length > 0) {
            result.push(String(view.activeTrack))
        }
        return result
    }

    function resolvedLaneTracks() {
        const ids = targetTrackIds()
        const result = []
        for (let index = 0; index < tracks.length; ++index) {
            if (listContains(ids, tracks[index].id))
                result.push(tracks[index])
        }
        return result
    }

    function trackById(trackId) {
        for (let index = 0; index < laneTracks.length; ++index) {
            if (String(laneTracks[index].id) === String(trackId))
                return laneTracks[index]
        }
        return null
    }

    function withinView(item) {
        const start = numberOr(item.start, Number.NaN)
        const end = numberOr(item.end, start)
        return isFinite(start) && end >= visibleStart && start <= visibleEnd
    }

    function countVisibleNotes() {
        let count = 0
        for (let laneIndex = 0; laneIndex < laneTracks.length; ++laneIndex) {
            const trackId = String(laneTracks[laneIndex].id)
            for (let noteIndex = 0; noteIndex < notes.length; ++noteIndex) {
                const note = notes[noteIndex]
                if (String(note.trackId) === trackId && withinView(note))
                    ++count
            }
        }
        return count
    }

    function emptyMessage() {
        if (String(view.source || "").length === 0)
            return "载入调音工程后显示真实音符"
        if (tracks.length === 0)
            return "工程没有可显示的演唱轨"
        if (laneTracks.length === 0)
            return String(view.mode || "single") === "multi"
                ? "至少勾选一条轨道以查看和编辑"
                : "选择一条轨道以查看音符"
        if (visibleNoteCount === 0)
            return "当前视口没有可见音符，可缩放或平移"
        return ""
    }

    function colorText(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback
        return String(value)
    }

    function shortText(value, limit) {
        const text = String(value === undefined || value === null ? "" : value)
        return text.length <= limit ? text : text.slice(0, Math.max(1, limit - 1)) + "…"
    }

    function formatTime(value) {
        const seconds = numberOr(value, 0)
        const negative = seconds < 0
        const absolute = Math.abs(seconds)
        const whole = Math.floor(absolute)
        const minutes = Math.floor(whole / 60)
        const rest = whole % 60
        const tenths = Math.floor((absolute - whole) * 10)
        return (negative ? "-" : "") + String(minutes).padStart(2, "0") + ":"
            + String(rest).padStart(2, "0") + "." + String(tenths)
    }

    function xForTime(value) {
        return plotLeft + (numberOr(value, visibleStart) - visibleStart) / visibleSpan * plotWidth
    }

    function timeForPlotX(value) {
        return visibleStart + clamp(value, 0, plotWidth) / plotWidth * visibleSpan
    }

    function clampViewport(start, span) {
        const boundedSpan = clamp(numberOr(span, fullSpan),
            Math.max(0.001, Math.min(fullSpan, 0.5)), fullSpan)
        const boundedStart = clamp(numberOr(start, origin), origin,
            Math.max(origin, fullEnd - boundedSpan))
        return { start: boundedStart, span: boundedSpan }
    }

    function zoomAt(factor, anchorTime) {
        const next = clampViewport(0, visibleSpan * factor)
        const ratio = clamp((numberOr(anchorTime, visibleStart + visibleSpan / 2) - visibleStart)
            / visibleSpan, 0, 1)
        const start = clampViewport(visibleStart + ratio * (visibleSpan - next.span),
            next.span)
        controller.setViewport(start.start, start.span)
    }

    function panBy(seconds) {
        const next = clampViewport(visibleStart + seconds, visibleSpan)
        controller.setViewport(next.start, next.span)
    }

    function fitTimeline() {
        controller.setViewport(origin, fullSpan)
    }

    function beginSelection(plotX) {
        pressX = plotX
        pressTime = timeForPlotX(plotX)
        dragTime = pressTime
    }

    function updateSelection(plotX) {
        if (pressX >= 0)
            dragTime = timeForPlotX(plotX)
    }

    function finishSelection(plotX) {
        if (pressX < 0)
            return
        const endTime = timeForPlotX(plotX)
        const moved = Math.abs(plotX - pressX)
        const first = Math.min(pressTime, endTime)
        const last = Math.max(pressTime, endTime)
        pressX = -1
        pressTime = Number.NaN
        dragTime = Number.NaN
        if (moved >= 2 && last > first)
            controller.selectSpan(first, last)
    }

    onTracksChanged: canvas.requestPaint()
    onNotesChanged: canvas.requestPaint()
    onRegionsChanged: canvas.requestPaint()
    onViewChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    Component.onCompleted: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true
        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, canvas.width, canvas.height)

            const surface = timeline.colorText(timeline.theme.surface, "#191b23")
            const base = timeline.colorText(timeline.theme.base, "#13141a")
            const raised = timeline.colorText(timeline.theme.raised, "#20222b")
            const line = timeline.colorText(timeline.theme.line, "#30323d")
            const textColor = timeline.colorText(timeline.theme.text, "#eeeef5")
            const muted = timeline.colorText(timeline.theme.muted, "#a6a9ba")
            const accent = timeline.colorText(timeline.theme.accent, "#c2a8ff")
            const smallFont = Math.max(10, Math.round(10 * timeline.scale))
            const labelFont = Math.max(11, Math.round(11 * timeline.scale))
            const timeFont = Math.max(10, Math.round(10 * timeline.scale))

            ctx.fillStyle = surface
            ctx.fillRect(0, 0, canvas.width, canvas.height)
            ctx.fillStyle = base
            ctx.globalAlpha = 0.58
            ctx.fillRect(timeline.plotLeft, timeline.plotTop,
                timeline.plotWidth, timeline.plotHeight)
            ctx.globalAlpha = 1

            const lanes = timeline.laneTracks
            const laneCount = Math.max(1, lanes.length)
            const laneHeight = timeline.plotHeight / laneCount
            const visibleNotesByTrack = Object.create(null)
            for (let noteIndex = 0; noteIndex < timeline.notes.length; ++noteIndex) {
                const note = timeline.notes[noteIndex]
                if (!timeline.withinView(note))
                    continue
                const key = String(note.trackId)
                if (!visibleNotesByTrack[key])
                    visibleNotesByTrack[key] = []
                visibleNotesByTrack[key].push(note)
            }

            ctx.font = String(timeFont) + "px sans-serif"
            ctx.textBaseline = "middle"
            ctx.fillStyle = muted
            ctx.strokeStyle = line
            ctx.lineWidth = 1

            for (let tick = 0; tick <= 5; ++tick) {
                const ratio = tick / 5
                const x = timeline.plotLeft + ratio * timeline.plotWidth
                ctx.globalAlpha = tick === 0 || tick === 5 ? 0.7 : 0.42
                ctx.beginPath()
                ctx.moveTo(x, timeline.plotTop)
                ctx.lineTo(x, timeline.plotBottom)
                ctx.stroke()
                ctx.globalAlpha = 1
                ctx.fillStyle = muted
                const tickText = timeline.formatTime(timeline.visibleStart
                    + timeline.visibleSpan * ratio)
                const textWidth = ctx.measureText(tickText).width
                const textX = Math.max(timeline.plotLeft,
                    Math.min(timeline.plotRight - textWidth,
                        x - textWidth / 2))
                ctx.fillText(tickText, textX, Math.round(14 * timeline.scale))
            }

            for (let laneIndex = 0; laneIndex < lanes.length; ++laneIndex) {
                const lane = lanes[laneIndex]
                const laneTop = timeline.plotTop + laneIndex * laneHeight
                const laneBottom = laneTop + laneHeight
                const trackId = String(lane.id)
                const active = String(lane.id) === String(timeline.view.activeTrack)

                if (laneIndex % 2 === 0) {
                    ctx.fillStyle = raised
                    ctx.globalAlpha = 0.2
                    ctx.fillRect(timeline.plotLeft, laneTop,
                        timeline.plotWidth, laneHeight)
                    ctx.globalAlpha = 1
                }

                ctx.strokeStyle = line
                ctx.globalAlpha = laneIndex === 0 ? 0 : 0.62
                ctx.beginPath()
                ctx.moveTo(timeline.plotLeft, laneTop)
                ctx.lineTo(timeline.plotRight, laneTop)
                ctx.stroke()
                ctx.globalAlpha = 1

                ctx.font = String(labelFont) + "px sans-serif"
                ctx.fillStyle = active ? textColor : muted
                ctx.globalAlpha = lane.editable === false ? 0.58 : 1
                const label = timeline.shortText(lane.name || lane.id, 12)
                ctx.fillText(label, Math.round(10 * timeline.scale),
                    laneTop + Math.max(12, Math.min(laneHeight * 0.38, 22)))
                ctx.font = String(smallFont) + "px sans-serif"
                ctx.fillStyle = muted
                const countText = String(lane.noteCount === undefined ? "" : lane.noteCount)
                    + (lane.editable === false ? " · 只读" : "")
                ctx.fillText(timeline.shortText(countText, 16),
                    Math.round(10 * timeline.scale),
                    laneTop + Math.max(27, Math.min(laneHeight - 8, 43)))
                ctx.globalAlpha = 1

                for (let regionIndex = 0; regionIndex < timeline.regions.length; ++regionIndex) {
                    const region = timeline.regions[regionIndex]
                    if (String(region.trackId) !== trackId || !timeline.withinView(region))
                        continue
                    const startX = timeline.xForTime(region.start)
                    const endX = timeline.xForTime(region.end)
                    const left = Math.max(timeline.plotLeft, Math.min(startX, endX))
                    const right = Math.min(timeline.plotRight, Math.max(startX, endX))
                    if (right <= left)
                        continue
                    ctx.fillStyle = timeline.colorText(region.color, accent)
                    ctx.globalAlpha = 0.26
                    ctx.fillRect(left, laneTop + Math.round(3 * timeline.scale),
                        Math.max(1, right - left), Math.max(1, laneHeight - Math.round(6 * timeline.scale)))
                    ctx.globalAlpha = 0.72
                    ctx.fillRect(left, laneTop + Math.round(3 * timeline.scale),
                        Math.max(1, right - left), Math.max(1, Math.round(2 * timeline.scale)))
                    ctx.globalAlpha = 1
                }

                const laneNotes = visibleNotesByTrack[trackId] || []
                let minPitch = Number.POSITIVE_INFINITY
                let maxPitch = Number.NEGATIVE_INFINITY
                for (let noteIndex = 0; noteIndex < laneNotes.length; ++noteIndex) {
                    const pitch = Number(laneNotes[noteIndex].pitch)
                    if (isFinite(pitch)) {
                        minPitch = Math.min(minPitch, pitch)
                        maxPitch = Math.max(maxPitch, pitch)
                    }
                }
                const hasPitch = isFinite(minPitch) && isFinite(maxPitch)
                const innerTop = laneTop + Math.round(8 * timeline.scale)
                const innerHeight = Math.max(1, laneHeight - Math.round(16 * timeline.scale))
                const noteHeight = Math.max(4, Math.min(Math.round(10 * timeline.scale),
                    innerHeight * 0.22))

                for (let noteIndex = 0; noteIndex < laneNotes.length; ++noteIndex) {
                    const note = laneNotes[noteIndex]
                    const noteStart = Number(note.start)
                    const noteEnd = Number(note.end)
                    if (!isFinite(noteStart) || !isFinite(noteEnd) || noteEnd < noteStart)
                        continue
                    const startX = timeline.xForTime(noteStart)
                    const endX = timeline.xForTime(noteEnd)
                    const left = Math.max(timeline.plotLeft, startX)
                    const right = Math.min(timeline.plotRight, Math.max(startX + 2,
                        endX))
                    if (right <= left)
                        continue
                    let centerY = laneTop + laneHeight / 2
                    const pitch = Number(note.pitch)
                    if (hasPitch && isFinite(pitch)) {
                        const ratio = maxPitch === minPitch ? 0.5
                            : (maxPitch - pitch) / (maxPitch - minPitch)
                        centerY = innerTop + ratio * innerHeight
                    }
                    ctx.fillStyle = active ? accent : muted
                    ctx.globalAlpha = lane.editable === false ? 0.46 : 0.78
                    ctx.fillRect(left, centerY - noteHeight / 2,
                        Math.max(2, right - left), noteHeight)
                }
                ctx.globalAlpha = 1
            }

            ctx.font = String(smallFont) + "px sans-serif"
            ctx.fillStyle = muted
            ctx.fillText(timeline.formatTime(timeline.origin),
                Math.round(10 * timeline.scale),
                Math.round(canvas.height - 12 * timeline.scale))
            const endText = timeline.formatTime(timeline.fullEnd)
            const endWidth = ctx.measureText(endText).width
            ctx.fillText(endText, Math.max(timeline.plotLeft,
                canvas.width - endWidth - Math.round(10 * timeline.scale)),
                Math.round(canvas.height - 12 * timeline.scale))
        }
    }

    Item {
        id: selectionLayer
        x: timeline.plotLeft
        y: timeline.plotTop
        width: timeline.plotWidth
        height: timeline.plotHeight
        clip: true
        visible: timeline.visibleNoteCount > 0

        Rectangle {
            readonly property real startValue: timeline.numberOr(timeline.view.start, Number.NaN)
            readonly property real endValue: timeline.numberOr(timeline.view.end, Number.NaN)
            readonly property real fromTime: Math.min(startValue, endValue)
            readonly property real toTime: Math.max(startValue, endValue)
            readonly property bool valid: isFinite(startValue) && isFinite(endValue)
            x: valid ? Math.max(0, timeline.xForTime(fromTime) - timeline.plotLeft) : 0
            y: 0
            width: valid ? Math.max(0, Math.min(timeline.plotWidth,
                timeline.xForTime(toTime) - timeline.xForTime(fromTime))) : 0
            height: parent.height
            visible: valid && width > 0
            color: Qt.rgba(timeline.theme.accent.r, timeline.theme.accent.g,
                timeline.theme.accent.b, 0.12)
            border.color: Qt.rgba(timeline.theme.accent.r, timeline.theme.accent.g,
                timeline.theme.accent.b, 0.7)
            border.width: 1
        }

        Rectangle {
            readonly property real fromTime: Math.min(timeline.pressTime, timeline.dragTime)
            readonly property real toTime: Math.max(timeline.pressTime, timeline.dragTime)
            readonly property bool valid: timeline.pressX >= 0 && isFinite(fromTime)
                && isFinite(toTime)
            x: valid ? Math.max(0, timeline.xForTime(fromTime) - timeline.plotLeft) : 0
            width: valid ? Math.max(0, Math.min(timeline.plotWidth,
                timeline.xForTime(toTime) - timeline.xForTime(fromTime))) : 0
            height: parent.height
            visible: valid
            color: Qt.rgba(timeline.theme.accent.r, timeline.theme.accent.g,
                timeline.theme.accent.b, 0.18)
            border.color: timeline.theme.accent
            border.width: 1
        }
    }

    MouseArea {
        x: timeline.plotLeft
        y: timeline.plotTop
        width: timeline.plotWidth
        height: timeline.plotHeight
        acceptedButtons: Qt.LeftButton
        hoverEnabled: true
        cursorShape: Qt.CrossCursor
        enabled: timeline.laneTracks.length > 0 && timeline.visibleNoteCount > 0
        onPressed: function(mouse) {
            timeline.beginSelection(mouse.x)
        }
        onPositionChanged: function(mouse) {
            if (pressed)
                timeline.updateSelection(mouse.x)
        }
        onReleased: function(mouse) {
            timeline.finishSelection(mouse.x)
        }
        onCanceled: {
            timeline.pressX = -1
            timeline.pressTime = Number.NaN
            timeline.dragTime = Number.NaN
        }
        onDoubleClicked: timeline.fitTimeline()
        onWheel: function(wheel) {
            if (wheel.angleDelta.y === 0)
                return
            const direction = wheel.angleDelta.y > 0 ? 0.82 : 1.22
            if (wheel.modifiers & Qt.ShiftModifier)
                timeline.panBy((wheel.angleDelta.y > 0 ? -1 : 1)
                    * timeline.visibleSpan * 0.12)
            else
                timeline.zoomAt(direction,
                    timeline.visibleStart + timeline.visibleSpan * 0.5)
            wheel.accepted = true
        }
    }

    Text {
        visible: timeline.emptyText.length > 0
        anchors.centerIn: parent
        width: Math.max(120, timeline.plotWidth - Math.round(36 * timeline.scale))
        horizontalAlignment: Text.AlignHCenter
        text: timeline.emptyText
        color: timeline.theme.muted
        font.pixelSize: Math.round(12 * timeline.scale)
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    Text {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: Math.round(10 * timeline.scale)
        anchors.bottomMargin: Math.round(7 * timeline.scale)
        visible: timeline.visibleNoteCount > 0
        text: "拖拽选区 · 滚轮缩放 · Shift+滚轮平移"
        color: timeline.theme.muted
        font.pixelSize: Math.round(10 * timeline.scale)
        opacity: 0.85
        textFormat: Text.PlainText
    }
}
