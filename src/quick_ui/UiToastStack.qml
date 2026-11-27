import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: stack
    objectName: "uiToastStack"
    required property QtObject theme
    required property QtObject controller
    readonly property int maxVisible: 3
    readonly property int defaultDurationMs: 2800

    anchors.top: parent.top
    anchors.right: parent.right
    anchors.topMargin: Math.round(14 * theme.scale)
    anchors.rightMargin: Math.round(16 * theme.scale)
    width: Math.min(parent ? parent.width - Math.round(32 * theme.scale) : 340, Math.round(340 * theme.scale))
    height: column.implicitHeight
    z: 95

    ListModel {
        id: toastModel
    }

    property int nextId: 1

    function push(message, kind) {
        const text = String(message || "").trim()
        if (!text)
            return
        const tone = String(kind || "info")
        while (toastModel.count >= maxVisible)
            toastModel.remove(0)
        toastModel.append({
            toastId: nextId++,
            message: text,
            kind: tone,
            expiresAt: Date.now() + defaultDurationMs
        })
        sweepTimer.restart()
    }

    function dismiss(toastId) {
        for (let i = 0; i < toastModel.count; ++i) {
            if (toastModel.get(i).toastId === toastId) {
                toastModel.remove(i)
                return
            }
        }
    }

    function accentFor(kind) {
        if (kind === "success")
            return theme.success
        if (kind === "warning")
            return theme.attention
        if (kind === "error")
            return theme.error
        return theme.accent
    }

    Timer {
        id: sweepTimer
        interval: 250
        repeat: true
        running: toastModel.count > 0
        onTriggered: {
            const now = Date.now()
            for (let i = toastModel.count - 1; i >= 0; --i) {
                if (toastModel.get(i).expiresAt <= now)
                    toastModel.remove(i)
            }
        }
    }

    Connections {
        target: stack.controller
        function onToastRequested(message, kind) {
            stack.push(message, kind)
        }
    }

    Column {
        id: column
        width: parent.width
        spacing: Math.round(8 * stack.theme.scale)

        Repeater {
            model: toastModel
            delegate: Rectangle {
                required property int toastId
                required property string message
                required property string kind
                width: column.width
                height: row.implicitHeight + Math.round(18 * stack.theme.scale)
                radius: stack.theme.radiusMd
                color: stack.theme.surface
                border.width: 1
                border.color: stack.theme.line

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.margins: Math.round(4 * stack.theme.scale)
                    width: Math.round(3 * stack.theme.scale)
                    radius: Math.round(2 * stack.theme.scale)
                    color: stack.accentFor(kind)
                }

                RowLayout {
                    id: row
                    anchors.fill: parent
                    anchors.leftMargin: Math.round(14 * stack.theme.scale)
                    anchors.rightMargin: Math.round(10 * stack.theme.scale)
                    spacing: Math.round(8 * stack.theme.scale)

                    Label {
                        Layout.fillWidth: true
                        text: message
                        color: stack.theme.text
                        font.pixelSize: stack.theme.fontSm
                        wrapMode: Text.WordWrap
                        textFormat: Text.PlainText
                    }
                    UiButton {
                        theme: stack.theme
                        quiet: true
                        text: "×"
                        implicitHeight: Math.round(22 * stack.theme.scale)
                        leftPadding: Math.round(6 * stack.theme.scale)
                        rightPadding: Math.round(6 * stack.theme.scale)
                        onClicked: stack.dismiss(toastId)
                    }
                }
            }
        }
    }
}
