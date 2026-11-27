import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    required property var page
    Layout.fillWidth: true
    spacing: page.editorGap

    ColumnLayout {
        Layout.fillWidth: true
        visible: page.inspectorTab === "note"
        spacing: page.editorGap
        TuningControls.PanelLabel {
            theme: page.theme
            text: page.vwaTr("音符参数")
        }
        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            text: page.selectedNoteCount > 1
                ? page.vwaTr("已选 {count} 个音符").replace("{count}", page.selectedNoteCount)
                : page.currentNote.id ? page.textOf(page.currentNote.lyric)
                    : page.vwaTr("单击卷帘中的音符以调整参数。")
        }
        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            visible: page.selectedNoteCount > 1 && !!page.view.noteTool
            text: page.vwaTr("拖动所选音符可一起移动。上下键升降半音，Ctrl 加上下键升降八度。")
        }
        Repeater {
            model: [{key:"pitch", label:page.vwaTr("音高")},
                    {key:"start", label:page.vwaTr("开始")},
                    {key:"end", label:page.vwaTr("结束")}]
            RowLayout {
                required property var modelData
                readonly property bool pitchField: modelData.key === "pitch"
                readonly property string reason: page.textOf(page.view.scoreEditReason
                    || (pitchField ? page.currentNote.pitchReason : page.currentNote.timingReason))
                Layout.fillWidth: true
                visible: page.selectedNoteCount === 1
                TuningControls.HelperText {
                    theme: page.theme
                    Layout.minimumWidth: Math.round(38 * page.scale)
                    text: modelData.label
                }
                UiNumberField {
                    objectName: "tuningNoteField_" + modelData.key
                    Layout.fillWidth: true
                    theme: page.theme
                    fieldLabel: modelData.label
                    unit: pitchField ? "MIDI" : "s"
                    currentValue: page.numberOr(page.currentNote[modelData.key], 0)
                    minimum: pitchField ? 0 : Math.floor(1000 * page.numberOr(
                        modelData.key === "start" ? page.currentNote.timeMin : page.currentNote.start, 0)) / 1000
                    maximum: pitchField ? 127 : Math.ceil(1000 * page.numberOr(page.currentNote.timeMax, 0)) / 1000
                    decimals: pitchField ? 0 : 3
                    arrowStep: pitchField ? 1 : .01
                    scopeKey: page.noteScopeVersion
                    enabled: !page.busy && !!page.view.noteTool && !reason
                    onCommitted: (scope, value) => page.editNoteField(modelData.key, value)
                }
                TuningControls.HelperText {
                    theme: page.theme
                    visible: !!reason
                    text: "ⓘ"
                    Accessible.name: reason
                    ToolTip.visible: lockReasonHover.hovered
                    ToolTip.text: reason
                    HoverHandler { id: lockReasonHover }
                }
            }
        }
        TuningControls.HelperText {
            objectName: "tuningNotePronunciation"
            Layout.fillWidth: true
            theme: page.theme
            visible: page.selectedNoteCount === 1 && !!page.currentNote.pronunciation
            text: page.vwaTr("发音") + "  " + page.textOf(page.currentNote.pronunciation)
            textFormat: Text.PlainText
        }
        TuningControls.PanelLabel {
            Layout.fillWidth: true
            theme: page.theme
            visible: nativeNoteFields.visible
            text: page.vwaTr("原生参数")
        }
        GridLayout {
            id: nativeNoteFields
            Layout.fillWidth: true
            visible: page.selectedNoteCount === 1 && page.noteDetails.length > 0
            columns: 2
            columnSpacing: page.editorGap
            rowSpacing: page.editorGap
            Repeater {
                model: nativeNoteFields.visible ? page.noteDetails.length : 0
                ColumnLayout {
                    id: detailColumn
                    required property int index
                    readonly property var detail: page.noteDetails[index] || ({})
                    readonly property string title: page.vwaTr(page.textOf(detail.label))
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    spacing: Math.round(3 * page.scale)
                    TuningControls.HelperText {
                        Layout.fillWidth: true
                        theme: page.theme
                        text: detailColumn.title
                    }
                    UiNumberField {
                        objectName: "tuningNoteDetail_" + page.textOf(detail.key)
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        theme: page.theme
                        fieldLabel: detailColumn.title
                        unit: page.textOf(detail.unit)
                        currentValue: page.numberOr(detail.value, 0)
                        minimum: page.numberOr(detail.minimum, 0)
                        maximum: page.numberOr(detail.maximum, 0)
                        decimals: page.numberOr(detail.decimals, 0)
                        arrowStep: 1
                        scopeKey: page.noteScopeVersion
                        enabled: !page.busy && !!page.view.noteTool && !page.view.scoreEditReason
                        onCommitted: (scope, value) => {
                            if (scope === page.noteScopeVersion && page.selectedNoteCount === 1)
                                page.controller.editNoteDetail(page.currentNote.id, detail.key, value)
                        }
                    }
                }
            }
        }
        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            visible: !!page.view.scoreEditReason || (!!page.currentNote.id && !page.view.noteTool)
            text: page.textOf(page.view.scoreEditReason) || page.vwaTr("切到「改音符」后可调整参数。")
        }
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: page.inspectorTab === "style"
        enabled: !page.view.formatReadOnly
        spacing: page.editorGap
        RowLayout {
            Layout.fillWidth: true
            TuningControls.PanelLabel {
                Layout.fillWidth: true
                theme: page.theme
                text: page.styleRegion ? page.vwaTr("当前片段") : page.vwaTr("新片段")
            }
            TuningControls.EditorButton {
                objectName: "tuningNewRegionBrush"
                theme: page.theme
                visible: !!page.styleRegion
                text: page.vwaTr("新片段")
                quiet: true
                enabled: !page.busy
                onClicked: page.controller.selectSpan(Number(page.view.start), Number(page.view.end))
            }
        }
        TuningControls.HelperText {
            objectName: "tuningStyleRegionScope"
            Layout.fillWidth: true
            theme: page.theme
            visible: !!page.styleRegion
            text: page.styleRegion ? page.styleRegion.trackName
                + " · " + Number(page.styleRegion.start).toFixed(2)
                + "–" + Number(page.styleRegion.end).toFixed(2) + " s" : ""
        }
        RowLayout {
            Layout.fillWidth: true
            TuningControls.PanelLabel { Layout.fillWidth: true; theme: page.theme; text: page.vwaTr("风格") }
            TuningControls.EditorButton {
                objectName: "tuningStylePitch"
                theme: page.theme
                text: page.vwaTr("附加音高")
                quiet: true
                selected: !!page.styleBrush.pitch
                enabled: !page.busy && page.hasSource
                    && (!page.styleBrush.recipeName || !!page.styleBrush.recipeHasPitch)
                onClicked: page.controller.setBrushValue(page.styleRegionId, "pitch", !selected)
            }
        }
        TuningControls.HelperText {
            objectName: "tuningExternalStyleName"
            Layout.fillWidth: true
            theme: page.theme
            visible: !!page.styleBrush.recipeName
            text: page.vwaTr("外部风格 · ") + page.textOf(page.styleBrush.recipeName)
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            uniformCellWidths: true
            columnSpacing: page.editorGap
            rowSpacing: page.editorGap
            Repeater {
                model: page.styles
                TuningControls.RecipeButton {
                    required property var modelData
                    objectName: "tuningStyle_" + String(modelData.id)
                    Layout.fillWidth: true
                    theme: page.theme
                    text: String(modelData.short || modelData.label)
                    fullLabel: String(modelData.label)
                    tint: modelData.color || page.theme.muted
                    selected: !page.styleBrush.recipeName && String(page.styleBrush.preset) === String(modelData.id)
                    enabled: !page.busy && page.hasSource
                    onClicked: page.controller.setBrushValue(page.styleRegionId, "preset", String(modelData.id))
                }
            }
        }
        TuningControls.StrengthRow {
            label: page.vwaTr("风格强度")
            sliderName: "tuningStyleStrengthSlider"
            valueName: "tuningStyleStrengthPercent"
            field: "strength"
            amount: page.numberOr(page.styleBrush.strength, 0)
        }
        TuningControls.EditorButton {
            objectName: "tuningStyleDetailToggle"
            theme: page.theme
            text: page.vwaTr("风格细节")
            quiet: true
            disclosure: page.showStyleDetails ? 1 : 0
            selected: page.showStyleDetails
            onClicked: page.showStyleDetails = !page.showStyleDetails
        }
        ColumnLayout {
            visible: page.showStyleDetails
            Layout.fillWidth: true
            spacing: 0
            Repeater {
                model: [{key:"onset", label:page.vwaTr("起音")}, {key:"sustain", label:page.vwaTr("持续")},
                        {key:"connection", label:page.vwaTr("衔接")}, {key:"release", label:page.vwaTr("收尾")}]
                TuningControls.StrengthRow {
                    required property var modelData
                    visible: modelData.key !== "connection" || !page.styleBrush.recipeName
                    label: modelData.label
                    sliderName: "tuningStyleDetail_" + modelData.key
                    field: modelData.key
                    amount: page.numberOr((page.styleBrush.detail || {})[modelData.key], 1)
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            TuningControls.PanelLabel { Layout.fillWidth: true; theme: page.theme; text: page.vwaTr("唱腔") }
            TuningControls.EditorButton {
                objectName: "tuningTechniquePitch"
                theme: page.theme
                text: page.vwaTr("倚滑与收腔")
                quiet: true
                selected: !!page.styleBrush.technique_pitch
                enabled: !page.busy && page.hasSource && page.styleBrush.technique !== "none"
                onClicked: page.controller.setBrushValue(page.styleRegionId, "technique_pitch", !selected)
            }
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            uniformCellWidths: true
            columnSpacing: page.editorGap
            rowSpacing: page.editorGap
            Repeater {
                model: page.techniques
                TuningControls.RecipeButton {
                    required property var modelData
                    objectName: "tuningTechnique_" + String(modelData.id)
                    Layout.fillWidth: true
                    theme: page.theme
                    text: String(modelData.short || modelData.label)
                    fullLabel: String(modelData.label)
                    tint: modelData.color || page.theme.muted
                    selected: String(page.styleBrush.technique) === String(modelData.id)
                    enabled: !page.busy && page.hasSource
                    onClicked: page.controller.setBrushValue(page.styleRegionId, "technique", String(modelData.id))
                }
            }
        }
        TuningControls.StrengthRow {
            label: page.vwaTr("唱腔强度")
            sliderName: "tuningTechniqueStrengthSlider"
            field: "technique_strength"
            amount: page.numberOr(page.styleBrush.technique_strength, 0)
            enabled: page.styleBrush.technique !== "none"
        }
        TuningControls.Divider {}
        TuningControls.EditorButton {
            objectName: "addTuningRegion"
            Layout.fillWidth: true
            theme: page.theme
            text: page.vwaTr("应用到时间选区")
            primary: true
            visible: !page.styleRegion
            enabled: page.canAddRegion
            onClicked: page.controller.addRegion()
        }
        UiCheckBox {
            objectName: "tuningAutoApplySelection"
            theme: page.theme
            text: page.vwaTr("框选即应用")
            font.pixelSize: Math.round(12 * page.scale)
            checked: !!page.view.autoApplySelection
            onClicked: page.controller.setAutoApplySelection(checked)
            ToolTip.visible: hovered || visualFocus
            ToolTip.text: page.vwaTr("在「框选风格」中松手即应用。按住 Shift 拖动只选时间。")
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 2
            TuningControls.PanelLabel { Layout.fillWidth: true; theme: page.theme; text: page.vwaTr("片段") }
            TuningControls.EditorButton {
                objectName: "undoTuningRegions"
                theme: page.theme; text: page.vwaTr("撤销"); quiet: true
                enabled: !page.busy && !!page.view.canUndoRegions
                onClicked: page.controller.undoRegions()
            }
            TuningControls.EditorButton {
                objectName: "redoTuningRegions"
                theme: page.theme; text: page.vwaTr("重做"); quiet: true
                enabled: !page.busy && !!page.view.canRedoRegions
                onClicked: page.controller.redoRegions()
            }
            TuningControls.EditorButton {
                objectName: "clearTuningRegions"
                theme: page.theme; text: page.vwaTr("清空…"); quiet: true
                enabled: !page.busy && page.regionCount > 0
                onClicked: page.clearRegionsDialog.open()
            }
        }
        Rectangle {
            Layout.fillWidth: true
            visible: page.regionCount > 0
            Layout.preferredHeight: Math.min(page.regionCount, 3) * Math.round(42 * page.scale) + 2
            radius: Math.round(8 * page.scale)
            color: page.theme.base
            border.width: 1
            border.color: page.theme.line
            clip: true

            ListView {
                id: regionList
                objectName: "tuningRegions"
                anchors.fill: parent
                anchors.margins: 1
                clip: true
                model: page.regions
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: UiScrollBar { theme: page.theme }

                delegate: Rectangle {
                    id: regionRow
                    required property var modelData
                    width: regionList.width
                    height: Math.round(42 * page.scale)
                    color: regionHover.hovered ? page.theme.raised : "transparent"

                    HoverHandler { id: regionHover }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Math.round(9 * page.scale)
                        anchors.rightMargin: Math.round(6 * page.scale)
                        spacing: Math.round(3 * page.scale)

                        Rectangle {
                            implicitWidth: Math.round(8 * page.scale)
                            implicitHeight: Math.round(30 * page.scale)
                            radius: width / 2
                            color: page.textOf(regionRow.modelData.color).length > 0
                                ? regionRow.modelData.color
                                : page.styleColor(regionRow.modelData.style)
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Text {
                                Layout.fillWidth: true
                                text: regionRow.modelData.label
                                    || page.styleLabel(regionRow.modelData.style)
                                color: page.theme.text
                                font.pixelSize: Math.round(11 * page.scale)
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                            Text {
                                Layout.fillWidth: true
                                text: page.trackName(regionRow.modelData.trackId)
                                    + " · " + Number(regionRow.modelData.start).toFixed(2)
                                    + "–" + Number(regionRow.modelData.end).toFixed(2) + " s"
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.scale)
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }

                        TuningControls.EditorButton {
                            theme: page.theme
                            glyph: "edit"
                            text: page.vwaTr("编辑片段")
                            quiet: true
                            enabled: !page.busy
                            onClicked: page.controller.editRegion(String(regionRow.modelData.id))
                        }
                        TuningControls.EditorButton {
                            objectName: "updateTuningRegion_" + String(regionRow.modelData.id)
                            theme: page.theme
                            glyph: "check"
                            text: page.vwaTr("更新片段")
                            quiet: true
                            enabled: !page.busy && page.hasRange && !page.styleRegion
                                && page.textOf(regionRow.modelData.id).length > 0
                            onClicked: page.controller.updateRegion(
                                String(regionRow.modelData.id))
                            Accessible.name: page.vwaTr("用当前选区和风格更新此逐轨片段")
                            ToolTip.text: page.vwaTr("轨道按此片段固定；范围和风格取当前选区")
                        }
                        TuningControls.EditorButton {
                            theme: page.theme
                            text: page.vwaTr("移除")
                            quiet: true
                            enabled: !page.busy
                                && page.textOf(regionRow.modelData.id).length > 0
                            onClicked: page.controller.removeRegion(
                                String(regionRow.modelData.id))
                        }
                    }

                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 1
                        color: page.theme.line
                        opacity: 0.7
                    }
                }
            }
        }

    }
}
