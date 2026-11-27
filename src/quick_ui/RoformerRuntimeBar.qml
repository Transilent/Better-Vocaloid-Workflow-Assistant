import QtQuick
import QtQuick.Layouts

RowLayout {
    id: root
    objectName: "roformerRuntimeStatusRow"
    required property QtObject theme
    required property QtObject controller
    required property var health
    property bool idle: true
    Layout.fillWidth: true
    spacing: Math.round(8 * theme.scale)

    Repeater {
        model: [
            { key: "cuda", label: "GPU (CUDA)" },
            { key: "cpu", label: "CPU" }
        ]
        delegate: Rectangle {
            required property var modelData
            readonly property var runtimesMap: root.health.runtimes || ({})
            readonly property bool installed: runtimesMap[modelData.key] === "ready"
                || (!runtimesMap[modelData.key] && root.health.ready_runtime === modelData.key)
            readonly property bool selected: root.controller.runtime === modelData.key
            objectName: "roformerStatus_" + modelData.key
            Layout.preferredHeight: Math.round(26 * root.theme.scale)
            Layout.preferredWidth: runtimePillText.implicitWidth + Math.round(18 * root.theme.scale)
            radius: height / 2
            color: installed
                ? Qt.tint(root.theme.success, Qt.rgba(0, 0, 0, 0.86))
                : root.theme.raised
            border.color: selected ? root.theme.accent : (installed ? root.theme.success : root.theme.line)
            border.width: selected ? 1.5 : 1

            Text {
                id: runtimePillText
                anchors.centerIn: parent
                text: modelData.label + " · " + root.controller.text(installed ? "已安装" : "未安装")
                color: installed ? root.theme.success : root.theme.muted
                font.pixelSize: Math.round(11 * root.theme.scale)
                font.weight: selected ? Font.DemiBold : Font.Normal
                textFormat: Text.PlainText
            }

            MouseArea {
                anchors.fill: parent
                enabled: root.idle
                cursorShape: root.idle ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: root.controller.setRuntime(modelData.key)
            }
        }
    }

    Item { Layout.fillWidth: true }
}
