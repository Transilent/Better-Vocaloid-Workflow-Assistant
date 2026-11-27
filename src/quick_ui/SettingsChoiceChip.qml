import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: chip
    required property QtObject tokens
    property bool selected: false

    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    checkable: false
    font.pixelSize: Math.round(12 * tokens.scale)
    font.weight: selected ? Font.DemiBold : Font.Normal
    leftPadding: Math.round(10 * tokens.scale)
    rightPadding: Math.round(10 * tokens.scale)
    topPadding: Math.round(6 * tokens.scale)
    bottomPadding: Math.round(6 * tokens.scale)
    implicitHeight: Math.max(Math.round(32 * tokens.scale), implicitContentHeight + Math.round(12 * tokens.scale))
    Accessible.name: text
    Accessible.role: Accessible.RadioButton
    Accessible.checked: selected

    background: Rectangle {
        radius: Math.round(16 * tokens.scale)
        color: chip.selected
            ? tokens.accent
            : chip.hovered ? tokens.hover : tokens.raised
        border.width: 1
        border.color: chip.visualFocus || chip.selected ? tokens.accent : tokens.line
        Behavior on color {
            enabled: !tokens.reduceMotion && !chip.down
            ColorAnimation { duration: 90 }
        }
    }
    contentItem: Text {
        text: chip.text
        font: chip.font
        color: chip.selected ? tokens.accentInk : tokens.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        textFormat: Text.PlainText
    }
}

