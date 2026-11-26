import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: pane
    objectName: "workshopPresetPane"
    required property QtObject theme
    required property QtObject controller
    required property QtObject support
    signal targetRequested()
    readonly property real unit: theme.scale
    readonly property var catalog: support ? support.presetLibrary : ({})
    readonly property var dataView: controller ? controller.view : ({})
    readonly property var entries: (catalog.entries || []).filter(row => row.type === "style_recipe" || row.type === "voicebank_calibration")
    readonly property var selected: entries.find(row => key(row) === key(dataView.selectedPreset || {})) || ({})
    readonly property bool calibration: selected.type === "voicebank_calibration"
    readonly property var preview: catalog.import_preview || ({})
    property var actionEntry: ({})
    readonly property var payload: {
        if (!selected.id || catalog.inspect_key !== key(selected) || !catalog.inspect_text)
            return {}
        try { return JSON.parse(catalog.inspect_text).payload || {} }
        catch (_) { return {} }
    }
    readonly property bool structured: selected.type === "style_recipe" && payload.coordinate_system === "note_and_phrase"
    readonly property var filtered: {
        const query = search.text.trim().toLowerCase()
        return entries.filter(row => (filter.currentValue === "all" || filter.currentValue === row.type)
            && (!query || ((row.name || "") + " " + (row.description || "")).toLowerCase().includes(query)))
    }

    function label(text) { return typeof quickTranslation !== "undefined" && quickTranslation ? quickTranslation.text(text) : text }
    function key(row) { return row.id ? row.id + "|" + row.version : "" }
    function typeLabel(type) {
        return label(type === "style_recipe" ? "风格配方" : type === "voicebank_calibration" ? "声库校准" : "其他预设")
    }
    function inspectSelection() {
        if (selected.id && catalog.inspect_key !== key(selected) && !catalog.inspect_busy && !catalog.busy)
            support.inspectPreset(selected.id, selected.version)
    }
    onSelectedChanged: inspectTimer.restart()
    Timer {
        id: inspectTimer
        interval: 0
        onTriggered: pane.inspectSelection()
    }
    Connections {
        target: pane.support
        function onPresetLibraryChanged() { inspectTimer.restart() }
    }

    component Caption: Text {
        font.pixelSize: Math.round(13 * pane.unit)
        wrapMode: Text.Wrap
        color: pane.theme.muted
        textFormat: Text.PlainText
    }
    component Heading: Text {
        font.pixelSize: Math.round(17 * pane.unit)
        font.weight: Font.DemiBold
        color: pane.theme.text
        textFormat: Text.PlainText
        elide: Text.ElideRight
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Math.round(13 * pane.unit)

        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: search
                objectName: "workshopPresetSearch"
                Layout.fillWidth: true
                placeholderText: pane.label("查找工坊预设")
                placeholderTextColor: pane.theme.muted
                selectByMouse: true
                font.pixelSize: Math.round(13 * pane.unit)
                background: Rectangle {
                    radius: Math.round(7 * pane.unit)
                    color: pane.theme.surface
                    border.color: search.activeFocus ? pane.theme.accent : pane.theme.line
                }
            }
            UiComboBox {
                id: filter
                objectName: "workshopPresetFilter"
                theme: pane.theme
                Layout.preferredWidth: Math.round(153 * pane.unit)
                model: [{value: "all", label: pane.label("全部")},
                        {value: "style_recipe", label: pane.label("风格配方")},
                        {value: "voicebank_calibration", label: pane.label("声库校准")}]
            }
            UiButton {
                objectName: "workshopPresetImport"
                theme: pane.theme
                primary: !pane.entries.length
                text: pane.label("导入预设…")
                enabled: !pane.catalog.busy
                onClicked: importFile.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Math.round(13 * pane.unit)

            Rectangle {
                Layout.fillHeight: true
                Layout.preferredWidth: Math.round(Math.max(270, Math.min(pane.width * .36 / pane.unit, 395)) * pane.unit)
                color: pane.theme.surface
                border.color: pane.theme.line
                radius: Math.round(9 * pane.unit)
                ListView {
                    id: list
                    objectName: "workshopPresetList"
                    anchors.fill: parent
                    anchors.margins: Math.round(8 * pane.unit)
                    clip: true
                    spacing: Math.round(3 * pane.unit)
                    boundsBehavior: Flickable.StopAtBounds
                    model: pane.filtered
                    ScrollBar.vertical: ScrollBar {}
                    delegate: ItemDelegate {
                        id: entry
                        required property var modelData
                        objectName: "workshopPresetRow_" + modelData.id
                        width: list.width
                        height: Math.round(58 * pane.unit)
                        highlighted: pane.key(pane.selected) === pane.key(modelData)
                        Accessible.name: modelData.name + " · " + pane.typeLabel(modelData.type)
                        onClicked: pane.controller.selectPreset(modelData.id, modelData.version)
                        background: Rectangle {
                            radius: Math.round(7 * pane.unit)
                            color: entry.highlighted ? pane.theme.selected : entry.hovered ? pane.theme.hover : "transparent"
                            border.color: entry.highlighted ? pane.theme.selectedLine : "transparent"
                        }
                        contentItem: RowLayout {
                            spacing: Math.round(9 * pane.unit)
                            Rectangle {
                                Layout.preferredWidth: 3 * pane.unit
                                Layout.preferredHeight: 27 * pane.unit
                                radius: width / 2
                                color: entry.modelData.type === "style_recipe" ? pane.theme.accent : pane.theme.success
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3 * pane.unit
                                Text {
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                    font.pixelSize: Math.round(14 * pane.unit)
                                    color: pane.theme.text
                                    text: entry.modelData.name
                                }
                                Text {
                                    color: pane.theme.muted
                                    font.pixelSize: Math.round(11 * pane.unit)
                                    text: pane.typeLabel(entry.modelData.type) + " · v" + entry.modelData.version
                                }
                            }
                            Text { text: entry.modelData.favorite ? "★" : ""; color: pane.theme.attention }
                        }
                    }
                }
                Caption {
                    anchors.centerIn: parent
                    width: parent.width - 35 * pane.unit
                    horizontalAlignment: Text.AlignHCenter
                    visible: list.count === 0
                    text: pane.catalog.busy ? pane.label("正在读取本机预设库…")
                        : pane.label("这里会列出制作或导入的风格和校准预设。")
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: pane.theme.surface
                border.color: pane.theme.line
                radius: Math.round(9 * pane.unit)
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(19 * pane.unit)
                    spacing: Math.round(11 * pane.unit)
                    Heading {
                        Layout.fillWidth: true
                        text: pane.selected.name || pane.label("选择一项预设")
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: !!pane.selected.id
                        text: pane.typeLabel(pane.selected.type) + " · v" + pane.selected.version
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: !!pane.selected.id && !!pane.selected.description
                        text: pane.selected.description || ""
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: pane.theme.line
                        visible: !!pane.selected.id
                    }
                    Caption {
                        Layout.fillWidth: true
                        color: pane.theme.text
                        visible: pane.calibration && !!pane.payload.voicebank
                        text: pane.label("声库") + "  " + (pane.payload.voicebank || "")
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: pane.calibration
                        text: pane.label("校准要求乐谱和声库设置匹配。导入后可关联原来的七份试音 WAV，参考演唱可在调音来源中指定。")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: pane.calibration
                        Caption {
                            id: association
                            readonly property string fullPath: pane.dataView.associatedFolder || ""
                            Layout.fillWidth: true
                            color: fullPath ? pane.theme.success : pane.theme.attention
                            text: fullPath ? fullPath.split(/[\\/]/).pop() || fullPath : pane.label("试音目录待关联")
                            wrapMode: Text.NoWrap
                            elide: Text.ElideMiddle
                            HoverHandler { id: folderHover }
                            ToolTip.visible: folderHover.hovered && !!fullPath
                            ToolTip.text: fullPath
                        }
                        UiButton {
                            objectName: "workshopAssociateTrialFolder"
                            theme: pane.theme
                            text: pane.label("关联试音目录…")
                            enabled: !pane.dataView.busy && !pane.catalog.busy
                            onClicked: trialFolder.open()
                        }
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: pane.selected.type === "style_recipe" && !!pane.payload.controls
                        text: (pane.payload.controls || []).map(control =>
                            pane.label({loudness:"力度", tension:"张力", breathiness:"气声", voicing:"发声"}[control.channel])
                            + (control.points ? " · " + control.points.length + " " + pane.label("控制点") : "")).join("  /  ")
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: pane.selected.type === "style_recipe" && !!(pane.payload.pitch || {}).enabled
                        text: pane.structured ? ["onset", "connection", "release", "vibrato"].filter(key => pane.payload.pitch[key])
                            .map(key => pane.label({onset: "句首倚滑", connection: "音间衔接", release: "句尾收腔",
                                                    vibrato: "长音颤音"}[key])).join(" · ")
                            : ((pane.payload.pitch || {}).gestures || []).map(action =>
                                pane.label(action.kind === "onset" ? "起音音高" : "收尾音高") + " " + action.cents + " ¢").join(" · ")
                    }
                    Caption {
                        Layout.fillWidth: true
                        visible: !!pane.selected.id
                        text: (pane.dataView.target || {}).title
                            ? pane.label("当前调音工程") + "  " + pane.dataView.target.title + " · " + pane.dataView.target.track
                            : pane.label("在调音工作流打开目标工程后可选用。")
                    }
                    Item { Layout.fillHeight: true }
                    Flow {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * pane.unit)
                        visible: !!pane.selected.id
                        UiButton {
                            theme: pane.theme
                            quiet: true
                            text: pane.label(pane.selected.favorite ? "取消收藏" : "收藏")
                            enabled: !pane.catalog.busy && !pane.selected.builtin
                            onClicked: pane.support.setPresetFavorite(pane.selected.id, pane.selected.version, !pane.selected.favorite)
                        }
                        UiButton {
                            theme: pane.theme
                            quiet: true
                            text: pane.label("重命名…")
                            enabled: !pane.catalog.busy && !pane.selected.builtin
                            onClicked: {
                                pane.actionEntry = pane.selected
                                aliasName.text = pane.actionEntry.alias || ""
                                aliasDialog.open()
                            }
                        }
                        UiButton {
                            theme: pane.theme
                            quiet: true
                            text: pane.label("移除…")
                            enabled: !pane.catalog.busy && !pane.selected.builtin
                            onClicked: { pane.actionEntry = pane.selected; removeDialog.open() }
                        }
                        UiButton {
                            objectName: "workshopPresetExport"
                            theme: pane.theme
                            text: pane.label("导出预设…")
                            enabled: !pane.catalog.busy
                            onClicked: { pane.actionEntry = pane.selected; exportFile.open() }
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Caption {
                Layout.fillWidth: true
                text: pane.catalog.error || pane.dataView.message || pane.catalog.message || ""
            }
            UiButton {
                theme: pane.theme
                quiet: true
                text: pane.label("去调音选择目标")
                onClicked: pane.targetRequested()
            }
            UiButton {
                objectName: "workshopEditStructure"
                visible: pane.structured
                theme: pane.theme
                text: pane.label("复制到风格制作")
                enabled: !pane.dataView.busy && !pane.catalog.busy
                onClicked: pane.controller.editSelectedStructure()
            }
            UiButton {
                objectName: "workshopUsePreset"
                theme: pane.theme
                primary: true
                text: pane.label(pane.calibration ? "选用校准预设" : "选用风格")
                enabled: !!pane.selected.id && !!(pane.dataView.target || {}).title && !pane.catalog.busy
                    && !pane.dataView.busy && (!pane.calibration || !!pane.dataView.associatedFolder)
                onClicked: pane.controller.useSelectedPreset()
            }
        }
    }

    FileDialog {
        id: importFile
        title: pane.label("导入预设…")
        nameFilters: ["VWA (*.vwa-preset.json *.json)"]
        onAccepted: {
            if (pane.support.previewPresetImport(selectedFile)) {
                importCopy.checked = false
                importPreview.open()
            }
        }
    }
    FileDialog {
        id: exportFile
        title: pane.label("导出预设…")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "vwa-preset.json"
        nameFilters: ["VWA (*.vwa-preset.json)"]
        onAccepted: pane.support.exportPreset(pane.actionEntry.id, pane.actionEntry.version, selectedFile)
    }
    FolderDialog {
        id: trialFolder
        title: pane.label("关联试音目录…")
        onAccepted: pane.controller.attachTrialFolder(selectedFolder)
    }
    UiDialog {
        id: importPreview
        theme: pane.theme
        title: pane.label("确认附加导入")
        width: Math.round(430 * pane.unit)
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: ColumnLayout {
            Caption {
                Layout.fillWidth: true
                text: (pane.preview.name || "") + " · " + pane.typeLabel(pane.preview.type)
                    + "\n" + pane.label("导入会添加到本机预设库。")
            }
            UiCheckBox {
                id: importCopy
                theme: pane.theme
                text: pane.label("创建独立副本")
                visible: !pane.preview.copy_required
            }
        }
        onAccepted: pane.support.importPreset(!!pane.preview.copy_required || importCopy.checked)
        onRejected: pane.support.cancelPresetImport()
    }
    UiDialog {
        id: aliasDialog
        theme: pane.theme
        title: pane.label("重命名…")
        width: Math.round(400 * pane.unit)
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: TextField { id: aliasName; maximumLength: 80; placeholderText: pane.label("本机别名"); color: pane.theme.text }
        onAccepted: pane.support.renamePreset(pane.actionEntry.id, pane.actionEntry.version, aliasName.text.trim())
    }
    UiDialog {
        id: removeDialog
        theme: pane.theme
        title: pane.label("移除预设")
        width: Math.round(400 * pane.unit)
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: Caption { text: pane.actionEntry.name || "" }
        onAccepted: pane.support.removePreset(pane.actionEntry.id, pane.actionEntry.version)
    }
}
