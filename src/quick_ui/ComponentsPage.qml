import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "componentsPage"
    required property QtObject theme
    required property QtObject controller
    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    readonly property bool stacked: availableWidth < (theme.scale > 1.2 ? 1050 : 875)
    readonly property var component: controller.selected
    readonly property bool idle: !controller.workBusy && !controller.loading

    function sizeLabel(bytes) {
        if (typeof bytes !== "number" || !isFinite(bytes) || bytes < 0)
            return controller.text("未知")
        const units = ["B", "KiB", "MiB", "GiB"]
        let unit = 0
        while (bytes >= 1024 && unit < units.length - 1) {
            bytes /= 1024
            unit += 1
        }
        return bytes.toFixed(unit === 0 ? 0 : 2) + " " + units[unit]
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 22
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("模型与组件")
                font.pixelSize: 25 * page.theme.scale
                font.weight: Font.DemiBold
                color: page.theme.text
                wrapMode: Text.Wrap
            }
            UiButton {
                objectName: "returnToPreparation"
                theme: page.theme
                text: page.controller.text("返回准备")
                onClicked: page.controller.returnToPreparation()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            spacing: 8
            TextField {
                id: search
                objectName: "componentSearch"
                Layout.fillWidth: true
                Layout.maximumWidth: 400
                Layout.preferredHeight: Math.max(36, implicitHeight)
                placeholderText: page.controller.text("查找组件")
                text: page.controller.query
                font.pixelSize: 13 * page.theme.scale
                color: page.theme.text
                placeholderTextColor: page.theme.muted
                Accessible.name: placeholderText
                selectByMouse: true
                onTextEdited: page.controller.setQuery(text)
                background: Rectangle {
                    radius: 7
                    color: page.theme.surface
                    border.color: search.activeFocus ? page.theme.accent : page.theme.line
                }
            }
            UiButton {
                objectName: "refreshComponents"
                theme: page.theme
                text: page.controller.text(page.controller.loading ? "正在检查…" : "刷新状态")
                enabled: page.idle
                onClicked: page.controller.refresh()
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            Layout.bottomMargin: 20
            columns: page.stacked ? 1 : 2
            columnSpacing: 14
            rowSpacing: 12

            Rectangle {
                objectName: "componentTable"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: tableContent.implicitHeight + 2
                color: page.theme.surface
                radius: 8
                border.color: page.theme.line
                clip: true
                ColumnLayout {
                    id: tableContent
                    anchors.fill: parent
                    anchors.margins: 1
                    spacing: 0
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round(32 * page.theme.scale)
                        color: page.theme.raised
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            anchors.rightMargin: 14
                            Text {
                                Layout.fillWidth: true
                                text: page.controller.text("组件")
                                color: page.theme.muted
                                font.pixelSize: 12 * page.theme.scale
                            }
                            Text {
                                text: page.controller.text("状态")
                                color: page.theme.muted
                                font.pixelSize: 12 * page.theme.scale
                            }
                        }
                    }
                    ListView {
                        id: list
                        objectName: "componentList"
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(1, Math.min(6, count)) * rowHeight
                        clip: true
                        model: page.controller.componentModel
                        readonly property int rowHeight: Math.round(51 * page.theme.scale)
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        delegate: ItemDelegate {
                            id: entry
                            required property string componentId
                            required property string title
                            required property string summary
                            required property string status
                            required property string statusText
                            objectName: "component_" + componentId
                            width: list.width
                            height: list.rowHeight
                            text: title
                            Accessible.name: title + " · " + statusText
                            highlighted: page.controller.selectedId === componentId
                            focusPolicy: Qt.StrongFocus
                            leftPadding: 14
                            rightPadding: 14
                            onClicked: page.controller.selectComponent(componentId)
                            background: Rectangle {
                                color: entry.highlighted ? page.theme.selected : entry.hovered ? page.theme.hover : "transparent"
                                border.color: entry.visualFocus ? page.theme.accent : "transparent"
                                Rectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: page.theme.line }
                            }
                            contentItem: RowLayout {
                                spacing: 8
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Text {
                                        Layout.fillWidth: true
                                        text: entry.title
                                        color: page.theme.text
                                        font.pixelSize: 14 * page.theme.scale
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: entry.summary
                                        color: page.theme.muted
                                        font.pixelSize: 11 * page.theme.scale
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                }
                                Rectangle {
                                    Layout.preferredWidth: 6
                                    Layout.preferredHeight: 6
                                    radius: 3
                                    color: entry.status === "ready" ? page.theme.success : entry.status === "error" ? page.theme.error : page.theme.attention
                                }
                                Text {
                                    text: entry.statusText
                                    color: page.theme.muted
                                    font.pixelSize: 12 * page.theme.scale
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: list.count === 0
                            text: page.controller.text(page.controller.loading ? "正在读取组件状态…" : "没有匹配的组件")
                            color: page.theme.muted
                            font.pixelSize: 13 * page.theme.scale
                        }
                    }
                }
            }

            Rectangle {
                objectName: "componentDetail"
                Layout.fillWidth: page.stacked
                Layout.preferredWidth: Math.round(312 * page.theme.scale)
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: details.implicitHeight + 30
                color: page.theme.surface
                radius: 8
                border.color: page.theme.line
                ColumnLayout {
                    id: details
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 15
                    spacing: 9
                    Text {
                        Layout.fillWidth: true
                        text: page.component.title || page.controller.text("选择组件")
                        color: page.theme.text
                        font.pixelSize: 18 * page.theme.scale
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: page.component.detail || ""
                        color: page.theme.muted
                        font.pixelSize: 13 * page.theme.scale
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                    ComboBox {
                        objectName: "runtimeChoice"
                        Layout.fillWidth: true
                        visible: page.component.id === "roformer"
                        enabled: page.idle
                        Accessible.name: page.controller.text("运行库")
                        font.pixelSize: 13 * page.theme.scale
                        model: [page.controller.text("仅 CPU"), "NVIDIA GPU / CUDA"]
                        currentIndex: page.controller.runtime === "cuda" ? 1 : 0
                        onActivated: index => page.controller.setRuntime(index === 1 ? "cuda" : "cpu")
                    }
                    ComboBox {
                        objectName: "downloadSourceChoice"
                        Layout.fillWidth: true
                        visible: !!page.component.can_install
                        enabled: page.idle
                        Accessible.name: page.controller.text("下载来源")
                        font.pixelSize: 13 * page.theme.scale
                        model: page.controller.downloadSources
                        textRole: "label"
                        valueRole: "value"
                        currentIndex: page.controller.downloadSources.findIndex(option => option.value === page.controller.downloadSource)
                        onActivated: page.controller.setDownloadSource(currentValue)
                    }
                    Text {
                        objectName: "componentPlan"
                        Layout.fillWidth: true
                        visible: !!page.component.can_install
                        text: page.controller.text("预计下载") + "  " + page.sizeLabel(page.component.download_bytes)
                            + (typeof page.component.required_bytes === "number" && page.component.required_bytes > 0
                               ? "\n" + page.controller.text("建议预留") + "  " + page.sizeLabel(page.component.required_bytes) : "")
                        color: page.theme.muted
                        font.pixelSize: 12 * page.theme.scale
                        wrapMode: Text.Wrap
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6
                        UiButton {
                            objectName: "useComponent"
                            theme: page.theme
                            visible: !!page.component.model
                            enabled: page.idle && !!page.component.can_use
                            primary: true
                            text: page.controller.text("使用此方案")
                            onClicked: page.controller.useComponent()
                        }
                        UiButton {
                            objectName: "installComponent"
                            theme: page.theme
                            visible: !!page.component.can_install
                            enabled: page.idle
                            primary: !page.component.can_use
                            text: page.controller.text(page.component.status === "ready" ? "校验 / 修复" : "下载并安装")
                            onClicked: page.controller.installSelected()
                        }
                        UiButton {
                            objectName: "verifyComponent"
                            theme: page.theme
                            visible: !!page.component.can_verify
                            enabled: page.idle
                            text: page.controller.text("完整校验")
                            onClicked: page.controller.verifySelected()
                        }
                    }
                }
            }
        }
    }
}
