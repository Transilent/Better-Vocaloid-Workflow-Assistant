import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "componentPlan"
    required property QtObject theme
    required property QtObject controller
    required property var component
    required property var health
    required property var volume
    required property real reserveBytes
    required property real freeBytes
    required property bool lowSpace
    required property var sizeLabelFn

    Layout.fillWidth: true
    Layout.preferredHeight: planFacts.implicitHeight + Math.round(24 * root.theme.scale)
    radius: Math.round(7 * root.theme.scale)
    color: root.theme.raised
    border.color: root.theme.line

    component PlanMetric: ColumnLayout {
        required property string label
        required property string value
        property color valueColor: root.theme.text
        spacing: Math.round(4 * root.theme.scale)
        Text {
            Layout.fillWidth: true
            text: parent.label
            font.pixelSize: Math.round(11 * root.theme.scale)
            color: root.theme.muted
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        Text {
            Layout.fillWidth: true
            text: parent.value
            font.pixelSize: Math.round(19 * root.theme.scale)
            font.weight: Font.DemiBold
            color: parent.valueColor
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
    }

    ColumnLayout {
        id: planFacts
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Math.round(12 * root.theme.scale)
        spacing: Math.round(10 * root.theme.scale)

        Text {
            Layout.fillWidth: true
            text: root.controller.text(!root.component.id ? "从左侧目录选择一个组件查看计划"
                : root.volume.path ? "安装计划" : "本机状态")
            color: root.theme.text
            font.pixelSize: Math.round(13 * root.theme.scale)
            font.weight: Font.DemiBold
            textFormat: Text.PlainText
        }
        RowLayout {
            Layout.fillWidth: true
            visible: !!root.volume.path
            spacing: Math.round(12 * root.theme.scale)
            PlanMetric {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                label: root.controller.text("完整下载大小")
                value: root.component.download_bytes > 0
                    ? root.sizeLabelFn(root.component.download_bytes) : root.controller.text("未知")
            }
            PlanMetric {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                label: root.controller.text("建议预留")
                value: root.reserveBytes > 0
                    ? root.sizeLabelFn(root.reserveBytes) : root.controller.text("未知")
            }
            PlanMetric {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                label: root.controller.text("卷可用空间")
                value: root.sizeLabelFn(root.freeBytes)
                valueColor: root.lowSpace ? root.theme.attention : root.theme.text
            }
        }
        Text {
            Layout.fillWidth: true
            visible: !!root.component.summary
            text: root.controller.text(root.component.summary || "")
            color: root.theme.text
            font.pixelSize: Math.round(12 * root.theme.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        Text {
            Layout.fillWidth: true
            visible: !!root.volume.path
            text: root.controller.text("组件根") + " · " + (root.volume.path || "")
            color: root.theme.muted
            font.pixelSize: Math.round(11 * root.theme.scale)
            elide: Text.ElideMiddle
            textFormat: Text.PlainText
            ToolTip.visible: volumeHover.hovered && !!root.volume.path
            ToolTip.text: root.volume.path || ""
            HoverHandler { id: volumeHover }
        }
        Text {
            Layout.fillWidth: true
            visible: typeof root.health.cache_bytes === "number"
            text: root.controller.text("可复用下载缓存") + " · " + root.sizeLabelFn(root.health.cache_bytes)
            color: root.theme.muted
            font.pixelSize: Math.round(11 * root.theme.scale)
            textFormat: Text.PlainText
        }
    }
}
