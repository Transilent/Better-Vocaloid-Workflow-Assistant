import QtQuick
import QtQuick.Controls

Item {
    id: strip
    objectName: "tuningViewportStrip"
    required property QtObject theme
    required property real timelineStart
    required property real timelineDuration
    required property real viewportStart
    required property real viewportSpan
    signal viewportRequested(real start, real span)

    implicitHeight: Math.round(29 * theme.scale)
    activeFocusOnTab: true
    enabled: timelineDuration > 0
    Accessible.role: Accessible.ScrollBar
    Accessible.name: "整曲视野"
    Accessible.description: strip.rangeText + "。拖中间平移，拖两端缩放。方向键移动，加减键缩放。"

    readonly property real total: Math.max(0.001, timelineDuration)
    readonly property real minimumSpan: Math.min(.5, total)
    readonly property real visibleSpan: Math.max(minimumSpan, Math.min(total, viewportSpan))
    readonly property real visibleStart: bound(viewportStart, timelineStart, timelineStart + total - visibleSpan)
    readonly property real visualWidth: Math.min(rail.width, Math.max(38 * theme.scale,
        rail.width * visibleSpan / total))
    readonly property real thumbTravel: Math.max(0, rail.width - visualWidth)
    readonly property real timeTravel: Math.max(0, total - visibleSpan)
    readonly property real visualStart: timeTravel > .0001
        ? thumbTravel * (visibleStart - timelineStart) / timeTravel : 0
    readonly property real gripWidth: Math.min(11 * theme.scale, visualWidth / 3)
    readonly property string rangeText: stamp(visibleStart) + " – " + stamp(visibleStart + visibleSpan)

    function bound(value, low, high) {
        return Math.max(low, Math.min(high, value));
    }

    function stamp(seconds) {
        const whole = Math.max(0, Math.round(seconds * 10));
        const minute = Math.floor(whole / 600);
        const second = Math.floor((whole % 600) / 10);
        return minute + ":" + String(second).padStart(2, "0") + "." + whole % 10;
    }

    function emitViewport(first, last) {
        const span = bound(last - first, minimumSpan, total);
        const start = bound(first, timelineStart, timelineStart + total - span);
        viewportRequested(start, span);
    }

    function zoom(factor) {
        const span = bound(visibleSpan * factor, minimumSpan, total);
        const center = visibleStart + visibleSpan / 2;
        emitViewport(center - span / 2, center + span / 2);
    }

    Keys.onPressed: event => {
        const shift = event.modifiers & Qt.ShiftModifier;
        const step = visibleSpan * (shift ? .025 : .12);
        if (event.key === Qt.Key_Left)
            emitViewport(visibleStart - step, visibleStart + visibleSpan - step);
        else if (event.key === Qt.Key_Right)
            emitViewport(visibleStart + step, visibleStart + visibleSpan + step);
        else if (event.key === Qt.Key_Plus || event.key === Qt.Key_Equal)
            zoom(.8);
        else if (event.key === Qt.Key_Minus)
            zoom(1.25);
        else if (event.key === Qt.Key_Home)
            emitViewport(timelineStart, timelineStart + visibleSpan);
        else if (event.key === Qt.Key_End)
            emitViewport(timelineStart + total - visibleSpan, timelineStart + total);
        else
            return;
        event.accepted = true;
    }

    Rectangle {
        id: rail
        anchors.fill: parent
        anchors.topMargin: Math.round(3 * strip.theme.scale)
        anchors.bottomMargin: anchors.topMargin
        color: strip.theme.base
        border.color: strip.activeFocus ? strip.theme.accent : strip.theme.line
        radius: Math.round(4 * strip.theme.scale)

        Repeater {
            model: 9
            Rectangle {
                required property int index
                x: Math.round(rail.width * (index + 1) / 10)
                width: 1
                height: Math.round(5 * strip.theme.scale)
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 2
                color: strip.theme.line
            }
        }

        Rectangle {
            id: thumb
            objectName: "tuningViewportThumb"
            x: strip.visualStart
            width: strip.visualWidth
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            radius: rail.radius
            color: strip.theme.selected
            border.color: pointer.pressed || strip.activeFocus ? strip.theme.accent : strip.theme.selectedLine

            Text {
                anchors.centerIn: parent
                visible: implicitWidth + 28 * strip.theme.scale < thumb.width
                color: strip.theme.text
                text: strip.rangeText
                font.pixelSize: Math.round(10 * strip.theme.scale)
                textFormat: Text.PlainText
            }
            Repeater {
                model: 2
                Rectangle {
                    required property int index
                    x: index === 0 ? Math.round(4 * strip.theme.scale)
                        : parent.width - width - Math.round(4 * strip.theme.scale)
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.round(3 * strip.theme.scale)
                    height: Math.round(11 * strip.theme.scale)
                    radius: width / 2
                    color: strip.theme.accent
                }
            }
        }

        MouseArea {
            id: pointer
            objectName: "tuningViewportInput"
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            hoverEnabled: true
            preventStealing: true
            property string dragMode: ""
            property real pressedAt: 0
            property real startBefore: 0
            property real endBefore: 0
            property real panSecondsPerPixel: 0
            property real nextStart: 0
            property real nextEnd: 0

            function targetAt(x) {
                if (x < thumb.x || x > thumb.x + thumb.width)
                    return "jump";
                if (x <= thumb.x + strip.gripWidth)
                    return "left";
                if (x >= thumb.x + thumb.width - strip.gripWidth)
                    return "right";
                return "pan";
            }

            function queue(x) {
                const distance = x - pressedAt;
                const shift = distance * strip.total / Math.max(1, width);
                if (dragMode === "left") {
                    nextStart = strip.bound(startBefore + shift, strip.timelineStart, endBefore - strip.minimumSpan);
                    nextEnd = endBefore;
                } else if (dragMode === "right") {
                    nextStart = startBefore;
                    nextEnd = strip.bound(endBefore + shift, startBefore + strip.minimumSpan, strip.timelineStart + strip.total);
                } else {
                    nextStart = startBefore + distance * panSecondsPerPixel;
                    nextEnd = endBefore + distance * panSecondsPerPixel;
                }
                if (!coalesce.running)
                    coalesce.start();
            }

            cursorShape: {
                const target = pressed ? dragMode : targetAt(mouseX);
                return target === "left" || target === "right" ? Qt.SizeHorCursor
                    : target === "pan" ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor) : Qt.PointingHandCursor;
            }
            onPressed: mouse => {
                strip.forceActiveFocus(Qt.MouseFocusReason);
                dragMode = targetAt(mouse.x);
                pressedAt = mouse.x;
                startBefore = strip.visibleStart;
                if (dragMode === "jump") {
                    dragMode = "pan";
                    startBefore = strip.bound(strip.timelineStart + mouse.x / Math.max(1, width) * strip.total
                        - strip.visibleSpan / 2, strip.timelineStart, strip.timelineStart + strip.total - strip.visibleSpan);
                }
                endBefore = startBefore + strip.visibleSpan;
                panSecondsPerPixel = strip.thumbTravel > 0 ? strip.timeTravel / strip.thumbTravel : 0;
                nextStart = startBefore;
                nextEnd = endBefore;
                if (startBefore !== strip.visibleStart)
                    strip.emitViewport(nextStart, nextEnd);
            }
            onPositionChanged: mouse => { if (pressed) queue(mouse.x); }
            onReleased: mouse => {
                queue(mouse.x);
                coalesce.stop();
                strip.emitViewport(nextStart, nextEnd);
                dragMode = "";
            }
            onCanceled: {
                coalesce.stop();
                dragMode = "";
            }
        }
    }

    Timer {
        id: coalesce
        interval: 16
        onTriggered: strip.emitViewport(pointer.nextStart, pointer.nextEnd)
    }
    ToolTip.visible: (pointer.containsMouse || activeFocus) && !pointer.pressed
    ToolTip.delay: 650
    ToolTip.text: "拖中间移动，拖两端缩放"
}
