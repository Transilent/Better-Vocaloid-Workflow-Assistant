import QtQuick
import QtQuick.Controls

ProgressBar {
    id: control
    required property QtObject theme
    property color fillColor: theme.accent

    implicitWidth: Math.round(140 * theme.scale)
    implicitHeight: Math.round(5 * theme.scale)

    background: Rectangle {
        radius: height / 2
        color: control.theme.dark ? "#242838" : "#cbd0de"
    }

    contentItem: Item {
        clip: true
        Rectangle {
            visible: !control.indeterminate
            width: control.visualPosition * parent.width
            height: parent.height
            radius: height / 2
            color: control.fillColor
            Behavior on width {
                enabled: !control.theme.reduceMotion
                NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
            }
        }
        Rectangle {
            id: shimmer
            visible: control.indeterminate
            width: Math.max(Math.round(28 * control.theme.scale), parent.width * 0.32)
            height: parent.height
            radius: height / 2
            color: control.fillColor
            x: -width
            SequentialAnimation on x {
                running: control.indeterminate && control.visible && !control.theme.reduceMotion
                loops: Animation.Infinite
                NumberAnimation {
                    from: -shimmer.width
                    to: control.width
                    duration: 1150
                    easing.type: Easing.InOutSine
                }
            }
        }
    }
}
