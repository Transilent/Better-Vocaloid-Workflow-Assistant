import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: dialogsRoot
    required property var page

    property alias outputDialog: outputDialog
    property alias presetImportFileDialog: presetImportFileDialog
    property alias presetExportFileDialog: presetExportFileDialog
    property alias storageCleanDialog: storageCleanDialog
    property alias presetImportPreviewDialog: presetImportPreviewDialog
    property alias presetImportCopy: presetImportCopy
    property alias presetConflictDialog: presetConflictDialog
    property alias presetTextDialog: presetTextDialog
    property alias presetNameField: presetNameField
    property alias presetRemoveDialog: presetRemoveDialog
    property alias presetRecoverDialog: presetRecoverDialog

    FolderDialog {
        id: outputDialog
        title: page.tr("选择任务输出目录")
        onAccepted: page.chooseDirectory(selectedFolder)
    }

    FileDialog {
        id: presetImportFileDialog
        objectName: "presetLibraryImportFileDialog"
        title: page.tr("选择预设文件")
        fileMode: FileDialog.OpenFile
        nameFilters: [page.tr("VWA 预设 (*.json)"), page.tr("所有文件 (*)")]
        onAccepted: {
            if (page.controller.previewPresetImport(selectedFile))
                presetImportPreviewDialog.open()
        }
    }

    FileDialog {
        id: presetExportFileDialog
        objectName: "presetLibraryExportFileDialog"
        title: page.tr("导出原始预设文件")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [page.tr("VWA 预设 (*.json)")]
        onAccepted: {
            var entry = page.presetExportEntry || ({})
            if (page.presetKey(entry).length > 0)
                page.controller.exportPreset(entry.id, entry.version, selectedFile)
            page.presetExportEntry = ({})
        }
    }

    UiDialog {
        id: storageCleanDialog
        objectName: "storageCleanConfirmDialog"
        theme: page.theme
        closePolicy: Popup.CloseOnEscape
        width: Math.min(parent ? parent.width - Math.round(30 * page.theme.scale) : Math.round(540 * page.theme.scale),
                        Math.round(540 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)
        property int count: 0
        property int bytes: 0
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.tr("清理选中缓存")
                color: page.theme.text
                font.pixelSize: Math.round(17 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("将清理 %1 项，共 %2。清理前会再次核验任务引用、编辑状态、运行锁和路径边界。")
                    .arg(storageCleanDialog.count).arg(page.sizeLabel(storageCleanDialog.bytes))
                color: page.theme.text
                font.pixelSize: Math.round(13 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("任务结果、成品视频、已修改的发布资料、模型、登录信息和正在使用的内容会保留。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.tr("取消")
                    onClicked: storageCleanDialog.close()
                }
                UiButton {
                    objectName: "confirmStorageCleanup"
                    theme: page.theme
                    primary: true
                    text: page.tr("清理选中缓存")
                    onClicked: {
                        page.controller.cleanStorage(page.selectedCachePaths())
                        storageCleanDialog.close()
                    }
                }
            }
        }
    }

    UiDialog {
        id: presetImportPreviewDialog
        objectName: "presetImportPreviewDialog"
        theme: page.theme
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(parent ? parent.width - Math.round(30 * page.theme.scale) : Math.round(600 * page.theme.scale),
                        Math.round(600 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)
        readonly property var preview: page.presetView.import_preview || ({})
        onOpened: presetImportCopy.checked = !!preview.copy_required
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.tr("导入预设预览")
                color: page.theme.text
                font.pixelSize: Math.round(18 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("默认附加到本机库，保留已有预设；导入不会应用到当前工程。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: previewDetails.implicitHeight + Math.round(22 * page.theme.scale)
                color: page.theme.base
                radius: Math.round(7 * page.theme.scale)
                border.color: page.theme.line
                ColumnLayout {
                    id: previewDetails
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(11 * page.theme.scale)
                    spacing: 4
                    Text {
                        Layout.fillWidth: true
                        text: String(presetImportPreviewDialog.preview.name || page.tr("未命名预设"))
                        color: page.theme.text
                        font.pixelSize: Math.round(14 * page.theme.scale)
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.typeLabel(presetImportPreviewDialog.preview.type) + "  ·  "
                            + String(presetImportPreviewDialog.preview.version || "") + "  ·  "
                            + String(presetImportPreviewDialog.preview.source_name || "")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("来源 ID") + "  " + String(presetImportPreviewDialog.preview.id || "")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                    }
                }
            }
            UiCheckBox {
                id: presetImportCopy
                objectName: "presetImportAsCopy"
                theme: page.theme
                Layout.fillWidth: true
                text: page.tr("创建独立副本（新 ID 与版本 0.1.0）")
                enabled: !presetImportPreviewDialog.preview.copy_required
                Accessible.name: text
            }
            Text {
                Layout.fillWidth: true
                text: presetImportPreviewDialog.preview.copy_required
                    ? page.tr("内置预设导出文件会始终创建独立本机副本，不会覆盖内置条目。")
                    : page.tr("若同一 ID 与版本已有不同内容，导入会停止并明确提示冲突；不会覆盖。")
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.tr("取消")
                    onClicked: {
                        page.controller.cancelPresetImport()
                        presetImportPreviewDialog.close()
                    }
                }
                UiButton {
                    objectName: "confirmPresetImport"
                    theme: page.theme
                    primary: true
                    text: page.tr("确认导入")
                    enabled: !!presetImportPreviewDialog.preview.path && !page.presetView.busy
                    onClicked: page.confirmPresetImport()
                }
            }
        }
    }

    UiDialog {
        id: presetConflictDialog
        objectName: "presetVersionConflictDialog"
        theme: page.theme
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(parent ? parent.width - Math.round(30 * page.theme.scale) : Math.round(590 * page.theme.scale),
                        Math.round(590 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.tr("预设冲突，未覆盖")
                color: page.theme.error
                font.pixelSize: Math.round(18 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: String(page.presetView.error || page.tr("本机预设库拒绝了本次操作。"))
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("“创建独立副本”会生成新的本机 ID 与 0.1.0 版本，同时保留本机已有条目和导入文件。")
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.tr("关闭")
                    onClicked: presetConflictDialog.close()
                }
                UiButton {
                    objectName: "retryPresetImportAsCopy"
                    theme: page.theme
                    primary: true
                    visible: page.presetView.error_code === "preset_version_conflict"
                        && !!page.presetView.import_preview.path
                    text: page.tr("创建独立副本并重试")
                    enabled: !page.presetView.busy
                    onClicked: {
                        if (page.controller.importPreset(true))
                            presetConflictDialog.close()
                    }
                }
            }
        }
    }

    UiDialog {
        id: presetTextDialog
        objectName: "presetLibraryTextDialog"
        theme: page.theme
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(parent ? parent.width - Math.round(30 * page.theme.scale) : Math.round(480 * page.theme.scale),
                        Math.round(480 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.presetTextMode === "copy" ? page.tr("复制内置预设") : page.tr("修改本机别名")
                color: page.theme.text
                font.pixelSize: Math.round(17 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.presetTextMode === "copy"
                    ? page.tr("创建新的本机 ID 与版本，不改写内置文件。")
                    : page.tr("别名只改变本机显示名称；清空会恢复文件中的名称。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            TextField {
                id: presetNameField
                objectName: "presetLibraryNameField"
                Layout.fillWidth: true
                maximumLength: 80
                selectByMouse: true
                color: page.theme.text
                placeholderText: page.tr("显示名称")
                Accessible.name: placeholderText
                background: Rectangle {
                    radius: Math.round(7 * page.theme.scale)
                    color: page.theme.base
                    border.color: presetNameField.activeFocus ? page.theme.accent : page.theme.line
                }
                onAccepted: page.confirmPresetText()
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.tr("取消")
                    onClicked: presetTextDialog.close()
                }
                UiButton {
                    objectName: "confirmPresetLibraryText"
                    theme: page.theme
                    primary: true
                    text: page.presetTextMode === "copy" ? page.tr("创建副本") : page.tr("保存别名")
                    enabled: !page.presetView.busy
                        && (page.presetTextMode !== "copy" || presetNameField.text.trim().length > 0)
                    onClicked: page.confirmPresetText()
                }
            }
        }
    }

    UiDialog {
        id: presetRemoveDialog
        objectName: "presetLibraryRemoveDialog"
        theme: page.theme
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(parent ? parent.width - Math.round(30 * page.theme.scale) : Math.round(540 * page.theme.scale),
                        Math.round(540 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.tr("从本机活动列表移除预设")
                color: page.theme.text
                font.pixelSize: Math.round(17 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: String(page.presetRemoveEntry.name || "") + "  "
                    + String(page.presetRemoveEntry.version || "")
                color: page.theme.text
                font.pixelSize: Math.round(13 * page.theme.scale)
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("这是逻辑移除：不可变预设文件与上一索引会保留，不会删除任务、素材或内置条目。内置预设不可移除。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.tr("取消")
                    onClicked: presetRemoveDialog.close()
                }
                UiButton {
                    objectName: "confirmPresetRemove"
                    theme: page.theme
                    primary: true
                    text: page.tr("从列表移除")
                    onClicked: page.confirmPresetRemove()
                }
            }
        }
    }

    UiDialog {
        id: presetRecoverDialog
        objectName: "presetLibraryRecoverDialog"
        theme: page.theme
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(parent ? parent.width - Math.round(30 * page.theme.scale) : Math.round(540 * page.theme.scale),
                        Math.round(540 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.tr("恢复上一份有效索引")
                color: page.theme.text
                font.pixelSize: Math.round(17 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("当前损坏索引会保留在历史中，引用文件会先重新校验。此操作不会自动应用预设。")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.tr("取消")
                    onClicked: presetRecoverDialog.close()
                }
                UiButton {
                    objectName: "confirmPresetRecovery"
                    theme: page.theme
                    primary: true
                    text: page.tr("恢复并刷新")
                    onClicked: {
                        if (page.controller.recoverPresetLibrary())
                            presetRecoverDialog.close()
                    }
                }
            }
        }
    }
}
