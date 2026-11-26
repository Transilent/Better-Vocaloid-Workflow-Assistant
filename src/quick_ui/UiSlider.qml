import QtQuick
import QtQuick.Controls

Slider {
    id: control
    required property QtObject theme
    property bool compact: false

    implicitHeight: Math.round((compact ? 24 : 28) * theme.scale)
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        width: control.availableWidth
        height: Math.round((control.hovered || control.pressed ? 5 : 4) * control.theme.scale)
        radius: height / 2
        color: control.theme.dark ? "#282c3d" : "#dcd8e8"
        Behavior on height {
            enabled: !control.theme.reduceMotion
            NumberAnimation { duration: 90; easing.type: Easing.OutCubic }
        }
        Rectangle {
            width: parent.width * control.visualPosition
            height: parent.height
            radius: parent.radius
            color: control.enabled ? control.theme.accent : control.theme.muted
            opacity: control.enabled ? 1.0 : 0.35
        }
    }

    handle: Item {
        x: control.leftPadding + control.visualPosition * Math.max(0, control.availableWidth - width)
        y: control.topPadding + (control.availableHeight - height) / 2
        width: Math.round(16 * control.theme.scale)
        height: width

        Rectangle {
            anchors.centerIn: parent
            width: Math.round((control.pressed ? 22 : control.hovered || control.visualFocus ? 20 : 0) * control.theme.scale)
            height: width
            radius: width / 2
            color: control.theme.accent
            opacity: control.enabled ? (control.pressed ? 0.22 : 0.14) : 0
            Behavior on width {
                enabled: !control.theme.reduceMotion
                NumberAnimation { duration: 100; easing.type: Easing.OutCubic }
            }
        }
        Rectangle {
            anchors.centerIn: parent
            width: Math.round((control.pressed ? 14 : control.hovered ? 13 : 12) * control.theme.scale)
            height: width
            radius: width / 2
            color: control.enabled ? (control.theme.dark ? "#f4f2fc" : "#ffffff") : control.theme.muted
            border.width: control.visualFocus ? 2 : 1.5
            border.color: control.enabled ? control.theme.accent : control.theme.line
            opacity: control.enabled ? 1.0 : 0.55
            Behavior on width {
                enabled: !control.theme.reduceMotion
                NumberAnimation { duration: 90; easing.type: Easing.OutCubic }
            }
        }
    }
}
