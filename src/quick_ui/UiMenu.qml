import QtQuick
import QtQuick.Controls

Menu {
    id: menu
    required property QtObject theme

    topPadding: Math.round(5 * theme.scale)
    bottomPadding: Math.round(5 * theme.scale)
    leftPadding: Math.round(5 * theme.scale)
    rightPadding: Math.round(5 * theme.scale)

    background: Rectangle {
        implicitWidth: Math.round(212 * menu.theme.scale)
        radius: menu.theme.radiusMd !== undefined ? menu.theme.radiusMd : Math.round(8 * menu.theme.scale)
        color: menu.theme.surface
        border.width: 1
        border.color: menu.theme.line
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: menu.theme.highlightTop !== undefined ? menu.theme.highlightTop : (menu.theme.dark ? "#14ffffff" : "#99ffffff")
        }
    }

    delegate: MenuItem {
        id: item
        implicitWidth: Math.round(200 * menu.theme.scale)
        implicitHeight: Math.round(32 * menu.theme.scale)
        leftPadding: Math.round(10 * menu.theme.scale)
        rightPadding: Math.round(10 * menu.theme.scale)
        contentItem: Text {
            text: item.text
            font.pixelSize: Math.round(13 * menu.theme.scale)
            color: item.enabled ? menu.theme.text : menu.theme.muted
            opacity: item.enabled ? 1.0 : 0.55
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        background: Rectangle {
            radius: menu.theme.radiusSm !== undefined ? menu.theme.radiusSm : Math.round(6 * menu.theme.scale)
            color: item.highlighted && item.enabled ? menu.theme.selected : "transparent"
        }
    }
}
