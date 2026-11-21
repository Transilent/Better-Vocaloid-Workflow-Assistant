import QtQuick
import QtQuick.Controls

Slider {
    id: control
    required property QtObject theme
    implicitHeight: Math.round(29 * theme.scale)

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        width: control.availableWidth
        height: Math.round(4 * control.theme.scale)
        radius: height / 2
        color: control.theme.line
        Rectangle {
            width: parent.width * control.visualPosition
            height: parent.height
            radius: parent.radius
            color: control.enabled ? control.theme.accent : control.theme.muted
            opacity: control.enabled ? 1 : .36
        }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * Math.max(0, control.availableWidth - width)
        y: control.topPadding + (control.availableHeight - height) / 2
        width: Math.round((control.pressed ? 15 : 13) * control.theme.scale)
        height: width
        radius: width / 2
        color: control.enabled ? control.theme.accent : control.theme.muted
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus ? control.theme.text : control.theme.surface
        opacity: control.enabled ? 1 : .55
        Behavior on width {
            enabled: !control.theme.reduceMotion
            NumberAnimation { duration: 100 }
        }
    }
}
