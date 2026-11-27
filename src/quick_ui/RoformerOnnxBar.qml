import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "roformerOnnxStatusPanel"
    required property QtObject theme
    required property QtObject controller
    required property var health
    property bool idle: true
    Layout.fillWidth: true
    spacing: Math.round(8 * theme.scale)

    readonly property var rec: health.recommendation || ({})
    readonly property bool amdRecommended: rec.recommend_onnx === true
    readonly property bool overVramRisk: controller.onnxPrecision === "fp16"
        && typeof rec.max_dedicated_vram_gib === "number"
        && rec.max_dedicated_vram_gib > 0 && rec.max_dedicated_vram_gib < 11.0

    Rectangle {
        objectName: "roformerOnnxExperimentalBanner"
        Layout.fillWidth: true
        Layout.preferredHeight: expText.implicitHeight + Math.round(16 * root.theme.scale)
        radius: Math.round(6 * root.theme.scale)
        color: Qt.tint(root.theme.attention, Qt.rgba(0, 0, 0, 0.88))
        border.color: root.theme.attention

        Text {
            id: expText
            anchors.fill: parent
            anchors.margins: Math.round(8 * root.theme.scale)
            text: root.controller.text("实验性组件提示：默认不启用，独立隔离运行库，不影响原版 PyTorch CUDA 与 DirectML。10s 分块上下文较原版减半，部分长乐句或显卡驱动下可能出现分离差异或 WebGPU 兼容问题；8 GB 及以下显存请仅用 10s FP16，12 GB+ 显存可选 20s FP16。")
            color: root.theme.attention
            font.pixelSize: Math.round(11 * root.theme.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
    }

    Rectangle {
        objectName: "roformerOnnxAmdBanner"
        Layout.fillWidth: true
        visible: !!root.rec.reason
        Layout.preferredHeight: recText.implicitHeight + Math.round(14 * root.theme.scale)
        radius: Math.round(6 * root.theme.scale)
        color: root.amdRecommended
            ? Qt.tint(root.theme.success, Qt.rgba(0, 0, 0, 0.88))
            : root.theme.raised
        border.color: root.amdRecommended ? root.theme.success : root.theme.line

        Text {
            id: recText
            anchors.fill: parent
            anchors.margins: Math.round(7 * root.theme.scale)
            text: root.rec.reason || ""
            color: root.amdRecommended ? root.theme.success : root.theme.muted
            font.pixelSize: Math.round(11 * root.theme.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
    }

    RowLayout {
        objectName: "roformerOnnxPrecisionRow"
        Layout.fillWidth: true
        spacing: Math.round(8 * root.theme.scale)

        Repeater {
            model: [
                { key: "fp16_10s", label: root.controller.text("10s FP16（≤8 GB 显存）") },
                { key: "fp16", label: root.controller.text("20s FP16（12 GB+ 显存）") }
            ]
            delegate: Rectangle {
                required property var modelData
                readonly property var precMap: root.health.precisions || ({})
                readonly property bool installed: precMap[modelData.key] === "ready"
                readonly property bool selected: root.controller.onnxPrecision === modelData.key
                readonly property bool recommended: root.amdRecommended && root.rec.recommended_precision === modelData.key
                objectName: "roformerOnnxStatus_" + modelData.key
                Layout.preferredHeight: Math.round(26 * root.theme.scale)
                Layout.preferredWidth: pillLabel.implicitWidth + Math.round(18 * root.theme.scale)
                radius: height / 2
                color: installed
                    ? Qt.tint(root.theme.success, Qt.rgba(0, 0, 0, 0.86))
                    : root.theme.raised
                border.color: selected ? root.theme.accent : (installed ? root.theme.success : root.theme.line)
                border.width: selected ? 1.5 : 1

                Text {
                    id: pillLabel
                    anchors.centerIn: parent
                    text: modelData.label + " · " + root.controller.text(installed ? "已安装" : (recommended ? "推荐下载" : "未安装"))
                    color: installed ? root.theme.success : (recommended ? root.theme.accent : root.theme.muted)
                    font.pixelSize: Math.round(11 * root.theme.scale)
                    font.weight: selected ? Font.DemiBold : Font.Normal
                    textFormat: Text.PlainText
                }

                MouseArea {
                    anchors.fill: parent
                    enabled: root.idle
                    cursorShape: root.idle ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.controller.setOnnxPrecision(modelData.key)
                }
            }
        }

        Item { Layout.fillWidth: true }
    }

    Text {
        objectName: "roformerOnnxVramWarning"
        Layout.fillWidth: true
        visible: root.overVramRisk
        text: root.controller.text("显存警告：当前显卡独立显存低于 11 GiB，运行 20s FP16 会触发严重共享内存换页（耗时可能飙升至数小时），请改选 10s FP16。")
        color: root.theme.error
        font.pixelSize: Math.round(11 * root.theme.scale)
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }
}
