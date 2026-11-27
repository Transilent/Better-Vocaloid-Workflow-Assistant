import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: sheet
    objectName: "shortcutSheetDialog"
    required property QtObject theme
    required property QtObject controller

    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: 0
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - Math.round(36 * theme.scale) : Math.round(680 * theme.scale),
                    Math.round(680 * theme.scale))
    height: Math.min(parent ? parent.height - Math.round(48 * theme.scale) : Math.round(520 * theme.scale),
                     Math.round(520 * theme.scale))

    readonly property var groups: controller && controller.shortcutGroups ? controller.shortcutGroups : []

    function tr(msg) {
        return controller && typeof controller.text === "function" ? controller.text(msg) : msg
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(sheet.theme.base.r, sheet.theme.base.g, sheet.theme.base.b, 0.72)
    }

    background: Rectangle {
        color: sheet.theme.surface
        radius: Math.round(12 * sheet.theme.scale)
        border.color: sheet.theme.line
    }

    contentItem: ColumnLayout {
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(56 * sheet.theme.scale)
            color: sheet.theme.raised
            radius: Math.round(12 * sheet.theme.scale)
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: parent.radius; color: parent.color
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Math.round(18 * sheet.theme.scale)
                anchors.rightMargin: Math.round(14 * sheet.theme.scale)
                Text {
                    Layout.fillWidth: true
                    text: sheet.tr("快捷键速查")
                    color: sheet.theme.text
                    font.pixelSize: Math.round(17 * sheet.theme.scale)
                    font.weight: Font.DemiBold
                }
                UiButton {
                    theme: sheet.theme
                    quiet: true
                    text: sheet.tr("关闭")
                    onClicked: sheet.close()
                }
            }
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: Math.round(16 * sheet.theme.scale)
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: parent.width
                spacing: Math.round(14 * sheet.theme.scale)

                Repeater {
                    model: sheet.groups
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(6 * sheet.theme.scale)

                        Text {
                            text: sheet.tr(String(modelData.group || ""))
                            color: sheet.theme.accent
                            font.pixelSize: Math.round(12 * sheet.theme.scale)
                            font.weight: Font.DemiBold
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: itemsCol.implicitHeight + Math.round(12 * sheet.theme.scale)
                            color: sheet.theme.base
                            radius: Math.round(8 * sheet.theme.scale)
                            border.color: sheet.theme.line

                            ColumnLayout {
                                id: itemsCol
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: Math.round(10 * sheet.theme.scale)
                                spacing: Math.round(6 * sheet.theme.scale)

                                Repeater {
                                    model: modelData.items || []
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Math.round(10 * sheet.theme.scale)
                                        Text {
                                            Layout.fillWidth: true
                                            text: sheet.tr(String(modelData.desc || ""))
                                            color: sheet.theme.text
                                            font.pixelSize: Math.round(13 * sheet.theme.scale)
                                            elide: Text.ElideRight
                                        }
                                        Rectangle {
                                            radius: Math.round(5 * sheet.theme.scale)
                                            color: sheet.theme.raised
                                            border.color: sheet.theme.line
                                            implicitWidth: keyLbl.implicitWidth + Math.round(14 * sheet.theme.scale)
                                            implicitHeight: keyLbl.implicitHeight + Math.round(6 * sheet.theme.scale)
                                            Text {
                                                id: keyLbl
                                                anchors.centerIn: parent
                                                text: sheet.tr(String(modelData.keys || ""))
                                                color: sheet.theme.text
                                                font.pixelSize: Math.round(11 * sheet.theme.scale)
                                                font.family: sheet.theme.monoFont || "Consolas"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
