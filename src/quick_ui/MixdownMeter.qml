import QtQuick

Item {
    id: meter
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
    }
    required property QtObject theme
    property real rms: NaN
    property real peak: NaN
    property color accentColor: theme.accent
    property string accessibleLabel: ""

    implicitWidth: Math.round(34 * theme.scale)
    implicitHeight: Math.round(116 * theme.scale)
    Accessible.role: Accessible.ProgressBar
    Accessible.name: accessibleLabel
    Accessible.description: validRms ? rmsText(rms) + " dBFS" : meter.vwaTr("等待真实电平")

    readonly property bool validRms: meter.safeLevel(rms) === meter.safeLevel(rms)
    readonly property bool validPeak: meter.safeLevel(peak) === meter.safeLevel(peak)

    function safeLevel(value) {
        const number = Number(value);
        return isFinite(number) ? Math.max(-60, Math.min(0, number)) : NaN;
    }

    function normalized(value) {
        const level = safeLevel(value);
        return isFinite(level) ? (level + 60) / 60 : NaN;
    }

    function rmsText(value) {
        const level = Number(value);
        return isFinite(level) ? level.toFixed(1) : "—";
    }

    Rectangle {
        id: trough
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.max(8, Math.round(9 * meter.theme.scale))
        height: Math.max(0, readout.y - Math.round(3 * meter.theme.scale))
        radius: width / 2
        color: meter.theme.base
        border.width: 1
        border.color: meter.theme.line
        clip: true

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: parent.height * (meter.validRms ? meter.normalized(meter.rms) : 0)
            radius: parent.radius
            color: meter.accentColor
            opacity: meter.validRms ? 0.88 : 0

            Behavior on height {
                enabled: !meter.theme.reduceMotion && meter.validRms
                NumberAnimation {
                    duration: 55
                    easing.type: Easing.OutQuad
                }
            }
        }

        Rectangle {
            visible: meter.validPeak
            anchors.left: parent.left
            anchors.right: parent.right
            y: Math.max(0, Math.min(parent.height - height, parent.height * (1 - meter.normalized(meter.peak)) - height / 2))
            height: Math.max(2, Math.round(2 * meter.theme.scale))
            color: meter.theme.text
            opacity: meter.validPeak ? 0.92 : 0
        }
    }

    Repeater {
        model: 5
        Rectangle {
            required property int index
            x: trough.x - Math.round(2 * meter.theme.scale)
            y: Math.round(trough.height * index / 4)
            width: Math.round(4 * meter.theme.scale)
            height: 1
            color: meter.theme.line
            opacity: index === 2 ? 0 : 0.7
        }
    }

    Text {
        id: readout
        objectName: "mixdownMeterReadout"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        text: meter.validRms ? meter.rmsText(meter.rms) : "—"
        color: meter.theme.muted
        font.pixelSize: Math.round(9 * meter.theme.scale)
        textFormat: Text.PlainText
    }
}
