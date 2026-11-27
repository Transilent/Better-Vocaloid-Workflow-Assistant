import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    required property QtObject theme

    parent: Overlay.overlay
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0
    modal: true
    focus: true
    padding: Math.round(18 * theme.scale)
    topPadding: title.length > 0 ? Math.round(12 * theme.scale) : padding

    Overlay.modal: Rectangle {
        color: dialog.theme.dark ? "#8c080a10" : "#5518162a"
    }

    background: Rectangle {
        radius: dialog.theme.radiusXl !== undefined ? dialog.theme.radiusXl : Math.round(14 * dialog.theme.scale)
        color: dialog.theme.surface
        border.width: 1
        border.color: dialog.theme.line
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: dialog.theme.highlightTop !== undefined ? dialog.theme.highlightTop : (dialog.theme.dark ? "#14ffffff" : "#99ffffff")
        }
    }

    header: Item {
        visible: dialog.title.length > 0
        implicitHeight: visible ? titleRow.implicitHeight + Math.round(20 * dialog.theme.scale) : 0
        RowLayout {
            id: titleRow
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: Math.round(18 * dialog.theme.scale)
            anchors.rightMargin: Math.round(18 * dialog.theme.scale)
            anchors.topMargin: Math.round(16 * dialog.theme.scale)
            spacing: Math.round(8 * dialog.theme.scale)
            Text {
                Layout.fillWidth: true
                text: dialog.title
                color: dialog.theme.text
                font.pixelSize: Math.round(16 * dialog.theme.scale)
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                textFormat: Text.PlainText
            }
        }
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: dialog.theme.lineSubtle !== undefined ? dialog.theme.lineSubtle : dialog.theme.line
        }
    }
}
