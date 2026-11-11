import QtQuick
import QtQuick.Controls

Button {
    id: control
    required property QtObject theme
    property bool selected: false
    property bool primary: false
    property bool quiet: false
    property bool leftAligned: false

    font.pixelSize: Math.round(13 * theme.scale)
    font.weight: selected || primary ? Font.DemiBold : Font.Normal
    leftPadding: 11
    rightPadding: 11
    topPadding: 7
    bottomPadding: 7
    implicitHeight: Math.max(36, implicitContentHeight + 14)
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    Accessible.name: text

    background: Rectangle {
        radius: 7
        color: control.primary ? (control.down ? "#a48ad9" : control.theme.accent)
            : control.selected ? "#312940" : control.down ? "#30313d"
            : control.hovered ? "#262833" : control.quiet ? "transparent" : "#20222b"
        border.width: control.visualFocus || !control.quiet ? 1 : 0
        border.color: control.visualFocus ? control.theme.accent : control.selected ? "#564369" : control.theme.line
        Behavior on color {
            enabled: !control.theme.reduceMotion && !control.down
            ColorAnimation { duration: 100 }
        }
    }
    contentItem: Text {
        text: control.text
        font: control.font
        color: control.primary ? "#211930" : control.theme.text
        opacity: control.enabled ? 1 : 0.45
        horizontalAlignment: control.leftAligned ? Text.AlignLeft : Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        textFormat: Text.PlainText
    }
}
