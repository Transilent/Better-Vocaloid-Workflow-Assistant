import QtQuick
import QtQuick.Controls

Button {
    id: control
    required property QtObject theme
    property bool selected: false
    property bool primary: false
    property bool quiet: false
    property bool borderlessSelected: false
    property bool leftAligned: false
    property int disclosure: -1   // -1: ordinary action; 0/1: collapsed/expanded.
    // Only familiar operations use a standalone pictogram. text still names
    // the command for focus, tools and assistive technology.
    property string glyph: ""
    // Optional leading vector icon shown alongside text (e.g. sidebar navigation & tabs).
    property string leadingGlyph: ""
    property bool stackedIcon: false
    property int leadingIconSize: 0

    font.pixelSize: Math.round(13 * theme.scale)
    font.weight: selected || primary ? Font.DemiBold : Font.Normal
    leftPadding: Math.round(11 * theme.scale)
    rightPadding: leftPadding
    topPadding: Math.round(7 * theme.scale)
    bottomPadding: topPadding
    implicitHeight: Math.max(Math.round(34 * theme.scale), implicitContentHeight + topPadding + bottomPadding)
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    Accessible.name: text
    ToolTip.visible: glyph.length > 0 && (hovered || visualFocus)
    ToolTip.delay: 500
    ToolTip.text: text
    scale: down && !theme.reduceMotion ? 0.985 : 1.0
    Behavior on scale {
        enabled: !control.theme.reduceMotion
        NumberAnimation { duration: 60; easing.type: Easing.OutCubic }
    }

    background: Rectangle {
        radius: control.theme.radiusMd !== undefined ? control.theme.radiusMd : Math.round(8 * control.theme.scale)
        color: !control.enabled && control.primary ? (control.theme.disabledBg || control.theme.raised)
            : control.primary ? (control.down ? control.theme.accentPressed : control.hovered ? (control.theme.accentHover || control.theme.accent) : control.theme.accent)
            : control.selected ? control.theme.selected
            : control.down ? control.theme.pressed
            : control.hovered ? control.theme.hover
            : control.quiet ? "transparent" : control.theme.raised
        border.width: control.visualFocus ? 2
            : control.primary ? 0
            : (control.selected && !control.borderlessSelected) || (!control.quiet && !control.selected) ? 1 : 0
        border.color: control.visualFocus ? control.theme.accent
            : control.selected ? control.theme.selectedLine : control.theme.line
        opacity: control.enabled ? 1 : (control.primary ? 0.72 : 0.55)
        Behavior on color {
            enabled: !control.theme.reduceMotion && !control.down
            ColorAnimation { duration: 100 }
        }
        Rectangle {
            visible: control.enabled && !control.quiet && !control.down && (control.primary || !control.selected)
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: control.theme.highlightTop || "transparent"
        }
    }
    contentItem: Item {
        readonly property int iconSize: Math.round(18 * control.theme.scale)
        readonly property int leadingSize: Math.round((control.leadingIconSize > 0 ? control.leadingIconSize : (control.stackedIcon ? 16 : 15)) * control.theme.scale)
        readonly property int leadingGap: Math.round((control.leadingIconSize >= 17 ? 9 : 6) * control.theme.scale)
        readonly property bool hasLeading: control.glyph.length === 0 && control.leadingGlyph.length > 0
        readonly property int disclosureInset: control.disclosure >= 0 ? Math.round(21 * control.theme.scale) : 0
        readonly property int textLeftInset: disclosureInset + (hasLeading && !control.stackedIcon ? leadingSize + leadingGap : 0)
        implicitWidth: control.glyph.length > 0 ? iconSize
            : (hasLeading && control.stackedIcon ? Math.max(leadingSize, label.implicitWidth)
                : label.implicitWidth + textLeftInset)
        implicitHeight: control.glyph.length > 0 ? Math.round(18 * control.theme.scale)
            : (hasLeading && control.stackedIcon ? leadingSize + Math.round(2 * control.theme.scale) + label.implicitHeight
                : Math.max(label.implicitHeight, hasLeading ? leadingSize : 0))
        readonly property color ink: (control.primary && control.enabled) ? control.theme.onAccent : control.theme.text
        readonly property color leadingInk: (control.primary && control.enabled) ? control.theme.onAccent
            : control.selected ? control.theme.accent
            : control.hovered ? control.theme.text : control.theme.muted
        opacity: control.enabled ? 1 : 0.45
        Loader {
            objectName: control.leadingGlyph.length > 0 ? "leadingIcon_" + control.leadingGlyph : ""
            active: parent.hasLeading
            x: control.stackedIcon ? Math.round((parent.width - width) / 2) : parent.disclosureInset
            y: control.stackedIcon ? Math.round((parent.height - (parent.leadingSize + Math.round(2 * control.theme.scale) + label.implicitHeight)) / 2)
                : Math.round((parent.height - height) / 2)
            width: parent.leadingSize
            height: parent.leadingSize
            sourceComponent: UiIcon { glyph: control.leadingGlyph; tint: control.contentItem.leadingInk }
        }
        Text {
            id: label
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: control.stackedIcon && parent.hasLeading ? undefined : parent.top
            anchors.bottom: parent.bottom
            anchors.leftMargin: parent.textLeftInset
            visible: control.glyph.length === 0
            text: control.text
            font.family: control.font.family
            font.pixelSize: control.stackedIcon && parent.hasLeading ? Math.round(10 * control.theme.scale) : control.font.pixelSize
            font.weight: control.font.weight
            color: parent.ink
            horizontalAlignment: control.stackedIcon && parent.hasLeading ? Text.AlignHCenter
                : (control.leftAligned ? Text.AlignLeft : Text.AlignHCenter)
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
        Loader {
            objectName: control.disclosure >= 0 ? "disclosureIndicator" : ""
            active: control.disclosure >= 0
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: Math.round(14 * control.theme.scale)
            height: width
            rotation: control.disclosure === 1 ? 90 : 0
            Behavior on rotation {
                enabled: !control.theme.reduceMotion
                NumberAnimation { duration: 100; easing.type: Easing.OutCubic }
            }
            sourceComponent: UiIcon { glyph: "chevron"; tint: control.theme.muted }
        }
    }
}
