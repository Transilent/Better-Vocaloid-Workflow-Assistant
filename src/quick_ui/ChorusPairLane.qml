import QtQuick
import "ChorusWaveform.js" as Wave

Rectangle {
    id: pair
    objectName: "chorusPairLane"
    required property QtObject theme
    required property QtObject controller
    required property var view
    required property int headerWidth
    property var guidePeaks: []
    property var takePeaks: []
    property var guideAlternates: ({})
    property var takeAlternates: ({})
    property real movement: 0
    property real anchorTime: NaN
    readonly property var guide: view.reference
    readonly property var take: view.take
    readonly property real totalShift: view.shiftMs + movement
    readonly property bool canAlign: !!take.id && take.role === "vocal"
        && view.rangeLast > view.rangeFirst
    color: theme.base
    border.color: theme.line
    radius: Math.round(7 * theme.scale)
    clip: true
    implicitHeight: Math.round(108 * theme.scale)

    function rebuild() {
        if (!visible)
            return
        guidePeaks = guide.id ? controller.peaks(guide.id) : []
        takePeaks = take.id ? controller.peaks(take.id) : []
        guideAlternates = Wave.alternatePeaks(guide, controller)
        takeAlternates = Wave.alternatePeaks(take, controller)
        wave.requestPaint()
    }
    function t(text) { return quickTranslation.text(text) }

    onVisibleChanged: rebuild()
    onMovementChanged: wave.requestPaint()
    Connections {
        target: pair.controller
        function onChanged() { pair.rebuild() }
    }

    Rectangle {
        width: pair.headerWidth
        height: parent.height
        color: pair.theme.raised
        Text {
            x: 8
            y: parent.height * .25 - height / 2
            width: parent.width - 16
            text: pair.guide.name || pair.t("时间参照")
            color: pair.theme.success
            elide: Text.ElideRight
            textFormat: Text.PlainText
            font.pixelSize: Math.round(11 * pair.theme.scale)
        }
        Text {
            x: 8
            y: parent.height * .75 - height / 2
            width: parent.width - 16
            text: pair.take.name || pair.t("选中音轨")
            color: pair.theme.accent
            elide: Text.ElideRight
            textFormat: Text.PlainText
            font.pixelSize: Math.round(11 * pair.theme.scale)
        }
    }
    Canvas {
        id: wave
        x: pair.headerWidth
        width: Math.max(1, parent.width - x)
        height: parent.height
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            const c = getContext("2d")
            c.reset()
            const range = Math.max(.01, pair.view.viewSpan)
            const at = x => pair.view.viewFirst + x * range / width
            const px = time => (time - pair.view.viewFirst) * width / range
            const band = Math.max(0, px(pair.view.rangeFirst))
            const tail = Math.min(width, px(pair.view.rangeLast))
            if (tail > band) {
                c.fillStyle = pair.theme.selected
                c.globalAlpha = .55
                c.fillRect(band, height / 2, tail - band, height / 2)
                c.globalAlpha = 1
            }
            c.fillStyle = pair.theme.line
            c.fillRect(0, 0, 1, height)
            c.fillRect(0, height / 2, width, 1)
            function envelope(take, values, alternates, mid, color, compare) {
                if (!take.duration || !values.length)
                    return
                c.strokeStyle = color
                c.beginPath()
                for (let x = 0; x < width; x += 1.6) {
                    const time = at(x)
                    const editing = compare && time >= pair.view.rangeFirst && time < pair.view.rangeLast
                    const fromTake = compare ? Wave.sourceTime(take.timing, time) : time
                    const shift = editing && !isFinite(pair.anchorTime) ? pair.movement / 1000 : 0
                    const sample = Wave.valueAt(take, values, fromTake - shift, alternates)
                    if (sample >= 0) {
                        const amplitude = .45 + Math.min(1., sample) * (height * .21)
                        c.moveTo(x, mid - amplitude)
                        c.lineTo(x, mid + amplitude)
                    }
                }
                c.stroke()
            }
            envelope(pair.guide, pair.guidePeaks, pair.guideAlternates, height * .25, pair.theme.success, false)
            envelope(pair.take, pair.takePeaks, pair.takeAlternates, height * .75, pair.theme.accent, true)
        }

        Repeater {
            model: pair.view.anchors
            Item {
                required property var modelData
                required property int index
                readonly property bool dragged: Math.abs(pair.anchorTime - modelData.time) < .001
                x: (modelData.time - pair.view.viewFirst + (dragged ? pair.movement / 1000 : 0))
                    * wave.width / Math.max(.01, pair.view.viewSpan)
                y: wave.height * .50
                width: Math.round(2 * pair.theme.scale)
                height: wave.height * .49
                Rectangle { anchors.fill: parent; color: pair.theme.accent; opacity: parent.dragged ? 1 : .68 }
                Rectangle {
                    width: Math.round(9 * pair.theme.scale)
                    height: width
                    rotation: 45
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 5
                    color: pair.theme.accent
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 3
                    visible: parent.dragged || parent.index === 0
                        || (parent.modelData.time - pair.view.anchors[parent.index - 1].time)
                            * wave.width / pair.view.viewSpan >= 15 * pair.theme.scale
                    text: parent.modelData.label
                    font.pixelSize: Math.round(10 * pair.theme.scale)
                    color: pair.theme.text
                    textFormat: Text.PlainText
                }
            }
        }
        Text {
            anchors.right: parent.right
            anchors.rightMargin: 8
            anchors.top: parent.top
            anchors.topMargin: 5
            text: (pair.totalShift > 0 ? "+" : "") + Math.round(pair.totalShift) + " ms"
            color: pair.theme.text
            font.pixelSize: Math.round(11 * pair.theme.scale)
        }
        Rectangle {
            x: (pair.controller.position - pair.view.viewFirst) * wave.width / Math.max(.01, pair.view.viewSpan)
            width: 1
            height: parent.height
            color: pair.theme.accent
        }
        MouseArea {
            id: hand
            objectName: "chorusTimeDrag"
            y: parent.height / 2
            height: parent.height / 2
            width: parent.width
            enabled: pair.canAlign
            cursorShape: Qt.SizeHorCursor
            property real pressX
            function seek(x) {
                pair.controller.seek(pair.view.viewFirst + x * pair.view.viewSpan / width)
            }
            onPressed: mouse => {
                pressX = mouse.x
                pair.movement = 0
                pair.anchorTime = NaN
                let near = 13 * pair.theme.scale
                for (const item of pair.view.anchors) {
                    const x = (item.time - pair.view.viewFirst) * width / pair.view.viewSpan
                    if (Math.abs(x - pressX) < near) {
                        near = Math.abs(x - pressX)
                        pair.anchorTime = item.time
                    }
                }
            }
            onPositionChanged: mouse => {
                if (pressed)
                    pair.movement = 1000 * (mouse.x - pressX) * pair.view.viewSpan / width
            }
            onReleased: mouse => {
                const drag = Math.abs(mouse.x - pressX) >= 4
                const delta = 1000 * (mouse.x - pressX) * pair.view.viewSpan / width
                const anchor = pair.anchorTime
                pair.movement = 0
                pair.anchorTime = NaN
                if (drag && isFinite(anchor))
                    pair.controller.moveTimeAnchor(anchor, anchor + delta / 1000)
                else if (drag)
                    pair.controller.slideSelection(delta)
                else
                    seek(mouse.x)
            }
            onCanceled: { pair.movement = 0; pair.anchorTime = NaN }
        }
    }
}
