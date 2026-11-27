import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: dialogsRoot
    required property var page

    property alias lyricBatchDialog: lyricBatchDialog
    property alias issuesDialog: issuesDialog
    property alias draftRecovery: draftRecovery
    property alias draftFile: draftFile
    property alias candidateSaveDialog: candidateSaveDialog
    property alias clearRegionsDialog: clearRegionsDialog
    property alias projectDialog: projectDialog
    property alias sessionDialog: sessionDialog
    property alias sessionLocationDialog: sessionLocationDialog
    property alias taskPickerDialog: taskPickerDialog
    property alias referenceDialog: referenceDialog
    property alias calibrationDialog: calibrationDialog
    property alias retainedDialog: retainedDialog
    property alias presetImportDialog: presetImportDialog
    property alias presetExportDialog: presetExportDialog
    property alias presetNameDialog: presetNameDialog
    property alias presetNameField: presetNameField

    TuningLyricBatchDialog {
        id: lyricBatchDialog
        theme: page.theme
        controller: page.controller
        sourceKey: page.timelineRoll.sourceKey
        onNoteRequested: note => {
            page.timelineRoll.locateNote(note, true)
            page.timelineRoll.forceActiveFocus(Qt.OtherFocusReason)
        }
    }

    UiDialog {
        id: issuesDialog
        objectName: "tuningIssuesDialog"
        theme: page.theme
        title: page.vwaTr("编辑问题")
        property var review: ({})
        readonly property var rows: review.rows || []
        width: Math.min(parent.width - 40, Math.round(570 * page.scale))
        height: Math.min(parent.height - 48, Math.round((rows.length ? 380 : 175) * page.scale))
        standardButtons: Dialog.Close
        contentItem: ColumnLayout {
            TuningControls.HelperText {
                theme: page.theme
                Layout.fillWidth: true
                text: issuesDialog.rows.length
                    ? page.vwaTr("点击一项，在当前轨道定位音符。")
                    : page.vwaTr("当前轨道未发现重叠音符或受保护发音。")
            }
            ListView {
                objectName: "tuningIssueList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                model: issuesDialog.rows
                clip: true
                spacing: Math.round(5 * page.scale)
                ScrollBar.vertical: UiScrollBar { theme: page.theme }
                delegate: TuningControls.EditorButton {
                    required property var modelData
                    required property int index
                    objectName: "tuningIssueLocate_" + index
                    width: ListView.view.width - Math.round(12 * page.scale)
                    theme: page.theme
                    leftAligned: true
                    text: Number(modelData.start).toFixed(2) + " s  ·  " + modelData.lyric
                        + (modelData.otherLyric ? " / " + modelData.otherLyric : "") + "  ·  " + modelData.reason
                    ToolTip.visible: hovered || visualFocus
                    ToolTip.text: text
                    onClicked: {
                        issuesDialog.close()
                        page.timelineRoll.locateNote(modelData)
                    }
                }
            }
            TuningControls.HelperText {
                theme: page.theme
                Layout.fillWidth: true
                visible: !!issuesDialog.review.truncated
                text: page.vwaTr("仅显示前 120 个问题。")
            }
        }
    }

    UiDialog {
        id: draftRecovery
        objectName: "tuningDraftRecoveryDialog"
        theme: page.theme
        title: page.vwaTr("核对调音草稿")
        width: Math.min(parent.width - 40, Math.round(590 * page.scale))
        closePolicy: Popup.NoAutoClose
        readonly property var review: page.view.draftReview || ({})

        function openReview(url) {
            open()
            page.controller.reviewDraftRecovery(url)
        }
        onClosed: page.controller.dismissDraftReview()
        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.scale)
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.textOf(page.view.message)
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!draftRecovery.review.path
                text: page.vwaTr("共享版本 ") + page.textOf(draftRecovery.review.revision) + "：" + page.textOf(draftRecovery.review.shared)
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!draftRecovery.review.path
                text: page.vwaTr("本地副本：") + page.textOf(draftRecovery.review.local) + "\n"
                    + page.fileName(draftRecovery.review.path)
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!draftRecovery.review.path
                text: page.vwaTr("载入共享草稿会保留本地副本。恢复本地编辑前，也会为当前共享版本另存副本。")
            }
        }
        footer: DialogButtonBox {
            TuningControls.EditorButton {
                theme: page.theme
                text: draftRecovery.review.path ? page.vwaTr("稍后处理") : page.vwaTr("关闭")
                enabled: !page.view.busy
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
                onClicked: draftRecovery.close()
            }
            TuningControls.EditorButton {
                objectName: "loadSharedTuningDraft"
                theme: page.theme
                text: page.vwaTr("载入共享草稿")
                enabled: !page.view.busy && !!draftRecovery.review.path
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: page.controller.resolveDraftRecovery(false)
            }
            TuningControls.EditorButton {
                objectName: "restoreLocalTuningDraft"
                theme: page.theme
                text: page.vwaTr("恢复本地编辑")
                enabled: !page.view.busy && !!draftRecovery.review.path
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: page.controller.resolveDraftRecovery(true)
            }
        }
    }

    FileDialog {
        id: draftFile
        title: page.vwaTr("选择本工程的草稿副本")
        currentFolder: page.view.draftFolder
        nameFilters: [page.vwaTr("草稿冲突副本 (*.conflict-*.json)")]
        onAccepted: draftRecovery.openReview(selectedFile)
    }

    FileDialog {
        id: candidateSaveDialog
        objectName: "tuningCandidateSaveDialog"
        title: page.vwaTr("另存已核验的调音工程")
        fileMode: FileDialog.SaveFile
        defaultSuffix: page.view.projectSuffix || "svp"
        nameFilters: [page.vwaTr(page.projectFilters[defaultSuffix] || page.projectFilters.svp)]
        onAccepted: page.controller.exportCandidate(selectedFile)
    }

    UiDialog {
        id: clearRegionsDialog
        objectName: "tuningClearRegionsDialog"
        theme: page.theme
        title: page.vwaTr("清空风格片段")
        width: Math.min(page.width - 28, Math.round(375 * page.scale))
        standardButtons: Dialog.Cancel | Dialog.Ok
        onAccepted: page.controller.clearRegions()
        contentItem: TuningControls.HelperText {
            theme: page.theme
            text: page.vwaTr("清空此工程的已应用片段。原工程文件不会改写，可用撤销恢复。")
        }
    }

    FileDialog {
        id: projectDialog
        objectName: "tuningProjectDialog"
        title: page.vwaTr("导入调音工程")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.vwaTr("调音工程 (*.svp *.ustx *.vpr *.vsqx *.acep *.ufdata)"),
            page.vwaTr("Synthesizer V Studio 1 / 2 (*.svp)"),
            page.vwaTr("OpenUtau 工程 (*.ustx)"),
            page.vwaTr(page.projectFilters.vpr),
            page.vwaTr(page.projectFilters.vsqx),
            page.vwaTr(page.projectFilters.acep),
            page.vwaTr(page.projectFilters.ufdata),
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: page.controller.chooseProject(selectedFile)
    }

    FileDialog {
        id: sessionDialog
        objectName: "tuningSessionDialog"
        title: page.vwaTr("继续已有调音会话")
        currentFolder: page.view.sessionFolder || ""
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.vwaTr("调音处理记录 (session.json)"),
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: page.controller.resumeSession(selectedFile)
    }

    FolderDialog {
        id: sessionLocationDialog
        objectName: "tuningSessionLocationDialog"
        title: page.vwaTr("选择独立调音会话位置")
        currentFolder: page.view.sessionFolder || ""
        onAccepted: page.controller.setSessionLocation(selectedFolder)
    }

    UiDialog {
        id: taskPickerDialog
        objectName: "tuningTaskPickerDialog"
        theme: page.theme
        title: page.vwaTr("从任务与结果载入工程")
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, Math.round(580 * page.scale))
        closePolicy: Popup.CloseOnEscape
        property string jobId: ""
        property string artifactId: ""
        readonly property var taskRows: page.controller.tuningTasks || []
        readonly property var projectRows: page.controller.tuningTaskProjects || []

        contentItem: ColumnLayout {
            spacing: Math.round(9 * page.scale)
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.textOf(page.controller.tuningTaskMessage)
            }
            UiComboBox {
                objectName: "tuningTaskPickerJob"
                Layout.fillWidth: true
                theme: page.theme
                model: taskPickerDialog.taskRows
                textRole: "title"
                valueRole: "jobId"
                Accessible.name: page.vwaTr("准备任务")
                function syncChoice() {
                    if (count > 0 && taskPickerDialog.jobId.length === 0) {
                        currentIndex = 0
                        taskPickerDialog.jobId = String(taskPickerDialog.taskRows[0].jobId)
                        page.controller.selectTuningTask(taskPickerDialog.jobId)
                    }
                }
                onActivated: function(index) {
                    if (index >= 0 && index < taskPickerDialog.taskRows.length) {
                        taskPickerDialog.jobId = String(taskPickerDialog.taskRows[index].jobId)
                        taskPickerDialog.artifactId = ""
                        page.controller.selectTuningTask(taskPickerDialog.jobId)
                    }
                }
                onCountChanged: syncChoice()
                onModelChanged: syncChoice()
            }
            UiComboBox {
                objectName: "tuningTaskPickerProject"
                Layout.fillWidth: true
                theme: page.theme
                model: taskPickerDialog.projectRows
                textRole: "label"
                valueRole: "artifactId"
                Accessible.name: page.vwaTr("任务中的调音工程")
                function syncChoice() {
                    if (count > 0)
                        currentIndex = 0
                    taskPickerDialog.artifactId = count > 0
                        ? String(taskPickerDialog.projectRows[0].artifactId) : ""
                }
                onActivated: function(index) {
                    if (index >= 0 && index < taskPickerDialog.projectRows.length)
                        taskPickerDialog.artifactId = String(taskPickerDialog.projectRows[index].artifactId)
                }
                onCountChanged: syncChoice()
                onModelChanged: syncChoice()
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("载入前会按任务与结果页面的同一文件清单重新核验路径、SHA 和上游版本；变更后会拒绝载入。")
            }
        }

        footer: RowLayout {
            spacing: Math.round(7 * page.scale)
            Item { Layout.fillWidth: true }
            UiButton {
                theme: page.theme
                text: page.vwaTr("取消")
                quiet: true
                onClicked: taskPickerDialog.close()
            }
            UiButton {
                objectName: "loadTuningTaskProject"
                theme: page.theme
                text: page.vwaTr("核验并载入")
                primary: true
                enabled: !page.busy && taskPickerDialog.jobId.length > 0
                    && taskPickerDialog.artifactId.length > 0
                onClicked: {
                    page.controller.loadTuningTaskArtifact(taskPickerDialog.jobId, taskPickerDialog.artifactId)
                    taskPickerDialog.close()
                }
            }
        }
    }

    FileDialog {
        id: referenceDialog
        objectName: "tuningReferenceDialog"
        property string trackId: ""
        title: page.vwaTr("绑定参考演唱音频")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.vwaTr("音频文件 (*.wav *.flac *.mp3 *.m4a *.ogg *.aac *.opus)"),
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: page.controller.bindReference(trackId, selectedFile)
    }

    FileDialog {
        id: calibrationDialog
        objectName: "tuningCalibrationDialog"
        title: page.vwaTr("绑定校准配置")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.vwaTr("校准配置 (*.json)"),
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: page.controller.bindCalibration(selectedFile)
    }

    FileDialog {
        id: retainedDialog
        objectName: "tuningRetainedDialog"
        property string trackId: ""
        property int sourceTrackIndex: 0
        readonly property string suffix: page.textOf(page.view.projectSuffix) || "svp"
        title: page.vwaTr("绑定原参数工程")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            suffix.toUpperCase() + " (*." + suffix + ")",
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: page.controller.bindRetained(trackId, selectedFile, sourceTrackIndex)
    }

    FileDialog {
        id: presetImportDialog
        objectName: "tuningPresetImportDialog"
        title: page.vwaTr("导入附加调音预设")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.vwaTr("调音预设 (*.json)"),
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: page.controller.importPresets(selectedFile)
    }

    FileDialog {
        id: presetExportDialog
        objectName: "tuningPresetExportDialog"
        title: page.vwaTr("导出调音预设")
        fileMode: FileDialog.SaveFile
        nameFilters: [
            page.vwaTr("调音预设 (*.json)"),
            page.vwaTr("所有文件 (*)")
        ]
        onAccepted: {
            if (page.selectedPresetKey.length > 0)
                page.controller.exportPreset(selectedFile, page.selectedPresetKey)
        }
    }

    UiDialog {
        id: presetNameDialog
        objectName: "tuningPresetNameDialog"
        theme: page.theme
        title: page.vwaTr("保存到本机预设库")
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 420, Math.round(430 * page.scale))
        padding: Math.round(18 * page.scale)

        contentItem: ColumnLayout {
            spacing: Math.round(8 * page.scale)
            Text {
                Layout.fillWidth: true
                text: page.vwaTr("预设名称")
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            TuningControls.ThemedTextField {
                id: presetNameField
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: page.vwaTr("例如：主唱轻推 · 当前工程")
                Accessible.name: page.vwaTr("调音预设名称")
            }
            TuningControls.HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.vwaTr("名称只用于当前明确的保存或导出操作；工程、音频和声库绑定仍保留在本机。")
            }
        }

        footer: RowLayout {
            spacing: Math.round(7 * page.scale)
            Item { Layout.fillWidth: true }
            UiButton {
                theme: page.theme
                text: page.vwaTr("取消")
                quiet: true
                onClicked: presetNameDialog.close()
            }
            UiButton {
                theme: page.theme
                text: page.vwaTr("保存")
                primary: true
                enabled: presetNameField.text.trim().length > 0 && !page.busy
                onClicked: {
                    page.controller.savePreset(presetNameField.text.trim())
                    presetNameDialog.close()
                }
            }
        }
    }
}
