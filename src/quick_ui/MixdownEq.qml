import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
    }
    objectName: "mixdownEq"
    required property QtObject theme
    required property QtObject controller

    property var bands: []
    property var eqCurve: []
    property var spectrum: ({})
    property real maxHz: 20000
    property bool showBandDetails: true
    property bool showPlotHeader: true
    property int minimumPlotHeight: Math.round(230 * theme.scale)

    property int activeBand: 0
    property int dragBand: -1
    property bool dragging: false
    property real dragFrequency: 0
    property real dragGain: 0
    property bool dragPending: false
    property bool dragEqEnabled: false
    readonly property color beforeInk: theme.dark ? "#e7b671" : "#925318"
    readonly property color afterInk: theme.dark ? "#63d5d9" : "#00747d"
    readonly property real spectrumFloor: -96
    readonly property var gridTheme: [theme.line, theme.selectedLine, theme.muted, theme.scale]
    readonly property color responseInk: theme.accent
    onGridThemeChanged: schedulePaint(gridCanvas)
    onBeforeInkChanged: schedulePaint(spectrumCanvas)
    onAfterInkChanged: schedulePaint(spectrumCanvas)
    onResponseInkChanged: schedulePaint(responseCanvas)

    readonly property real expectedMaxHz: Math.max(30, safeNumber(maxHz, 20000))
    readonly property int availableBands: Math.min(4, bands ? bands.length : 0)
    readonly property bool hasSpectrum: spectrum && spectrum.freqs && spectrum.before && spectrum.freqs.length > 0 && spectrum.before.length > 0
    readonly property string spectrumSource: spectrum && spectrum.source ? String(spectrum.source) : ""
    readonly property string currentType: activeBand >= 0 ? String(bandValue(activeBand, "type", "bell")) : "bell"

    onBandsChanged: {
        if (activeBand >= availableBands)
            activeBand = availableBands > 0 ? 0 : -1;
    }
    onEqCurveChanged: schedulePaint(responseCanvas)
    onSpectrumChanged: schedulePaint(spectrumCanvas)
    onMaxHzChanged: {
        schedulePaint(gridCanvas)
        schedulePaint(spectrumCanvas)
    }

    Timer {
        id: dragPublish
        interval: 16
        onTriggered: root.flushDrag()
    }

    function safeNumber(value, fallback) {
        const number = Number(value);
        return isFinite(number) ? number : fallback;
    }

    function clamp(value, low, high) {
        return Math.max(low, Math.min(high, value));
    }

    function bandObject(index) {
        return root.bands && index >= 0 && index < root.bands.length ? root.bands[index] : null;
    }

    function bandValue(index, key, fallback) {
        const item = bandObject(index);
        if (!item || item[key] === undefined || item[key] === null)
            return fallback;
        return item[key];
    }

    function isBandEnabled(index) {
        return !!bandValue(index, "enabled", true);
    }

    function bandFrequency(index) {
        if (root.dragging && root.dragBand === index)
            return root.dragFrequency;
        return clamp(safeNumber(bandValue(index, "freq_hz", 1000), 1000), 20, root.expectedMaxHz);
    }

    function bandGain(index) {
        if (root.dragging && root.dragBand === index)
            return root.dragGain;
        return clamp(safeNumber(bandValue(index, "gain_db", 0), 0), -18, 18);
    }

    function qValue(index) {
        return clamp(safeNumber(bandValue(index, "q", 0.707), 0.707), 0.1, 24);
    }

    function logPosition(frequency) {
        const hz = clamp(safeNumber(frequency, 20), 20, root.expectedMaxHz);
        return Math.log(hz / 20) / Math.log(root.expectedMaxHz / 20);
    }

    function frequencyAt(position) {
        return 20 * Math.pow(root.expectedMaxHz / 20, clamp(position, 0, 1));
    }

    function plotGeometry(widthValue, heightValue) {
        const left = Math.round(48 * root.theme.scale);
        const right = Math.round(53 * root.theme.scale);
        const top = Math.round(18 * root.theme.scale);
        const bottom = Math.round(26 * root.theme.scale);
        return {
            left: left,
            top: top,
            width: Math.max(1, widthValue - left - right),
            height: Math.max(1, heightValue - top - bottom)
        };
    }

    function xForFrequency(frequency, geometry) {
        return geometry.left + root.logPosition(frequency) * geometry.width;
    }

    function yForGain(gain, geometry) {
        return geometry.top + (18 - clamp(safeNumber(gain, 0), -18, 18)) / 36 * geometry.height;
    }

    function gainAtY(y, geometry) {
        return clamp(18 - (y - geometry.top) / geometry.height * 36, -18, 18);
    }

    function bandColor(index) {
        const colors = [root.theme.attention, root.theme.success, root.theme.accent, root.theme.error];
        return colors[Math.max(0, index) % colors.length];
    }

    function typeLabel(kind) {
        switch (String(kind || "").toLowerCase()) {
        case "highpass":
            return "高通";
        case "lowshelf":
            return "低架";
        case "highshelf":
            return "高架";
        case "bell":
            return "峰值";
        default:
            return String(kind || "类型");
        }
    }

    function typeOptions() {
        return [
            {
                label: root.vwaTr("高通"),
                value: "highpass"
            },
            {
                label: root.vwaTr("低架"),
                value: "lowshelf"
            },
            {
                label: root.vwaTr("峰值"),
                value: "bell"
            },
            {
                label: root.vwaTr("高架"),
                value: "highshelf"
            }
        ];
    }

    function typeIndex(kind) {
        const options = typeOptions();
        for (let index = 0; index < options.length; ++index) {
            if (options[index].value === String(kind || ""))
                return index;
        }
        return 2;
    }

    function frequencyLabel(value) {
        const hz = safeNumber(value, 0);
        if (hz >= 1000)
            return (hz / 1000).toFixed(hz >= 10000 ? 1 : 2).replace(/\.0+$/, "") + " kHz";
        return Math.round(hz) + " Hz";
    }

    function gainLabel(value) {
        const gain = safeNumber(value, 0);
        return (gain >= 0 ? "+" : "") + gain.toFixed(1) + " dB";
    }

    function schedulePaint(canvas) {
        if (canvas && canvas.visible && canvas.available)
            canvas.requestPaint()
    }

    function flushDrag() {
        if (!root.dragPending || root.dragBand < 0)
            return
        root.dragPending = false
        if (!root.dragEqEnabled && root.isBandEnabled(root.dragBand)) {
            root.ensureEqEnabled()
            root.dragEqEnabled = true
        }
        root.controller.moveBand(root.dragBand, root.dragFrequency, root.dragGain)
    }

    function nearestBand(x, y, geometry) {
        let found = -1;
        let distance = Math.round(20 * root.theme.scale);
        for (let index = 0; index < root.availableBands; ++index) {
            const dx = x - root.xForFrequency(root.bandFrequency(index), geometry);
            const dy = y - root.yForGain(root.bandGain(index), geometry);
            const next = Math.sqrt(dx * dx + dy * dy);
            if (next <= distance) {
                distance = next;
                found = index;
            }
        }
        return found;
    }

    function ensureEqEnabled() {
        if (root.controller && typeof root.controller.setOption === "function")
            root.controller.setOption("eqEnabled", true);
    }
    function commitNumeric(key, raw, fallback) {
        if (root.activeBand < 0)
            return;
        const value = Number(raw);
        if (!isFinite(value))
            return;
        let low = 0;
        let high = 100000;
        if (key === "freq_hz") {
            low = 20;
            high = root.expectedMaxHz;
        } else if (key === "gain_db") {
            low = -18;
            high = 18;
        } else if (key === "q") {
            low = 0.1;
            high = 24;
        }
        root.ensureEqEnabled();
        root.controller.setBand(root.activeBand, key, root.clamp(value, low, high));
    }

    function editBandWithKey(event) {
        if (root.activeBand < 0 || root.availableBands === 0
                || (event.modifiers & (Qt.AltModifier | Qt.MetaModifier)))
            return;
        const index = root.activeBand;
        const fine = (event.modifiers & Qt.ShiftModifier) !== 0;
        const ctrl = (event.modifiers & Qt.ControlModifier) !== 0;
        const locked = root.bandValue(index, "type", "") === "highpass";
        const key = event.key;
        if (key === Qt.Key_Space) {
            root.controller.setBand(index, "enabled", !root.isBandEnabled(index));
        } else if (key === Qt.Key_Return || key === Qt.Key_Enter) {
            root.controller.resetBand(index);
        } else if (!ctrl && (key === Qt.Key_Left || key === Qt.Key_Right)) {
            const ratio = Math.pow(2, (key === Qt.Key_Right ? 1 : -1) / (fine ? 240 : 24));
            root.controller.setBand(index, "freq_hz", root.bandFrequency(index) * ratio);
        } else if (key === Qt.Key_PageUp || key === Qt.Key_PageDown || (ctrl && (key === Qt.Key_Up || key === Qt.Key_Down))) {
            const direction = key === Qt.Key_PageUp || key === Qt.Key_Up ? 1 : -1;
            if (!locked)
                root.controller.setBand(index, "q", root.qValue(index) * Math.pow(fine ? 1.01 : 1.06, direction));
        } else if (!ctrl && (key === Qt.Key_Up || key === Qt.Key_Down)) {
            if (!locked)
                root.controller.setBand(index, "gain_db", root.bandGain(index) + (key === Qt.Key_Up ? 1 : -1) * (fine ? 0.1 : 0.5));
        } else {
            return;
        }
        event.accepted = true;
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Math.round(9 * root.theme.scale)

        RowLayout {
            visible: root.showPlotHeader
            Layout.fillWidth: true
            spacing: Math.round(8 * root.theme.scale)

            Text {
                text: root.vwaTr("四段 EQ")
                color: root.theme.text
                font.pixelSize: Math.round(16 * root.theme.scale)
                font.weight: Font.DemiBold
            }
            Row {
                spacing: Math.round(2 * root.theme.scale)
                Repeater {
                    model: 3
                    Rectangle {
                        width: Math.round(4 * root.theme.scale)
                        height: Math.max(1, Math.round(root.theme.scale))
                        radius: height / 2
                        color: root.beforeInk
                    }
                }
            }
            Text {
                text: root.vwaTr("处理前")
                color: root.beforeInk
                font.pixelSize: Math.round(11 * root.theme.scale)
            }
            Rectangle {
                Layout.preferredWidth: Math.round(16 * root.theme.scale)
                Layout.preferredHeight: Math.round(5 * root.theme.scale)
                color: Qt.rgba(root.afterInk.r, root.afterInk.g, root.afterInk.b, 0.22)
                Rectangle {
                    anchors { left: parent.left; right: parent.right; top: parent.top }
                    height: Math.max(1, Math.round(2 * root.theme.scale))
                    radius: height / 2
                    color: root.afterInk
                }
            }
            Text {
                text: root.vwaTr("处理后")
                color: root.afterInk
                font.pixelSize: Math.round(11 * root.theme.scale)
            }
            Item {
                Layout.fillWidth: true
            }
            Text {
                Layout.maximumWidth: Math.round(230 * root.theme.scale)
                text: root.hasSpectrum ? root.vwaTr("实时频谱") : root.vwaTr("等待真实频谱")
                color: root.hasSpectrum ? root.theme.muted : root.theme.attention
                font.pixelSize: Math.round(10 * root.theme.scale)
                elide: Text.ElideRight
            }
        }

        Rectangle {
            id: chartFrame
            readonly property var plotBounds: root.plotGeometry(width, height)
            objectName: "mixdownEqPlot"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: root.minimumPlotHeight
            radius: Math.round(9 * root.theme.scale)
            gradient: Gradient {
                GradientStop { position: 0; color: root.theme.dark ? "#1b1d27" : root.theme.surface }
                GradientStop { position: 1; color: root.theme.base }
            }
            border.width: 1
            border.color: activeFocus ? root.theme.accent : root.theme.line
            clip: true
            activeFocusOnTab: true
            Accessible.role: Accessible.Pane
            Accessible.name: root.vwaTr("混音 EQ 曲线")
            Accessible.description: root.vwaTr("拖动改频率和增益，滚轮调 Q，Alt 点击旁路，双击恢复。焦点在图内时方向键精调，Space 启停当前频段，Enter 恢复。")
            Keys.onPressed: event => root.editBandWithKey(event)

            Canvas {
                id: gridCanvas
                anchors.fill: parent
                renderTarget: Canvas.Image
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onVisibleChanged: if (visible) requestPaint()
                onAvailableChanged: if (available && visible) requestPaint()

                onPaint: {
                    const ctx = getContext("2d");
                    ctx.clearRect(0, 0, width, height);
                    ctx.globalAlpha = 1;
                    if (width < 20 || height < 20)
                        return;
                    const geometry = chartFrame.plotBounds;
                    const scale = root.theme.scale;
                    ctx.lineWidth = Math.max(1, scale);
                    ctx.font = Math.round(10 * scale) + "px sans-serif";
                    ctx.textBaseline = "middle";

                    const frequencyTicks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
                        .filter(hz => hz <= root.expectedMaxHz);
                    const lastTick = frequencyTicks[frequencyTicks.length - 1];
                    const lastLabelStart = root.xForFrequency(lastTick, geometry)
                        - ctx.measureText(root.frequencyLabel(lastTick)).width;
                    const labelGap = 5 * scale;
                    let previousLabelEnd = -Infinity;
                    for (let tick = 0; tick < frequencyTicks.length; ++tick) {
                        const hz = frequencyTicks[tick];
                        const x = root.xForFrequency(hz, geometry);
                        ctx.strokeStyle = root.theme.line;
                        ctx.globalAlpha = 0.55;
                        ctx.beginPath();
                        ctx.moveTo(x, geometry.top);
                        ctx.lineTo(x, geometry.top + geometry.height);
                        ctx.stroke();
                        ctx.globalAlpha = 1;
                        const label = root.frequencyLabel(hz);
                        const labelWidth = ctx.measureText(label).width;
                        const first = tick === 0;
                        const last = hz === lastTick && !first;
                        const left = first ? x : last ? x - labelWidth : x - labelWidth / 2;
                        if (!first && !last && (left < previousLabelEnd + labelGap
                                || left + labelWidth + labelGap > lastLabelStart))
                            continue;
                        ctx.fillStyle = root.theme.muted;
                        ctx.textAlign = first ? "left" : last ? "right" : "center";
                        ctx.fillText(label, x, geometry.top + geometry.height + Math.round(14 * scale));
                        previousLabelEnd = left + labelWidth;
                    }

                    const gainTicks = [18, 9, 0, -9, -18];
                    for (let tick = 0; tick < gainTicks.length; ++tick) {
                        const gain = gainTicks[tick];
                        const y = root.yForGain(gain, geometry);
                        ctx.strokeStyle = gain === 0 ? root.theme.selectedLine : root.theme.line;
                        ctx.globalAlpha = gain === 0 ? 0.95 : 0.55;
                        ctx.beginPath();
                        ctx.moveTo(geometry.left, y);
                        ctx.lineTo(geometry.left + geometry.width, y);
                        ctx.stroke();
                        ctx.globalAlpha = 1;
                        ctx.fillStyle = root.theme.muted;
                        ctx.textAlign = "right";
                        ctx.fillText((gain > 0 ? "+" : "") + gain + " dB", geometry.left - Math.round(7 * scale), y);
                    }

                    // Spectral level and EQ gain have separate scales.
                    ctx.textAlign = "left"
                    ctx.fillStyle = root.theme.muted
                    const right = geometry.left + geometry.width + Math.round(7 * scale)
                    for (let i = 0; i <= 4; ++i)
                        ctx.fillText(i === 0 ? "0 dBFS" : String(root.spectrumFloor * i / 4),
                                     right, geometry.top + geometry.height * i / 4)
                }
            }

            Canvas {
                id: spectrumCanvas
                objectName: "mixdownEqSpectrum"
                x: chartFrame.plotBounds.left
                y: chartFrame.plotBounds.top
                width: chartFrame.plotBounds.width
                height: chartFrame.plotBounds.height
                renderTarget: Canvas.Image
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onVisibleChanged: if (visible) requestPaint()
                onAvailableChanged: if (available && visible) requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    if (!root.hasSpectrum)
                        return
                    const freqs = root.spectrum.freqs;
                    root.drawSpectrum(ctx, root.spectrum.after, freqs, width, height, root.afterInk, false);
                    root.drawSpectrum(ctx, root.spectrum.before, freqs, width, height, root.beforeInk, true);
                }
            }

            Canvas {
                id: responseCanvas
                objectName: "mixdownEqResponse"
                anchors.fill: parent
                renderTarget: Canvas.Image
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onVisibleChanged: if (visible) requestPaint()
                onAvailableChanged: if (available && visible) requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    const geometry = chartFrame.plotBounds
                    const scale = root.theme.scale
                    if (root.eqCurve && root.eqCurve.length > 0) {
                        ctx.beginPath();
                        let firstX = NaN;
                        let lastX = NaN;
                        for (let index = 0; index < root.eqCurve.length; ++index) {
                            const point = root.eqCurve[index];
                            if (!point)
                                continue;
                            const nx = Number(point.x);
                            const gain = Number(point.y);
                            if (!isFinite(nx) || !isFinite(gain))
                                continue;
                            const x = geometry.left + root.clamp(nx, 0, 1) * geometry.width;
                            const y = root.yForGain(gain, geometry);
                            if (!isFinite(firstX)) {
                                firstX = x;
                                ctx.moveTo(x, y);
                            } else {
                                ctx.lineTo(x, y);
                            }
                            lastX = x;
                        }
                        if (isFinite(firstX) && lastX > firstX) {
                            ctx.strokeStyle = root.responseInk;
                            ctx.lineWidth = Math.max(2, 2.4 * scale);
                            ctx.lineJoin = "round";
                            ctx.stroke();
                            const zeroY = root.yForGain(0, geometry);
                            ctx.lineTo(lastX, zeroY);
                            ctx.lineTo(firstX, zeroY);
                            ctx.closePath();
                            ctx.globalAlpha = 0.10;
                            ctx.fillStyle = root.responseInk;
                            ctx.fill();
                            ctx.globalAlpha = 1;
                        }
                    }
                }
            }

            Repeater {
                model: root.availableBands
                Rectangle {
                    required property int index
                    readonly property bool picked: root.activeBand === index
                    readonly property bool bandOn: root.isBandEnabled(index)
                    width: Math.round((picked ? 17 : 14) * root.theme.scale)
                    height: width
                    x: root.xForFrequency(root.bandFrequency(index), chartFrame.plotBounds) - width / 2
                    y: root.yForGain(root.bandGain(index), chartFrame.plotBounds) - height / 2
                    radius: width / 2
                    color: root.theme.base
                    border.color: bandOn ? root.bandColor(index) : root.theme.muted
                    border.width: Math.max(1, Math.round(2 * root.theme.scale))
                    opacity: bandOn ? 1 : 0.7
                    Rectangle {
                        visible: parent.picked
                        anchors.centerIn: parent
                        width: parent.width + Math.round(7 * root.theme.scale)
                        height: width
                        radius: width / 2
                        color: "transparent"
                        border.color: root.theme.text
                    }
                    Text {
                        anchors.centerIn: parent
                        text: String(parent.index + 1)
                        color: root.theme.text
                        font.weight: Font.DemiBold
                        font.pixelSize: Math.round(9 * root.theme.scale)
                    }
                }
            }

            Text {
                visible: !root.hasSpectrum
                anchors.horizontalCenter: parent.horizontalCenter
                y: chartFrame.plotBounds.top + chartFrame.plotBounds.height * 0.17
                text: root.vwaTr("等待真实频谱")
                color: root.theme.muted
                font.pixelSize: Math.round(11 * root.theme.scale)
            }

            MouseArea {
                id: plotMouse
                objectName: "mixdownEqPlotInput"
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                preventStealing: true
                cursorShape: root.activeBand >= 0 && containsMouse ? Qt.SizeAllCursor : Qt.ArrowCursor

                onPressed: function (mouse) {
                    const geometry = root.plotGeometry(width, height);
                    const hit = root.nearestBand(mouse.x, mouse.y, geometry);
                    chartFrame.forceActiveFocus(Qt.MouseFocusReason);
                    if (hit < 0) {
                        mouse.accepted = false;
                        return;
                    }
                    root.activeBand = hit;
                    if ((mouse.modifiers & Qt.AltModifier) !== 0) {
                        root.controller.setBand(hit, "enabled", !root.isBandEnabled(hit));
                        return;
                    }
                    // Snapshot the actual node before switching the drawing to
                    // the drag values. A press alone must not move the node.
                    root.dragFrequency = root.bandFrequency(hit);
                    root.dragGain = root.bandGain(hit);
                    root.dragBand = hit;
                    root.dragPending = root.dragEqEnabled = false;
                    root.dragging = true;
                }
                onPositionChanged: function (mouse) {
                    if (!root.dragging || root.dragBand < 0)
                        return;
                    const geometry = root.plotGeometry(width, height);
                    root.dragFrequency = root.clamp(root.frequencyAt((mouse.x - geometry.left) / geometry.width), 20, root.expectedMaxHz);
                    root.dragGain = root.bandValue(root.dragBand, "type", "") === "highpass" ? 0 : root.gainAtY(mouse.y, geometry);
                    root.dragPending = true;
                    if (!dragPublish.running)
                        dragPublish.start();
                }
                onReleased: {
                    dragPublish.stop()
                    root.flushDrag()
                    root.dragging = false;
                    root.dragBand = -1;
                }
                onCanceled: {
                    dragPublish.stop()
                    root.dragPending = false
                    root.dragging = false;
                    root.dragBand = -1;
                }
                onDoubleClicked: function (mouse) {
                    const geometry = root.plotGeometry(width, height);
                    const hit = root.nearestBand(mouse.x, mouse.y, geometry);
                    if (hit >= 0) {
                        root.activeBand = hit;
                        dragPublish.stop()
                        root.dragPending = false
                        root.dragging = false;
                        root.dragBand = -1;
                        root.controller.resetBand(hit);
                    }
                }
                onWheel: function (wheel) {
                    const hit = root.nearestBand(wheel.x, wheel.y, root.plotGeometry(width, height));
                    if (hit < 0) {
                        wheel.accepted = false;
                        return;
                    }
                    root.activeBand = hit;
                    chartFrame.forceActiveFocus(Qt.MouseFocusReason);
                    if (root.bandValue(hit, "type", "") !== "highpass") {
                        const delta = wheel.angleDelta.y || wheel.pixelDelta.y * 8;
                        const fine = (wheel.modifiers & Qt.ShiftModifier) !== 0;
                        root.controller.setBand(hit, "q", root.qValue(hit) * Math.pow(fine ? 1.035 : 1.18, delta / 120));
                    }
                    wheel.accepted = true;
                }
            }

            Rectangle {
                visible: root.activeBand >= 0 && root.availableBands > 0
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: Math.round(8 * root.theme.scale)
                radius: Math.round(6 * root.theme.scale)
                color: root.theme.surface
                border.color: root.theme.line
                width: activeBandLabel.implicitWidth + Math.round(14 * root.theme.scale)
                height: activeBandLabel.implicitHeight + Math.round(8 * root.theme.scale)
                Text {
                    id: activeBandLabel
                    anchors.centerIn: parent
                    text: "B" + (root.activeBand + 1) + " · " + root.frequencyLabel(root.bandFrequency(root.activeBand)) + " · " + root.gainLabel(root.bandGain(root.activeBand))
                    color: root.theme.text
                    font.pixelSize: Math.round(10 * root.theme.scale)
                }
            }
        }

        GridLayout {
            id: bandCards
            visible: root.showBandDetails
            Layout.fillWidth: true
            columns: root.width >= Math.round(660 * root.theme.scale) ? 4 : 2
            columnSpacing: Math.round(7 * root.theme.scale)
            rowSpacing: Math.round(7 * root.theme.scale)

            Repeater {
                model: root.availableBands
                Rectangle {
                    required property int index
                    Layout.fillWidth: true
                    implicitHeight: Math.round(54 * root.theme.scale)
                    radius: Math.round(7 * root.theme.scale)
                    color: root.activeBand === index ? root.theme.selected : root.theme.surface
                    border.width: 1
                    border.color: root.activeBand === index ? root.theme.selectedLine : root.theme.line
                    opacity: root.isBandEnabled(index) ? 1 : 0.62

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: Math.round(9 * root.theme.scale)
                        spacing: Math.round(7 * root.theme.scale)
                        Rectangle {
                            width: Math.round(8 * root.theme.scale)
                            height: width
                            radius: width / 2
                            color: root.bandColor(index)
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                text: "B" + (index + 1) + " · " + root.typeLabel(root.bandValue(index, "type", "bell"))
                                color: root.theme.text
                                font.pixelSize: Math.round(11 * root.theme.scale)
                                font.weight: Font.DemiBold
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.frequencyLabel(root.bandFrequency(index)) + "  " + root.gainLabel(root.bandGain(index)) + "  Q " + root.qValue(index).toFixed(2)
                                color: root.theme.muted
                                font.pixelSize: Math.round(9 * root.theme.scale)
                                elide: Text.ElideRight
                            }
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            root.activeBand = index;
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: detailGrid.implicitHeight + Math.round(18 * root.theme.scale)
            radius: Math.round(8 * root.theme.scale)
            color: root.theme.surface
            border.color: root.theme.line
            visible: root.showBandDetails && root.availableBands > 0

            GridLayout {
                id: detailGrid
                anchors.fill: parent
                anchors.margins: Math.round(9 * root.theme.scale)
                columns: root.width >= Math.round(680 * root.theme.scale) ? 7 : 3
                columnSpacing: Math.round(7 * root.theme.scale)
                rowSpacing: Math.round(7 * root.theme.scale)

                Text {
                    text: "B" + (root.activeBand + 1)
                    color: root.theme.muted
                    font.pixelSize: Math.round(11 * root.theme.scale)
                    font.weight: Font.DemiBold
                    Layout.alignment: Qt.AlignVCenter
                }

                UiComboBox {
                    id: typeCombo
                    Layout.fillWidth: true
                    Layout.minimumWidth: Math.round(96 * root.theme.scale)
                    theme: root.theme
                    model: root.typeOptions()
                    currentIndex: root.typeIndex(root.currentType)
                    onActivated: {
                        if (root.activeBand >= 0 && currentValue)
                            root.ensureEqEnabled();
                        root.controller.setBand(root.activeBand, "type", currentValue);
                    }
                    Accessible.name: root.vwaTr("频段类型")
                }

                TextField {
                    id: freqField
                    Layout.fillWidth: true
                    Layout.minimumWidth: Math.round(72 * root.theme.scale)
                    text: root.activeBand >= 0 ? Math.round(root.bandValue(root.activeBand, "freq_hz", 1000)) : ""
                    color: root.theme.text
                    font.pixelSize: Math.round(11 * root.theme.scale)
                    horizontalAlignment: TextInput.AlignHCenter
                    selectByMouse: true
                    validator: DoubleValidator {
                        bottom: 20
                        top: root.expectedMaxHz
                        decimals: 0
                    }
                    placeholderText: "Hz"
                    Accessible.name: root.vwaTr("中心频率")
                    background: Rectangle {
                        radius: Math.round(6 * root.theme.scale)
                        color: root.theme.base
                        border.color: freqField.activeFocus ? root.theme.accent : root.theme.line
                    }
                    onEditingFinished: root.commitNumeric("freq_hz", text, 1000)
                }

                TextField {
                    id: gainField
                    Layout.fillWidth: true
                    Layout.minimumWidth: Math.round(66 * root.theme.scale)
                    text: root.activeBand >= 0 ? Number(root.bandValue(root.activeBand, "gain_db", 0)).toFixed(1) : ""
                    color: root.theme.text
                    font.pixelSize: Math.round(11 * root.theme.scale)
                    horizontalAlignment: TextInput.AlignHCenter
                    selectByMouse: true
                    validator: DoubleValidator {
                        bottom: -18
                        top: 18
                        decimals: 1
                    }
                    placeholderText: "dB"
                    Accessible.name: root.vwaTr("频段增益")
                    background: Rectangle {
                        radius: Math.round(6 * root.theme.scale)
                        color: root.theme.base
                        border.color: gainField.activeFocus ? root.theme.accent : root.theme.line
                    }
                    onEditingFinished: root.commitNumeric("gain_db", text, 0)
                }

                TextField {
                    id: qField
                    Layout.fillWidth: true
                    Layout.minimumWidth: Math.round(62 * root.theme.scale)
                    text: root.activeBand >= 0 ? root.qValue(root.activeBand).toFixed(2) : ""
                    color: root.theme.text
                    font.pixelSize: Math.round(11 * root.theme.scale)
                    horizontalAlignment: TextInput.AlignHCenter
                    selectByMouse: true
                    validator: DoubleValidator {
                        bottom: 0.1
                        top: 24
                        decimals: 2
                    }
                    placeholderText: "Q"
                    Accessible.name: root.vwaTr("频段 Q")
                    background: Rectangle {
                        radius: Math.round(6 * root.theme.scale)
                        color: root.theme.base
                        border.color: qField.activeFocus ? root.theme.accent : root.theme.line
                    }
                    onEditingFinished: root.commitNumeric("q", text, 0.707)
                }

                UiButton {
                    theme: root.theme
                    objectName: "mixdownEqBandToggle"
                    enabled: root.activeBand >= 0
                    selected: enabled && root.isBandEnabled(root.activeBand)
                    text: selected ? root.vwaTr("关闭") : root.vwaTr("启用")
                    onClicked: {
                        const turnOn = !root.isBandEnabled(root.activeBand)
                        if (turnOn)
                            root.ensureEqEnabled()
                        root.controller.setBand(root.activeBand, "enabled", turnOn)
                    }
                    Accessible.name: text
                }
            }
        }
    }

    function drawSpectrum(ctx, values, freqs, width, height, ink, isBefore) {
        if (!values || !freqs)
            return;
        const count = Math.min(values.length, freqs.length);
        const maxHz = root.expectedMaxHz;
        const pxPerLog = width / Math.log(maxHz / 20);
        const floor = root.spectrumFloor;
        let firstX = NaN;
        let lastX = NaN;
        let hasLevel = false;
        ctx.beginPath();
        for (let index = 0; index < count; ++index) {
            const hz = Number(freqs[index]);
            const level = Number(values[index]);
            if (!isFinite(level) || !isFinite(hz) || hz < 20 || hz > maxHz)
                continue;
            const x = Math.log(hz / 20) * pxPerLog;
            const y = root.clamp(level / floor, 0, 1) * height;
            if (!isFinite(firstX)) {
                firstX = x;
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
            lastX = x;
            hasLevel = hasLevel || level > floor;
        }
        if (!hasLevel || !isFinite(firstX) || !(lastX > firstX))
            return;

        const scale = root.theme.scale;
        ctx.strokeStyle = ink;
        ctx.globalAlpha = isBefore ? 0.86 : 0.93;
        ctx.lineWidth = Math.max(1, (isBefore ? 1.2 : 1.85) * scale);
        ctx.lineJoin = "round";
        ctx.setLineDash(isBefore ? [3.5 * scale, 3 * scale] : []);
        ctx.stroke();
        ctx.globalAlpha = 1;

        if (!isBefore) {
            ctx.lineTo(lastX, height);
            ctx.lineTo(firstX, height);
            ctx.closePath();
            ctx.globalAlpha = 0.12;
            ctx.fillStyle = ink;
            ctx.fill();
            ctx.globalAlpha = 1;
        }
    }
}
