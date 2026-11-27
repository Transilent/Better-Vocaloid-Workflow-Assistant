import QtQuick
import QtQuick.Controls

ScrollBar {
    id: bar
    required property QtObject theme

    implicitWidth: orientation === Qt.Vertical ? Math.round(10 * theme.scale) : 0
    implicitHeight: orientation === Qt.Horizontal ? Math.round(10 * theme.scale) : 0
    padding: Math.round(2 * theme.scale)
    hoverEnabled: true

    contentItem: Rectangle {
        implicitWidth: Math.round((bar.hovered || bar.pressed ? 6 : 4) * bar.theme.scale)
        implicitHeight: Math.round((bar.hovered || bar.pressed ? 6 : 4) * bar.theme.scale)
        radius: Math.min(width, height) / 2
        color: bar.pressed ? bar.theme.accent : bar.theme.muted
        opacity: bar.active || bar.hovered || bar.pressed ? (bar.pressed ? 0.82 : bar.hovered ? 0.58 : 0.34) : 0.0
        Behavior on opacity {
            enabled: !bar.theme.reduceMotion
            NumberAnimation { duration: 150 }
        }
    }
    background: Item {}
}
