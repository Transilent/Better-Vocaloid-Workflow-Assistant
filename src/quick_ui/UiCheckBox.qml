import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    required property QtObject theme

    implicitHeight: Math.max(Math.round(30 * theme.scale), implicitContentHeight + Math.round(6 * theme.scale))
    spacing: Math.round(8 * theme.scale)
    font.pixelSize: Math.round(13 * theme.scale)
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text

    indicator: Rectangle {
        implicitWidth: Math.round(16 * control.theme.scale)
        implicitHeight: implicitWidth
        x: control.leftPadding
        y: (control.height - height) / 2
        radius: Math.round(5 * control.theme.scale)
        color: control.checked
            ? (control.down ? control.theme.accentPressed : control.theme.accent)
            : (control.hovered ? control.theme.hover : control.theme.raised)
        border.width: control.visualFocus ? 2 : 1
        border.color: control.visualFocus
            ? control.theme.accent
            : (control.checked ? control.theme.accent : control.theme.line)
        opacity: control.enabled ? 1.0 : 0.48
        Behavior on color {
            enabled: !control.theme.reduceMotion
            ColorAnimation { duration: 100 }
        }
        UiIcon {
            anchors.centerIn: parent
            width: Math.round(12 * control.theme.scale)
            height: width
            visible: control.checked
            glyph: "check"
            tint: control.theme.accentInk !== undefined ? control.theme.accentInk : (control.theme.dark ? "#141022" : "#ffffff")
        }
    }

    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        font: control.font
        color: control.enabled ? control.theme.text : control.theme.muted
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
}
