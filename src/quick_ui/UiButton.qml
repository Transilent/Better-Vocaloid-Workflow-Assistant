import QtQuick
import QtQuick.Controls

Button {
    id: control
    required property QtObject theme
    property bool selected: false
    property bool primary: false
    property bool quiet: false
    property bool leftAligned: false
    // Only familiar operations use a standalone pictogram. text still names
    // the command for focus, tools and assistive technology.
    property string glyph: ""

    font.pixelSize: Math.round(13 * theme.scale)
    font.weight: selected || primary ? Font.DemiBold : Font.Normal
    leftPadding: Math.round(11 * theme.scale)
    rightPadding: leftPadding
    topPadding: Math.round(7 * theme.scale)
    bottomPadding: topPadding
    implicitHeight: Math.max(Math.round(36 * theme.scale), implicitContentHeight + topPadding + bottomPadding)
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    Accessible.name: text
    ToolTip.visible: glyph.length > 0 && (hovered || visualFocus)
    ToolTip.delay: 500
    ToolTip.text: text

    background: Rectangle {
        radius: Math.round(7 * control.theme.scale)
        color: control.primary ? (control.down ? control.theme.accentPressed : control.theme.accent)
            : control.selected ? control.theme.selected : control.down ? control.theme.pressed
            : control.hovered ? control.theme.hover : control.quiet ? "transparent" : control.theme.raised
        border.width: control.visualFocus || control.selected || !control.quiet ? 1 : 0
        border.color: control.visualFocus ? control.theme.accent : control.selected ? control.theme.selectedLine : control.theme.line
        opacity: control.enabled ? 1 : 0.55
        Behavior on color {
            enabled: !control.theme.reduceMotion && !control.down
            ColorAnimation { duration: 100 }
        }
    }
    contentItem: Item {
        implicitWidth: control.glyph.length > 0 ? Math.round(18 * control.theme.scale) : label.implicitWidth
        implicitHeight: control.glyph.length > 0 ? Math.round(18 * control.theme.scale) : label.implicitHeight
        readonly property color ink: control.primary ? control.theme.onAccent : control.theme.text
        opacity: control.enabled ? 1 : 0.45
        Text {
            id: label
            anchors.fill: parent
            visible: control.glyph.length === 0
            text: control.text
            font: control.font
            color: parent.ink
            horizontalAlignment: control.leftAligned ? Text.AlignLeft : Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        Loader {
            anchors.centerIn: parent
            width: parent.implicitWidth
            height: parent.implicitHeight
            active: control.glyph.length > 0
            sourceComponent: UiIcon { glyph: control.glyph; tint: control.contentItem.ink }
        }
    }
}
