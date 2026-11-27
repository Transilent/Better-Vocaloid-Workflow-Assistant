import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dlg
    objectName: "whatsNewDialog"
    required property QtObject theme
    required property QtObject controller
    required property QtObject navigationApi

    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    padding: 0
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - Math.round(32 * theme.scale) : Math.round(580 * theme.scale),
                    Math.round(580 * theme.scale))
    height: Math.min(parent ? parent.height - Math.round(48 * theme.scale) : Math.round(460 * theme.scale),
                     Math.max(Math.round(320 * theme.scale), contentCol.implicitHeight + Math.round(24 * theme.scale)))

    readonly property var items: controller && controller.whatsNewItems ? controller.whatsNewItems : []
    readonly property string featureVersion: controller && controller.whatsNewVersion ? String(controller.whatsNewVersion) : ""

    function tr(msg) {
        return controller && typeof controller.text === "function" ? controller.text(msg) : msg
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(dlg.theme.base.r, dlg.theme.base.g, dlg.theme.base.b, 0.72)
    }

    background: Rectangle {
        color: dlg.theme.surface
        radius: Math.round(12 * dlg.theme.scale)
        border.color: dlg.theme.line
    }

    contentItem: ColumnLayout {
        id: contentCol
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(64 * dlg.theme.scale)
            color: dlg.theme.raised
            radius: Math.round(12 * dlg.theme.scale)
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: parent.radius; color: parent.color
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Math.round(18 * dlg.theme.scale)
                anchors.rightMargin: Math.round(14 * dlg.theme.scale)
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: dlg.tr("版本新功能导览") + (dlg.featureVersion ? (" · v" + dlg.featureVersion) : "")
                        color: dlg.theme.accent
                        font.pixelSize: Math.round(11 * dlg.theme.scale)
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: dlg.tr("看看本次升级带来的新工作台与效率工具")
                        color: dlg.theme.text
                        font.pixelSize: Math.round(16 * dlg.theme.scale)
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Math.round(16 * dlg.theme.scale)
            spacing: Math.round(10 * dlg.theme.scale)

            Repeater {
                model: dlg.items
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: itemRow.implicitHeight + Math.round(20 * dlg.theme.scale)
                    color: dlg.theme.base
                    radius: Math.round(9 * dlg.theme.scale)
                    border.color: dlg.theme.line

                    RowLayout {
                        id: itemRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: Math.round(12 * dlg.theme.scale)
                        spacing: Math.round(10 * dlg.theme.scale)

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text {
                                Layout.fillWidth: true
                                text: dlg.tr(String(modelData.title || ""))
                                color: dlg.theme.text
                                font.pixelSize: Math.round(13 * dlg.theme.scale)
                                font.weight: Font.DemiBold
                                wrapMode: Text.Wrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: dlg.tr(String(modelData.summary || ""))
                                color: dlg.theme.muted
                                font.pixelSize: Math.round(12 * dlg.theme.scale)
                                wrapMode: Text.Wrap
                                lineHeight: 1.3
                            }
                        }
                        UiButton {
                            theme: dlg.theme
                            quiet: true
                            visible: String(modelData.pageId || "").length > 0
                            text: dlg.tr("去体验")
                            onClicked: {
                                const pid = String(modelData.pageId || "")
                                dlg.controller.dismissWhatsNew()
                                if (pid && dlg.navigationApi && dlg.navigationApi.navigate)
                                    dlg.navigationApi.navigate(pid)
                            }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Math.round(4 * dlg.theme.scale)
                UiButton {
                    theme: dlg.theme
                    quiet: true
                    text: dlg.tr("观看完整教程")
                    onClicked: {
                        dlg.controller.dismissWhatsNew()
                        dlg.controller.showTutorial()
                    }
                }
                Item { Layout.fillWidth: true }
                UiButton {
                    objectName: "whatsNewGotIt"
                    theme: dlg.theme
                    primary: true
                    text: dlg.tr("我知道了")
                    onClicked: dlg.controller.dismissWhatsNew()
                }
            }
        }
    }

    onRejected: controller.dismissWhatsNew()
}
