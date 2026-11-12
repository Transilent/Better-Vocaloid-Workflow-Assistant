import QtQuick
import QtQuick.Controls

Item {
    id: strip
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
    }
    objectName: "tuningViewportStrip"
    required property QtObject theme
    required property real timelineStart
    required property real timelineDuration
    required property real viewportStart
    required property real viewportSpan
    property real leadingInset: 0
    property real trailingInset: 0
    signal viewportRequested(real start, real span)

    implicitHeight: Math.round(32 * theme.scale)
    activeFocusOnTab: true
    enabled: timelineDuration > 0
    Accessible.role: Accessible.ScrollBar
    Accessible.name: strip.vwaTr("整曲视野")
    Accessible.description: strip.rangeText + strip.vwaTr("。拖中间平移，拖两端缩放。方向键移动，加减键缩放。")

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
    // At the minimum thumb width, leave a distinct center for panning.
    readonly property real gripWidth: Math.min(16 * theme.scale, Math.max(0, (visualWidth - 10 * theme.scale) / 2))
    readonly property string rangeStart: stamp(visibleStart)
    readonly property string rangeEnd: stamp(visibleStart + visibleSpan)
    readonly property string rangeText: rangeStart + " – " + rangeEnd

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
        if (pointer.pressed || event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            return;
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

    Text {
        objectName: "tuningViewportRange"
        anchors.left: parent.left
        anchors.right: rail.left
        anchors.rightMargin: Math.round(5 * strip.theme.scale)
        anchors.verticalCenter: rail.verticalCenter
        visible: strip.enabled && width > 0
        text: width >= Math.round(97 * strip.theme.scale) ? strip.rangeText
            : strip.rangeStart + "\n– " + strip.rangeEnd
        color: strip.theme.muted
        font.pixelSize: Math.round(10 * strip.theme.scale)
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
        textFormat: Text.PlainText
        Accessible.ignored: true
    }

    Rectangle {
        id: rail
        anchors.fill: parent
        anchors.leftMargin: strip.leadingInset
        anchors.rightMargin: strip.trailingInset
        anchors.topMargin: Math.round(2 * strip.theme.scale)
        anchors.bottomMargin: anchors.topMargin
        color: strip.theme.base
        border.width: strip.activeFocus ? 2 : 1
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
            color: pointer.pointedPart === "pan" ? strip.theme.pressed : strip.theme.selected
            border.color: pointer.pressed || strip.activeFocus ? strip.theme.accent : strip.theme.selectedLine

            Repeater {
                model: 2
                Rectangle {
                    required property int index
                    readonly property bool leftGrip: index === 0
                    readonly property bool pointed: pointer.pointedPart === (leftGrip ? "left" : "right")
                    x: leftGrip ? 0 : parent.width - width
                    anchors.verticalCenter: parent.verticalCenter
                    width: strip.gripWidth
                    height: Math.max(0, parent.height - Math.round(4 * strip.theme.scale))
                    radius: Math.round(4 * strip.theme.scale)
                    color: pointed ? strip.theme.selectedLine : "transparent"
                    Rectangle {
                        anchors.centerIn: parent
                        width: Math.round(3 * strip.theme.scale)
                        height: Math.round((parent.pointed ? 15 : 11) * strip.theme.scale)
                        radius: width / 2
                        color: parent.pointed ? strip.theme.text : strip.theme.accent
                    }
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
            readonly property string pointedPart: pressed ? dragMode : containsMouse ? targetAt(mouseX) : ""

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
                const target = pointedPart;
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
    ToolTip.text: strip.rangeText + " · " + strip.vwaTr("拖中间移动，拖两端缩放")
}
