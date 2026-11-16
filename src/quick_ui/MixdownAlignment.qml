import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "mixdownAlignment"
    required property QtObject theme
    required property QtObject controller
    required property var tracks
    required property real positionMs
    required property bool canSeek
    required property bool canEdit

    readonly property var state: controller.alignment
    readonly property var durations: state.durations || ({})
    readonly property var errors: state.errors || ({})
    readonly property var lanes: tracks.filter(row => row.id === "instrumental")
                                  .concat(tracks.filter(row => row.id !== "instrumental"))
    readonly property var active: {
        const selected = lanes.find(row => row.id === selectedId && row.path);
        return selected || lanes.find(row => row.id !== "instrumental" && row.path) || null;
    }
    readonly property real totalMs: Math.max(1, ...lanes.map(row =>
        Number(durations[row.id] || 0) + Number(row.offset || 0) * 1000))
    readonly property real minSpan: Math.min(totalMs, 250)
    readonly property real span: clamp(totalMs / zoom, minSpan, totalMs)
    readonly property real maxStart: Math.max(0, totalMs - span)
    readonly property real start: clamp(pan, 0, maxStart)
    readonly property int labelWidth: Math.round(116 * theme.scale)
    readonly property int rulerHeight: Math.round(25 * theme.scale)
    readonly property var inks: [theme.attention, theme.success, theme.accent, theme.text]
    readonly property string lanePositions: lanes.map(row => row.id + ":" + row.offset).join("|")
    property var windows: ({})
    property string selectedId: ""
    property string draggingId: ""
    property real dragMs: 0
    property real zoom: 1
    property real pan: 0

    spacing: Math.round(7 * theme.scale)

    function tr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message;
    }
    function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }
    function time(ms, precise) {
        const seconds = Math.floor(Math.max(0, ms) / 1000);
        const fraction = Math.floor(Math.max(0, ms)) % 1000;
        return Math.floor(seconds / 60) + ":" + ("0" + seconds % 60).slice(-2)
            + (precise ? "." + ("00" + fraction).slice(-3) : "");
    }
    function setZoom(factor) {
        const next = clamp(zoom * factor, 1, totalMs / minSpan);
        const at = positionMs >= start && positionMs <= start + span
            ? positionMs : start + span * 0.5;
        const relative = (at - start) / span;
        const nextSpan = clamp(totalMs / next, minSpan, totalMs);
        pan = clamp(at - relative * nextSpan, 0, totalMs - nextSpan);
        zoom = next;
    }
    function resetView() { pan = 0; zoom = 1; }
    function seek(fraction) {
        if (canSeek)
            controller.commitSeek(Math.round(start + clamp(fraction, 0, 1) * span));
    }
    function refresh() {
        if (visible && state.status !== "empty")
            windows = controller.alignmentWindow(start, start + span);
        else
            windows = ({});
    }
    function clearDrag() { draggingId = ""; dragMs = 0; }

    onVisibleChanged: {
        if (!visible) clearDrag();
        Qt.callLater(refresh);
    }
    onStateChanged: {
        if (state.status === "empty") resetView();
        else if (draggingId && !durations[draggingId]) clearDrag();
        Qt.callLater(refresh);
    }
    onLanePositionsChanged: Qt.callLater(refresh)
    onStartChanged: Qt.callLater(refresh)
    onSpanChanged: Qt.callLater(refresh)

    RowLayout {
        Layout.fillWidth: true
        spacing: Math.round(6 * root.theme.scale)
        Text {
            Layout.fillWidth: true
            text: root.tr("人声与伴奏对齐")
            color: root.theme.text
            font.pixelSize: Math.round(15 * root.theme.scale)
            font.weight: Font.DemiBold
            elide: Text.ElideRight
        }
        UiButton {
            objectName: "mixdownAlignmentZoomOut"
            theme: root.theme
            text: "−"
            Accessible.name: root.tr("缩小时间轴")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            enabled: root.zoom > 1
            onClicked: root.setZoom(.5)
        }
        UiButton {
            objectName: "mixdownAlignmentZoomIn"
            theme: root.theme
            text: "+"
            Accessible.name: root.tr("放大时间轴")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            enabled: root.span > root.minSpan
            onClicked: root.setZoom(2)
        }
        UiButton {
            objectName: "mixdownAlignmentFit"
            theme: root.theme
            text: root.tr("全曲")
            enabled: root.zoom > 1
            onClicked: root.resetView()
        }
    }
    Text {
        Layout.fillWidth: true
        text: root.tr("向左右拖动歌声，伴奏固定。点时间尺可试听该处。")
        color: root.theme.muted
        wrapMode: Text.Wrap
        font.pixelSize: Math.round(11 * root.theme.scale)
    }

    Rectangle {
        id: timeline
        objectName: "mixdownAlignmentTimeline"
        Layout.fillWidth: true
        Layout.fillHeight: true
        color: root.theme.base
        radius: Math.round(7 * root.theme.scale)
        border.color: root.theme.line
        clip: true

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 1
            spacing: 1
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: root.rulerHeight
                spacing: 1
                Item { Layout.preferredWidth: root.labelWidth }
                Item {
                    id: ruler
                    objectName: "mixdownAlignmentRuler"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Repeater {
                        model: root.totalMs > 1 ? 5 : 0
                        Text {
                            required property int index
                            x: root.clamp(ruler.width * index / 4 - implicitWidth / 2, 3,
                                          Math.max(3, ruler.width - implicitWidth - 3))
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.time(root.start + root.span * index / 4, root.span < 15000)
                            color: root.theme.muted
                            font.family: "Consolas"
                            font.pixelSize: Math.round(10 * root.theme.scale)
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: root.canSeek
                        cursorShape: Qt.PointingHandCursor
                        onClicked: mouse => root.seek(mouse.x / width)
                    }
                }
            }

            Repeater {
                model: root.lanes.length
                RowLayout {
                    id: lane
                    required property int index
                    readonly property var row: root.lanes[index] || ({})
                    readonly property string identifier: row.id || ""
                    readonly property bool backing: identifier === "instrumental"
                    readonly property bool loaded: !!root.durations[identifier]
                    readonly property bool selected: !backing && root.active !== null && root.active.id === identifier
                    readonly property real offset: Number(row.offset || 0) * 1000
                    readonly property color ink: root.inks[index % root.inks.length]
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: Math.round(52 * root.theme.scale)
                    spacing: 1

                    UiButton {
                        Layout.preferredWidth: root.labelWidth
                        Layout.fillHeight: true
                        theme: root.theme
                        text: root.tr(String(lane.row.label || ""))
                        leftAligned: true
                        quiet: true
                        enabled: !lane.backing && !!lane.row.path
                        selected: lane.selected
                        ToolTip.text: lane.backing ? root.tr("伴奏是对齐基准") : String(lane.row.name || "")
                        ToolTip.visible: hovered && !!ToolTip.text
                        onClicked: root.selectedId = lane.identifier
                    }
                    Rectangle {
                        id: plot
                        objectName: "mixdownAlignmentLane-" + lane.identifier
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: lane.selected ? root.theme.selected : root.theme.surface
                        clip: true

                        Canvas {
                            id: wave
                            anchors.fill: parent
                            readonly property var segment: root.windows[lane.identifier]
                            readonly property real viewStart: root.start
                            readonly property real viewSpan: root.span
                            readonly property real shift: root.draggingId === lane.identifier ? root.dragMs : 0
                            readonly property color ink: lane.ink
                            readonly property color grid: root.theme.line
                            onSegmentChanged: requestPaint()
                            onViewStartChanged: requestPaint()
                            onViewSpanChanged: requestPaint()
                            onShiftChanged: requestPaint()
                            onInkChanged: requestPaint()
                            onGridChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onHeightChanged: requestPaint()
                            onPaint: {
                                const ctx = getContext("2d");
                                ctx.clearRect(0, 0, width, height);
                                ctx.strokeStyle = grid;
                                ctx.lineWidth = 1;
                                ctx.beginPath();
                                for (let i = 1; i < 4; i++) {
                                    const x = Math.round(width * i / 4) + .5;
                                    ctx.moveTo(x, 0); ctx.lineTo(x, height);
                                }
                                ctx.moveTo(0, height * .5); ctx.lineTo(width, height * .5);
                                ctx.stroke();
                                if (!segment || !segment.peaks || viewSpan <= 0)
                                    return;
                                const samples = segment.peaks;
                                const step = segment.step * width / viewSpan;
                                const left = (segment.start + shift - viewStart) * width / viewSpan;
                                const half = height * .42 / 32767;
                                const first = Math.max(0, Math.floor(-left / step));
                                const last = Math.min(samples.length / 2, Math.ceil((width - left) / step));
                                ctx.fillStyle = ink;
                                const extent = (lane.offset + shift + Number(root.durations[lane.identifier] || 0)
                                    - viewStart) * width / viewSpan;
                                if (last <= first)
                                    return;
                                ctx.beginPath();
                                for (let i = first; i < last; i++) {
                                    const x = Math.min(extent, left + (i + .5) * step);
                                    const y = height * .5 - samples[2 * i + 1] * half - .5;
                                    if (i === first) ctx.moveTo(left + i * step, y);
                                    ctx.lineTo(x, y);
                                    if (i + 1 === last) ctx.lineTo(Math.min(extent, left + last * step), y);
                                }
                                for (let i = last - 1; i >= first; i--) {
                                    const x = Math.min(extent, left + (i + .5) * step);
                                    const y = height * .5 - samples[2 * i] * half + .5;
                                    if (i + 1 === last) ctx.lineTo(Math.min(extent, left + last * step), y);
                                    ctx.lineTo(x, y);
                                    if (i === first) ctx.lineTo(left + i * step, y);
                                }
                                ctx.closePath();
                                ctx.fill();
                            }
                        }
                        Text {
                            visible: !lane.loaded
                            anchors.verticalCenter: parent.verticalCenter
                            x: Math.round(9 * root.theme.scale)
                            width: Math.max(0, parent.width - 2 * x)
                            text: lane.row.path ? (root.errors[lane.identifier] || root.tr("正在读取音频波形…"))
                                                : root.tr("未选素材")
                            textFormat: Text.PlainText
                            color: root.errors[lane.identifier] ? root.theme.attention : root.theme.muted
                            font.pixelSize: Math.round(11 * root.theme.scale)
                            elide: Text.ElideRight
                            ToolTip.visible: laneHover.hovered && truncated
                            ToolTip.text: text
                            HoverHandler { id: laneHover }
                        }
                        MouseArea {
                            id: gesture
                            anchors.fill: parent
                            readonly property bool canDrag: root.canEdit && !lane.backing && lane.loaded
                            property real pressX: 0
                            property real pressSpan: 0
                            property real pressOffset: 0
                            property string pressPath: ""
                            property bool moved: false
                            enabled: root.canSeek || canDrag
                            preventStealing: canDrag
                            cursorShape: canDrag ? (pressed && moved ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                                                 : Qt.PointingHandCursor
                            onPressed: mouse => {
                                forceActiveFocus(Qt.MouseFocusReason);
                                pressX = mouse.x;
                                pressSpan = root.span;
                                pressOffset = lane.offset;
                                pressPath = String(lane.row.path || "");
                                moved = false;
                                if (!lane.backing && pressPath)
                                    root.selectedId = lane.identifier;
                            }
                            onPositionChanged: mouse => {
                                if (!pressed || !canDrag || pressPath !== lane.row.path) return;
                                const dx = mouse.x - pressX;
                                if (!moved && Math.abs(dx) < 4 * root.theme.scale) return;
                                moved = true;
                                root.draggingId = lane.identifier;
                                const delta = root.clamp(dx / Math.max(1, width), -1, 1) * pressSpan;
                                root.dragMs = Math.round(root.clamp(pressOffset + delta, -30000, 30000)) - pressOffset;
                            }
                            onReleased: mouse => {
                                if (moved && canDrag && root.draggingId === lane.identifier && pressPath === lane.row.path
                                        && lane.offset === pressOffset && root.dragMs !== 0)
                                    root.controller.setTrack(lane.identifier, "offset", (pressOffset + root.dragMs) / 1000);
                                else if (!moved)
                                    root.seek(mouse.x / width);
                                root.clearDrag();
                            }
                            onCanceled: root.clearDrag()
                        }
                    }
                }
            }
        }
        Rectangle {
            objectName: "mixdownAlignmentPlayhead"
            visible: root.canSeek && root.positionMs >= root.start && root.positionMs <= root.start + root.span
            x: root.labelWidth + 2 + (root.positionMs - root.start) * ruler.width / root.span
            y: root.rulerHeight
            width: Math.max(1, Math.round(root.theme.scale))
            height: Math.max(0, parent.height - y)
            color: root.theme.accent
        }
    }
    Slider {
        id: panSlider
        objectName: "mixdownAlignmentPan"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.round(18 * root.theme.scale)
        from: 0
        to: Math.max(1, root.maxStart)
        value: root.start
        enabled: root.maxStart > 0
        visible: enabled
        wheelEnabled: false
        Accessible.name: root.tr("滚动时间轴")
        onMoved: root.pan = value
        background: Rectangle {
            y: (panSlider.height - height) / 2
            width: panSlider.width
            height: Math.round(6 * root.theme.scale)
            radius: height / 2
            color: root.theme.line
        }
        handle: Rectangle {
            width: Math.max(20 * root.theme.scale, panSlider.width * root.span / root.totalMs)
            height: Math.round(10 * root.theme.scale)
            x: panSlider.visualPosition * (panSlider.width - width)
            y: (panSlider.height - height) / 2
            radius: height / 2
            color: panSlider.pressed || panSlider.activeFocus ? root.theme.accent : root.theme.muted
        }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: Math.round(6 * root.theme.scale)
        Text {
            Layout.fillWidth: true
            text: root.active ? root.tr(String(root.active.label)) + " · " + root.tr("对齐偏移")
                              : root.tr("选择歌声文件后即可对齐")
            color: root.theme.muted
            font.pixelSize: Math.round(11 * root.theme.scale)
            elide: Text.ElideRight
        }
        UiNumberField {
            objectName: "mixdownAlignmentOffset"
            Layout.preferredWidth: Math.round(94 * root.theme.scale)
            theme: root.theme
            fieldLabel: root.tr("对齐偏移")
            unit: "ms"
            currentValue: root.active ? Math.round(root.active.offset * 1000
                + (root.draggingId === root.active.id ? root.dragMs : 0)) : 0
            minimum: -30000
            maximum: 30000
            decimals: 0
            arrowStep: 10
            scopeKey: root.active ? root.lanes.indexOf(root.active) : -1
            enabled: root.active !== null && root.canEdit
            onCommitted: (scope, value) => {
                if (root.active)
                    root.controller.setTrack(root.active.id, "offset", value / 1000);
            }
        }
        UiButton {
            objectName: "mixdownAlignmentReset"
            theme: root.theme
            text: root.tr("归零")
            enabled: root.canEdit && root.active !== null && root.active.offset !== 0
            onClicked: root.controller.setTrack(root.active.id, "offset", 0)
        }
        UiButton {
            theme: root.theme
            visible: root.state.status === "error" || root.state.status === "partial"
            text: root.tr("重读波形")
            onClicked: root.controller.retryAlignment()
        }
    }
}
