import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "diagnosticsDialog"
    required property QtObject theme
    required property QtObject controller
    readonly property var diagnostic: controller.diagnostics
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: Math.round(16 * theme.scale)
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - 36 : 760, Math.round(760 * theme.scale))
    height: Math.min(parent ? parent.height - 36 : 580, Math.round(580 * theme.scale))

    function tr(message) { return controller.text(message) }
    function sync() {
        if (diagnostic.open && !opened)
            open()
        else if (!diagnostic.open && opened)
            close()
    }

    Component.onCompleted: sync()
    onDiagnosticChanged: sync()
    onClosed: if (diagnostic.open) controller.closeDiagnostics()

    Overlay.modal: Rectangle { color: Qt.rgba(dialog.theme.base.r, dialog.theme.base.g, dialog.theme.base.b, 0.82) }
    background: Rectangle {
        color: dialog.theme.surface
        radius: Math.round(10 * dialog.theme.scale)
        border.color: dialog.theme.line
    }
    contentItem: ColumnLayout {
        spacing: Math.round(8 * dialog.theme.scale)
        Text {
            Layout.fillWidth: true
            text: dialog.tr("本机诊断摘要")
            color: dialog.theme.text
            font.pixelSize: Math.round(18 * dialog.theme.scale)
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: dialog.tr("包含当前操作和近期技术事件；不会上传，也不包含项目路径、歌词或账号。")
            color: dialog.theme.muted
            font.pixelSize: Math.round(12 * dialog.theme.scale)
            wrapMode: Text.Wrap
        }
        Text {
            Layout.fillWidth: true
            visible: dialog.diagnostic.busy
            text: dialog.tr("正在读取本机诊断…")
            color: dialog.theme.muted
            font.pixelSize: Math.round(12 * dialog.theme.scale)
        }
        ScrollView {
            id: summary
            objectName: "diagnosticsScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            background: Rectangle {
                color: dialog.theme.base
                radius: Math.round(7 * dialog.theme.scale)
                border.color: dialog.theme.line
            }
            TextArea {
                objectName: "diagnosticsText"
                width: summary.availableWidth
                text: dialog.diagnostic.text || ""
                readOnly: true
                selectByMouse: true
                selectByKeyboard: true
                wrapMode: TextEdit.WrapAnywhere
                textFormat: TextEdit.PlainText
                color: dialog.theme.text
                selectionColor: dialog.theme.accent
                selectedTextColor: dialog.theme.base
                font.family: "Consolas"
                font.pixelSize: Math.round(11 * dialog.theme.scale)
                padding: Math.round(10 * dialog.theme.scale)
                background: null
                Accessible.name: dialog.tr("本机诊断摘要正文")
            }
        }
        Text {
            objectName: "diagnosticsNotice"
            Layout.fillWidth: true
            text: dialog.diagnostic.message || ""
            visible: text.length > 0
            color: dialog.theme.muted
            font.pixelSize: Math.round(11 * dialog.theme.scale)
            wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true
            spacing: Math.round(7 * dialog.theme.scale)
            UiButton {
                objectName: "copyDiagnostics"
                theme: dialog.theme
                text: dialog.tr("复制摘要")
                enabled: !dialog.diagnostic.busy && !!dialog.diagnostic.text
                onClicked: dialog.controller.copyDiagnostics()
            }
            UiButton {
                objectName: "refreshDiagnostics"
                theme: dialog.theme
                quiet: true
                text: dialog.tr("刷新摘要")
                enabled: !dialog.diagnostic.busy
                onClicked: dialog.controller.refreshDiagnostics()
            }
            UiButton {
                objectName: "openDiagnosticLogs"
                theme: dialog.theme
                quiet: true
                text: dialog.tr("打开日志目录")
                onClicked: dialog.controller.openDiagnosticLogs()
            }
            UiButton {
                objectName: "diagnosticsFeedback"
                theme: dialog.theme
                quiet: true
                text: dialog.tr("GitHub 反馈")
                onClicked: dialog.controller.openFeedback()
            }
            UiButton {
                id: closeButton
                objectName: "closeDiagnostics"
                theme: dialog.theme
                quiet: true
                text: dialog.tr("关闭")
                onClicked: dialog.controller.closeDiagnostics()
            }
        }
    }
    onOpened: closeButton.forceActiveFocus()
}
