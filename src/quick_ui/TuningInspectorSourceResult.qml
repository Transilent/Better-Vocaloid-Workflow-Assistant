import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    required property var page
    Layout.fillWidth: true
    spacing: page.editorGap

    ColumnLayout {
        Layout.fillWidth: true
        visible: page.inspectorTab === "source"
        spacing: page.editorGap
        TuningControls.PanelLabel {
            theme: page.theme
            text: page.vwaTr("表达来源与调音选项")
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Math.round(5 * page.scale)
            rowSpacing: Math.round(5 * page.scale)

            TuningControls.EditorButton {
                objectName: "expressionPreserve"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("保留原表现")
                selected: page.textOf(page.view.expressionMode) === "preserve"
                enabled: !page.busy && page.hasSource
                onClicked: page.controller.setExpressionMode("preserve")
            }
            TuningControls.EditorButton {
                objectName: "expressionReference"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("参考音频")
                selected: page.textOf(page.view.expressionMode) === "reference"
                enabled: !page.busy && page.hasSource && !page.view.formatReadOnly
                    && !page.expressionSourceReason("reference")
                onClicked: page.controller.setExpressionMode("reference")
            }
            TuningControls.EditorButton {
                objectName: "expressionCalibrated"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("校准 + 参考")
                selected: page.textOf(page.view.expressionMode) === "calibrated"
                enabled: !page.busy && page.hasSource && !page.view.formatReadOnly
                    && !page.expressionSourceReason("calibrated")
                onClicked: page.controller.setExpressionMode("calibrated")
            }
            TuningControls.EditorButton {
                objectName: "expressionRetained"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("沿用原参数")
                selected: page.textOf(page.view.expressionMode) === "retained"
                enabled: !page.busy && page.hasSource && !page.view.formatReadOnly
                    && !page.expressionSourceReason("retained")
                onClicked: page.controller.setExpressionMode("retained")
            }
        }

        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            readonly property string currentMode: page.textOf(page.view.expressionMode)
            text: page.expressionSourceReason(currentMode)
                || (currentMode === "reference"
                    ? page.vwaTr("选择与工程时间对齐的分轨干声，每条目标轨对应一份。")
                    : currentMode === "retained"
                        ? page.vwaTr("选择同格式、同音符时间的工程。源轨索引从首条演唱轨的 0 开始。")
                        : page.expressionSourceReason("reference")
                            || page.expressionSourceReason("calibrated"))
            visible: page.hasSource && text.length > 0
        }

        Text {
            Layout.fillWidth: true
            text: page.vwaTr("焦点轨：") + (page.activeId.length > 0
                ? page.trackName(page.activeId) : page.vwaTr("未选择"))
            color: page.theme.text
            font.pixelSize: Math.round(11 * page.scale)
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        TuningControls.HelperText {
            Layout.fillWidth: true
            visible: text.length > 0
            theme: page.theme
            text: page.textOf(page.view.trackReadOnlyReason)
        }

        GridLayout {
            Layout.fillWidth: true
            columns: page.compact ? 1 : 2
            columnSpacing: Math.round(6 * page.scale)
            rowSpacing: Math.round(6 * page.scale)

            TuningControls.HelperText {
                Layout.alignment: Qt.AlignVCenter
                theme: page.theme
                text: page.vwaTr("参考音频")
                visible: page.expressionSourceAllows(["reference", "calibrated"])
            }
            TuningControls.EditorButton {
                objectName: "bindReferenceAudio"
                visible: page.expressionSourceAllows(["reference", "calibrated"])
                Layout.fillWidth: true
                theme: page.theme
                text: page.textOf(page.bindingField("reference", "")).length > 0
                    ? page.vwaTr("更换参考…") : page.vwaTr("选择参考…")
                enabled: !page.busy && page.activeId.length > 0
                    && page.activeTrackData !== null
                    && page.activeTrackData.editable !== false
                    && page.expressionSourceAllows(["reference", "calibrated"])
                onClicked: {
                    page.referenceDialog.trackId = page.activeId
                    page.referenceDialog.open()
                }
            }
            Text {
                Layout.fillWidth: true
                Layout.columnSpan: page.compact ? 1 : 2
                visible: page.textOf(page.bindingField("reference", "")).length > 0
                text: page.fileName(page.bindingField("reference", ""))
                color: page.theme.muted
                font.pixelSize: Math.round(10 * page.scale)
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
            }
            TuningControls.HelperText {
                theme: page.theme
                visible: page.textOf(page.bindingField("reference", "")).length > 0
                text: page.vwaTr("参考声部")
            }
            UiComboBox {
                theme: page.theme
                Layout.fillWidth: true
                visible: page.textOf(page.bindingField("reference", "")).length > 0
                model: [{label: page.vwaTr("主唱"), value: "lead"}, {label: page.vwaTr("和声"), value: "backing"},
                        {label: page.vwaTr("合并人声"), value: "vocals"}]
                currentIndex: indexOfValue(page.bindingField("referenceVoice", ""))
                enabled: !page.busy
                onActivated: page.controller.setReferenceVoice(page.activeId, currentValue)
            }

            TuningControls.HelperText {
                Layout.alignment: Qt.AlignVCenter
                theme: page.theme
                text: page.vwaTr("校准配置")
                visible: page.view.expressionMode === "calibrated"
            }
            TuningControls.EditorButton {
                objectName: "bindCalibrationProfile"
                visible: page.view.expressionMode === "calibrated"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("选择 JSON…")
                enabled: !page.busy && page.hasSource
                    && page.textOf(page.view.expressionMode) === "calibrated"
                onClicked: page.calibrationDialog.open()
            }
            TuningControls.EditorButton {
                objectName: "tuningChooseWorkshopCalibration"
                visible: page.view.expressionMode === "calibrated"
                Layout.fillWidth: true
                Layout.columnSpan: page.compact ? 1 : 2
                theme: page.theme
                text: page.vwaTr("从工坊预设库选用")
                enabled: !page.busy
                onClicked: page.workshopRequested("presets")
            }
            Text {
                Layout.fillWidth: true
                Layout.columnSpan: page.compact ? 1 : 2
                visible: page.textOf(page.view.calibration).length > 0
                text: page.controller.calibrationLabel(page.textOf(page.view.calibration))
                color: page.theme.muted
                font.pixelSize: Math.round(10 * page.scale)
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
            }

            TuningControls.HelperText {
                Layout.alignment: Qt.AlignVCenter
                theme: page.theme
                text: page.vwaTr("原参数工程")
                visible: page.view.expressionMode === "retained"
            }
            RowLayout {
                Layout.fillWidth: true
                visible: page.view.expressionMode === "retained"
                spacing: Math.round(5 * page.scale)
                SpinBox {
                    id: retainedIndexInput
                    objectName: "retainedSourceTrackIndex"
                    Layout.preferredWidth: Math.round(96 * page.scale)
                    from: 0
                    to: 255
                    value: page.retainedIndexDraft
                    editable: true
                    enabled: !page.busy && page.activeId.length > 0
                    Accessible.name: page.vwaTr("原工程源轨索引")
                    onValueModified: page.retainedIndexDraft = value
                }
                TuningControls.EditorButton {
                    objectName: "bindRetainedProject"
                    Layout.fillWidth: true
                    theme: page.theme
                    text: page.textOf(page.bindingField("retained", "")).length > 0
                        ? page.vwaTr("更换原工程…") : page.vwaTr("选择原工程…")
                    enabled: !page.busy && page.activeId.length > 0
                        && page.activeTrackData !== null
                        && page.activeTrackData.editable !== false
                        && page.textOf(page.view.expressionMode) === "retained"
                    onClicked: {
                        page.retainedDialog.trackId = page.activeId
                        page.retainedDialog.sourceTrackIndex = page.retainedIndexDraft
                        page.retainedDialog.open()
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                Layout.columnSpan: page.compact ? 1 : 2
                visible: page.textOf(page.bindingField("retained", "")).length > 0
                text: page.fileName(page.bindingField("retained", ""))
                    + page.vwaTr("  ·  源轨索引 ") + page.retainedIndexDraft
                color: page.theme.muted
                font.pixelSize: Math.round(10 * page.scale)
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
            }
        }

        GridLayout {
            Layout.fillWidth: true
            visible: page.view.expressionMode === "reference"
            columns: 2
            columnSpacing: Math.round(5 * page.scale)
            rowSpacing: Math.round(5 * page.scale)

            TuningControls.EditorButton {
                objectName: "optionReferenceDynamics"
                visible: selected || (page.view.referenceControls || []).indexOf("dynamics") >= 0
                Layout.fillWidth: true
                theme: page.theme
                text: page.boolOptionText("reference_dynamics", page.vwaTr("参考力度"))
                selected: page.boolOr(page.optionValue("reference_dynamics", false), false)
                enabled: !page.busy
                onClicked: page.toggleOption("reference_dynamics")
            }
            TuningControls.EditorButton {
                objectName: "optionReferenceTension"
                visible: selected || (page.view.referenceControls || []).indexOf("tension") >= 0
                Layout.fillWidth: true
                theme: page.theme
                text: page.boolOptionText("reference_tension", page.vwaTr("参考张力"))
                selected: page.boolOr(page.optionValue("reference_tension", false), false)
                enabled: !page.busy
                onClicked: page.toggleOption("reference_tension")
            }
            TuningControls.EditorButton {
                objectName: "optionReferenceBreathiness"
                visible: selected || (page.view.referenceControls || []).indexOf("breathiness") >= 0
                Layout.fillWidth: true
                theme: page.theme
                text: page.boolOptionText("reference_breathiness", page.vwaTr("参考气声"))
                selected: page.boolOr(page.optionValue("reference_breathiness", false), false)
                enabled: !page.busy
                onClicked: page.toggleOption("reference_breathiness")
            }
            TuningControls.EditorButton {
                objectName: "optionReferenceVoicing"
                visible: selected || (page.view.referenceControls || []).indexOf("voicing") >= 0
                Layout.fillWidth: true
                theme: page.theme
                text: page.boolOptionText("reference_voicing", page.vwaTr("参考发声"))
                selected: page.boolOr(page.optionValue("reference_voicing", false), false)
                enabled: !page.busy
                onClicked: page.toggleOption("reference_voicing")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: page.expressionSourceAllows(["reference", "calibrated"])
            spacing: Math.round(5 * page.scale)
            TuningControls.HelperText {
                Layout.alignment: Qt.AlignVCenter
                theme: page.theme
                text: page.vwaTr("动态强度")
            }
            TuningControls.PercentSlider {
                Layout.fillWidth: true
                objectName: "tuningDynamicsStrengthSlider"
                strength: page.numberOr(page.optionValue("dynamics_strength", 0), 0)
                enabled: !page.busy && page.optionKnown("dynamics_strength")
                Accessible.name: page.vwaTr("动态强度")
                onMoved: page.controller.setTuningOption("dynamics_strength", value / 100)
            }
            TuningControls.HelperText {
                theme: page.theme
                Layout.minimumWidth: Math.round(34 * page.scale)
                horizontalAlignment: Text.AlignRight
                text: page.optionKnown("dynamics_strength")
                    ? page.strengthText(page.optionValue("dynamics_strength", 0)) : "—"
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: !page.nativeStyleProject
            spacing: Math.round(5 * page.scale)
            TuningControls.HelperText {
                Layout.alignment: Qt.AlignVCenter
                theme: page.theme
                text: page.vwaTr("表达采样")
            }
            TuningControls.EditorButton {
                objectName: "tuningDensityDense"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("加密采样")
                selected: page.optionKnown("expression_density")
                    && String(page.optionValue("expression_density", "")) === "dense"
                enabled: !page.busy
                onClicked: page.controller.setTuningOption("expression_density", "dense")
            }
            TuningControls.EditorButton {
                objectName: "tuningDensityOriginal"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("原始采样")
                selected: page.optionKnown("expression_density")
                    && String(page.optionValue("expression_density", "")) === "original"
                enabled: !page.busy
                onClicked: page.controller.setTuningOption("expression_density", "original")
            }
        }

        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            text: page.vwaTr("这里只编辑工程与参数；声库合成、真实听感和外部编辑器中的手动渲染保持独立。")
        }
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: page.inspectorTab === "presets"
        spacing: page.editorGap
        TuningControls.PanelLabel {
            theme: page.theme
            text: page.vwaTr("调音预设与风格")
        }


        UiComboBox {
            id: presetPicker
            objectName: "tuningPresetPicker"
            Layout.fillWidth: true
            theme: page.theme
            model: page.presets
            textRole: "label"
            valueRole: "key"
            currentIndex: page.presetKeyIndex(page.selectedPresetKey)
            enabled: !page.busy && page.presets.length > 0
            Accessible.name: page.vwaTr("调音预设与风格")
            onActivated: function(index) {
                if (index >= 0 && index < page.presets.length) {
                    page.selectedPresetKey = String(page.presets[index].key)
                    page.controller.dismissPresetReview()
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            TuningControls.EditorButton {
                objectName: "previewTuningPreset"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("查看差异")
                enabled: page.hasSource && !page.busy && page.selectedPresetKey.length > 0
                onClicked: page.controller.previewPreset(page.selectedPresetKey)
            }
            TuningControls.EditorButton {
                objectName: "unbindTuningPreset"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("解除绑定")
                enabled: !page.busy && page.textOf(page.view.presetName).length > 0
                onClicked: page.controller.unbindPreset()
            }
        }
        ColumnLayout {
            readonly property var review: page.view.presetReview || ({})
            Layout.fillWidth: true
            visible: !!review.name
            spacing: page.editorGap
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: parent.review.name + (parent.review.isRecipe ? ""
                      : parent.review.brushOnly ? page.vwaTr(" · 仅下一片段") : page.vwaTr(" · 绑定当前工程"))
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!parent.review.isRecipe
                text: page.textOf(parent.review.channels) + " · " + page.vwaTr("默认强度")
                      + " " + page.strengthText(parent.review.strength)
            }
            Repeater {
                model: parent.review.changes || []
                TuningControls.HelperText {
                    required property var modelData
                    Layout.fillWidth: true
                    theme: page.theme
                    text: page.presetFieldName(modelData.field) + "："
                        + page.presetValue(modelData.field, modelData.before) + " → "
                        + page.presetValue(modelData.field, modelData.after)
                }
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: (parent.review.missing || []).length > 0
                text: (parent.review.missing || []).join("\n")
            }
            TuningControls.EditorButton {
                objectName: "applyReviewedTuningPreset"
                Layout.fillWidth: true
                theme: page.theme
                text: parent.review.isRecipe ? page.vwaTr("选用风格")
                    : parent.review.brushOnly ? page.vwaTr("应用到下一片段") : page.vwaTr("确认绑定预设")
                enabled: !page.busy
                primary: true
                onClicked: {
                    const isRecipe = parent.review.isRecipe
                    page.controller.confirmPreset()
                    if (isRecipe)
                        page.inspectorTab = "style"
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: page.textOf(page.view.presetName).length > 0
                ? page.vwaTr("当前工程绑定：") + page.view.presetName
                : page.vwaTr("当前工程尚未绑定预设。")
            color: page.theme.muted
            font.pixelSize: Math.round(11 * page.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        Text {
            Layout.fillWidth: true
            visible: page.textOf(page.view.presetPending).length > 0
            text: page.textOf(page.view.presetPending)
            color: page.theme.attention
            font.pixelSize: Math.round(11 * page.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: Math.round(6 * page.scale)
            rowSpacing: Math.round(6 * page.scale)
            TuningControls.EditorButton {
                objectName: "importTuningPresets"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("导入附加…")
                enabled: !page.busy
                onClicked: page.presetImportDialog.open()
            }
            TuningControls.EditorButton {
                objectName: "exportTuningPreset"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("导出预设…")
                enabled: !page.busy && page.selectedPresetKey.length > 0
                onClicked: page.presetExportDialog.open()
            }
            TuningControls.EditorButton {
                objectName: "saveTuningPreset"
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("保存到本机库")
                enabled: !page.busy && page.hasSource
                onClicked: {
                    page.presetNameField.text = page.textOf(page.view.presetName)
                    page.presetNameDialog.open()
                }
            }
        }
    }
    ColumnLayout {
        Layout.fillWidth: true
        visible: page.inspectorTab === "result"
        spacing: page.editorGap
        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(7 * page.scale)
            TuningControls.PanelLabel {
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("候选与结果")
            }
            Rectangle {
                implicitHeight: Math.round(22 * page.scale)
                implicitWidth: stateText.implicitWidth + Math.round(14 * page.scale)
                radius: height / 2
                color: Qt.rgba(page.statusColor().r, page.statusColor().g,
                    page.statusColor().b, 0.16)
                Text {
                    id: stateText
                    anchors.centerIn: parent
                    text: page.statusLabel()
                    color: page.statusColor()
                    font.pixelSize: Math.round(10 * page.scale)
                    font.weight: Font.DemiBold
                    textFormat: Text.PlainText
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: page.textOf(page.view.previewSummary).length > 0
            text: page.textOf(page.view.previewSummary)
            color: page.theme.text
            font.pixelSize: Math.round(11 * page.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        Text {
            Layout.fillWidth: true
            visible: page.textOf(page.view.message).length > 0
            text: page.textOf(page.view.message)
            color: page.statusColor()
            font.pixelSize: Math.round(11 * page.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }

        RowLayout {
            Layout.fillWidth: true
            visible: page.candidatePresent
            spacing: Math.round(7 * page.scale)
            Text {
                Layout.fillWidth: true
                text: page.candidateText()
                color: page.theme.text
                font.pixelSize: Math.round(11 * page.scale)
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
                HoverHandler { id: candidateHover }
                ToolTip.visible: candidateHover.hovered
                ToolTip.text: typeof page.view.candidate === "string"
                    ? page.view.candidate : page.candidateText()
            }
            Text {
                visible: page.boolOr(page.view.candidateStale, false)
                text: page.vwaTr("已过期")
                color: page.theme.error
                font.pixelSize: Math.round(10 * page.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            TuningControls.EditorButton {
                objectName: "openTuningCandidate"
                theme: page.theme
                visible: !page.ufdataProject
                text: page.vwaTr("打开候选")
                enabled: page.candidateOpenable && !page.view.busy
                    && (!page.acepProject || !!page.view.savedCopy)
                onClicked: page.controller.openCandidate()
            }
        }

        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            visible: page.candidatePresent
            text: {
                if (page.boolOr(page.view.candidateHistorical, false)) {
                    const reason = page.textOf(page.view.message)
                    return page.vwaTr("这是安全保留的历史候选；当前完整回执核验失败，因此不能登记。")
                        + (reason.length > 0 ? "\n" + reason : "")
                }
                const report = page.view.candidateDetails || {}
                const state = {done:page.vwaTr("已处理"), applied:page.vwaTr("已处理"),
                    partial:page.vwaTr("部分处理"), unchanged:page.vwaTr("无变化")}[report.status] || ""
                return state + " · " + (report.tracks || 0) + page.vwaTr(" 轨")
                    + (report.notesPreserved ? page.vwaTr(" · 音符与时值保留") : "")
                    + (report.noteChanges ? " · " + page.vwaTr("已修改 {count} 个音符")
                        .replace("{count}", report.noteChanges) : "")
                    + (report.pitchSpans ? " · " + page.vwaTr("手绘 {count} 段")
                        .replace("{count}", report.pitchSpans) : "")
                    + (report.pitchChanged ? page.vwaTr("\n已修改局部音高") : "")
                    + "\nSHA " + page.shortHash(report.sha256)
            }
        }
        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            visible: !!page.view.formatNotice && (page.acepProject || page.ufdataProject)
            text: page.ufdataProject
                ? page.vwaTr("请在 ACE Studio 的原工程中导入另存的 UFData。")
                : page.textOf(page.view.formatNotice)
        }
        Flow {
            Layout.fillWidth: true
            spacing: page.editorGap
            visible: !!page.view.candidateVerified
            TuningControls.EditorButton {
                objectName: "saveTuningCandidate"
                theme: page.theme
                text: page.acepProject ? page.vwaTr("另存 ACEP…")
                    : page.ufdataProject ? page.vwaTr("另存 UFData…")
                    : page.vprProject ? page.vwaTr("另存 VPR…")
                    : page.vsqxProject ? page.vwaTr("另存 VSQX…")
                    : page.ustxProject ? page.vwaTr("另存 USTX…") : page.vwaTr("另存 SVP…")
                enabled: !page.view.busy
                onClicked: {
                    if (page.acepProject)
                        page.candidateSaveDialog.selectedFile = page.controller.suggestedCandidatePath()
                    page.candidateSaveDialog.open()
                }
            }
            TuningControls.EditorButton {
                objectName: "openTuningReceipt"
                theme: page.theme
                text: page.boolOr(page.view.candidateHistorical, false) ? page.vwaTr("查看原回执") : page.vwaTr("核验回执")
                visible: !!page.view.candidateVerified
                    || page.textOf(page.view.candidateReceipt).length > 0
                enabled: !page.view.busy
                onClicked: page.controller.openResultReceipt()
            }
        }
        TuningControls.HelperText {
            Layout.fillWidth: true
            theme: page.theme
            visible: !!page.view.savedCopy
            text: page.vwaTr("另存：") + page.fileName(page.view.savedCopy)
        }

        TuningControls.HelperText {
            Layout.fillWidth: true
            visible: page.boolOr(page.view.candidateStale, false)
            theme: page.theme
            text: page.vwaTr("候选基线已变化，请重新冻结、生成或明确确认后再继续。")
        }
        TuningControls.EditorButton {
            objectName: "tuningOpenMixdown"
            theme: page.theme
            visible: page.candidatePresent
            text: page.vwaTr("导入已合成的歌声…")
            enabled: !page.busy
            onClicked: page.controller.openMixdown()
        }

        Rectangle {
            Layout.fillWidth: true
            visible: page.boolOr(page.view.candidateNeedsConfirmation, false)
            implicitHeight: confirmLayout.implicitHeight + Math.round(20 * page.scale)
            radius: Math.round(8 * page.scale)
            color: page.theme.raised
            border.width: 1
            border.color: page.boolOr(page.view.candidateConfirmed, false)
                ? page.theme.success : page.theme.attention

            ColumnLayout {
                id: confirmLayout
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(10 * page.scale)
                spacing: Math.round(6 * page.scale)

                Text {
                    Layout.fillWidth: true
                    text: page.boolOr(page.view.candidateConfirmed, false)
                        ? page.vwaTr("候选基线已明确确认。")
                        : page.vwaTr("候选与当前工程基线不同，需要人工确认。")
                    color: page.boolOr(page.view.candidateConfirmed, false)
                        ? page.theme.success : page.theme.attention
                    font.pixelSize: Math.round(11 * page.scale)
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
                RowLayout {
                    Layout.fillWidth: true
                    TuningControls.EditorButton {
                        objectName: "confirmCandidateBaseline"
                        Layout.fillWidth: true
                        theme: page.theme
                        text: page.vwaTr("确认候选基线")
                        primary: !page.boolOr(page.view.candidateConfirmed, false)
                        enabled: !page.busy
                            && !page.boolOr(page.view.candidateConfirmed, false)
                        onClicked: page.controller.confirmCandidate(true)
                    }
                    TuningControls.EditorButton {
                        objectName: "unconfirmCandidateBaseline"
                        Layout.fillWidth: true
                        theme: page.theme
                        text: page.vwaTr("恢复待确认")
                        enabled: !page.busy
                            && page.boolOr(page.view.candidateConfirmed, false)
                        onClicked: page.controller.confirmCandidate(false)
                    }
                }
            }
        }
    }
}
