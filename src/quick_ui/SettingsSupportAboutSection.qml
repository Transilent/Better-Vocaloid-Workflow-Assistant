import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    required property var page
    Layout.fillWidth: true
    spacing: Math.round(14 * page.theme.scale)

    Rectangle {
        objectName: "supportSection"
        visible: page.settingsCategory === "support"
        Layout.fillWidth: true
        Layout.leftMargin: page.pageMargin
        Layout.rightMargin: page.pageMargin
        Layout.bottomMargin: page.pageMargin
        Layout.preferredHeight: supportContent.implicitHeight + Math.round(30 * page.theme.scale)
        color: page.theme.surface
        radius: Math.round(9 * page.theme.scale)
        border.color: page.theme.line

        ColumnLayout {
            id: supportContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Math.round(15 * page.theme.scale)
            spacing: Math.round(10 * page.theme.scale)

            Text {
                Layout.fillWidth: true
                text: page.tr("支持与日志")
                color: page.theme.text
                font.pixelSize: Math.round(18 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("反馈前先查看本机诊断摘要；任务失败时可在「任务与结果」打开对应日志。公开反馈前请检查正文、截图和日志。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: Math.round(7 * page.theme.scale)
                UiButton {
                    objectName: "showDiagnostics"
                    theme: page.theme
                    text: page.tr("本机诊断")
                    onClicked: page.controller.showDiagnostics()
                }
                UiButton {
                    objectName: "openLogGuide"
                    theme: page.theme
                    text: page.tr("查看日志说明")
                    onClicked: Qt.openUrlExternally(page.supportLogGuideUrl)
                }
                UiButton {
                    objectName: "supportSidebarOpenFeedback"
                    theme: page.theme
                    primary: true
                    text: page.tr("GitHub 反馈")
                    onClicked: page.controller.openFeedback()
                }
                UiButton {
                    objectName: "supportSidebarReplayTutorial"
                    theme: page.theme
                    quiet: true
                    text: page.tr("重新查看教程")
                    onClicked: page.controller.showTutorial()
                }
            }
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(10 * page.theme.scale)
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: page.tr("交流社区 · QQ 群")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                    }
                    Text {
                        objectName: "supportSidebarQQGroup"
                        text: page.communityNumber
                        color: page.theme.text
                        font.pixelSize: Math.round(20 * page.theme.scale)
                        font.weight: Font.DemiBold
                        font.family: page.theme.monoFont || "Consolas"
                        textFormat: Text.PlainText
                    }
                }
                UiButton {
                    objectName: "supportSidebarCopyQQGroup"
                    theme: page.theme
                    text: page.tr("复制群号")
                    onClicked: page.controller.copyCommunity()
                }
            }
        }
    }
    Rectangle {
        objectName: "aboutSection"
        Layout.fillWidth: true
        Layout.leftMargin: page.pageMargin
        Layout.rightMargin: page.pageMargin
        Layout.preferredHeight: aboutContent.implicitHeight + Math.round(30 * page.theme.scale)
        visible: page.settingsCategory === "about"
        color: page.theme.surface
        radius: Math.round(9 * page.theme.scale)
        border.color: page.theme.line

        ColumnLayout {
            id: aboutContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Math.round(15 * page.theme.scale)
            spacing: Math.round(10 * page.theme.scale)

            Text {
                Layout.fillWidth: true
                text: page.tr("关于 VWA")
                color: page.theme.text
                font.pixelSize: Math.round(18 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: "VWA"
                color: page.theme.text
                font.pixelSize: Math.round(25 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("本地创作工作台")
                color: page.theme.muted
                font.pixelSize: Math.round(13 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(1 * page.theme.scale)
                color: page.theme.line
            }
            Text {
                objectName: "aboutVersion"
                Layout.fillWidth: true
                text: page.tr("当前版本") + "  " + page.formatVersion(page.controller.currentVersion)
                color: page.theme.text
                font.pixelSize: Math.round(13 * page.theme.scale)
                wrapMode: Text.Wrap
            }
        }
    }
}
