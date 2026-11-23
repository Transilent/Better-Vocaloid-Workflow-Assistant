import QtQuick

Rectangle {
    id: pair
    objectName: "chorusPairLane"
    required property QtObject theme
    required property QtObject controller
    required property var view
    required property int headerWidth
    property var guidePeaks: []
    property var takePeaks: []
    property real movement: 0
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
            function envelope(take, values, mid, color, delay) {
                if (!take.duration || !values.length)
                    return
                c.strokeStyle = color
                c.beginPath()
                for (let x = 0; x < width; x += 1.6) {
                    const time = at(x)
                    const localShift = time >= pair.view.rangeFirst && time < pair.view.rangeLast ? delay : 0
                    const sample = Math.floor((time - take.offset - localShift) * values.length / take.duration)
                    if (sample >= 0 && sample < values.length) {
                        const amplitude = .45 + Math.min(1., values[sample]) * (height * .21)
                        c.moveTo(x, mid - amplitude)
                        c.lineTo(x, mid + amplitude)
                    }
                }
                c.stroke()
            }
            envelope(pair.guide, pair.guidePeaks, height * .25, pair.theme.success, 0)
            envelope(pair.take, pair.takePeaks, height * .75, pair.theme.accent, pair.totalShift / 1000)
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
            onPressed: mouse => { pressX = mouse.x; pair.movement = 0 }
            onPositionChanged: mouse => {
                if (pressed)
                    pair.movement = 1000 * (mouse.x - pressX) * pair.view.viewSpan / width
            }
            onReleased: mouse => {
                const drag = Math.abs(mouse.x - pressX) >= 4
                const destination = pair.view.shiftMs + 1000 * (mouse.x - pressX) * pair.view.viewSpan / width
                pair.movement = 0
                if (drag)
                    pair.controller.editSelection("timing", destination)
                else
                    seek(mouse.x)
            }
            onCanceled: pair.movement = 0
        }
    }
}
