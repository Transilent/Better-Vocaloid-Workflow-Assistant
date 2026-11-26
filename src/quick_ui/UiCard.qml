import QtQuick

Rectangle {
    id: card
    required property QtObject theme
    property bool elevated: false
    property bool highlighted: false

    radius: theme.radiusLg !== undefined ? theme.radiusLg : Math.round(11 * theme.scale)
    color: elevated ? theme.raised : theme.surface
    border.width: 1
    border.color: highlighted ? theme.selectedLine : (theme.lineSubtle !== undefined ? theme.lineSubtle : theme.line)

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: parent.radius
        anchors.rightMargin: parent.radius
        height: 1
        color: card.theme.highlightTop !== undefined ? card.theme.highlightTop : (card.theme.dark ? "#14ffffff" : "#99ffffff")
    }
}
