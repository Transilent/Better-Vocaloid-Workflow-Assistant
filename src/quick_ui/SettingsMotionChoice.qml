import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: motion
    required property QtObject tokens
    property bool selected: false
    property string detail: ""

    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    leftPadding: Math.round(11 * tokens.scale)
    rightPadding: Math.round(11 * tokens.scale)
    topPadding: Math.round(9 * tokens.scale)
    bottomPadding: Math.round(9 * tokens.scale)
    implicitHeight: Math.max(Math.round(46 * tokens.scale), contentRow.implicitHeight + Math.round(18 * tokens.scale))
    Accessible.name: text
    Accessible.role: Accessible.CheckBox
    Accessible.checked: selected

    background: Rectangle {
        radius: Math.round(7 * tokens.scale)
        color: motion.hovered ? Qt.lighter(tokens.base, 1.08) : tokens.base
        border.color: motion.visualFocus ? tokens.accent : tokens.line
    }
    contentItem: RowLayout {
        id: contentRow
        spacing: Math.round(10 * tokens.scale)
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                Layout.fillWidth: true
                text: motion.text
                color: tokens.text
                font.pixelSize: Math.round(13 * tokens.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: motion.detail
                color: tokens.muted
                font.pixelSize: Math.round(11 * tokens.scale)
                wrapMode: Text.Wrap
            }
        }
        Rectangle {
            width: Math.round(34 * tokens.scale)
            height: Math.round(19 * tokens.scale)
            radius: height / 2
            color: motion.selected ? tokens.accent : tokens.line
            Rectangle {
                width: Math.round(15 * tokens.scale)
                height: width
                radius: width / 2
                y: Math.round(2 * tokens.scale)
                x: motion.selected ? parent.width - width - Math.round(2 * tokens.scale) : Math.round(2 * tokens.scale)
                color: motion.selected ? tokens.base : tokens.muted
                Behavior on x {
                    enabled: !tokens.reduceMotion
                    NumberAnimation { duration: 110; easing.type: Easing.OutCubic }
                }
            }
        }
    }
}

