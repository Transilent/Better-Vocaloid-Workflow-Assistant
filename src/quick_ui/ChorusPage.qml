import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtQuick.Window

FocusScope {
    id: page
    objectName: "chorusPage"
    required property QtObject theme
    required property QtObject controller
    readonly property var view: controller.view
    readonly property var activity: controller.activity
    readonly property bool ready: view.count > 0
    property bool showInspector: true
    property string importRole: "vocal"
    property string pendingProjectAction: ""
    property url pendingProjectUrl
    property bool exportWhole: true

    function t(text) { return quickTranslation.text(text) }
    function stamp(seconds) {
        const ms = Math.round(Math.abs(seconds) * 1000)
        return (seconds < 0 ? "−" : "") + Math.floor(ms / 60000) + ":"
            + String(Math.floor(ms / 1000) % 60).padStart(2, "0")
            + "." + String(ms % 1000).padStart(3, "0")
    }
    function modeName(value) {
        return t(value === "timing" ? "仅对齐" : value === "pitch" ? "仅修音"
                 : value === "processed" ? "处理后" : "原声")
    }
    function pickAudio(role) {
        importRole = role
        audioDialog.open()
    }
    function textFocus(item) {
        for (let cursor = item; cursor; cursor = cursor.parent) {
            if (cursor.inputMethodHints !== undefined || cursor.editText !== undefined)
                return true
        }
        return false
    }
    function projectAction(kind, chosenUrl) {
        if (view.dirty) {
            pendingProjectAction = kind
            pendingProjectUrl = chosenUrl
            discardDialog.open()
        } else if (kind === "new") {
            controller.newProject(false)
        } else {
            controller.openProject(chosenUrl, false)
        }
    }

    Shortcut {
        objectName: "chorusSpace"
        sequence: "Space"
        context: Qt.WindowShortcut
        enabled: page.visible && page.ready && !page.textFocus(page.Window.activeFocusItem)
            && !discardDialog.visible && !exportDialog.visible
        onActivated: page.controller.togglePlay()
    }
    Shortcut {
        sequences: [StandardKey.Undo]
        context: Qt.WindowShortcut
        enabled: page.visible && page.view.undo && !page.textFocus(page.Window.activeFocusItem)
        onActivated: page.controller.undo()
    }
    Shortcut {
        sequences: [StandardKey.Redo]
        context: Qt.WindowShortcut
        enabled: page.visible && page.view.redo && !page.textFocus(page.Window.activeFocusItem)
        onActivated: page.controller.redo()
    }
    Shortcut {
        sequences: [StandardKey.Save]
        context: Qt.WindowShortcut
        enabled: page.visible && page.ready
        onActivated: page.view.path ? page.controller.saveProject("") : saveDialog.open()
    }

    component Caption: Text {
        color: page.theme.muted
        font.pixelSize: Math.round(11 * page.theme.scale)
        textFormat: Text.PlainText
        elide: Text.ElideRight
    }
    component Field: TextField {
        color: page.theme.text
        placeholderTextColor: page.theme.muted
        selectByMouse: true
        font.pixelSize: Math.round(13 * page.theme.scale)
        implicitHeight: Math.round(34 * page.theme.scale)
        background: Rectangle {
            color: page.theme.base
            border.color: parent.activeFocus ? page.theme.accent : page.theme.line
            radius: Math.round(6 * page.theme.scale)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Math.round(16 * page.theme.scale)
        spacing: Math.round(8 * page.theme.scale)

        RowLayout {
            Layout.fillWidth: true
            spacing: 7
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                Field {
                    objectName: "chorusTitle"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(33 * page.theme.scale)
                    text: page.view.title
                    font.pixelSize: Math.round(22 * page.theme.scale)
                    font.weight: Font.DemiBold
                    background: null
                    leftPadding: 0
                    Accessible.name: page.t("歌曲名称")
                    onEditingFinished: page.controller.nameProject(text)
                }
                Caption {
                    Layout.fillWidth: true
                    text: page.t("合唱工作台") + "  ·  " + page.view.count + " " + page.t("轨")
                        + "  ·  " + (page.view.rate / 1000) + " kHz"
                        + (page.view.dirty ? "  ·  " + page.t("未保存") : "")
                }
            }
            UiButton { theme: page.theme; text: page.t("新建"); quiet: true; enabled: !page.view.busy
                onClicked: page.projectAction("new", "") }
            UiButton { theme: page.theme; text: page.t("打开"); quiet: true; enabled: !page.view.busy
                onClicked: openDialog.open() }
            UiButton { theme: page.theme; text: page.t("保存"); enabled: page.ready
                onClicked: page.view.path ? page.controller.saveProject("") : saveDialog.open() }
            UiButton {
                objectName: "chorusExport"
                theme: page.theme
                text: page.t("分轨交付")
                primary: true
                enabled: !page.view.busy && page.view.vocals > 0
                onClicked: exportDialog.open()
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            UiButton { objectName: "chorusImportVocal"; theme: page.theme; text: "+ " + page.t("干声")
                enabled: !page.view.busy; onClicked: page.pickAudio("vocal") }
            UiButton { theme: page.theme; text: "+ " + page.t("导唱")
                enabled: !page.view.busy; onClicked: page.pickAudio("guide") }
            UiButton { theme: page.theme; text: "+ " + page.t("伴奏")
                enabled: !page.view.busy; onClicked: page.pickAudio("backing") }
            UiButton { theme: page.theme; text: "+ " + page.t("乐谱")
                enabled: !page.view.busy; onClicked: scoreDialog.open() }
            Caption {
                width: Math.min(350 * page.theme.scale, implicitWidth)
                height: Math.round(36 * page.theme.scale)
                verticalAlignment: Text.AlignVCenter
                visible: !!page.view.score
                text: page.view.score
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: [{id: "all", label: page.t("全曲全览")}, {id: "phrase", label: page.t("唱段核对")},
                        {id: "pitch", label: page.t("单轨音高")}]
                UiButton {
                    required property var modelData
                    theme: page.theme
                    text: modelData.label
                    selected: page.view.mode === modelData.id
                    quiet: !selected
                    enabled: page.ready
                    onClicked: page.controller.setMode(modelData.id)
                }
            }
            UiButton {
                theme: page.theme
                text: page.t("编辑与处理")
                selected: page.showInspector
                quiet: !selected
                disclosure: page.showInspector ? 1 : 0
                onClicked: page.showInspector = !page.showInspector
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Math.round(10 * page.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 5
                ChorusTimeline {
                    id: timeline
                    theme: page.theme
                    controller: page.controller
                    view: page.view
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: Math.round(136 * page.theme.scale)
                    onTakeSelected: page.forceActiveFocus()
                }
                ChorusPitchLane {
                    theme: page.theme
                    controller: page.controller
                    view: page.view
                    headerWidth: timeline.headerWidth
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(Math.min(200, page.height * .25 / page.theme.scale) * page.theme.scale)
                    visible: page.view.mode === "pitch" && page.ready
                }
                ChorusPairLane {
                    theme: page.theme
                    controller: page.controller
                    view: page.view
                    headerWidth: timeline.headerWidth
                    Layout.fillWidth: true
                    visible: page.view.mode === "phrase" && page.ready
                }
                TuningViewportStrip {
                    theme: page.theme
                    Layout.fillWidth: true
                    timelineStart: page.view.first
                    timelineDuration: Math.max(0, page.view.last - page.view.first)
                    viewportStart: page.view.viewFirst
                    viewportSpan: page.view.viewSpan
                    leadingInset: timeline.headerWidth
                    onViewportRequested: (first, span) => page.controller.viewport(first, span)
                }
            }

            Rectangle {
                id: sidePanel
                objectName: "chorusInspector"
                visible: page.showInspector
                Layout.preferredWidth: Math.round((page.width < 990 ? 221 : 253) * page.theme.scale)
                Layout.fillHeight: true
                color: page.theme.surface
                border.color: page.theme.line
                radius: Math.round(8 * page.theme.scale)

                ScrollView {
                    id: inspector
                    anchors.fill: parent
                    anchors.margins: Math.round(10 * page.theme.scale)
                    contentWidth: availableWidth
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: inspector.availableWidth
                        spacing: 6

                        Text {
                            Layout.fillWidth: true
                            text: page.t("批量处理")
                            color: page.theme.text
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        UiComboBox {
                            id: kind
                            theme: page.theme
                            Layout.fillWidth: true
                            fieldLabel: page.t("处理内容")
                            model: [{label: page.t("对齐与修音"), value: "both"},
                                    {label: page.t("仅对齐"), value: "timing"},
                                    {label: page.t("仅修音"), value: "pitch"}]
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            UiComboBox {
                                id: scope
                                theme: page.theme
                                Layout.fillWidth: true
                                fieldLabel: page.t("处理轨道")
                                model: [{label: page.t("全组"), value: "all"}, {label: page.t("同声部"), value: "part"},
                                        {label: page.t("本轨"), value: "track"}]
                            }
                            UiComboBox {
                                id: range
                                theme: page.theme
                                Layout.fillWidth: true
                                fieldLabel: page.t("处理时间")
                                model: [{label: page.t("整曲"), value: "whole"}, {label: page.t("选段"), value: "selection"}]
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Caption { text: page.t("强度") }
                            Slider {
                                id: strength
                                Layout.fillWidth: true
                                from: 0
                                to: 100
                                value: 75
                                stepSize: 1
                                Accessible.name: page.t("处理强度")
                            }
                            Caption { text: Math.round(strength.value) + "%" }
                        }
                        CheckBox {
                            id: review
                            text: page.t("包含存疑建议")
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        RowLayout {
                            spacing: 5
                            UiButton {
                                objectName: "chorusAnalyze"
                                theme: page.theme
                                text: page.t("生成建议")
                                enabled: page.view.vocals > 0 && !page.view.busy
                                onClicked: page.controller.analyze(kind.currentValue, scope.currentValue,
                                    range.currentValue === "whole", false, review.checked, strength.value)
                            }
                            UiButton {
                                objectName: "chorusBatch"
                                theme: page.theme
                                text: page.t("生成并应用")
                                primary: true
                                enabled: page.view.vocals > 0 && !page.view.busy
                                onClicked: page.controller.analyze(kind.currentValue, scope.currentValue,
                                    range.currentValue === "whole", true, review.checked, strength.value)
                            }
                        }
                        UiButton {
                            theme: page.theme
                            visible: page.view.suggestions > 0
                            text: page.t("应用已生成建议") + " · " + page.view.suggestions
                            enabled: !page.view.busy
                            onClicked: page.controller.applySuggestions(review.checked)
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.line }
                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                text: page.t("待核对") + "  " + page.view.issues.length
                                color: page.view.issues.length > 0 ? page.theme.attention : page.theme.muted
                                font.pixelSize: Math.round(13 * page.theme.scale)
                                font.weight: Font.DemiBold
                            }
                        }
                        ListView {
                            objectName: "chorusIssueList"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(contentHeight, Math.round(118 * page.theme.scale))
                            visible: count > 0
                            clip: true
                            model: page.view.issues
                            spacing: 3
                            delegate: UiButton {
                                required property var modelData
                                width: ListView.view.width
                                theme: page.theme
                                leftAligned: true
                                quiet: true
                                selected: page.view.issue === modelData.id
                                text: "◇  " + page.stamp(modelData.time) + "  " + modelData.track
                                ToolTip.visible: hovered
                                ToolTip.text: page.t(modelData.reason)
                                onClicked: page.controller.focusIssue(modelData.id)
                            }
                        }
                        RowLayout {
                            visible: page.view.issue >= 0
                            UiButton { theme: page.theme; text: page.t("接受本处")
                                enabled: page.view.canAcceptIssue; onClicked: page.controller.acceptIssue() }
                            UiButton { theme: page.theme; text: page.t("保留原样")
                                onClicked: page.controller.keepIssue() }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.line }

                        Text {
                            Layout.fillWidth: true
                            text: page.view.take.name || page.t("音轨属性")
                            color: page.theme.accent
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: !!page.view.take.id
                            spacing: 6
                            Caption { text: page.t("音轨名称与声部") }
                            Field {
                                Layout.fillWidth: true
                                text: page.view.take.name || ""
                                Accessible.name: page.t("音轨名称")
                                onEditingFinished: page.controller.trackName(page.view.selected, text)
                            }
                            Field {
                                objectName: "chorusPart"
                                Layout.fillWidth: true
                                text: page.view.take.part || ""
                                placeholderText: page.t("声部，如上和声")
                                Accessible.name: page.t("声部")
                                onEditingFinished: page.controller.trackPart(page.view.selected, text)
                            }
                            UiComboBox {
                                id: trackRole
                                theme: page.theme
                                Layout.fillWidth: true
                                fieldLabel: page.t("音轨角色")
                                model: [{label: page.t("待处理人声"), value: "vocal"}, {label: page.t("导唱"), value: "guide"},
                                        {label: page.t("伴奏"), value: "backing"}]
                                currentIndex: Math.max(0, indexOfValue(page.view.take.role))
                                onActivated: page.controller.trackRole(page.view.selected, currentValue)
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Caption { text: page.t("起点") + " (s)" }
                                Field {
                                    Layout.fillWidth: true
                                    text: String(page.view.take.offset || 0)
                                    validator: DoubleValidator { notation: DoubleValidator.StandardNotation; locale: "C" }
                                    Accessible.name: page.t("音轨起点")
                                    onEditingFinished: if (acceptableInput) page.controller.offsetTrack(page.view.selected, Number(text))
                                }
                            }
                            Caption { text: page.t("时间参照") }
                            UiComboBox {
                                id: timeRef
                                theme: page.theme
                                Layout.fillWidth: true
                                fieldLabel: page.t("本轨时间参照")
                                model: [{label: page.t("工程默认参照"), value: ""}].concat(
                                    page.controller.tracks.filter(track => track.id !== page.view.selected)
                                    .map(track => ({label: track.name, value: track.id})))
                                currentIndex: Math.max(0, indexOfValue(page.view.take.timeRef || ""))
                                onActivated: page.controller.bindTime(page.view.selected, currentValue)
                            }
                            UiButton {
                                theme: page.theme
                                text: page.view.guide === page.view.selected ? page.t("当前工程参照") : page.t("设为工程时间参照")
                                selected: page.view.guide === page.view.selected
                                enabled: page.view.guide !== page.view.selected
                                onClicked: page.controller.setGuide(page.view.selected)
                            }
                            Caption { text: page.t("音高目标") }
                            UiComboBox {
                                id: pitchRef
                                objectName: "chorusPitchTarget"
                                theme: page.theme
                                Layout.fillWidth: true
                                fieldLabel: page.t("本轨音高目标")
                                model: [{label: page.t("本轨稳定音"), value: ""}].concat(
                                    page.view.scoreLanes.map(lane => ({label: lane.name, value: lane.id})))
                                currentIndex: Math.max(0, indexOfValue(page.view.take.scoreLane || ""))
                                onActivated: page.controller.bindScore(page.view.selected, currentValue)
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: !!page.view.score
                                Caption { text: page.t("乐谱偏移") + " (s)" }
                                Field {
                                    Layout.fillWidth: true
                                    text: String(page.view.scoreOffset)
                                    validator: DoubleValidator { notation: DoubleValidator.StandardNotation; locale: "C" }
                                    Accessible.name: page.t("乐谱偏移")
                                    onEditingFinished: if (acceptableInput) page.controller.offsetScore(Number(text))
                                }
                            }
                            Caption { text: page.t("手调当前选段") }
                            RowLayout {
                                Layout.fillWidth: true
                                Field {
                                    id: move
                                    Layout.fillWidth: true
                                    text: "0"
                                    validator: DoubleValidator { notation: DoubleValidator.StandardNotation; locale: "C" }
                                    Accessible.name: page.t("移动毫秒")
                                }
                                UiButton {
                                    theme: page.theme
                                    text: page.t("移时") + " ms"
                                    enabled: move.acceptableInput
                                    onClicked: page.controller.editSelection("timing", Number(move.text))
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Field {
                                    id: cents
                                    Layout.fillWidth: true
                                    text: "0"
                                    validator: DoubleValidator { notation: DoubleValidator.StandardNotation; locale: "C" }
                                    Accessible.name: page.t("修正音分")
                                }
                                UiButton {
                                    theme: page.theme
                                    text: page.t("音高") + " ¢"
                                    enabled: cents.acceptableInput
                                    onClicked: page.controller.editSelection("pitch", Number(cents.text))
                                }
                            }
                            UiButton {
                                theme: page.theme
                                quiet: true
                                text: page.t("移除音轨")
                                onClicked: page.controller.removeTrack(page.view.selected)
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: page.view.busy || !!page.view.notice || page.view.loading
            Caption {
                Layout.fillWidth: true
                color: page.view.error ? page.theme.error : page.theme.muted
                text: page.view.busy ? (page.activity.detail || page.t("正在处理"))
                    + "  ·  " + (page.activity.elapsed || 0) + " s"
                    : page.view.loading ? (page.activity.detail || page.t("正在准备试听")) + (page.view.activeVariant
                        ? "  ·  " + page.t("当前") + " " + page.modeName(page.view.activeVariant) : "")
                    : page.t(page.view.notice)
                ToolTip.visible: statusHit.hovered
                ToolTip.text: text
                HoverHandler { id: statusHit }
            }
            ProgressBar {
                Layout.preferredWidth: Math.round(94 * page.theme.scale)
                visible: page.view.busy || page.view.loading
                from: 0
                to: Math.max(1, page.activity.total || 0)
                value: page.activity.done || 0
                indeterminate: !page.activity.total
            }
            UiButton { theme: page.theme; text: page.t("取消"); visible: page.view.busy
                onClicked: page.controller.cancel() }
            UiButton { theme: page.theme; text: page.t("打开输出目录"); visible: !!page.view.export
                onClicked: page.controller.openExport() }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            UiButton {
                objectName: "chorusPlay"
                theme: page.theme
                text: page.view.playing ? page.t("暂停") : page.t("播放")
                glyph: page.view.playing ? "pause" : "play"
                primary: true
                enabled: page.ready
                onClicked: page.controller.togglePlay()
            }
            UiButton { theme: page.theme; text: page.t("循环选段"); selected: page.view.loop; enabled: page.ready
                onClicked: page.controller.setLoop(!page.view.loop) }
            Caption { text: page.stamp(page.controller.position); Layout.minimumWidth: Math.round(67 * page.theme.scale) }
            Slider {
                id: seek
                objectName: "chorusSeek"
                property real seekTarget: 0
                Layout.fillWidth: true
                Layout.minimumWidth: Math.round(88 * page.theme.scale)
                from: page.view.first
                to: Math.max(from + .001, page.view.last)
                enabled: page.ready
                Accessible.name: page.t("播放位置")
                Binding { target: seek; property: "value"; value: page.controller.position; when: !seek.pressed }
                onPressedChanged: {
                    if (pressed)
                        seekTarget = value
                    else
                        page.controller.seek(seekTarget)
                }
                onMoved: {
                    seekTarget = value
                    if (!pressed)
                        page.controller.seek(value)
                }
            }
            Repeater {
                model: ["original", "timing", "pitch", "processed"]
                UiButton {
                    required property string modelData
                    theme: page.theme
                    text: page.modeName(modelData)
                    selected: page.view.variant === modelData
                    quiet: !selected
                    enabled: page.ready
                    onClicked: page.controller.setVariant(modelData)
                }
            }
            UiButton { theme: page.theme; text: page.t("撤销"); glyph: "undo"; quiet: true; enabled: page.view.undo
                onClicked: page.controller.undo() }
        }
    }

    FileDialog {
        id: audioDialog
        title: page.t("导入录音")
        fileMode: FileDialog.OpenFiles
        nameFilters: [page.t("音频文件") + " (*.wav *.flac *.aif *.aiff *.mp3 *.ogg *.opus)", page.t("所有文件") + " (*)"]
        onAccepted: page.controller.importAudio(page.importRole, selectedFiles)
    }
    FileDialog {
        id: scoreDialog
        title: page.t("导入乐谱")
        nameFilters: [page.t("乐谱与歌声工程") + " (*.mid *.midi *.svp *.ustx *.vpr *.vsqx *.acep *.ufdata)"]
        onAccepted: page.controller.importScore(selectedFile)
    }
    FileDialog {
        id: openDialog
        title: page.t("打开合唱工程")
        nameFilters: [page.t("合唱工程") + " (*.vwachorus)"]
        onAccepted: page.projectAction("open", selectedFile)
    }
    FileDialog {
        id: saveDialog
        title: page.t("保存合唱工程")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "vwachorus"
        nameFilters: [page.t("合唱工程") + " (*.vwachorus)"]
        onAccepted: page.controller.saveProject(selectedFile)
    }
    FolderDialog {
        id: exportFolder
        title: page.t("选择分轨导出目录")
        onAccepted: page.controller.exportStems(selectedFolder, page.exportWhole)
    }
    Dialog {
        id: discardDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: page.t("合唱工程有未保存修改")
        standardButtons: Dialog.Discard | Dialog.Cancel
        Label { text: page.t("请先保存工程，或放弃当前修改后继续。") }
        onDiscarded: {
            if (page.pendingProjectAction === "new")
                page.controller.newProject(true)
            else
                page.controller.openProject(page.pendingProjectUrl, true)
            close()
        }
    }
    Dialog {
        id: exportDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: page.t("分轨交付")
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            Caption { text: page.modeName(page.view.variant) + "  ·  " + page.t("同起点 WAV") + "  ·  32-bit float" }
            UiComboBox {
                id: exportScope
                Layout.fillWidth: true
                theme: page.theme
                fieldLabel: page.t("导出范围")
                model: [{label: page.t("整曲"), value: "whole"}, {label: page.t("选段"), value: "selection"}]
            }
        }
        onAccepted: {
            page.exportWhole = exportScope.currentValue === "whole"
            exportFolder.open()
        }
    }
}
