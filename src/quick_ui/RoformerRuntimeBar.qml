import QtQuick
import QtQuick.Layouts

Flow {
    id: root
    objectName: "roformerRuntimeStatusRow"
    required property QtObject theme
    required property QtObject controller
    required property var health
    property bool idle: true
    Layout.fillWidth: true
    spacing: Math.round(8 * theme.scale)

    function tr(msg) { return root.controller ? root.controller.text(msg) : msg }

    Repeater {
        model: [
            { key: "cuda", label: "GPU (CUDA)" },
            { key: "cpu", label: "CPU" }
        ]
        delegate: Rectangle {
            required property var modelData
            readonly property var runtimesMap: root.health && root.health.runtimes ? root.health.runtimes : ({})
            readonly property bool installed: runtimesMap[modelData.key] === "ready"
                || (!runtimesMap[modelData.key] && root.health && root.health.ready_runtime === modelData.key)
            readonly property bool selected: !!root.controller && root.controller.runtime === modelData.key
            objectName: "roformerStatus_" + modelData.key
            height: Math.round(28 * root.theme.scale)
            width: Math.min(root.width > 0 ? root.width : 9999, runtimePillText.implicitWidth + Math.round(20 * root.theme.scale))
            radius: height / 2
            color: installed
                ? Qt.rgba(root.theme.success.r, root.theme.success.g, root.theme.success.b, root.theme.dark ? 0.16 : 0.12)
                : (selected
                    ? Qt.rgba(root.theme.accent.r, root.theme.accent.g, root.theme.accent.b, root.theme.dark ? 0.14 : 0.10)
                    : root.theme.raised)
            border.color: selected ? root.theme.accent : (installed ? root.theme.success : root.theme.line)
            border.width: selected ? 1.5 : 1

            Text {
                id: runtimePillText
                anchors.centerIn: parent
                text: (selected ? "● " : "") + modelData.label + " · " + root.tr(installed ? "已安装" : "未安装")
                color: installed ? root.theme.success : (selected ? root.theme.accent : root.theme.muted)
                font.pixelSize: Math.round(11 * root.theme.scale)
                font.weight: selected ? Font.DemiBold : Font.Normal
                textFormat: Text.PlainText
            }

            MouseArea {
                anchors.fill: parent
                enabled: root.idle
                cursorShape: root.idle ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: if (root.controller) root.controller.setRuntime(modelData.key)
            }
        }
    }
}
