import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    required property var page
    objectName: "storagePanels"
    visible: page.settingsCategory === "storage"
    Layout.fillWidth: true
    Layout.leftMargin: page.pageMargin
    Layout.rightMargin: page.pageMargin
    columns: page.storageSideBySide ? 2 : 1
    columnSpacing: Math.round(14 * page.theme.scale)
    rowSpacing: Math.round(14 * page.theme.scale)
    Rectangle {
        objectName: "downloadSection"
        visible: page.settingsCategory === "storage"
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: page.storageSideBySide
            ? Math.round(430 * page.theme.scale) : -1
        Layout.preferredHeight: downloadContent.implicitHeight + Math.round(30 * page.theme.scale)
        color: page.theme.surface
        radius: Math.round(9 * page.theme.scale)
        border.color: page.theme.line

        ColumnLayout {
            id: downloadContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Math.round(15 * page.theme.scale)
            spacing: Math.round(12 * page.theme.scale)

            Text {
                Layout.fillWidth: true
                text: page.tr("下载与存储")
                color: page.theme.text
                font.pixelSize: Math.round(18 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("这里选择程序更新的来源。模型和组件的来源在各自的安装卡片中选择；并行下载数通用。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }

            GridLayout {
                Layout.fillWidth: true
                columns: page.narrow ? 1 : 2
                columnSpacing: Math.round(20 * page.theme.scale)
                rowSpacing: Math.round(12 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                        text: page.tr("程序更新来源")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                    }
                    UiComboBox {
                        id: sourceCombo
                        objectName: "downloadSourceChoice"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: page.controller.downloadSources || []
                        currentIndex: page.indexOfValue(model, page.setting("download_source", "auto"))
                        onActivated: index => page.changeSetting("download_source", currentValue)
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                        text: page.tr("并行下载数")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                    }
                    UiComboBox {
                        id: workersCombo
                        objectName: "downloadWorkersChoice"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: [
                            { label: "1", value: 1 },
                            { label: "2", value: 2 },
                            { label: "3", value: 3 },
                            { label: "4", value: 4 },
                            { label: "5", value: 5 },
                            { label: "6", value: 6 },
                            { label: "7", value: 7 },
                            { label: "8", value: 8 }
                        ]
                        currentIndex: page.indexOfValue(model, page.setting("download_workers", 4))
                        onActivated: index => page.changeSetting("download_workers", Number(currentValue))
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                        text: page.tr("网络代理模式")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                    }
                    UiComboBox {
                        id: proxyModeCombo
                        objectName: "networkProxyChoice"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: [
                            { label: page.tr("直连（忽略系统代理，默认）"), value: "direct" },
                            { label: page.tr("跟随系统代理"), value: "system" }
                        ]
                        currentIndex: page.indexOfValue(model, page.setting("network_proxy_mode", "direct"))
                        onActivated: index => page.changeSetting("network_proxy_mode", String(currentValue))
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: page.theme.line
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: page.tr("任务输出目录")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(8 * page.theme.scale)
                    Text {
                        id: outputPath
                        objectName: "outputDirectory"
                        Layout.fillWidth: true
                        text: String(page.setting("output_directory", "")).length > 0
                            ? String(page.setting("output_directory", ""))
                            : page.tr("使用系统默认目录")
                        color: String(page.setting("output_directory", "")).length > 0 ? page.theme.text : page.theme.muted
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: outputHover.hovered && truncated
                        ToolTip.text: String(page.setting("output_directory", ""))
                        HoverHandler { id: outputHover }
                    }
                    UiButton {
                        objectName: "chooseOutputDirectory"
                        theme: page.theme
                        text: page.tr("选择目录")
                        onClicked: page.outputDialog.open()
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: page.tr("用于后续准备、调音和导出任务的共同目录。单个任务仍可保留自己的结果位置。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    wrapMode: Text.Wrap
                }
            }
        }
    }
    Rectangle {
        objectName: "storageSection"
        visible: page.settingsCategory === "storage"
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        Layout.preferredWidth: page.storageSideBySide
            ? Math.round(430 * page.theme.scale) : -1
        Layout.preferredHeight: storageContent.implicitHeight + 30 * page.theme.scale
        color: page.theme.surface
        border.color: page.theme.line
        radius: 9 * page.theme.scale
        ColumnLayout {
            id: storageContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 15 * page.theme.scale
            spacing: 8 * page.theme.scale
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: page.tr("存储与缓存")
                    color: page.theme.text
                    font.pixelSize: 17 * page.theme.scale
                    font.weight: Font.DemiBold
                }
                UiButton {
                    objectName: "scanStorage"
                    theme: page.theme
                    text: page.tr("扫描占用")
                    enabled: !page.storageView.busy
                    onClicked: page.controller.refreshStorage()
                }
                UiButton {
                    objectName: "reviewStorageCleanup"
                    theme: page.theme
                    text: page.tr("清理所选…")
                    enabled: !page.storageView.busy && !page.workBusy && page.selectedCachePaths().length > 0
                    onClicked: page.storageCleanDialog.open()
                }
            }
            Text {
                Layout.fillWidth: true
                visible: !!page.storageView.scanned
                text: page.tr("输出盘可用") + " " + page.storageView.free_label
                    + "  ·  " + page.tr("任务") + " " + page.storageView.tasks_label
                    + "  ·  " + page.tr("发布资料") + " " + page.storageView.publication_label
                    + "  ·  " + page.tr("依赖与模型") + " " + page.storageView.dependencies_label
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            ListView {
                id: cacheRows
                objectName: "storageCacheRows"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(count, 5) * 37 * page.theme.scale
                visible: count > 0
                clip: true
                model: page.storageView.rows || []
                ScrollBar.vertical: UiScrollBar { theme: page.theme }
                delegate: CheckDelegate {
                    required property var modelData
                    width: cacheRows.width
                    height: 37 * page.theme.scale
                    text: modelData.title + "  ·  " + modelData.size
                          + (modelData.protected ? "  ·  " + modelData.protected : "")
                    checked: page.cachePathSelected(modelData.path)
                    enabled: !page.storageView.busy && !modelData.protected
                    onToggled: page.toggleCache(modelData.path, checked)
                    ToolTip.visible: hovered
                    ToolTip.text: modelData.path
                }
            }
            Text {
                Layout.fillWidth: true
                text: page.storageView.error || page.storageView.message || ""
                color: page.storageView.error ? page.theme.error : page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
        }
    }
}
