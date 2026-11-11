import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "preparationPage"
    required property QtObject theme
    required property QtObject controller
    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    FileDialog {
        id: musicDialog
        title: page.controller.text("选择本地音频")
        nameFilters: [page.controller.text("音频文件") + " (*.wav *.flac *.mp3 *.m4a *.ogg *.aac *.opus *.wma)",
                      page.controller.text("所有文件") + " (*)"]
        onAccepted: page.controller.chooseMusic(selectedFile)
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: 14

        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: 24
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            spacing: 6
            Text {
                Layout.fillWidth: true
                text: page.controller.text("准备工作流")
                color: page.theme.text
                font.pixelSize: 25 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("选择本地素材和分离方案。")
                color: page.theme.muted
                font.pixelSize: 13 * page.theme.scale
                wrapMode: Text.Wrap
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.preferredHeight: draft.implicitHeight + 32
            color: page.theme.surface
            radius: 9
            border.color: page.theme.line

            ColumnLayout {
                id: draft
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 16
                spacing: 12
                Text {
                    text: page.controller.text("当前素材")
                    font.pixelSize: 13 * page.theme.scale
                    color: page.theme.muted
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "selectedMusic"
                        Layout.fillWidth: true
                        text: page.controller.preparation.music_name || page.controller.text("未选择音频")
                        color: page.theme.text
                        font.pixelSize: 15 * page.theme.scale
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: fileHover.hovered && truncated
                        ToolTip.text: page.controller.preparation.music_path
                        HoverHandler { id: fileHover }
                    }
                    UiButton {
                        objectName: "chooseMusic"
                        theme: page.theme
                        text: page.controller.text("选择音频")
                        onClicked: musicDialog.open()
                    }
                    UiButton {
                        theme: page.theme
                        visible: page.controller.preparation.music_path.length > 0
                        text: page.controller.text("移除")
                        quiet: true
                        onClicked: page.controller.clearMusic()
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.line }
                Text {
                    text: page.controller.text("分离方案")
                    color: page.theme.muted
                    font.pixelSize: 13 * page.theme.scale
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "selectedSeparator"
                        Layout.fillWidth: true
                        text: page.controller.preparation.separator_model === "roformer"
                            ? "BS-RoFormer · " + page.controller.preparation.roformer_device.toUpperCase()
                            : "Karaoke 2"
                        font.pixelSize: 15 * page.theme.scale
                        color: page.theme.text
                        elide: Text.ElideRight
                    }
                    UiButton {
                        objectName: "openComponents"
                        theme: page.theme
                        primary: true
                        text: page.controller.text("模型与组件")
                        onClicked: page.controller.navigate("components")
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.bottomMargin: 24
            text: page.controller.text("此试用入口可选择素材和组件。完整的音乐处理请从现有工作流启动。")
            color: page.theme.muted
            font.pixelSize: 12 * page.theme.scale
            wrapMode: Text.Wrap
        }
    }
}
