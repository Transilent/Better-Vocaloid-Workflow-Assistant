import QtQuick
import QtQuick.Controls

Item {
    id: timeline
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
    }
    objectName: "tuningTimelineCanvas"
    required property QtObject theme
    required property QtObject controller
    required property var view
    required property var tracks
    required property var notes
    required property var regions
    property bool compact: false
    signal noteFocused()
    signal regionFocused()

    readonly property real scale: theme.scale || 1
    readonly property bool dark: theme.dark
    readonly property string sourceKey: String(view.source || "") + "|" + String(view.sha256 || "")
    readonly property bool hasSource: !!view.source
    readonly property bool multi: view.mode === "multi"
    readonly property bool scoreEditable: hasSource && !multi && !view.busy && view.scoreEditReason === ""
    readonly property bool noteTool: scoreEditable && !!view.noteTool
    readonly property bool styleTool: !view.busy && !!view.styleTool
    readonly property string pitchTool: !view.busy && !multi ? String(view.pitchTool || "") : ""
    readonly property color handColor: dark ? "#69e8d2" : "#116961"
    readonly property var manualCurves: controller.pitchCurves || []
    readonly property var beatMarks: controller.beatMarks || []
    property real inspectionTime: Number.NaN
    property bool inspectPointer: false
    readonly property real pointerSeconds: inspectPointer && scoreMouse.containsMouse
        ? timeForPlotX(scoreMouse.mouseX) : Number.NaN
    readonly property real cursorTime: isFinite(inspectionTime) ? inspectionTime : pointerSeconds
    readonly property string activeTrack: String(view.activeTrack || "")
    readonly property string selectedTrackKey: JSON.stringify(view.selectedTracks || [])
    readonly property var targetTracks: resolveTracks()
    readonly property string targetKey: JSON.stringify(Object.keys(targetTracks))
    readonly property var laneTracks: displayLanes()
    readonly property int hiddenLaneCount: Math.max(0, tracks.length - laneTracks.length)

    readonly property real origin: numberOr(view.timelineStart, 0)
    readonly property real fullSpan: Math.max(0.001, numberOr(view.duration, 1))
    readonly property real fullEnd: origin + fullSpan
    readonly property real visibleSpan: clamp(numberOr(view.viewportSpan, fullSpan),
        Math.min(fullSpan, 0.5), fullSpan)
    readonly property real visibleStart: clamp(numberOr(view.viewportStart, origin),
        origin, fullEnd - visibleSpan)
    readonly property real visibleEnd: visibleStart + visibleSpan
    readonly property real selectionStart: numberOr(view.start, Number.NaN)
    readonly property real selectionEnd: numberOr(view.end, Number.NaN)

    readonly property int pitchRowHeight: Math.round((compact ? 17 : 20) * scale)
    readonly property real rulerHeight: Math.round(25 * scale)
    readonly property real laneRowHeight: Math.round((compact ? 37 : 43) * scale)
    readonly property real laneAreaHeight: multi ? laneTracks.length * laneRowHeight : 0
    readonly property real focusHeaderHeight: multi && hasSource ? Math.round(22 * scale) : 0
    readonly property real regionRowHeight: multi ? laneRowHeight : Math.round(19 * scale)
    readonly property real regionBandHeight: multi
        ? Math.round(14 * scale) : Math.max(0, regionRowHeight - 2)
    readonly property real plotLeft: Math.round((multi ? 108 : compact ? 48 : 60) * scale)
    readonly property real plotRight: Math.max(plotLeft + 1, width - Math.round(13 * scale))
    readonly property real plotTop: rulerHeight + regionRowCount * regionRowHeight + focusHeaderHeight
    readonly property real plotBottom: Math.max(plotTop + 1, height)
    readonly property real plotWidth: plotRight - plotLeft
    readonly property real plotHeight: plotBottom - plotTop
    readonly property real rowsInView: Math.min(128, plotHeight / pitchRowHeight)
    readonly property real pitchOffset: clamp(topRow, 0, 128 - rowsInView)
    readonly property var visibleNotes: collectNotes([activeTrack])
    readonly property var laneNotes: multi ? collectNotes(laneTracks.map(entry => String(entry.track.id))) : []
    readonly property var interactionNotes: multi ? laneNotes : visibleNotes
    readonly property var styleSpans: collectRegions()
    readonly property var styledTracks: multi ? laneTracks.map(entry => String(entry.track.id))
        : activeTrack.length > 0 ? [activeTrack] : []
    readonly property int regionRowCount: hasSource ? (multi ? laneTracks.length : 1) : 0
    readonly property var styleBlocks: visibleStyleBlocks()
    readonly property var noteEdges: collectEdges()
    readonly property var noteRects: layoutNotes()
    readonly property int visibleNoteCount: visibleNotes.length
    readonly property var focusedNote: visibleNotes.find(entry =>
        entry.trackId === focusedTrackId && entry.id === focusedNoteId)
    readonly property var selectedNotes: view.selectedNoteIds || []
    readonly property var selectedNoteSet: new Set(selectedNotes)
    readonly property var hoveredBox: scoreMouse.containsMouse && !noteGesture && !pitchGesture
        ? noteBoxAt(scoreMouse.mouseX + plotLeft, scoreMouse.mouseY + plotTop) : null
    readonly property var hoveredNote: hoveredBox ? hoveredBox.note : null
    readonly property string instructions: vwaTr("左右键选择相邻音符。中键拖动平移，滚轮缩放，Shift 滚轮横移。")

    property real topRow: 63
    property bool centerOnSource: true
    property bool checkTargetPitch: true
    property string focusedTrackId: ""
    property string focusedNoteId: ""
    property real pressX: -1
    property real pressY: -1
    property real pressTime: Number.NaN
    property real dragTime: Number.NaN
    property bool selectOnly: false
    property string gestureType: ""
    property var marqueeEnd: null
    property var panGesture: null
    property var hoveredLaneNote: null
    property var noteGesture: null
    property var noteGhost: null
    property var lyricNote: null
    property var pitchGesture: null

    activeFocusOnTab: hasSource
    Accessible.role: Accessible.Pane
    Accessible.name: multi ? timeline.vwaTr("多轨泳道与焦点钢琴卷帘") : timeline.vwaTr("单轨钢琴卷帘")
    Accessible.description: (multi ? timeline.vwaTr("上方泳道只切换焦点，勾选批量目标不会随焦点变化。") : "")
        + (focusedNote ? noteDescription(focusedNote) + "。" : "")
        + (scoreEditable ? vwaTr("双击音符或按 F2 改词。") + " " : "")
        + (noteTool ? vwaTr("拖音符改变位置或音高，拖右端改变时长。") + " " : "") + instructions

    function numberOr(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback
        const parsed = Number(value)
        return isFinite(parsed) ? parsed : fallback
    }

    function clamp(value, low, high) {
        return Math.max(low, Math.min(Math.max(low, high), value))
    }

    function trackColor(index) {
        const colors = ["#4e9af1", "#e0715b", "#51ad72", "#b179d6", "#d2a347", "#42aeb3"]
        return index <= 0 ? theme.accent : colors[index % colors.length]
    }

    function resolveTracks() {
        const ids = multi ? JSON.parse(selectedTrackKey) : [activeTrack]
        const result = Object.create(null)
        let order = 0
        for (let i = 0; i < tracks.length; ++i) {
            const track = tracks[i]
            if (ids.indexOf(String(track.id)) >= 0)
                result[String(track.id)] = { track: track, index: i, order: order++ }
        }
        return result
    }

    function displayLanes() {
        const all = tracks.map((track, index) => ({track: track, index: index, order: index}))
        if (all.length <= 3)
            return all
        const activeIndex = all.findIndex(entry => String(entry.track.id) === activeTrack)
        const start = activeIndex < 0 ? 0 : clamp(activeIndex - 1, 0, all.length - 3)
        return all.slice(start, start + 3)
    }

    function collectNotes(trackIds) {
        const wanted = new Set((trackIds || []).map(String))
        const result = []
        for (const note of notes) {
            const trackId = String(note.trackId)
            const lane = laneTracks.find(entry => String(entry.track.id) === trackId)
            if (!wanted.has(trackId) || (lane === undefined && trackId !== activeTrack))
                continue
            const start = numberOr(note.start, Number.NaN)
            const end = numberOr(note.end, Number.NaN)
            const pitch = numberOr(note.pitch, Number.NaN)
            if (!(end > start && end > visibleStart && start < visibleEnd
                    && Number.isInteger(pitch) && pitch >= 0 && pitch <= 127))
                continue
            result.push({ id: String(note.id), trackId: trackId,
                lane: lane || {track: findTrack(trackId), index: trackOrder(trackId), order: 0},
                lyric: String(note.lyric || "").trim(), pitch: pitch, start: start, end: end,
                readOnlyReason: String(note.readOnlyReason || ""), lyricReason: String(note.lyricReason || ""),
                timingReason: String(note.timingReason || ""), pitchReason: String(note.pitchReason || ""),
                drawReason: String(note.drawReason || "") })
        }
        return result.sort((a, b) => a.start - b.start || a.lane.order - b.lane.order
            || a.pitch - b.pitch || a.end - b.end || a.id.localeCompare(b.id))
    }

    function findTrack(trackId) {
        return tracks.find(entry => String(entry.id) === String(trackId)) || {id: trackId, name: ''}
    }

    function trackOrder(trackId) {
        const index = tracks.findIndex(entry => String(entry.id) === String(trackId))
        return Math.max(0, index)
    }

    function collectRegions() {
        const result = Object.create(null)
        const ids = new Set(laneTracks.map(entry => String(entry.track.id)))
        ids.add(activeTrack)
        for (const region of regions) {
            const id = String(region.trackId)
            if (ids.has(id)) {
                if (!result[id])
                    result[id] = []
                result[id].push(region)
            }
        }
        for (const id of Object.keys(result))
            result[id].sort((a, b) => Number(a.start) - Number(b.start))
        return result
    }

    function visibleStyleBlocks() {
        const blocks = []
        for (let row = 0; row < styledTracks.length; ++row) {
            const track = styledTracks[row]
            for (const region of styleSpans[track] || []) {
                if (region.end > visibleStart && region.start < visibleEnd)
                    blocks.push({region: region, row: row})
            }
        }
        return blocks
    }

    function collectEdges() {
        const all = new Set()
        const perTrack = Object.create(null)
        const snapTracks = new Set(Object.keys(targetTracks))
        for (const note of interactionNotes) {
            if (note.lane.track.editable === false)
                continue
            if (!perTrack[note.trackId])
                perTrack[note.trackId] = new Set()
            for (const edge of [note.start, note.end]) {
                if (snapTracks.has(note.trackId))
                    all.add(edge)
                perTrack[note.trackId].add(edge)
            }
        }
        const byTime = (a, b) => a - b
        const tracks = Object.create(null)
        for (const id of Object.keys(perTrack))
            tracks[id] = Array.from(perTrack[id]).sort(byTime)
        return {all: Array.from(all).sort(byTime), tracks: tracks}
    }

    function snapTime(seconds, modifiers, trackId, ignore) {
        if (!view.snapEnabled || (modifiers & Qt.AltModifier))
            return seconds
        if (ignore && view.beatDivision)
            return controller.snapBeat(seconds)
        const edges = trackId ? noteEdges.tracks[trackId] || [] : noteEdges.all
        if (!edges.length)
            return view.beatDivision ? controller.snapBeat(seconds) : seconds
        let left = 0
        let right = edges.length
        while (left < right) {
            const mid = (left + right) >> 1
            if (edges[mid] < seconds)
                left = mid + 1
            else
                right = mid
        }
        let a = left - 1
        let b = left
        function ignored(edge) { return ignore && (edge === ignore.start || edge === ignore.end) }
        while (a >= 0 && ignored(edges[a])) --a
        while (b < edges.length && ignored(edges[b])) ++b
        const before = a >= 0 ? edges[a] : Infinity
        const after = b < edges.length ? edges[b] : Infinity
        const nearest = Math.abs(seconds - before) <= Math.abs(seconds - after) ? before : after
        if (Math.abs(nearest - seconds) * plotWidth / visibleSpan <= 9 * scale)
            return nearest
        return view.beatDivision ? controller.snapBeat(seconds) : seconds
    }

    function styleForNote(note) {
        if (note.readOnlyReason)
            return null
        const spans = styleSpans[note.trackId]
        if (!spans)
            return null
        let first = 0
        let last = spans.length
        while (first < last) {
            const middle = (first + last) >> 1
            if (Number(spans[middle].end) <= note.start + 0.000001)
                first = middle + 1
            else
                last = middle
        }
        return first < spans.length && Number(spans[first].start) < note.end - 0.000001
            ? spans[first] : null
    }

    function pitchName(pitch) {
        const names = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
        return names[pitch % 12] + String(Math.floor(pitch / 12) - 1)
    }

    function isSharp(pitch) {
        return [1, 3, 6, 8, 10].indexOf(pitch % 12) >= 0
    }

    function yForPitch(pitch) {
        return plotTop + (127 - pitch - pitchOffset) * pitchRowHeight
    }

    function xForTime(value) {
        return plotLeft + (value - visibleStart) / visibleSpan * plotWidth
    }

    function timeForPlotX(value) {
        return visibleStart + clamp(value, 0, plotWidth) / plotWidth * visibleSpan
    }

    function laneY(row) {
        return rulerHeight + row * laneRowHeight
    }

    function laneAt(y) {
        if (!multi || y < rulerHeight || y >= rulerHeight + laneAreaHeight)
            return null
        const index = Math.floor((y - rulerHeight) / laneRowHeight)
        return laneTracks[index] || null
    }

    function laneNoteAt(x, y, trackId) {
        if (x < plotLeft || x > plotRight)
            return null
        const at = timeForPlotX(x - plotLeft)
        const candidates = laneNotes.filter(note => note.trackId === String(trackId)
            && note.start <= at + 0.000001 && note.end >= at - 0.000001)
        return candidates.sort((a, b) => a.end - a.start - (b.end - b.start))[0] || null
    }

    function laneDescription(lane) {
        if (!lane)
            return ""
        const values = laneNotes.filter(note => note.trackId === String(lane.track.id))
        const lyric = values.map(note => note.lyric).join("")
        return String(lane.track.name || lane.track.id) + " · "
            + (lyric || vwaTr("当前时间没有歌词")) + " · " + vwaTr("点击切换焦点");
    }

    function noteDescription(note) {
        const reason = pitchTool ? note.drawReason : noteTool ? note.timingReason || note.pitchReason : note.lyricReason
        return (note.lyric || vwaTr("空歌词")) + " · " + pitchName(note.pitch)
            + " · " + String(note.lane.track.name || note.trackId) + "\n"
            + formatTime(note.start, 3) + "–" + formatTime(note.end, 3)
            + (note.readOnlyReason || (scoreEditable && reason)
                ? "\n" + vwaTr(note.readOnlyReason || reason) : "")
    }

    function formatTime(value, decimals) {
        const factor = Math.pow(10, decimals)
        const units = Math.round(Math.abs(value) * factor)
        const seconds = Math.floor(units / factor)
        const fraction = decimals > 0
            ? "." + String(units % factor).padStart(decimals, "0") : ""
        return (value < 0 ? "−" : "") + String(Math.floor(seconds / 60)).padStart(2, "0")
            + ":" + String(seconds % 60).padStart(2, "0") + fraction
    }

    function fittedText(context, value, maxWidth) {
        if (!value || maxWidth <= 0)
            return ""
        if (context.measureText(value).width <= maxWidth)
            return value
        const characters = Array.from(value)
        let low = 0
        let high = characters.length
        while (low < high) {
            const middle = Math.ceil((low + high) / 2)
            if (context.measureText(characters.slice(0, middle).join("") + "…").width <= maxWidth)
                low = middle
            else
                high = middle - 1
        }
        return low > 0 ? characters.slice(0, low).join("") + "…" : ""
    }

    function appendNoteRects(result, cluster, slots, rowTop) {
        const inset = Math.max(2, Math.round(2 * scale))
        const step = (pitchRowHeight - 2 * inset) / slots
        for (const entry of cluster) {
            const top = rowTop + inset + entry.slot * step
            const bottom = top + step - (slots > 1 ? 1 : 0)
            const y = Math.max(plotTop, top)
            const endY = Math.min(plotBottom, bottom)
            if (endY > y) {
                result.push({ note: entry.note, x: entry.left, y: y,
                    width: entry.right - entry.left, height: endY - y,
                    full: entry.full && y === top && endY === bottom })
            }
        }
    }

    function layoutNotes() {
        // Reserve subrows only where notes collide on the same semitone. Paint
        // and hit testing both use the resulting, clipped rectangles.
        const buckets = Object.create(null)
        for (const note of visibleNotes) {
            const top = yForPitch(note.pitch)
            if (top + pitchRowHeight <= plotTop || top >= plotBottom)
                continue
            if (!buckets[note.pitch])
                buckets[note.pitch] = []
            buckets[note.pitch].push(note)
        }
        const result = []
        const maxSlots = Math.max(1, Math.floor((pitchRowHeight - 4) / 5))
        for (const key of Object.keys(buckets)) {
            const rowTop = yForPitch(Number(key))
            let cluster = []
            let ends = []
            let clusterEnd = -Infinity
            for (const note of buckets[key]) {
                const rawLeft = xForTime(note.start)
                const rawRight = xForTime(note.end)
                const left = Math.max(plotLeft, rawLeft)
                const right = Math.min(plotRight, Math.max(rawLeft + 2, rawRight))
                if (right <= left)
                    continue
                if (left >= clusterEnd && cluster.length) {
                    appendNoteRects(result, cluster, ends.length, rowTop)
                    cluster = []
                    ends = []
                    clusterEnd = -Infinity
                }
                let slot = ends.findIndex(end => end <= left)
                if (slot < 0)
                    slot = ends.length < maxSlots ? ends.length : ends.indexOf(Math.min(...ends))
                ends[slot] = Math.max(ends[slot] || -Infinity, right)
                clusterEnd = Math.max(clusterEnd, right)
                cluster.push({ note: note, left: left, right: right, slot: slot,
                    full: rawLeft >= plotLeft - 0.5 && rawRight <= plotRight + 0.5 })
            }
            appendNoteRects(result, cluster, ends.length || 1, rowTop)
        }
        return result
    }

    function noteBoxAt(x, y) {
        for (let i = noteRects.length - 1; i >= 0; --i) {
            const box = noteRects[i]
            if (x >= box.x && x < box.x + box.width && y >= box.y && y < box.y + box.height)
                return box
        }
        return null
    }

    function resizeHit(box, x) {
        return box && !box.note.timingReason && box.note.end <= visibleEnd
            && box.width >= 9 * scale && x >= box.x + box.width - Math.min(box.width / 3, 9 * scale)
    }

    function dragRequest(gesture, x, y, modifiers) {
        const note = gesture.note
        const dx = x - pressX
        const horizontal = !note.timingReason && Math.abs(dx) >= 2 * scale
        const edge = gesture.action === "resize" ? note.end : note.start
        const seconds = horizontal ? snapTime(edge + dx * visibleSpan / plotWidth, modifiers, note.trackId, note) : edge
        const pitch = gesture.action === "move" && !note.pitchReason
            ? clamp(note.pitch - Math.round((y - pressY) / pitchRowHeight), 0, 127) : note.pitch
        return {seconds: seconds, pitch: pitch, horizontal: horizontal}
    }

    function pitchNoteAt(x, y) {
        const exact = noteBoxAt(plotLeft + x, plotTop + y)
        if (exact)
            return exact.note
        const time = timeForPlotX(x)
        let nearest = null
        let distance = ((view.pitchMaxCents || 200) / 100 + 0.65) * pitchRowHeight
        for (const note of visibleNotes) {
            const gap = Math.abs(plotTop + y - yForPitch(note.pitch) - pitchRowHeight / 2)
            if (note.start <= time && time < note.end && gap < distance) {
                distance = gap
                nearest = note
            }
        }
        return nearest
    }

    function pitchSample(x, y, modifiers, force) {
        const gesture = pitchGesture
        if (!gesture)
            return
        const note = gesture.note
        const edge = Number(view.pitchEdgeSeconds || 0.018)
        const seconds = clamp(timeForPlotX(x), note.start + edge, note.end - edge)
        const range = Number(view.pitchMaxCents || 200)
        const pitch = (yForPitch(note.pitch) + pitchRowHeight / 2 - (plotTop + y)) / pitchRowHeight * 100
        const snap = view.snapEnabled && !(modifiers & Qt.AltModifier) ? 10 : 1
        const sample = [seconds, clamp(Math.round(pitch / snap) * snap, -range, range)]
        const points = gesture.points
        const last = points.length ? points[points.length - 1] : null
        if (!force && last && Math.abs(xForTime(last[0]) - xForTime(seconds)) < 2 * scale
                && Math.abs(last[1] - sample[1]) < 4)
            return
        if (points.length >= 2048)
            points[points.length - 1] = sample
        else
            points.push(sample)
        pitchInk.requestPaint()
    }

    function movePitch(rows) {
        const next = clamp(pitchOffset + rows, 0, 128 - rowsInView)
        if (next === pitchOffset)
            return false
        topRow = next
        return true
    }

    function revealPitch(pitch, trackId, noteId) {
        const midi = numberOr(pitch, Number.NaN)
        if (!isFinite(midi) || midi < 0 || midi > 127)
            return
        const row = 127 - midi
        if (row < pitchOffset || row + 1 > pitchOffset + rowsInView)
            topRow = clamp(Math.round(row - (rowsInView - 1) / 2), 0, 128 - rowsInView)
        if (noteId !== undefined) {
            focusedTrackId = String(trackId)
            focusedNoteId = String(noteId)
        }
        centerOnSource = false
        checkTargetPitch = false
    }

    Timer {
        id: pitchSuggestion
        interval: 0
        onTriggered: timeline.suggestPitch()
    }

    function suggestPitch() {
        if (!visibleNotes.length || (!centerOnSource && !checkTargetPitch))
            return
        if (centerOnSource || !noteRects.length) {
            const first = visibleNotes.find(note => note.trackId === activeTrack) || visibleNotes[0]
            topRow = clamp(Math.round(127 - first.pitch - (rowsInView - 1) / 2), 0, 128 - rowsInView)
        }
        centerOnSource = false
        checkTargetPitch = false
    }

    function moveNoteCursor(direction) {
        const anchor = selectedNotes.length ? selectedNotes[selectedNotes.length - 1] : focusedNoteId
        const next = controller.adjacentNote(anchor, direction > 0 ? visibleStart : visibleEnd, direction)
        if (!next.id)
            return false
        locateNote(next)
        return true
    }

    function pitchKey(direction, event) {
        if (gestureType)
            return true
        const note = view.selectedNote || {}
        const modifiers = event.modifiers
        if (noteTool && note.id && (modifiers === Qt.NoModifier || modifiers === Qt.ControlModifier)) {
            const next = note.pitch + direction * (modifiers === Qt.ControlModifier ? 12 : 1)
            if (next >= 0 && next <= 127 && controller.editNoteGeometry(note.id, "move", note.start, next, false))
                revealPitch(next, note.trackId, note.id)
            return true
        }
        return movePitch(-direction)
    }

    function selectNote(note) {
        if (!note || view.busy === true)
            return
        revealPitch(note.pitch, note.trackId, note.id)
        controller.selectNote(note.id)
        if (!multi && !styleTool && !pitchTool)
            noteFocused()
    }

    function locateNote(note, preserveSelection) {
        if (note.start < visibleStart || note.start >= visibleEnd)
            controller.setViewport(note.start - visibleSpan * 0.15, visibleSpan)
        if (preserveSelection)
            revealPitch(note.pitch, note.trackId, note.id)
        else
            selectNote(note)
    }

    function selectNoteWithModifiers(note, modifiers) {
        if (noteTool && (modifiers & Qt.ControlModifier)) {
            const ids = selectedNotes.filter(identity => identity !== note.id)
            if (!selectedNoteSet.has(note.id)) ids.push(note.id)
            controller.selectNotes(ids)
            const current = view.selectedNote || {}
            if (current.id) revealPitch(current.pitch, current.trackId, current.id)
            else focusedNoteId = focusedTrackId = ""
            noteFocused()
        } else if (noteTool && (modifiers & Qt.ShiftModifier)) {
            controller.extendNoteSelection(note.id)
            revealPitch(note.pitch, note.trackId, note.id)
            noteFocused()
        } else {
            selectNote(note)
        }
    }

    function clearSelection() {
        focusedNoteId = ""
        focusedTrackId = ""
        controller.clearSelection()
    }

    function openLyric(note) {
        if (!scoreEditable || !note || note.trackId !== activeTrack || note.lyricReason)
            return false
        clearGesture()
        locateNote(note)
        lyricNote = note
        lyricInput.text = note.lyric
        lyricInput.forceActiveFocus(Qt.OtherFocusReason)
        lyricInput.selectAll()
        return true
    }

    function closeLyric() {
        if (lyricNote)
            lyricNote = null
    }

    function finishLyric(direction) {
        if (!lyricNote || lyricInput.inputMethodComposing)
            return
        const noteId = lyricNote.id
        if (!controller.editNoteLyric(noteId, lyricInput.text))
            return
        const next = direction ? controller.nextEditableLyric(noteId, direction) : null
        if (!next || !next.id || !openLyric(next)) {
            closeLyric()
            timeline.forceActiveFocus(Qt.OtherFocusReason)
        }
    }

    function zoomAt(factor, anchorTime) {
        const next = clamp(visibleSpan * factor, Math.min(fullSpan, 0.5), fullSpan)
        const ratio = clamp((anchorTime - visibleStart) / visibleSpan, 0, 1)
        controller.setViewport(clamp(anchorTime - next * ratio, origin, fullEnd - next), next)
    }

    function panBy(seconds) {
        controller.setViewport(clamp(visibleStart + seconds, origin, fullEnd - visibleSpan), visibleSpan)
    }

    function beginPan(x, y) {
        return { x: x, y: y, left: visibleStart, top: pitchOffset, span: visibleSpan }
    }

    function movePan(pan, x, y) {
        controller.setViewport(pan.left - (x - pan.x) * pan.span / plotWidth, pan.span)
        topRow = clamp(pan.top - (y - pan.y) / pitchRowHeight, 0, 128 - rowsInView)
    }

    function viewportWheel(wheel, plotX) {
        const vertical = wheel.pixelDelta.y || wheel.angleDelta.y
        const horizontal = wheel.pixelDelta.x || wheel.angleDelta.x
        if (!vertical && !horizontal)
            return false
        if (wheel.modifiers & Qt.AltModifier)
            return vertical !== 0 && movePitch(vertical > 0 ? -2 : 2)
        if ((wheel.modifiers & Qt.ShiftModifier) || Math.abs(horizontal) > Math.abs(vertical))
            panBy(((horizontal || vertical) > 0 ? -1 : 1) * visibleSpan * 0.12)
        else
            zoomAt(vertical > 0 ? 0.82 : 1.22, timeForPlotX(plotX))
        return true
    }

    function startRange(x, y, modifiers) {
        pressX = x
        pressY = y
        selectOnly = !!(modifiers & Qt.ShiftModifier)
        gestureType = styleTool && !selectOnly ? "style" : "range"
        pressTime = snapTime(timeForPlotX(x), modifiers, "")
        dragTime = pressTime
    }

    function finishRange(x, modifiers) {
        const kind = gestureType
        const moved = Math.abs(x - pressX) >= 3 * scale
        const end = snapTime(timeForPlotX(x), modifiers, "")
        const first = Math.min(pressTime, end)
        const last = Math.max(pressTime, end)
        clearGesture()
        if (view.busy || !moved)
            return false
        if (last > first && kind === "style")
            controller.finishTimelineSelection(first, last)
        else if (last > first)
            controller.selectSpan(first, last)
        return true
    }

    function finishMarquee(x, y) {
        const left = Math.min(x, pressX) + plotLeft
        const right = Math.max(x, pressX) + plotLeft
        const top = Math.min(y, pressY) + plotTop
        const bottom = Math.max(y, pressY) + plotTop
        const moved = right - left >= 3 * scale || bottom - top >= 3 * scale
        clearGesture()
        if (!moved) {
            clearSelection()
            return
        }
        const hits = noteRects.filter(box => box.x < right && box.x + box.width > left
            && box.y < bottom && box.y + box.height > top)
        controller.selectNotes(hits.map(box => box.note.id))
        if (hits.length) {
            const note = hits[hits.length - 1].note
            revealPitch(note.pitch, note.trackId, note.id)
            noteFocused()
        }
    }

    function clearGesture() {
        noteGesture = null
        noteGhost = null
        if (pitchGesture) {
            pitchGesture = null
            pitchInk.requestPaint()
        }
        pressX = -1
        pressY = -1
        pressTime = Number.NaN
        dragTime = Number.NaN
        selectOnly = false
        gestureType = ""
        marqueeEnd = null
        panGesture = null
    }

    function emptyMessage() {
        if (!hasSource)
            return "载入调音工程后显示钢琴卷帘"
        if (!visibleNoteCount)
            return multi ? "焦点轨在当前时间没有音符；点击泳道切换焦点，或勾选批量目标"
                : "当前时间没有音符，可搜索歌词或平移"
        return ""
    }

    onSourceKeyChanged: {
        closeLyric()
        clearGesture()
        focusedTrackId = ""
        focusedNoteId = ""
        centerOnSource = true
        pitchSuggestion.restart()
    }
    onTargetKeyChanged: score.requestPaint()
    onVisibleNotesChanged: {
        pitchSuggestion.restart()
        score.requestPaint()
    }
    onLaneNotesChanged: score.requestPaint()
    onLaneTracksChanged: score.requestPaint()
    onNoteRectsChanged: score.requestPaint()
    onStyleSpansChanged: score.requestPaint()
    onActiveTrackChanged: {
        closeLyric()
        clearGesture()
        score.requestPaint()
    }
    onScoreEditableChanged: if (!scoreEditable) closeLyric()
    onNoteToolChanged: {
        clearGesture()
        score.requestPaint()
    }
    onPitchToolChanged: clearGesture()
    onStyleToolChanged: clearGesture()
    onVisibleChanged: if (!visible) { closeLyric(); clearGesture() }
    onVisibleStartChanged: score.requestPaint()
    onVisibleSpanChanged: score.requestPaint()
    onPitchOffsetChanged: score.requestPaint()
    onPitchRowHeightChanged: score.requestPaint()
    onSelectionStartChanged: score.requestPaint()
    onSelectionEndChanged: score.requestPaint()
    onBeatMarksChanged: score.requestPaint()
    onDragTimeChanged: score.requestPaint()
    onFocusedNoteChanged: score.requestPaint()
    onSelectedNoteSetChanged: score.requestPaint()
    onMarqueeEndChanged: score.requestPaint()
    onNoteGhostChanged: score.requestPaint()
    onHoveredNoteChanged: score.requestPaint()
    onHoveredLaneNoteChanged: score.requestPaint()
    onActiveFocusChanged: score.requestPaint()
    onDarkChanged: score.requestPaint()
    onWidthChanged: score.requestPaint()
    onHeightChanged: score.requestPaint()
    Component.onCompleted: {
        pitchSuggestion.restart()
        score.requestPaint()
    }

    Connections {
        target: timeline.controller
        function onNoteLocated(pitch, track, identity) { timeline.revealPitch(pitch, track, identity) }
    }

    Keys.onLeftPressed: event => event.accepted = !!gestureType || moveNoteCursor(-1)
    Keys.onRightPressed: event => event.accepted = !!gestureType || moveNoteCursor(1)
    Keys.onUpPressed: event => event.accepted = pitchKey(1, event)
    Keys.onDownPressed: event => event.accepted = pitchKey(-1, event)
    Keys.onPressed: function(event) {
        if (gestureType) {
            event.accepted = true
        } else if (event.key === Qt.Key_F2 && scoreEditable) {
            event.accepted = openLyric(focusedNote)
        } else if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
            event.accepted = movePitch((event.key === Qt.Key_PageUp ? -1 : 1)
                * Math.max(1, Math.floor(rowsInView) - 1))
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                   || event.key === Qt.Key_Space) {
            if (!focusedNote)
                moveNoteCursor(1)
            if (focusedNote) {
                selectNote(focusedNote)
                event.accepted = true
            }
        }
    }

    Canvas {
        id: score
        anchors.fill: parent
        antialiasing: true

        function fill(ctx, color, x, y, width, height) {
            ctx.fillStyle = color
            ctx.fillRect(x, y, width, height)
        }

        function paintKeysAndRows(ctx) {
            const keyWidth = timeline.plotLeft
            const height = timeline.pitchRowHeight
            const font = Math.round(11 * timeline.scale)
            ctx.save()
            ctx.beginPath()
            ctx.rect(0, timeline.plotTop, timeline.plotRight, timeline.plotHeight)
            ctx.clip()
            ctx.textAlign = "left"
            ctx.textBaseline = "middle"
            for (let row = Math.floor(timeline.pitchOffset); row < 128; ++row) {
                const pitch = 127 - row
                const y = timeline.yForPitch(pitch)
                if (y >= timeline.plotBottom)
                    break
                const sharp = timeline.isSharp(pitch)
                const octave = pitch % 12 === 0
                fill(ctx, sharp ? (timeline.dark ? "#20212b" : "#eeedf4") : timeline.theme.base,
                    keyWidth, y, timeline.plotWidth, height)
                fill(ctx, timeline.dark ? "#cbced8" : "#f9f9fc", 0, y, keyWidth, height)
                if (sharp) {
                    fill(ctx, "#303440", 0, y + 1, Math.round(keyWidth * 0.77), height - 2)
                    fill(ctx, "#454a5b", Math.round(keyWidth * 0.77) - 3, y + 1, 3, height - 2)
                }
                fill(ctx, octave ? "#7f8193" : "#a7a9b8", 0, y, keyWidth, 1)
                fill(ctx, octave ? timeline.theme.muted : timeline.theme.line,
                    keyWidth, y, timeline.plotWidth, octave ? 1.5 : 1)
                ctx.font = (octave ? "bold " : "") + font + "px sans-serif"
                ctx.fillStyle = sharp ? "#fbfbff" : octave ? "#202236" : "#505266"
                ctx.fillText(timeline.pitchName(pitch), Math.round(6 * timeline.scale), y + height / 2)
            }
            fill(ctx, timeline.theme.line, keyWidth - 1, timeline.plotTop, 1, timeline.plotHeight)
            ctx.restore()
        }

        function paintRuler(ctx) {
            const font = Math.round(11 * timeline.scale)
            ctx.font = font + "px sans-serif"
            ctx.textBaseline = "middle"
            const marks = timeline.beatMarks
            if (marks.length > 0) {
                let labelEnd = timeline.plotLeft
                ctx.fillStyle = timeline.theme.muted
                ctx.fillText("音高", Math.round(7 * timeline.scale), timeline.rulerHeight / 2)
                for (const mark of marks) {
                    const x = Math.round(timeline.xForTime(mark[0]))
                    if (x < timeline.plotLeft || x >= timeline.plotRight)
                        continue
                    const bar = mark[1] === 2
                    const tick = bar ? 8 : mark[1] === 1 ? 6 : 3
                    ctx.globalAlpha = bar ? 0.53 : mark[1] === 1 ? 0.29 : 0.14
                    fill(ctx, timeline.theme.accent, x, timeline.rulerHeight - tick,
                        1, timeline.height - timeline.rulerHeight + tick)
                    ctx.globalAlpha = 1
                    if (bar && x >= labelEnd) {
                        const label = String(mark[2]) + (mark[3] ? "  " + mark[3] : "")
                        const end = x + 5 + ctx.measureText(label).width
                        if (end <= timeline.plotRight) {
                            ctx.fillStyle = timeline.theme.muted
                            ctx.fillText(label, x + 5, timeline.rulerHeight / 2)
                            labelEnd = end + Math.round(12 * timeline.scale)
                        }
                    }
                }
                fill(ctx, timeline.theme.line, 0, timeline.rulerHeight - 1, width, 1)
                return
            }
            const desired = timeline.visibleSpan * Math.round(74 * timeline.scale) / timeline.plotWidth
            const unit = Math.pow(10, Math.floor(Math.log10(desired)))
            const ratio = desired / unit
            const step = (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 5 ? 5 : 10) * unit
            const minor = step / 2
            const first = Math.ceil((timeline.visibleStart - 0.000001) / minor)
            const last = Math.floor((timeline.visibleEnd + 0.000001) / minor)
            const decimals = Math.max(0, Math.min(3, -Math.floor(Math.log10(step) + 0.000001)))
            ctx.fillStyle = timeline.theme.muted
            ctx.fillText("音高", Math.round(7 * timeline.scale), timeline.rulerHeight / 2)
            for (let tick = first; tick <= last; ++tick) {
                const x = Math.round(timeline.xForTime(tick * minor))
                if (x < timeline.plotLeft || x >= timeline.plotRight)
                    continue
                const major = tick % 2 === 0
                ctx.globalAlpha = major ? 0.9 : 0.5
                fill(ctx, timeline.theme.line, x, timeline.rulerHeight, 1,
                    Math.max(0, timeline.height - timeline.rulerHeight))
                ctx.globalAlpha = 1
                if (major) {
                    const label = timeline.formatTime(tick * minor, decimals)
                    const textWidth = ctx.measureText(label).width
                    if (x + textWidth + 4 <= timeline.plotRight) {
                        ctx.fillStyle = timeline.theme.muted
                        ctx.fillText(label, x + 4, timeline.rulerHeight / 2)
                    }
                }
                fill(ctx, timeline.theme.line, x, timeline.rulerHeight - (major ? 6 : 3),
                    1, major ? 6 : 3)
            }
            ctx.globalAlpha = 1
            fill(ctx, timeline.theme.line, 0, timeline.rulerHeight - 1, width, 1)
        }

        function paintLanes(ctx) {
            if (!timeline.multi)
                return
            const font = Math.round(10 * timeline.scale)
            const noteFont = Math.round(9 * timeline.scale)
            for (let row = 0; row < timeline.laneTracks.length; ++row) {
                const lane = timeline.laneTracks[row]
                const trackId = String(lane.track.id)
                const top = timeline.laneY(row)
                const active = trackId === timeline.activeTrack
                const trackColor = timeline.trackColor(lane.index)
                const values = timeline.laneNotes.filter(note => note.trackId === trackId)
                fill(ctx, active ? timeline.theme.raised
                    : (row % 2 ? timeline.theme.base : timeline.theme.surface),
                    0, top, width, timeline.laneRowHeight)
                if (active) {
                    ctx.globalAlpha = 0.12
                    fill(ctx, timeline.theme.accent, 0, top, width, timeline.laneRowHeight)
                    ctx.globalAlpha = 1
                }
                fill(ctx, trackColor, 0, top, Math.max(2, Math.round(3 * timeline.scale)),
                    timeline.laneRowHeight)
                ctx.font = (active ? "bold " : "") + font + "px sans-serif"
                ctx.fillStyle = active ? timeline.theme.text : timeline.theme.muted
                ctx.textAlign = "left"
                ctx.textBaseline = "middle"
                ctx.fillText(timeline.fittedText(ctx, String(lane.track.name || trackId),
                    timeline.plotLeft - Math.round(13 * timeline.scale)),
                    Math.round(7 * timeline.scale), top + timeline.laneRowHeight / 2)
                fill(ctx, timeline.theme.line, 0, top + timeline.laneRowHeight - 1,
                    timeline.plotRight, 1)

                ctx.save()
                ctx.beginPath()
                ctx.rect(timeline.plotLeft, top, timeline.plotWidth, timeline.laneRowHeight)
                ctx.clip()
                for (const region of timeline.styleSpans[trackId] || []) {
                    if (region.end <= timeline.visibleStart || region.start >= timeline.visibleEnd)
                        continue
                    const x = Math.max(timeline.plotLeft, timeline.xForTime(Number(region.start)))
                    const end = Math.min(timeline.plotRight, timeline.xForTime(Number(region.end)))
                    if (end > x) {
                        ctx.globalAlpha = 0.1
                        fill(ctx, region.color || trackColor, x, top, end - x,
                            timeline.laneRowHeight)
                        ctx.globalAlpha = 1
                    }
                }

                if (!values.length) {
                    ctx.font = font + "px sans-serif"
                    ctx.fillStyle = timeline.theme.muted
                    ctx.fillText(timeline.vwaTr("当前时间没有歌词"), timeline.plotLeft + 5,
                        top + timeline.laneRowHeight / 2)
                } else {
                    let minimumPitch = 127
                    let maximumPitch = 0
                    for (const note of values) {
                        minimumPitch = Math.min(minimumPitch, note.pitch)
                        maximumPitch = Math.max(maximumPitch, note.pitch)
                    }
                    const barTop = top + timeline.regionBandHeight + 4 * timeline.scale
                    const barBottom = top + timeline.laneRowHeight - 13 * timeline.scale
                    const span = Math.max(1, maximumPitch - minimumPitch)
                    ctx.font = noteFont + "px sans-serif"
                    ctx.textBaseline = "middle"
                    for (const note of values) {
                        const rawLeft = timeline.xForTime(note.start)
                        const rawRight = timeline.xForTime(note.end)
                        const left = Math.max(timeline.plotLeft, rawLeft)
                        const right = Math.min(timeline.plotRight,
                            Math.max(rawLeft + 2 * timeline.scale, rawRight))
                        if (right <= left)
                            continue
                        const ratio = (note.pitch - minimumPitch) / span
                        const y = barBottom - ratio * Math.max(2, barBottom - barTop)
                        const style = timeline.styleForNote(note)
                        const color = style && style.color ? style.color : trackColor
                        fill(ctx, color, left, y, right - left,
                            Math.max(3 * timeline.scale, 1.8 * timeline.scale))
                        if (note === timeline.hoveredLaneNote) {
                            ctx.strokeStyle = timeline.theme.text
                            ctx.lineWidth = 1
                            ctx.strokeRect(left + 0.5, y - 1.5, Math.max(0, right - left - 1),
                                Math.max(1, 4.5 * timeline.scale))
                        }
                        const label = timeline.fittedText(ctx, note.lyric,
                            right - left - Math.round(2 * timeline.scale))
                        if (label) {
                            ctx.fillStyle = active ? timeline.theme.text : timeline.theme.muted
                            ctx.fillText(label, left + timeline.scale,
                                top + timeline.laneRowHeight - Math.round(6 * timeline.scale))
                        }
                    }
                }
                ctx.restore()
            }

            if (!timeline.laneTracks.length) {
                ctx.font = font + "px sans-serif"
                ctx.fillStyle = timeline.theme.muted
                ctx.fillText(timeline.vwaTr("勾选批量目标后显示逐轨泳道"),
                    timeline.plotLeft + 5, timeline.rulerHeight + timeline.laneRowHeight / 2)
            }
            const headerY = timeline.laneY(timeline.laneTracks.length)
            fill(ctx, timeline.theme.raised, 0, headerY, width, timeline.focusHeaderHeight)
            fill(ctx, timeline.theme.accent, 0, headerY, Math.round(3 * timeline.scale),
                timeline.focusHeaderHeight)
            const focus = timeline.targetTracks[timeline.activeTrack]
            const focusName = focus ? String(focus.track.name)
                : String(timeline.findTrack(timeline.activeTrack).name || timeline.activeTrack || "—")
            ctx.font = "bold " + font + "px sans-serif"
            ctx.fillStyle = timeline.theme.text
            ctx.textAlign = "left"
            ctx.textBaseline = "middle"
            const hidden = timeline.hiddenLaneCount > 0
                ? " · " + timeline.vwaTr("另有 {count} 轨，从焦点框选择").replace("{count}", timeline.hiddenLaneCount)
                : ""
            ctx.fillText(timeline.fittedText(ctx,
                timeline.vwaTr("焦点钢琴卷帘") + " · " + focusName + hidden,
                timeline.plotRight - timeline.plotLeft - Math.round(10 * timeline.scale)),
                timeline.plotLeft + Math.round(5 * timeline.scale),
                headerY + timeline.focusHeaderHeight / 2)
            fill(ctx, timeline.theme.line, 0, headerY + timeline.focusHeaderHeight - 1,
                width, 1)
        }

        function paintRegions(ctx) {
            if (timeline.multi)
                return
            ctx.font = Math.round(10 * timeline.scale) + "px sans-serif"
            for (let row = 0; row < timeline.regionRowCount; ++row) {
                const y = timeline.rulerHeight + row * timeline.regionRowHeight
                fill(ctx, timeline.theme.raised, 0, y, width, timeline.regionRowHeight)
                fill(ctx, timeline.theme.line, 0, y + timeline.regionRowHeight - 1, width, 1)
                const track = timeline.styledTracks[row]
                const label = timeline.vwaTr("风格")
                ctx.fillStyle = timeline.theme.muted
                ctx.fillText(timeline.fittedText(ctx, label, timeline.plotLeft - 10), 6, y + timeline.regionRowHeight / 2)
                if (!(timeline.styleSpans[track] || []).length) {
                    ctx.fillText(timeline.vwaTr("还未应用片段"), timeline.plotLeft + 5, y + timeline.regionRowHeight / 2)
                }
            }
        }

        function paintSelection(ctx, first, last, drag) {
            if (!(last > first))
                return
            const x = Math.max(timeline.plotLeft, timeline.xForTime(first))
            const end = Math.min(timeline.plotRight, timeline.xForTime(last))
            if (end <= x)
                return
            ctx.globalAlpha = drag ? 0.19 : 0.12
            fill(ctx, timeline.theme.accent, x, timeline.rulerHeight, end - x,
                Math.max(0, timeline.height - timeline.rulerHeight))
            ctx.globalAlpha = drag ? 0.95 : 0.7
            fill(ctx, timeline.theme.accent, x, timeline.rulerHeight, 1,
                Math.max(0, timeline.height - timeline.rulerHeight))
            fill(ctx, timeline.theme.accent, Math.max(x, end - 1), timeline.rulerHeight, 1,
                Math.max(0, timeline.height - timeline.rulerHeight))
            ctx.globalAlpha = 1
        }

        function paintGesture(ctx) {
            ctx.save()
            ctx.beginPath()
            ctx.rect(timeline.plotLeft, timeline.plotTop, timeline.plotWidth, timeline.plotHeight)
            ctx.clip()
            if (timeline.marqueeEnd) {
                const x = timeline.plotLeft + Math.min(timeline.pressX, timeline.marqueeEnd.x)
                const y = timeline.plotTop + Math.min(timeline.pressY, timeline.marqueeEnd.y)
                const w = Math.abs(timeline.pressX - timeline.marqueeEnd.x)
                const h = Math.abs(timeline.pressY - timeline.marqueeEnd.y)
                ctx.globalAlpha = .18
                fill(ctx, timeline.theme.accent, x, y, w, h)
                ctx.globalAlpha = 1
                ctx.strokeStyle = timeline.theme.accent
                ctx.lineWidth = 1
                ctx.strokeRect(x, y, w, h)
            }
            const preview = timeline.noteGhost || {}
            for (const note of preview.notes || []) {
                const x = timeline.xForTime(note.start)
                const y = timeline.yForPitch(note.pitch) + 2
                const w = Math.max(2, timeline.xForTime(note.end) - x)
                const h = timeline.pitchRowHeight - 4
                ctx.globalAlpha = .78
                fill(ctx, timeline.theme.accent, x, y, w, h)
                ctx.globalAlpha = 1
                ctx.strokeStyle = timeline.theme.text
                ctx.lineWidth = 1
                ctx.strokeRect(x + .5, y + .5, w - 1, h - 1)
            }
            ctx.restore()
        }

        function paintNotes(ctx) {
            const font = Math.round(12 * timeline.scale)
            const pad = Math.round(5 * timeline.scale)
            ctx.font = font + "px sans-serif"
            for (const box of timeline.noteRects) {
                const note = box.note
                const track = timeline.trackColor(note.lane.index)
                const style = timeline.styleForNote(note)
                const color = style && style.color ? style.color : track
                ctx.globalAlpha = note.readOnlyReason || note.lane.track.editable === false ? 0.55 : 1
                fill(ctx, color, box.x, box.y, box.width, box.height)
                if (timeline.multi && style)
                    fill(ctx, track, box.x, box.y, Math.min(box.width, 3 * timeline.scale), box.height)
                ctx.globalAlpha = 1
                if (box.full && box.height >= font + 2 && box.width >= Math.round(18 * timeline.scale)) {
                    const label = timeline.fittedText(ctx, note.lyric, box.width - 2 * pad)
                    if (label) {
                        const rgb = Qt.darker(color, 1)
                        ctx.fillStyle = 0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b > 0.58
                            ? "#121522" : "#ffffff"
                        ctx.fillText(label, box.x + pad, box.y + box.height / 2)
                    }
                }
                const selected = timeline.selectedNoteSet.has(note.id)
                    || (timeline.activeFocus && note === timeline.focusedNote && !timeline.selectedNotes.length)
                if (selected || note === timeline.hoveredNote) {
                    ctx.strokeStyle = selected ? timeline.theme.text : timeline.theme.muted
                    ctx.lineWidth = 1
                    ctx.strokeRect(box.x + 0.5, box.y + 0.5,
                        Math.max(0, box.width - 1), Math.max(0, box.height - 1))
                    if (timeline.noteTool && !note.timingReason && note.end <= timeline.visibleEnd
                            && box.width >= 9 * timeline.scale)
                        fill(ctx, timeline.theme.text, box.x + box.width - 4 * timeline.scale,
                            box.y + 3, Math.round(timeline.scale), Math.max(1, box.height - 6))
                }
            }
        }

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            fill(ctx, timeline.theme.surface, 0, 0, width, height)
            ctx.textBaseline = "middle"
            ctx.textAlign = "left"
            paintLanes(ctx)
            paintKeysAndRows(ctx)
            paintRegions(ctx)
            paintRuler(ctx)
            if (!timeline.selectedNotes.length)
                paintSelection(ctx, timeline.selectionStart, timeline.selectionEnd, false)
            paintSelection(ctx, Math.min(timeline.pressTime, timeline.dragTime),
                Math.max(timeline.pressTime, timeline.dragTime), true)
            paintNotes(ctx)
            paintGesture(ctx)
        }
    }

    Rectangle {
        x: visible ? timeline.xForTime(timeline.cursorTime) : 0
        y: timeline.rulerHeight
        width: 1
        height: Math.max(0, timeline.height - y)
        color: timeline.theme.accent
        opacity: 0.75
        visible: timeline.cursorTime >= timeline.visibleStart && timeline.cursorTime <= timeline.visibleEnd
        z: 2
    }

    Repeater {
        id: laneInputs
        model: timeline.multi ? timeline.laneTracks : []
        Item {
            id: laneInput
            required property var modelData
            required property int index
            objectName: "tuningTrackLane_" + String(modelData.track.id)
            x: 0
            y: timeline.laneY(index)
            width: timeline.plotRight
            height: timeline.laneRowHeight
            Accessible.role: Accessible.Pane
            Accessible.name: String(modelData.track.name || modelData.track.id)
            Accessible.description: timeline.laneDescription(modelData)

            MouseArea {
                id: laneHit
                property bool headerPress: false
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton
                hoverEnabled: true
                preventStealing: true
                cursorShape: Qt.PointingHandCursor
                onPressed: mouse => {
                    if (timeline.view.busy) {
                        mouse.accepted = false
                        return
                    }
                    headerPress = mouse.button === Qt.LeftButton && mouse.x < timeline.plotLeft
                    timeline.forceActiveFocus(Qt.MouseFocusReason)
                    if (mouse.button === Qt.MiddleButton) {
                        timeline.gestureType = "pan"
                        timeline.panGesture = timeline.beginPan(mouse.x, mouse.y)
                    } else if (mouse.x >= timeline.plotLeft) {
                        timeline.startRange(mouse.x - timeline.plotLeft, mouse.y, mouse.modifiers)
                    }
                }
                onPositionChanged: function(mouse) {
                    if (pressed && timeline.panGesture) {
                        timeline.movePan(timeline.panGesture, mouse.x, mouse.y)
                        return
                    }
                    if (pressed && isFinite(timeline.pressTime))
                        timeline.dragTime = timeline.snapTime(timeline.timeForPlotX(mouse.x - timeline.plotLeft), mouse.modifiers, "")
                    timeline.hoveredLaneNote = timeline.laneNoteAt(mouse.x,
                        mouse.y + laneInput.y, modelData.track.id)
                }
                onExited: {
                    if (timeline.hoveredLaneNote
                            && timeline.hoveredLaneNote.trackId === String(modelData.track.id))
                        timeline.hoveredLaneNote = null
                }
                onReleased: function(mouse) {
                    const header = headerPress
                    headerPress = false
                    if (timeline.view.busy || mouse.button !== Qt.LeftButton) {
                        timeline.clearGesture()
                        return
                    }
                    if (!header && !timeline.gestureType)
                        return
                    if (isFinite(timeline.pressTime) && timeline.finishRange(mouse.x - timeline.plotLeft, mouse.modifiers))
                        return
                    if (mouse.x < 0 || mouse.x > width || mouse.y < 0 || mouse.y > height)
                        return
                    const note = timeline.laneNoteAt(mouse.x, mouse.y + laneInput.y,
                        modelData.track.id)
                    if (note)
                        timeline.selectNote(note)
                    else
                        timeline.controller.focusTrack(String(modelData.track.id))
                }
                onCanceled: { headerPress = false; timeline.clearGesture() }
                onWheel: wheel => wheel.accepted = pressed || timeline.viewportWheel(wheel, wheel.x - timeline.plotLeft)
                ToolTip.visible: containsMouse
                ToolTip.delay: 500
                ToolTip.text: timeline.laneDescription(modelData)
            }
        }
    }

    Repeater {
        model: timeline.styleBlocks
        Item {
            id: styleBlock
            required property var modelData
            readonly property var region: modelData.region
            readonly property color tint: region.color || timeline.theme.accent
            readonly property real firstX: timeline.xForTime(Number(region.start))
            readonly property real lastX: timeline.xForTime(Number(region.end))
            x: Math.max(timeline.plotLeft, firstX)
            y: timeline.rulerHeight + modelData.row * timeline.regionRowHeight + 1
            width: Math.max(0, Math.min(timeline.plotRight, lastX) - x)
            height: timeline.regionBandHeight
            visible: width > 0

            function openRegion() {
                timeline.regionFocused()
                timeline.controller.editRegion(String(region.id))
            }

            Button {
                id: regionButton
                objectName: "tuningStyleBand_" + String(styleBlock.region.id)
                anchors.fill: parent
                hoverEnabled: true
                padding: 0
                leftPadding: Math.round(5 * timeline.scale)
                rightPadding: leftPadding
                text: String(styleBlock.region.label)
                enabled: !timeline.view.busy
                Accessible.name: styleBlock.region.trackName + " · " + text
                ToolTip.visible: (styleDrag.containsMouse || visualFocus) && !styleDrag.pressed
                ToolTip.text: Accessible.name + " · " + timeline.formatTime(styleBlock.region.start, 3)
                    + " – " + timeline.formatTime(styleBlock.region.end, 3)
                ToolTip.delay: 500
                onClicked: styleBlock.openRegion()
                contentItem: Text {
                    text: regionButton.text
                    color: "#ffffff"
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                    font.pixelSize: Math.round(10 * timeline.scale)
                    textFormat: Text.PlainText
                }
                background: Rectangle {
                    radius: 3
                    color: Qt.darker(styleBlock.tint, styleDrag.containsMouse ? 1 : 1.25)
                    border.color: regionButton.visualFocus || styleBlock.region.id === timeline.view.selectedRegion
                        ? timeline.theme.text : styleBlock.tint
                    Repeater {
                        model: styleDrag.containsMouse && parent.width >= 26 ? 2 : 0
                        Rectangle {
                            required property int index
                            x: index === 0 ? 2 : parent.width - 4
                            y: 3
                            width: 2
                            height: Math.max(0, parent.height - 6)
                            radius: 1
                            color: "#ffffff"
                            opacity: 0.65
                        }
                    }
                }
            }

            Rectangle {
                visible: styleDrag.pressed && styleDrag.moved && timeline.gestureType === "clip"
                x: Math.max(timeline.plotLeft, timeline.xForTime(styleDrag.start)) - styleBlock.x
                width: Math.max(0, Math.min(timeline.plotRight, timeline.xForTime(styleDrag.end))
                    - styleBlock.x - x)
                height: parent.height
                border.color: timeline.theme.text
                color: Qt.rgba(styleBlock.tint.r, styleBlock.tint.g, styleBlock.tint.b, 0.55)
                radius: 3
            }
            MouseArea {
                id: styleDrag
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                hoverEnabled: true
                preventStealing: true
                enabled: regionButton.enabled
                property real pressX
                property real start
                property real end
                property bool moved: false
                property string mode: ""
                function grip(x) {
                    const hit = Math.min(8 * timeline.scale, width / 3)
                    return x < hit && styleBlock.firstX >= timeline.plotLeft ? "left"
                        : x > width - hit && styleBlock.lastX <= timeline.plotRight ? "right" : "move"
                }
                cursorShape: (pressed ? mode : grip(mouseX)) !== "move" ? Qt.SizeHorCursor
                    : (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                onPressed: mouse => {
                    timeline.gestureType = "clip"
                    pressX = mouse.x
                    mode = grip(mouse.x)
                    start = Number(styleBlock.region.start)
                    end = Number(styleBlock.region.end)
                    moved = false
                    regionButton.forceActiveFocus(Qt.MouseFocusReason)
                }
                onPositionChanged: mouse => {
                    const delta = mouse.x - pressX
                    if (!pressed || timeline.gestureType !== "clip" || (!moved && Math.abs(delta) < 3))
                        return
                    moved = true
                    const first = Number(styleBlock.region.start)
                    const last = Number(styleBlock.region.end)
                    const shift = delta * timeline.visibleSpan / timeline.plotWidth
                    const track = String(styleBlock.region.trackId)
                    if (mode === "left") {
                        start = timeline.clamp(timeline.snapTime(first + shift, mouse.modifiers, track),
                                               timeline.origin, last - .025)
                    } else if (mode === "right") {
                        end = timeline.clamp(timeline.snapTime(last + shift, mouse.modifiers, track),
                                             first + .025, timeline.fullEnd)
                    } else {
                        const nearFirst = timeline.snapTime(first + shift, mouse.modifiers, track) - first
                        const nearLast = timeline.snapTime(last + shift, mouse.modifiers, track) - last
                        const snapped = nearFirst === shift ? nearLast : nearLast === shift ? nearFirst
                            : (Math.abs(nearFirst - shift) <= Math.abs(nearLast - shift) ? nearFirst : nearLast)
                        const bounded = timeline.clamp(snapped, timeline.origin - first, timeline.fullEnd - last)
                        start = first + bounded
                        end = last + bounded
                    }
                }
                onReleased: {
                    if (timeline.gestureType !== "clip")
                        return
                    timeline.clearGesture()
                    if (moved) {
                        const nextStart = start
                        const nextEnd = end
                        moved = false
                        timeline.regionFocused()
                        timeline.controller.moveRegion(String(styleBlock.region.id), nextStart, nextEnd)
                    } else {
                        styleBlock.openRegion()
                    }
                }
                onCanceled: { moved = false; timeline.clearGesture() }
            }
        }
    }

    MouseArea {
        width: timeline.plotLeft
        y: timeline.plotTop
        height: timeline.plotHeight
        acceptedButtons: Qt.NoButton
        hoverEnabled: true
        enabled: timeline.hasSource
        ToolTip.visible: containsMouse
        ToolTip.delay: 600
        ToolTip.text: timeline.vwaTr("在键轨滚动查看其他音高")
        onWheel: function(wheel) {
            const delta = wheel.pixelDelta.y || wheel.angleDelta.y
            wheel.accepted = delta !== 0 && timeline.movePitch(delta > 0 ? -2 : 2)
        }
    }

    MouseArea {
        id: scoreMouse
        objectName: "tuningScoreMouse"
        x: timeline.plotLeft
        y: timeline.plotTop
        width: timeline.plotWidth
        height: timeline.plotHeight
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        preventStealing: true
        hoverEnabled: true
        enabled: timeline.hasSource && timeline.activeTrack.length > 0
        cursorShape: timeline.panGesture ? Qt.ClosedHandCursor
            : timeline.pitchTool ? (timeline.hoveredNote && timeline.hoveredNote.drawReason
                    ? Qt.ForbiddenCursor : Qt.CrossCursor)
            : timeline.noteGesture && timeline.noteGesture.action
                ? (timeline.noteGesture.action === "resize" ? Qt.SizeHorCursor : Qt.ClosedHandCursor)
            : timeline.styleTool ? Qt.CrossCursor
            : timeline.noteTool && timeline.resizeHit(timeline.hoveredBox, mouseX + x) ? Qt.SizeHorCursor
            : timeline.noteTool && timeline.hoveredNote && (!timeline.hoveredNote.timingReason || !timeline.hoveredNote.pitchReason)
                ? Qt.OpenHandCursor : timeline.hoveredNote ? Qt.PointingHandCursor : Qt.CrossCursor
        ToolTip.visible: !!timeline.hoveredNote && !pressed
        ToolTip.delay: 500
        ToolTip.text: timeline.hoveredNote ? timeline.noteDescription(timeline.hoveredNote) : ""
        onPressed: function(mouse) {
            timeline.forceActiveFocus(Qt.MouseFocusReason)
            if (timeline.view.busy === true) {
                mouse.accepted = false
                return
            }
            if (mouse.button === Qt.MiddleButton) {
                timeline.gestureType = "pan"
                timeline.panGesture = timeline.beginPan(mouse.x, mouse.y)
                return
            }
            timeline.pressX = mouse.x
            timeline.pressY = mouse.y
            timeline.selectOnly = !!(mouse.modifiers & Qt.ShiftModifier)
            if (!timeline.selectOnly && timeline.pitchTool) {
                timeline.gestureType = "pitch"
                const note = timeline.pitchNoteAt(mouse.x, mouse.y)
                if (note && !note.drawReason) {
                    timeline.selectNote(note)
                    timeline.pitchGesture = {note: note, kind: timeline.pitchTool, points: []}
                    timeline.pitchSample(mouse.x, mouse.y, mouse.modifiers, true)
                }
                return
            }
            const box = timeline.noteBoxAt(mouse.x + x, mouse.y + y)
            if (!timeline.selectOnly && timeline.noteTool && box) {
                if (mouse.modifiers & Qt.ControlModifier) {
                    timeline.gestureType = "toggle"
                    timeline.noteGesture = {note: box.note}
                    return
                }
                timeline.gestureType = "note"
                const action = timeline.resizeHit(box, mouse.x + x) ? "resize"
                    : box.note.timingReason && box.note.pitchReason ? "" : "move"
                timeline.noteGesture = {note: box.note, action: action}
                if (!timeline.selectedNoteSet.has(box.note.id)) timeline.selectNote(box.note)
                return
            }
            if (timeline.noteTool && !timeline.selectOnly) {
                timeline.gestureType = "marquee"
                return
            }
            timeline.startRange(mouse.x, mouse.y, mouse.modifiers)
        }
        onPositionChanged: function(mouse) {
            if (!pressed)
                return
            if (timeline.panGesture) {
                timeline.movePan(timeline.panGesture, mouse.x, mouse.y)
            } else if (timeline.gestureType === "marquee") {
                timeline.marqueeEnd = {x: mouse.x, y: mouse.y}
            } else if (timeline.pitchGesture) {
                timeline.pitchSample(mouse.x, mouse.y, mouse.modifiers, false)
            } else if (timeline.noteGesture && timeline.noteGesture.action) {
                const gesture = timeline.noteGesture
                const request = timeline.dragRequest(gesture, mouse.x, mouse.y, mouse.modifiers)
                if (request.horizontal || request.pitch !== gesture.note.pitch) {
                    timeline.noteGhost = timeline.controller.previewNoteGeometry(
                        gesture.note.id, gesture.action, request.seconds, request.pitch, request.horizontal)
                } else {
                    timeline.noteGhost = null
                }
            } else if (isFinite(timeline.pressTime))
                timeline.dragTime = timeline.snapTime(timeline.timeForPlotX(mouse.x), mouse.modifiers, "")
        }
        onReleased: function(mouse) {
            const kind = timeline.gestureType
            if (!kind)
                return
            if (kind === "pan") {
                timeline.movePan(timeline.panGesture, mouse.x, mouse.y)
                timeline.clearGesture()
                return
            }
            if (kind === "marquee") {
                timeline.finishMarquee(mouse.x, mouse.y)
                return
            }
            if (kind === "toggle") {
                const note = timeline.noteGesture.note
                const clicked = Math.hypot(mouse.x - timeline.pressX, mouse.y - timeline.pressY) < 4 * timeline.scale
                timeline.clearGesture()
                if (clicked) timeline.selectNoteWithModifiers(note, Qt.ControlModifier)
                return
            }
            if (kind === "pitch") {
                const gesture = timeline.pitchGesture
                if (gesture) {
                    timeline.pitchSample(mouse.x, mouse.y, mouse.modifiers, true)
                    timeline.clearGesture()
                    const times = gesture.points.map(point => point[0])
                    if (gesture.kind === "erase" || Math.max(...times) - Math.min(...times)
                            >= Number(timeline.view.pitchStrokeMinimum || .012))
                        timeline.controller.editPitchStroke(gesture.note.id, gesture.kind, gesture.points)
                } else {
                    timeline.clearGesture()
                }
                return
            }
            if (kind === "note") {
                const gesture = timeline.noteGesture
                const request = gesture.action ? timeline.dragRequest(gesture, mouse.x, mouse.y, mouse.modifiers) : null
                timeline.clearGesture()
                if (request && (request.horizontal || request.pitch !== gesture.note.pitch)) {
                    if (timeline.controller.editNoteGeometry(gesture.note.id, gesture.action,
                            request.seconds, request.pitch, request.horizontal))
                        timeline.revealPitch(request.pitch, gesture.note.trackId, gesture.note.id)
                } else timeline.selectNote(gesture.note)
                return
            }
            const dx = Math.abs(mouse.x - timeline.pressX)
            const dy = Math.abs(mouse.y - timeline.pressY)
            const click = dx < 3 * timeline.scale && dy < 3 * timeline.scale
            const modifiers = timeline.selectOnly ? mouse.modifiers | Qt.ShiftModifier : mouse.modifiers
            if (timeline.finishRange(mouse.x, mouse.modifiers) || !click || timeline.view.busy)
                return
            const box = timeline.noteBoxAt(mouse.x + x, mouse.y + y)
            if (box) timeline.selectNoteWithModifiers(box.note, modifiers)
            else timeline.clearSelection()
        }
        onCanceled: timeline.clearGesture()
        onDoubleClicked: function(mouse) {
            if (timeline.pitchTool || mouse.button !== Qt.LeftButton || mouse.modifiers & (Qt.ShiftModifier | Qt.ControlModifier))
                return
            const box = timeline.noteBoxAt(mouse.x + x, mouse.y + y)
            if (box)
                timeline.openLyric(box.note)
            else
                timeline.controller.setViewport(timeline.origin, timeline.fullSpan)
        }
        onWheel: wheel => wheel.accepted = pressed || !!timeline.lyricNote || timeline.viewportWheel(wheel, wheel.x)
    }

    Canvas {
        id: pitchInk
        objectName: "tuningHandPitchInk"
        x: timeline.plotLeft
        y: timeline.plotTop
        width: timeline.plotWidth
        height: timeline.plotHeight
        readonly property real leftTime: timeline.visibleStart
        readonly property real timeSpan: timeline.visibleSpan
        readonly property real firstRow: timeline.pitchOffset
        readonly property int semitone: timeline.pitchRowHeight
        onLeftTimeChanged: requestPaint()
        onTimeSpanChanged: requestPaint()
        onFirstRowChanged: requestPaint()
        onSemitoneChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        Connections {
            target: timeline
            function onManualCurvesChanged() { pitchInk.requestPaint() }
            function onDarkChanged() { pitchInk.requestPaint() }
        }

        function line(ctx, points, pitch, ink) {
            if (points.length < 2)
                return
            ctx.beginPath()
            for (let i = 0; i < points.length; ++i) {
                const point = points[i]
                const x = timeline.xForTime(point[0]) - timeline.plotLeft
                const y = timeline.yForPitch(pitch === null ? point[1] : pitch + point[1] / 100)
                    - timeline.plotTop + timeline.pitchRowHeight / 2
                if (i === 0) ctx.moveTo(x, y)
                else ctx.lineTo(x, y)
            }
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.strokeStyle = timeline.dark ? "#103333" : "#f5fffc"
            ctx.lineWidth = 4.6 * timeline.scale
            ctx.stroke()
            ctx.strokeStyle = ink
            ctx.lineWidth = 2.2 * timeline.scale
            ctx.stroke()
        }

        onPaint: {
            const ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            for (const curve of timeline.manualCurves)
                line(ctx, curve.points, null, timeline.handColor)
            const gesture = timeline.pitchGesture
            if (!gesture || !gesture.points.length)
                return
            if (gesture.kind === "draw") {
                line(ctx, gesture.points, gesture.note.pitch, timeline.handColor)
            } else {
                let first = Infinity
                let last = -Infinity
                for (const point of gesture.points) {
                    first = Math.min(first, point[0])
                    last = Math.max(last, point[0])
                }
                const left = timeline.xForTime(first) - timeline.plotLeft
                const right = timeline.xForTime(last) - timeline.plotLeft
                ctx.fillStyle = Qt.rgba(0.96, 0.43, 0.55, 0.33)
                const row = timeline.yForPitch(gesture.note.pitch) - timeline.plotTop
                ctx.fillRect(left, row - 2 * semitone, Math.max(2, right - left), 5 * semitone)
            }
        }
    }

    Rectangle {
        visible: !!timeline.noteGhost && !!timeline.noteGhost.reason
        x: timeline.plotLeft + Math.round(6 * timeline.scale)
        y: timeline.plotTop + Math.round(6 * timeline.scale)
        z: 2
        width: Math.max(0, Math.min(timeline.plotWidth - 12 * timeline.scale, editReason.implicitWidth + 18 * timeline.scale))
        height: editReason.implicitHeight + 8 * timeline.scale
        radius: 4
        color: timeline.theme.base
        border.color: timeline.theme.error
        Text {
            id: editReason
            anchors.centerIn: parent
            width: Math.max(0, parent.width - 12 * timeline.scale)
            text: (timeline.noteGhost || {}).reason || ""
            elide: Text.ElideRight
            color: timeline.theme.error
            font.pixelSize: Math.round(12 * timeline.scale)
        }
    }

    TextField {
        id: lyricInput
        objectName: "tuningInlineLyric"
        visible: !!timeline.lyricNote
        z: 3
        width: Math.min(timeline.plotWidth, Math.round(212 * timeline.scale))
        height: Math.round(31 * timeline.scale)
        x: visible ? timeline.clamp(timeline.xForTime(timeline.lyricNote.start),
            timeline.plotLeft, timeline.plotRight - width) : 0
        y: visible ? timeline.clamp(timeline.yForPitch(timeline.lyricNote.pitch),
            timeline.plotTop, timeline.plotBottom - height) : 0
        font.pixelSize: Math.round(13 * timeline.scale)
        color: timeline.theme.text
        placeholderTextColor: timeline.theme.muted
        selectByMouse: true
        selectionColor: timeline.theme.accent
        selectedTextColor: timeline.theme.base
        placeholderText: timeline.vwaTr("音符歌词")
        Accessible.name: placeholderText
        Accessible.description: timeline.vwaTr("回车保存，Tab 下一字，Shift+Tab 上一字，Esc 取消。")
        background: Rectangle {
            radius: 5
            color: timeline.theme.surface
            border.color: timeline.theme.accent
            border.width: 1
        }
        onAccepted: timeline.finishLyric(0)
        onActiveFocusChanged: {
            if (activeFocus || !timeline.lyricNote)
                return
            const note = timeline.lyricNote
            const draft = text
            timeline.closeLyric()
            if (!inputMethodComposing && draft !== note.lyric && note.trackId === timeline.activeTrack
                    && timeline.scoreEditable)
                timeline.controller.editNoteLyric(note.id, draft)
        }
        Keys.onPressed: function(event) {
            if (inputMethodComposing)
                return
            if (event.key === Qt.Key_Tab && event.modifiers === Qt.NoModifier) {
                timeline.finishLyric(1)
                event.accepted = true
            } else if (event.key === Qt.Key_Backtab || (event.key === Qt.Key_Tab && event.modifiers === Qt.ShiftModifier)) {
                timeline.finishLyric(-1)
                event.accepted = true
            } else if (event.key === Qt.Key_Escape) {
                timeline.closeLyric()
                timeline.forceActiveFocus(Qt.OtherFocusReason)
                event.accepted = true
            }
        }
    }

    ScrollBar {
        objectName: "tuningPitchScrollBar"
        x: timeline.plotRight
        y: timeline.plotTop
        width: Math.max(1, timeline.width - x)
        height: timeline.plotHeight
        orientation: Qt.Vertical
        policy: ScrollBar.AlwaysOn
        size: timeline.rowsInView / 128
        position: timeline.pitchOffset / 128
        minimumSize: Math.min(1, Math.round(24 * timeline.scale) / height)
        enabled: timeline.hasSource
        Accessible.name: timeline.vwaTr("音高滚动")
        onPositionChanged: {
            if (pressed)
                timeline.topRow = timeline.clamp(position * 128, 0, 128 - timeline.rowsInView)
        }
    }

    Text {
        x: timeline.plotLeft
        y: timeline.plotTop + (timeline.plotHeight - height) / 2
        width: timeline.plotWidth
        visible: text.length > 0
        horizontalAlignment: Text.AlignHCenter
        text: timeline.emptyMessage()
        color: timeline.theme.muted
        font.pixelSize: Math.round(12 * timeline.scale)
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: timeline.theme.accent
        border.width: timeline.activeFocus ? 1 : 0
    }
}
