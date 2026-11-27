import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: banner
    objectName: "recoveryBanner"
    required property QtObject theme
    required property QtObject controller
    readonly property QtObject recovery: controller ? controller.recovery : null
    readonly property bool mixdownPriority: controller && controller.route === "audio_mixdown" && !!recovery && recovery.hasMixdownDraft
    readonly property bool chorusTarget: !mixdownPriority && !!recovery && recovery.hasChorusDraft
    readonly property bool mixdownTarget: !chorusTarget && !!recovery && recovery.hasMixdownDraft
    readonly property bool active: chorusTarget || mixdownTarget
    readonly property string workspaceTag: chorusTarget ? controller.text("修音工作台") : controller.text("简易混音")
    readonly property string draftLabel: chorusTarget
        ? (recovery ? recovery.chorusDraftLabel : "")
        : (recovery ? recovery.mixdownDraftLabel : "")

    visible: active
    Layout.fillWidth: true
    implicitHeight: active ? row.implicitHeight + Math.round(14 * theme.scale) : 0
    color: theme.raised
    border.width: 0

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: theme.line
    }

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: Math.round(14 * banner.theme.scale)
        anchors.rightMargin: Math.round(14 * banner.theme.scale)
        spacing: Math.round(10 * banner.theme.scale)

        Rectangle {
            width: Math.round(8 * banner.theme.scale)
            height: width
            radius: width / 2
            color: banner.theme.attention
        }

        Label {
            Layout.fillWidth: true
            text: banner.controller.text("发现上次异常退出前的未保存草稿")
                + "（" + banner.workspaceTag + "）"
                + (banner.draftLabel ? "：" + banner.draftLabel : "")
            color: banner.theme.text
            font.pixelSize: banner.theme.fontSm
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }

        UiButton {
            objectName: "recoveryRestoreButton"
            theme: banner.theme
            primary: true
            implicitHeight: Math.round(28 * banner.theme.scale)
            text: banner.controller.text("恢复草稿")
            onClicked: {
                if (banner.chorusTarget) {
                    banner.controller.navigate("chorus")
                    banner.recovery.restoreChorusDraft()
                } else if (banner.mixdownTarget) {
                    banner.controller.navigate("audio_mixdown")
                    banner.recovery.restoreMixdownDraft()
                }
            }
        }

        UiButton {
            objectName: "recoveryDiscardButton"
            theme: banner.theme
            quiet: true
            implicitHeight: Math.round(28 * banner.theme.scale)
            text: banner.controller.text("丢弃")
            onClicked: {
                if (banner.chorusTarget)
                    banner.recovery.discardChorusDraft()
                else if (banner.mixdownTarget)
                    banner.recovery.discardMixdownDraft()
            }
        }
    }
}
