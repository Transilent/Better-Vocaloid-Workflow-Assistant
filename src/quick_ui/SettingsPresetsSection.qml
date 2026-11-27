import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    required property var page
    objectName: "presetLibrarySection"
    visible: page.settingsCategory === "presets"
    Layout.fillWidth: true
    Layout.leftMargin: page.pageMargin
    Layout.rightMargin: page.pageMargin
    Layout.preferredHeight: presetContent.implicitHeight + 30 * page.theme.scale
    color: page.theme.surface
    border.color: page.theme.line
    radius: 9 * page.theme.scale
    ColumnLayout {
        id: presetContent
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 15 * page.theme.scale
        spacing: 8 * page.theme.scale
        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: page.tr("本机预设库")
                color: page.theme.text
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            UiButton {
                objectName: "refreshPresetLibrary"
                theme: page.theme
                text: page.tr(page.presetView.loaded ? "刷新预设库" : "载入预设库")
                enabled: !page.presetView.busy
                onClicked: page.controller.refreshPresetLibrary()
            }
            UiButton {
                objectName: "importPresetLibrary"
                theme: page.theme
                text: page.tr("附加导入…")
                enabled: !!page.presetView.loaded && !page.presetView.busy
                onClicked: page.presetImportFileDialog.open()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: !!page.presetView.loaded
            TextField {
                Layout.fillWidth: true
                placeholderText: page.tr("搜索名称、ID 或版本")
                text: page.presetQuery
                onTextEdited: page.presetQuery = text
            }
            UiComboBox {
                theme: page.theme
                model: ["all", "preparation", "mixdown", "tuning", "style_recipe", "voicebank_calibration"].map(
                           value => ({label:value === "all" ? page.tr("全部类型") : page.typeLabel(value), value:value}))
                currentIndex: page.indexOfValue(model, page.presetType)
                onActivated: page.presetType = currentValue
            }
        }
        ListView {
            id: presetRows
            objectName: "presetLibraryRows"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(count, 5) * 38 * page.theme.scale
            visible: count > 0
            clip: true
            model: page.filteredPresets
            ScrollBar.vertical: UiScrollBar { theme: page.theme }
            delegate: ItemDelegate {
                required property var modelData
                width: presetRows.width
                height: 38 * page.theme.scale
                highlighted: page.isPresetSelected(modelData)
                text: (modelData.favorite ? "★ " : "") + modelData.name + "  ·  "
                    + page.typeLabel(modelData.type) + "  ·  " + modelData.version
                    + (modelData.builtin ? "  ·  " + page.tr("内置") : "")
                onClicked: page.selectPreset(modelData)
            }
        }
        Flow {
            Layout.fillWidth: true
            visible: page.presetKey(page.selectedPreset).length > 0
            spacing: 6 * page.theme.scale
            UiButton {
                theme: page.theme
                text: page.tr("导出原文件…")
                enabled: !page.presetView.busy
                onClicked: {
                    page.presetExportEntry = page.selectedPreset
                    page.presetExportFileDialog.open()
                }
            }
            UiButton {
                theme: page.theme
                text: page.tr("创建副本…")
                enabled: !page.presetView.busy
                onClicked: page.openPresetText("copy")
            }
            UiButton {
                theme: page.theme
                text: page.tr("修改别名…")
                enabled: !page.presetView.busy && !page.selectedPreset.builtin
                onClicked: page.openPresetText("alias")
            }
            UiButton {
                theme: page.theme
                text: page.tr(page.selectedPreset.favorite ? "取消收藏" : "收藏")
                enabled: !page.presetView.busy && !page.selectedPreset.builtin
                onClicked: page.controller.setPresetFavorite(page.selectedPreset.id, page.selectedPreset.version,
                                                             !page.selectedPreset.favorite)
            }
            UiButton {
                theme: page.theme
                text: page.tr("移除…")
                enabled: !page.presetView.busy && !page.selectedPreset.builtin
                onClicked: { page.presetRemoveEntry = page.selectedPreset; page.presetRemoveDialog.open() }
            }
        }
        Rectangle {
            objectName: "presetInspectPanel"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(Math.round(230 * page.theme.scale),
                                             inspectContent.implicitHeight + Math.round(24 * page.theme.scale))
            visible: page.presetKey(page.selectedPreset).length > 0
            color: page.theme.base
            radius: 7 * page.theme.scale
            border.color: page.theme.line
            ColumnLayout {
                id: inspectContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(11 * page.theme.scale)
                spacing: 6 * page.theme.scale
                Text {
                    Layout.fillWidth: true
                    text: page.tr("预设内容（只读）")
                    color: page.theme.text
                    font.pixelSize: 12 * page.theme.scale
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    visible: !!page.presetView.inspect_error
                    text: String(page.presetView.inspect_error || "")
                    color: page.theme.error
                    font.pixelSize: 11 * page.theme.scale
                    wrapMode: Text.Wrap
                }
                ScrollView {
                    id: presetInspectScroll
                    objectName: "presetInspectScroll"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(160 * page.theme.scale)
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                    TextArea {
                        objectName: "presetInspectText"
                        width: presetInspectScroll.availableWidth
                        text: page.presetView.inspect_busy
                            ? page.tr("正在读取预设内容…")
                            : String(page.presetView.inspect_text || "")
                        readOnly: true
                        selectByMouse: true
                        selectByKeyboard: true
                        wrapMode: TextEdit.NoWrap
                        textFormat: TextEdit.PlainText
                        color: page.theme.text
                        selectionColor: page.theme.accent
                        selectedTextColor: page.theme.base
                        font.family: "Consolas"
                        font.pixelSize: 11 * page.theme.scale
                        background: Rectangle { color: "transparent" }
                        Accessible.name: page.tr("预设 JSON 内容")
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: page.presetView.error || (page.presetView.loaded ? page.presetView.message
                      : page.tr("导入默认附加，同版本冲突时可创建独立副本。"))
                color: page.presetView.error ? page.theme.error : page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            UiButton {
                theme: page.theme
                text: page.tr("恢复索引…")
                enabled: !page.presetView.busy
                visible: !!page.presetView.error && !page.presetView.conflict
                onClicked: page.presetRecoverDialog.open()
            }
        }
    }
}
