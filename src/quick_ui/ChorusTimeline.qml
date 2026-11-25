import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "ChorusWaveform.js" as Wave

Rectangle {
    id: editor
    objectName: "chorusTimeline"
    required property QtObject theme
    required property QtObject controller
    required property var view
    property int customHeaderWidth: 0
    readonly property int headerWidth: customHeaderWidth > 0 ? customHeaderWidth
        : Math.round((width < 690 ? 126 : 169) * theme.scale)
    readonly property real plotWidth: Math.max(1, width - headerWidth - 2)
    readonly property real leftTime: view.viewFirst
    readonly property real span: Math.max(.01, view.viewSpan)
    readonly property bool movedWave: (view.playing ? view.activeVariant : view.variant) === "timing"
        || (view.playing ? view.activeVariant : view.variant) === "processed"
    property bool dragging: false
    property real dragFirst: 0
    property real dragLast: 0
    signal takeSelected()
    signal phraseSelected()

    color: theme.surface
    border.color: theme.line
    radius: Math.round(8 * theme.scale)
    clip: true

    function t(text) { return quickTranslation.text(text) }
    function toX(time) { return (time - leftTime) * plotWidth / span }
    function toTime(px) { return leftTime + Math.max(0, Math.min(plotWidth, px)) * span / plotWidth }
    function clock(seconds) {
        const absolute = Math.abs(seconds)
        return (seconds < 0 ? "−" : "") + Math.floor(absolute / 60) + ":"
            + String(Math.floor(absolute % 60)).padStart(2, "0")
    }
    function roleName(role) {
        return t(role === "guide" ? "导唱" : role === "backing" ? "伴奏" : role === "take" ? "备选" : "人声")
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 1
        spacing: 0

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round((editor.view.mode === "all" ? 26 : 22) * editor.theme.scale)
            Text {
                anchors.left: parent.left
                anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: editor.t("工程时间")
                color: editor.theme.muted
                font.pixelSize: Math.round(11 * editor.theme.scale)
            }
            Canvas {
                id: ruler
                objectName: "chorusRuler"
                x: editor.headerWidth
                width: editor.plotWidth
                height: parent.height
                property real timeOrigin: editor.leftTime
                property real range: editor.span
                onTimeOriginChanged: requestPaint()
                onRangeChanged: requestPaint()
                onWidthChanged: requestPaint()
                onPaint: {
                    const c = getContext("2d")
                    c.reset()
                    const steps = [.1, .2, .5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
                    const want = editor.span / Math.max(1, width / (66 * editor.theme.scale))
                    let step = steps.find(value => value >= want) || Math.ceil(want / 60) * 60
                    c.fillStyle = editor.theme.muted
                    c.strokeStyle = editor.theme.line
                    c.font = Math.round(10 * editor.theme.scale) + "px sans-serif"
                    for (let ts = Math.ceil(editor.leftTime / step) * step;
                         ts < editor.leftTime + editor.span; ts += step) {
                        const px = editor.toX(ts)
                        c.beginPath()
                        c.moveTo(px, 0)
                        c.lineTo(px, height)
                        c.stroke()
                        const label = step < 1 ? editor.clock(ts) + "." + (Math.floor(Math.abs(ts) * 10 + .001) % 10) : editor.clock(ts)
                        if (px < width - 30)
                            c.fillText(label, px + 4, height - 8 * editor.theme.scale)
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: editor.view.count > 0
                    cursorShape: Qt.SizeHorCursor
                    preventStealing: true
                    onPressed: mouse => {
                        editor.controller.setFollowHold(true)
                        editor.controller.seek(editor.toTime(mouse.x))
                    }
                    onPositionChanged: mouse => {
                        if (pressed)
                            editor.controller.seek(editor.toTime(mouse.x))
                    }
                    onReleased: editor.controller.setFollowHold(false)
                    onCanceled: editor.controller.setFollowHold(false)
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round((editor.view.mode === "all" ? 30 : 24) * editor.theme.scale)
            color: editor.theme.raised
            Text {
                anchors.left: parent.left
                anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: editor.t("唱段")
                color: editor.theme.muted
                font.pixelSize: Math.round(11 * editor.theme.scale)
            }
            Rectangle {
                x: editor.headerWidth
                width: editor.plotWidth
                height: parent.height
                color: editor.theme.base
                clip: true
                Repeater {
                    model: editor.controller.phrases
                    delegate: Rectangle {
                        required property var modelData
                        x: editor.toX(modelData.start)
                        y: 3
                        width: Math.max(1, editor.toX(modelData.end) - x - 2)
                        height: parent.height - 6
                        visible: x + width > 0 && x < parent.width
                        color: modelData.end > editor.view.rangeFirst && modelData.start < editor.view.rangeLast
                            ? editor.theme.selected : editor.theme.raised
                        border.color: modelData.confirmed ? editor.theme.success : editor.theme.selectedLine
                        radius: 4
                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 5
                            anchors.rightMargin: 4
                            text: modelData.name
                            color: editor.theme.text
                            font.pixelSize: Math.round(11 * editor.theme.scale)
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        TapHandler {
                            onTapped: {
                                editor.controller.focusPhrase(modelData.start, modelData.end)
                                editor.phraseSelected()
                            }
                        }
                    }
                }
                Text {
                    visible: editor.controller.phrases.length === 0
                    anchors.left: parent.left
                    anchors.leftMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: editor.t("框选时间，或分析后点选唱段")
                    color: editor.theme.muted
                    font.pixelSize: Math.round(11 * editor.theme.scale)
                }
            }
        }

        Flickable {
            id: lanes
            objectName: "chorusTrackViewport"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: width
            contentHeight: trackStack.height
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            flickableDirection: Flickable.VerticalFlick
            interactive: contentHeight > height && !editor.dragging
            ScrollBar.vertical: ScrollBar { policy: lanes.interactive ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }

            Column {
                id: trackStack
                width: lanes.width
                Repeater {
                    model: editor.controller.tracks
                    delegate: Item {
                        id: row
                        required property var modelData
                        width: trackStack.width
                        height: Math.round((editor.view.mode === "all" ? 54 : 38) * editor.theme.scale)
                        readonly property bool selected: modelData.id === editor.view.selected
                        readonly property color ink: modelData.lead ? editor.theme.success
                            : selected ? editor.theme.accent : editor.theme.muted

                        Rectangle {
                            width: editor.headerWidth
                            height: parent.height - 1
                            color: row.selected ? editor.theme.selected : editor.theme.surface
                            Rectangle {
                                visible: row.selected
                                width: 2
                                height: parent.height
                                color: editor.theme.accent
                            }
                            TapHandler {
                                onTapped: {
                                    editor.takeSelected()
                                    editor.controller.selectTrack(row.modelData.id)
                                }
                            }
                            Column {
                                anchors.left: parent.left
                                anchors.leftMargin: 9
                                anchors.right: toggles.left
                                anchors.rightMargin: 4
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 2
                                Text {
                                    width: parent.width
                                    text: row.modelData.name
                                    color: row.selected || row.modelData.lead ? row.ink : editor.theme.text
                                    font.pixelSize: Math.round(13 * editor.theme.scale)
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    width: parent.width
                                    text: row.modelData.lead ? editor.t("时间参照")
                                        : row.modelData.part || editor.roleName(row.modelData.role)
                                    color: editor.theme.muted
                                    font.pixelSize: Math.round(11 * editor.theme.scale)
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }
                            Row {
                                id: toggles
                                anchors.right: parent.right
                                anchors.rightMargin: 5
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 2
                                UiButton {
                                    theme: editor.theme
                                    text: "M"
                                    implicitWidth: Math.round(21 * theme.scale)
                                    implicitHeight: Math.round(25 * theme.scale)
                                    padding: 1
                                    quiet: true
                                    selected: row.modelData.muted
                                    ToolTip.text: editor.t("静音")
                                    ToolTip.visible: hovered
                                    onClicked: editor.controller.mute(row.modelData.id)
                                }
                                UiButton {
                                    theme: editor.theme
                                    text: "S"
                                    implicitWidth: Math.round(21 * theme.scale)
                                    implicitHeight: Math.round(25 * theme.scale)
                                    padding: 1
                                    quiet: true
                                    selected: editor.view.solo === row.modelData.id
                                    ToolTip.text: editor.t("独听")
                                    ToolTip.visible: hovered
                                    onClicked: editor.controller.solo(row.modelData.id)
                                }
                            }
                        }

                        Canvas {
                            id: wave
                            objectName: "chorusWaveform"
                            x: editor.headerWidth
                            width: editor.plotWidth
                            height: row.height - 1
                            property var peaks: []
                            property var alternatePeaks: ({})
                            property real first: editor.leftTime
                            property real span: editor.span
                            property bool after: editor.movedWave
                            property color ink: row.ink
                            Component.onCompleted: {
                                peaks = editor.controller.peaks(row.modelData.id)
                                alternatePeaks = Wave.alternatePeaks(row.modelData, editor.controller)
                                requestPaint()
                            }
                            onFirstChanged: requestPaint()
                            onSpanChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onHeightChanged: requestPaint()
                            onAfterChanged: requestPaint()
                            onInkChanged: requestPaint()
                            onPaint: {
                                const c = getContext("2d")
                                c.reset()
                                c.fillStyle = row.selected ? editor.theme.selected : editor.theme.base
                                c.fillRect(0, 0, width, height)
                                const mid = height / 2
                                c.strokeStyle = editor.theme.line
                                c.beginPath()
                                c.moveTo(0, mid)
                                c.lineTo(width, mid)
                                c.stroke()
                                if (peaks.length === 0)
                                    return
                                const edits = after ? row.modelData.timing : []
                                c.strokeStyle = row.ink
                                c.globalAlpha = row.modelData.muted ? .3 : .88
                                c.beginPath()
                                for (let px = 0; px < width; px += 1.7) {
                                    const time = editor.toTime(px)
                                    const sourceTime = Wave.sourceTime(edits, time)
                                    const peak = Wave.valueAt(row.modelData, peaks, sourceTime, alternatePeaks)
                                    if (peak < 0)
                                        continue
                                    const value = Math.max(.35, Math.min(1, peak) * (height * .44))
                                    c.moveTo(px, mid - value)
                                    c.lineTo(px, mid + value)
                                }
                                c.stroke()
                            }
                            MouseArea {
                                anchors.fill: parent
                                preventStealing: true
                                property real pressX
                                onPressed: mouse => {
                                    pressX = mouse.x
                                    editor.takeSelected()
                                    editor.controller.selectTrack(row.modelData.id)
                                    editor.dragFirst = editor.toTime(mouse.x)
                                    editor.dragLast = editor.dragFirst
                                }
                                onPositionChanged: mouse => {
                                    if (pressed && Math.abs(mouse.x - pressX) > 4)
                                        editor.dragging = true
                                    if (editor.dragging)
                                        editor.dragLast = editor.toTime(mouse.x)
                                }
                                onReleased: mouse => {
                                    if (editor.dragging)
                                        editor.controller.selectRange(editor.dragFirst, editor.dragLast)
                                    else
                                        editor.controller.seek(editor.toTime(mouse.x))
                                    editor.dragging = false
                                }
                                onCanceled: editor.dragging = false
                                onDoubleClicked: mouse => {
                                    const time = editor.toTime(mouse.x)
                                    const phrase = editor.controller.phrases.find(part => part.start <= time && time < part.end)
                                    if (phrase)
                                        editor.controller.focusPhrase(phrase.start, phrase.end)
                                }
                            }
                        }
                        Rectangle {
                            anchors.bottom: parent.bottom
                            height: 1
                            width: parent.width
                            color: editor.theme.line
                        }
                    }
                }
            }
        }
    }

    Text {
        anchors.centerIn: parent
        visible: editor.view.count === 0
        text: editor.t("拖入干声开始")
        color: editor.theme.muted
        font.pixelSize: Math.round(15 * editor.theme.scale)
    }
    DropArea {
        anchors.fill: parent
        enabled: !editor.view.busy
        onEntered: drag => {
            if (!drag.hasUrls)
                drag.accepted = false
        }
        onDropped: drop => {
            const audio = drop.urls.filter(url => /\.(wav|flac|aif|aiff|mp3|ogg|opus)$/i.test(String(url)))
            if (audio.length) {
                editor.controller.importAudio("vocal", audio)
                drop.acceptProposedAction()
            }
        }
    }
    Item {
        x: editor.headerWidth + 1
        y: 1
        width: editor.plotWidth
        height: editor.height - 2
        clip: true
        WheelHandler {
            id: timelineWheel
            enabled: editor.view.count > 0
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: event => {
                const dy = event.angleDelta.y
                const dx = event.angleDelta.x
                if (event.modifiers & Qt.ControlModifier) {
                    if (dy !== 0) {
                        const factor = dy > 0 ? .8 : 1.25
                        editor.controller.zoomViewportAt(factor, editor.toTime(timelineWheel.point.position.x))
                        event.accepted = true
                    }
                } else if ((event.modifiers & Qt.ShiftModifier) || (Math.abs(dx) > Math.abs(dy) && dx !== 0)) {
                    const delta = Math.abs(dx) > Math.abs(dy) ? dx : dy
                    if (delta !== 0) {
                        editor.controller.panViewport((-delta / 120) * editor.span * .14)
                        event.accepted = true
                    }
                } else {
                    event.accepted = false
                }
            }
        }
        Rectangle {
            readonly property real first: editor.dragging ? editor.dragFirst : editor.view.rangeFirst
            readonly property real last: editor.dragging ? editor.dragLast : editor.view.rangeLast
            x: editor.toX(Math.min(first, last))
            width: editor.toX(Math.max(first, last)) - x
            y: Math.round((editor.view.mode === "all" ? 56 : 46) * editor.theme.scale)
            height: Math.max(0, parent.height - y)
            visible: editor.view.count > 0 && width < editor.plotWidth && width > 1
            color: editor.theme.accent
            opacity: .14
        }
        Item {
            objectName: "chorusTimelinePlayhead"
            readonly property real px: Math.round(editor.toX(editor.controller.position))
            x: px
            y: 0
            width: 1
            height: parent.height
            visible: editor.view.count > 0 && px >= -1 && px <= editor.plotWidth + 1
            Rectangle {
                x: 0
                y: 0
                width: 1
                height: parent.height
                color: editor.theme.accent
            }
            Rectangle {
                x: -Math.floor(width / 2)
                y: 0
                width: Math.round(7 * editor.theme.scale)
                height: Math.round(5 * editor.theme.scale)
                radius: 2
                color: editor.theme.accent
            }
        }
    }
}
