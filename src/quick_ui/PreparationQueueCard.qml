import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "prepQueueCard"
    required property QtObject theme
    required property QtObject controller
    readonly property QtObject ux: controller && controller.ux ? controller.ux : null
    readonly property var items: ux && ux.prepQueue ? ux.prepQueue : []
    readonly property bool busy: !!(controller && controller.preparationBusy)
    readonly property real s: theme.scale || 1.0

    function vwaTr(text) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(text) : text
    }

    visible: items.length > 0
    Layout.fillWidth: true
    implicitHeight: visible ? col.implicitHeight + Math.round(16 * s) : 0
    radius: theme.radiusMd !== undefined ? theme.radiusMd : Math.round(8 * s)
    color: theme.raised
    border.width: 1
    border.color: theme.line

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Math.round(8 * card.s)
        spacing: Math.round(6 * card.s)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(6 * card.s)
            Text {
                Layout.fillWidth: true
                text: card.vwaTr("等待队列") + "（" + card.items.length + "）"
                color: card.theme.text
                font.pixelSize: Math.round(12 * card.s)
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                textFormat: Text.PlainText
            }
            UiButton {
                objectName: "prepQueueStart"
                theme: card.theme
                primary: !card.busy
                quiet: card.busy
                enabled: !card.busy && card.items.length > 0
                implicitHeight: Math.round(26 * card.s)
                text: card.vwaTr("开始队列")
                onClicked: if (card.ux) card.ux.startPrepQueue()
            }
            UiButton {
                objectName: "prepQueueClear"
                theme: card.theme
                quiet: true
                enabled: card.items.length > 0
                implicitHeight: Math.round(26 * card.s)
                text: card.vwaTr("清空")
                onClicked: if (card.ux) card.ux.clearPrepQueue()
            }
        }

        Repeater {
            model: card.items
            delegate: Rectangle {
                required property var modelData
                required property int index
                Layout.fillWidth: true
                implicitHeight: row.implicitHeight + Math.round(8 * card.s)
                radius: Math.round(6 * card.s)
                color: card.theme.surface
                border.width: 1
                border.color: card.theme.lineSubtle

                RowLayout {
                    id: row
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: Math.round(6 * card.s)
                    spacing: Math.round(4 * card.s)

                    Text {
                        Layout.fillWidth: true
                        text: (index + 1) + ". " + String(modelData.title || modelData.fileName || "")
                        color: card.theme.text
                        font.pixelSize: Math.round(11 * card.s)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                    }
                    UiButton {
                        objectName: "prepQueueMoveUp_" + index
                        theme: card.theme
                        quiet: true
                        enabled: index > 0
                        implicitHeight: Math.round(24 * card.s)
                        text: "↑"
                        onClicked: if (card.ux) card.ux.moveQueuedPreparation(String(modelData.id || ""), -1)
                    }
                    UiButton {
                        objectName: "prepQueueMoveDown_" + index
                        theme: card.theme
                        quiet: true
                        enabled: index < card.items.length - 1
                        implicitHeight: Math.round(24 * card.s)
                        text: "↓"
                        onClicked: if (card.ux) card.ux.moveQueuedPreparation(String(modelData.id || ""), 1)
                    }
                    UiButton {
                        objectName: "prepQueueRemove_" + index
                        theme: card.theme
                        quiet: true
                        implicitHeight: Math.round(24 * card.s)
                        text: card.vwaTr("移除")
                        onClicked: if (card.ux) card.ux.removeQueuedPreparation(String(modelData.id || ""))
                    }
                }
            }
        }
    }
}
