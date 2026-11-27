import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: overlay
    objectName: "globalDropOverlay"
    required property QtObject theme
    required property QtObject controller
    property var pendingDrop: ({category: "empty", paths: [], primaryName: "", targets: []})

    anchors.fill: parent

    function t(msg) {
        return controller && typeof controller.text === "function" ? controller.text(msg) : msg
    }

    DropArea {
        id: globalDrop
        objectName: "globalWindowDropArea"
        anchors.fill: parent
        keys: ["text/uri-list"]
        onEntered: function(drag) {
            if (!drag.hasUrls || drag.urls.length === 0) {
                drag.accepted = false
                return
            }
            const info = overlay.controller.ux.inspectDropUrls(drag.urls)
            drag.accepted = !!(info && info.targets && info.targets.length > 0)
        }
        onDropped: function(drop) {
            if (!drop.hasUrls || drop.urls.length === 0)
                return
            const info = overlay.controller.ux.inspectDropUrls(drop.urls)
            if (!info || !info.targets || info.targets.length === 0)
                return
            if (info.targets.length === 1) {
                overlay.controller.ux.routeDroppedFiles(info.targets[0].id, info.paths)
                return
            }
            overlay.pendingDrop = info
            dropChooser.open()
        }
    }

    Rectangle {
        objectName: "globalDropBackdrop"
        parent: Overlay.overlay
        anchors.fill: parent
        visible: globalDrop.containsDrag
        color: overlay.theme.dark ? "#990e1016" : "#99e4e7ef"
        z: 88
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width - Math.round(48 * overlay.theme.scale), Math.round(420 * overlay.theme.scale))
            height: hintCol.implicitHeight + Math.round(36 * overlay.theme.scale)
            radius: overlay.theme.radiusXl
            color: overlay.theme.surface
            border.width: 2
            border.color: overlay.theme.accent
            ColumnLayout {
                id: hintCol
                anchors.centerIn: parent
                width: parent.width - Math.round(32 * overlay.theme.scale)
                spacing: Math.round(6 * overlay.theme.scale)
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: overlay.t("松手即可智能导入文件")
                    color: overlay.theme.text
                    font.pixelSize: overlay.theme.fontTitle
                    font.weight: Font.DemiBold
                }
                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: overlay.t("支持 .vwachorus / .svp / .ustx / .vpr / .vsqx / .acep / .wav / .mp4")
                    color: overlay.theme.muted
                    font.pixelSize: overlay.theme.fontSm
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
    }

    Popup {
        id: dropChooser
        objectName: "globalDropChooser"
        parent: Overlay.overlay
        x: parent ? Math.round((parent.width - width) / 2) : 0
        y: parent ? Math.round((parent.height - height) / 2) : 0
        width: parent ? Math.min(parent.width - Math.round(32 * overlay.theme.scale), Math.round(460 * overlay.theme.scale)) : 460
        modal: true
        focus: true
        padding: Math.round(18 * overlay.theme.scale)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        Overlay.modal: Rectangle {
            color: overlay.theme.dark ? "#8007090f" : "#4418162a"
        }

        background: Rectangle {
            radius: overlay.theme.radiusXl
            color: overlay.theme.surface
            border.width: 1
            border.color: overlay.theme.line
        }

        contentItem: ColumnLayout {
            spacing: Math.round(10 * overlay.theme.scale)

            Label {
                Layout.fillWidth: true
                text: overlay.t("选择要打开的工作区")
                color: overlay.theme.text
                font.pixelSize: overlay.theme.fontTitle
                font.weight: Font.DemiBold
            }

            Label {
                Layout.fillWidth: true
                text: overlay.pendingDrop.primaryName || ""
                color: overlay.theme.muted
                font.pixelSize: overlay.theme.fontSm
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
            }

            Repeater {
                model: overlay.pendingDrop.targets || []
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: optCol.implicitHeight + Math.round(16 * overlay.theme.scale)
                    radius: overlay.theme.radiusMd
                    color: optMouse.containsMouse ? overlay.theme.hover : overlay.theme.raised
                    border.width: 1
                    border.color: optMouse.containsMouse ? overlay.theme.accent : overlay.theme.line

                    ColumnLayout {
                        id: optCol
                        anchors.fill: parent
                        anchors.margins: Math.round(10 * overlay.theme.scale)
                        spacing: Math.round(2 * overlay.theme.scale)
                        Label {
                            Layout.fillWidth: true
                            text: overlay.t(modelData.label)
                            color: overlay.theme.text
                            font.pixelSize: overlay.theme.fontBody
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }
                        Label {
                            Layout.fillWidth: true
                            text: overlay.t(modelData.subtitle)
                            color: overlay.theme.muted
                            font.pixelSize: overlay.theme.fontXs
                            wrapMode: Text.WordWrap
                            textFormat: Text.PlainText
                        }
                    }

                    MouseArea {
                        id: optMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            const targetId = modelData.id
                            const paths = overlay.pendingDrop.paths
                            dropChooser.close()
                            overlay.controller.ux.routeDroppedFiles(targetId, paths)
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: overlay.theme
                    quiet: true
                    text: overlay.t("取消")
                    onClicked: dropChooser.close()
                }
            }
        }
    }
}
