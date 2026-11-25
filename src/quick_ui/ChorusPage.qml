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
    property string importRole: "vocal"
    property string pendingProjectAction: ""
    property url pendingProjectUrl
    property bool exportWhole: true
    property string exportVariant: "original"
    property bool timelineCollapsed: false
    property real timelinePitchHeight: Math.round(118 * theme.scale)
    property real timelinePhraseHeight: Math.round(160 * theme.scale)
    property bool showBatchAdvanced: false
    property bool showTrackAdvanced: false

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
    function runBatch(apply) {
        controller.analyze(kind.currentValue, scope.currentValue, range.currentValue === "whole",
                           apply, review.checked, page.view.timingStrength, ensemble.visible, page.view.pitchStrength)
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

    readonly property bool shortcutReady: page.visible && page.ready
        && !page.textFocus(page.Window.activeFocusItem)
        && !discardDialog.visible && !exportDialog.visible
        && !prepScoreDialog.visible && !mixdownHandoffDialog.visible
    readonly property bool pitchShortcutReady: shortcutReady && page.view.mode === "pitch"
        && page.view.take.role === "vocal"

    Shortcut {
        objectName: "chorusSpace"
        sequence: "Space"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady
        onActivated: page.controller.togglePlay()
    }
    Shortcut {
        sequence: "F"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady
        onActivated: page.controller.setFollowPlayhead(!page.view.followPlayhead)
    }
    Shortcut {
        sequence: "L"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady
        onActivated: page.controller.setLoop(!page.view.loop)
    }
    Shortcut {
        sequence: "Left"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady && !viewportStrip.activeFocus
        onActivated: page.controller.stepPosition(-.25)
    }
    Shortcut {
        sequence: "Right"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady && !viewportStrip.activeFocus
        onActivated: page.controller.stepPosition(.25)
    }
    Shortcut {
        sequence: "Shift+Left"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady && !viewportStrip.activeFocus
        onActivated: page.controller.stepPosition(-1.)
    }
    Shortcut {
        sequence: "Shift+Right"
        context: Qt.WindowShortcut
        enabled: page.shortcutReady && !viewportStrip.activeFocus
        onActivated: page.controller.stepPosition(1.)
    }
    Shortcut {
        sequence: "1"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady
        onActivated: page.controller.setPitchTool("note")
    }
    Shortcut {
        sequence: "2"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && pitchLane.hasNotes
        onActivated: page.controller.setPitchTool("split")
    }
    Shortcut {
        sequence: "3"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && pitchLane.canDraw
        onActivated: page.controller.setPitchTool("draw")
    }
    Shortcut {
        sequence: "Up"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.selectedNoteCount > 0
        onActivated: page.controller.shiftPitchNotes([], 100)
    }
    Shortcut {
        sequence: "Down"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.selectedNoteCount > 0
        onActivated: page.controller.shiftPitchNotes([], -100)
    }
    Shortcut {
        sequence: "Alt+Up"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.selectedNoteCount > 0
        onActivated: page.controller.shiftPitchNotes([], 10)
    }
    Shortcut {
        sequence: "Alt+Down"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.selectedNoteCount > 0
        onActivated: page.controller.shiftPitchNotes([], -10)
    }
    Shortcut {
        sequence: "Q"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.selectedNoteCount > 0
        onActivated: page.controller.snapPitchNotesToSemitone([])
    }
    Shortcut {
        sequence: "S"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.controller.canSplitAtPlayhead
        onActivated: page.controller.splitSelectedNoteAtPlayhead()
    }
    Shortcut {
        sequence: "M"
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.canMergeNotes
        onActivated: page.controller.mergePitchNotes([])
    }
    Shortcut {
        sequences: [StandardKey.Delete, "Backspace"]
        context: Qt.WindowShortcut
        enabled: page.pitchShortcutReady && page.view.selectedNoteCount > 0
        onActivated: page.controller.resetPitchNotes([], false)
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
        implicitHeight: Math.round(32 * page.theme.scale)
        background: Rectangle {
            color: page.theme.base
            border.color: parent.activeFocus ? page.theme.accent : page.theme.line
            radius: Math.round(6 * page.theme.scale)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Math.round(10 * page.theme.scale)
        spacing: Math.round(6 * page.theme.scale)

        // 1. Unified single-row studio header bar
        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(6 * page.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: Math.round(130 * page.theme.scale)
                spacing: Math.round(6 * page.theme.scale)
                Field {
                    objectName: "chorusTitle"
                    Layout.preferredWidth: Math.min(Math.round(170 * page.theme.scale), Math.max(Math.round(88 * page.theme.scale), implicitWidth + 12))
                    Layout.preferredHeight: Math.round(30 * page.theme.scale)
                    text: page.view.title
                    font.pixelSize: Math.round(16 * page.theme.scale)
                    font.weight: Font.DemiBold
                    background: null
                    leftPadding: 0
                    rightPadding: 2
                    Accessible.name: page.t("歌曲名称")
                    onEditingFinished: page.controller.nameProject(text)
                }
                Caption {
                    Layout.fillWidth: true
                    visible: page.width >= 960
                    text: page.t("修音工作台") + " · " + page.view.count + page.t("轨")
                        + (page.view.score ? " · " + page.view.score + (!page.view.scoreConfirmed ? " (" + page.t("草稿") + ")" : "") : "")
                        + (page.view.dirty ? " · " + page.t("未保存") : "")
                }
            }

            // Primary 3-mode workflow switch (segmented pill)
            Rectangle {
                radius: Math.round(7 * page.theme.scale)
                color: page.theme.raised
                border.color: page.theme.line
                Layout.preferredWidth: modeRow.implicitWidth + 4
                Layout.minimumWidth: Layout.preferredWidth
                Layout.preferredHeight: modeRow.implicitHeight + 4
                implicitWidth: Layout.preferredWidth
                implicitHeight: Layout.preferredHeight
                RowLayout {
                    id: modeRow
                    anchors.fill: parent
                    anchors.margins: 2
                    spacing: 2
                    Repeater {
                        model: [{id: "all", label: page.t("全曲"), icon: "waveform"},
                                {id: "phrase", label: page.t("唱段对齐"), icon: "align_tracks"},
                                {id: "pitch", label: page.t("音高精修"), icon: "pitch_curve"}]
                        UiButton {
                            required property var modelData
                            objectName: "chorusMode-" + modelData.id
                            theme: page.theme
                            implicitHeight: Math.round(28 * page.theme.scale)
                            leftPadding: Math.round(9 * page.theme.scale)
                            rightPadding: leftPadding
                            text: modelData.label
                            leadingGlyph: modelData.icon
                            leadingIconSize: 14
                            selected: page.view.mode === modelData.id
                            quiet: !selected
                            enabled: page.ready
                            onClicked: {
                                page.controller.setMode(modelData.id)
                                if (modelData.id === "all")
                                    page.controller.inspectorSection("batch")
                                else if (modelData.id === "phrase")
                                    page.controller.inspectorSection("phrase")
                            }
                        }
                    }
                }
            }

            // Grouped action buttons (Import, Project, Export, Inspector)
            UiButton {
                objectName: "chorusConfirmScore"
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                visible: !!page.view.score && !page.view.scoreConfirmed
                text: page.t("确认乐谱")
                enabled: !page.view.busy
                onClicked: page.controller.confirmScore(true)
            }
            UiButton {
                objectName: "chorusImportMenu"
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                text: "+ " + page.t("导入") + " ▾"
                enabled: !page.view.busy
                onClicked: importMenu.popup()
                Menu {
                    id: importMenu
                    MenuItem {
                        objectName: "chorusImportVocal"
                        text: page.t("导入干声…")
                        onTriggered: page.pickAudio("vocal")
                    }
                    MenuItem {
                        text: page.t("导入导唱…")
                        onTriggered: page.pickAudio("guide")
                    }
                    MenuItem {
                        text: page.t("导入伴奏…")
                        onTriggered: page.pickAudio("backing")
                    }
                    MenuSeparator {}
                    MenuItem {
                        text: page.t("导入乐谱 / 歌声工程…")
                        onTriggered: scoreDialog.open()
                    }
                    MenuItem {
                        objectName: "chorusImportPrepScore"
                        text: page.t("从准备任务导入谱轨…")
                        onTriggered: {
                            page.controller.refreshPreparationScores()
                            prepScoreDialog.open()
                        }
                    }
                }
            }
            UiButton {
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                quiet: true
                text: page.t("工程") + " ▾"
                enabled: !page.view.busy
                onClicked: projectMenu.popup()
                Menu {
                    id: projectMenu
                    MenuItem {
                        text: page.t("新建工程")
                        onTriggered: page.projectAction("new", "")
                    }
                    MenuItem {
                        text: page.t("打开工程…")
                        onTriggered: openDialog.open()
                    }
                    MenuSeparator {}
                    MenuItem {
                        text: page.t("保存工程 (Ctrl+S)")
                        enabled: page.ready
                        onTriggered: page.view.path ? page.controller.saveProject("") : saveDialog.open()
                    }
                    MenuItem {
                        text: page.t("另存为…")
                        enabled: page.ready
                        onTriggered: saveDialog.open()
                    }
                }
            }
            UiButton {
                objectName: "chorusSendToMixdown"
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                text: page.t("送入混音")
                leadingGlyph: "nav_mixdown"
                leadingIconSize: 14
                enabled: !page.view.busy && page.view.vocals > 0
                ToolTip.visible: hovered
                ToolTip.text: page.t("渲染当前修音分轨并直接送入简易混音（最多支持 16 路独立人声）")
                onClicked: mixdownHandoffDialog.open()
            }
            UiButton {
                objectName: "chorusExport"
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                text: page.t("分轨导出")
                leadingGlyph: "export_file"
                leadingIconSize: 14
                primary: true
                enabled: !page.view.busy && page.view.vocals > 0
                onClicked: exportDialog.open()
            }
            UiButton {
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                text: page.t("面板")
                selected: page.view.inspector
                quiet: !selected
                disclosure: page.view.inspector ? 1 : 0
                ToolTip.visible: hovered
                ToolTip.text: page.t("展开或收起右侧编辑与自动处理面板")
                onClicked: page.controller.toggleInspector()
            }
        }

        // 2. Main workspace (Left multi-track + detail editor | Right inspector)
        RowLayout {
            id: workspaceArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Math.round(8 * page.theme.scale)

            ColumnLayout {
                id: canvasColumn
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 4
                readonly property int laneHeaderWidth: Math.round((width < 690 ? 126 : 169) * page.theme.scale)

                ChorusTimeline {
                    id: timeline
                    theme: page.theme
                    controller: page.controller
                    view: page.view
                    customHeaderWidth: canvasColumn.laneHeaderWidth
                    visible: page.view.mode === "all" || !page.timelineCollapsed || !page.ready
                    Layout.fillWidth: true
                    Layout.fillHeight: page.view.mode === "all" || !page.ready
                    Layout.minimumHeight: visible ? Math.round((page.view.mode === "all" ? 140 : 88) * page.theme.scale) : 0
                    Layout.preferredHeight: {
                        if (!visible)
                            return 0
                        if (page.view.mode === "all" || !page.ready)
                            return Math.round(240 * page.theme.scale)
                        const raw = page.view.mode === "pitch" ? page.timelinePitchHeight : page.timelinePhraseHeight
                        return Math.round(Math.max(88 * page.theme.scale,
                            Math.min(Math.max(120, workspaceArea.height * .52), raw)))
                    }
                    onTakeSelected: page.forceActiveFocus()
                    onPhraseSelected: page.controller.inspectorSection("phrase")
                }

                // Draggable divider between multi-track timeline and detail editor
                Item {
                    id: detailSplitter
                    objectName: "chorusDetailSplitter"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.round(6 * page.theme.scale)
                    visible: page.view.mode !== "all" && page.ready && !page.timelineCollapsed
                    Rectangle {
                        anchors.centerIn: parent
                        width: Math.round(42 * page.theme.scale)
                        height: Math.round(2 * page.theme.scale)
                        radius: 1
                        color: splitterDrag.containsMouse || splitterDrag.pressed
                            ? page.theme.accent : page.theme.line
                    }
                    MouseArea {
                        id: splitterDrag
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.SplitVCursor
                        property real startGlobalY: 0
                        property real startHeight: 120
                        onPressed: mouse => {
                            startGlobalY = mapToGlobal(0, mouse.y).y
                            startHeight = timeline.height
                        }
                        onPositionChanged: mouse => {
                            if (!pressed)
                                return
                            const dy = mapToGlobal(0, mouse.y).y - startGlobalY
                            const clamped = Math.max(88 * page.theme.scale,
                                Math.min(Math.max(140, workspaceArea.height * .52), startHeight + dy))
                            if (page.view.mode === "pitch")
                                page.timelinePitchHeight = clamped
                            else
                                page.timelinePhraseHeight = clamped
                        }
                        onDoubleClicked: page.timelineCollapsed = true
                    }
                }

                // Slim single-row contextual toolbar for detail editor (pitch or phrase)
                RowLayout {
                    Layout.fillWidth: true
                    visible: page.view.mode !== "all" && page.ready
                    spacing: Math.round(4 * page.theme.scale)

                    // Quick vocal track switcher (keeps switching instant even when timeline is collapsed)
                    UiComboBox {
                        id: quickTrackPicker
                        theme: page.theme
                        visible: page.controller.tracks.filter(row => row.role === "vocal").length > 1
                        Layout.preferredWidth: Math.round(132 * page.theme.scale)
                        Layout.preferredHeight: Math.round(28 * page.theme.scale)
                        fieldLabel: page.t("当前修音轨")
                        model: page.controller.tracks.filter(row => row.role !== "backing")
                            .map(row => ({label: row.name, value: row.id}))
                        currentIndex: Math.max(0, indexOfValue(page.view.selected))
                        onActivated: page.controller.selectTrack(currentValue)
                    }

                    // Pitch-mode compact single-row controls
                    Rectangle {
                        visible: page.view.mode === "pitch"
                        radius: Math.round(6 * page.theme.scale)
                        color: page.theme.raised
                        border.color: page.theme.line
                        implicitWidth: pitchToolRow.implicitWidth + 4
                        implicitHeight: pitchToolRow.implicitHeight + 4
                        RowLayout {
                            id: pitchToolRow
                            anchors.centerIn: parent
                            spacing: 2
                            UiButton {
                                objectName: "chorusNoteMode"
                                theme: page.theme
                                implicitHeight: Math.round(25 * page.theme.scale)
                                leftPadding: Math.round(8 * page.theme.scale)
                                rightPadding: leftPadding
                                text: page.t("音符")
                                quiet: !selected
                                selected: page.view.pitchTool === "note"
                                ToolTip.visible: hovered
                                ToolTip.text: page.t("拖动调音高 · 双击对齐半音 · 右键菜单 (1)")
                                onClicked: page.controller.setPitchTool("note")
                            }
                            UiButton {
                                objectName: "chorusSplitMode"
                                theme: page.theme
                                implicitHeight: Math.round(25 * page.theme.scale)
                                leftPadding: Math.round(8 * page.theme.scale)
                                rightPadding: leftPadding
                                text: page.t("拆分")
                                quiet: !selected
                                selected: page.view.pitchTool === "split"
                                enabled: pitchLane.hasNotes && page.view.take.role === "vocal"
                                ToolTip.visible: hovered
                                ToolTip.text: page.t("点击音符拆分为两段 (2)")
                                onClicked: page.controller.setPitchTool(page.view.pitchTool === "split" ? "note" : "split")
                            }
                            UiButton {
                                objectName: "chorusDrawMode"
                                theme: page.theme
                                implicitHeight: Math.round(25 * page.theme.scale)
                                leftPadding: Math.round(8 * page.theme.scale)
                                rightPadding: leftPadding
                                text: page.t("画线")
                                quiet: !selected
                                selected: pitchLane.drawing
                                enabled: pitchLane.canDraw
                                ToolTip.visible: hovered
                                ToolTip.text: page.t("手绘连续音高曲线 (3)")
                                onClicked: page.controller.setPitchTool(pitchLane.drawing ? "note" : "draw")
                            }
                        }
                    }

                    UiButton {
                        objectName: "chorusExtractPitch"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        primary: true
                        visible: page.view.mode === "pitch" && !pitchLane.canDraw
                        enabled: page.view.take.role === "vocal" && !page.view.busy
                        text: page.t("提取本轨音高")
                        onClicked: page.controller.analyze("pitch", "track", false, false, false, page.view.pitchStrength)
                    }

                    // Contextual note actions (only shown when notes are selected)
                    UiButton {
                        objectName: "chorusSnapSemitone"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: true
                        visible: page.view.mode === "pitch" && page.view.selectedNoteCount > 0
                        enabled: page.view.take.role === "vocal"
                        text: page.t("对齐半音")
                        ToolTip.visible: hovered
                        ToolTip.text: page.t("将选中音符对齐到最近半音 (Q) · ↑↓键调音高")
                        onClicked: page.controller.snapPitchNotesToSemitone([])
                    }
                    UiButton {
                        objectName: "chorusMergeNotes"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: true
                        visible: page.view.mode === "pitch" && page.view.canMergeNotes
                        enabled: page.view.take.role === "vocal"
                        text: page.t("合并")
                        ToolTip.visible: hovered
                        ToolTip.text: page.t("合并选中的相邻音符 (M)")
                        onClicked: page.controller.mergePitchNotes([])
                    }
                    UiButton {
                        objectName: "chorusLoopNotes"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: true
                        visible: page.view.mode === "pitch" && page.view.selectedNoteCount > 0
                        text: page.t("试听")
                        ToolTip.visible: hovered
                        ToolTip.text: page.t("循环试听选中音符")
                        onClicked: page.controller.loopSelectedNotes()
                    }
                    UiButton {
                        objectName: "chorusResetNotes"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: true
                        visible: page.view.mode === "pitch" && page.view.selectedNoteCount > 0
                        enabled: page.view.take.role === "vocal"
                        text: page.t("还原")
                        ToolTip.visible: hovered
                        ToolTip.text: page.t("还原选中音符 (Del)")
                        onClicked: page.controller.resetPitchNotes([], false)
                    }

                    // Idle pitch controls (when no notes selected)
                    UiButton {
                        objectName: "chorusPitchSnap"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        text: page.t("吸附半音")
                        quiet: !selected
                        selected: page.view.pitchSnap
                        visible: page.view.mode === "pitch" && pitchLane.canDraw && page.view.selectedNoteCount === 0
                        ToolTip.visible: hovered
                        ToolTip.text: page.t("拖动音符吸附到半音，按住 Alt 可细调音分")
                        onClicked: page.controller.setPitchSnap(!page.view.pitchSnap)
                    }
                    UiButton {
                        objectName: "chorusSplitAtPlayhead"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: true
                        visible: page.view.mode === "pitch" && pitchLane.hasNotes
                            && page.controller.canSplitAtPlayhead && page.view.selectedNoteCount === 0
                        enabled: page.view.take.role === "vocal"
                        text: page.t("播放头拆分")
                        ToolTip.visible: hovered
                        ToolTip.text: page.t("在播放头位置拆分音符 (S)")
                        onClicked: page.controller.splitSelectedNoteAtPlayhead()
                    }

                    // Phrase-mode compact inline hints & quick actions
                    Caption {
                        visible: page.view.mode === "phrase"
                        text: (page.view.take.name || page.t("选中音轨"))
                            + (page.view.reference.name ? "  ⇄  " + page.view.reference.name : "")
                            + "  ·  " + page.t("拖动下方波形或菱形锚点对齐")
                    }

                    Item { Layout.fillWidth: true }

                    UiButton {
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: true
                        visible: page.view.mode === "pitch"
                        text: page.t("更多") + " ▾"
                        onClicked: pitchMoreMenu.popup()
                        Menu {
                            id: pitchMoreMenu
                            MenuItem {
                                text: page.view.pitchSnap ? page.t("✓ 拖动吸附半音") : page.t("拖动吸附半音")
                                onTriggered: page.controller.setPitchSnap(!page.view.pitchSnap)
                            }
                            MenuItem {
                                text: page.t("在播放头拆分音符 (S)")
                                enabled: page.controller.canSplitAtPlayhead && page.view.take.role === "vocal"
                                onTriggered: page.controller.splitSelectedNoteAtPlayhead()
                            }
                            MenuItem {
                                text: page.t("保留所选音符原音（锁定）")
                                enabled: page.view.selectedNoteCount > 0 && page.view.take.role === "vocal"
                                onTriggered: page.controller.resetPitchNotes([], true)
                            }
                            MenuSeparator {}
                            MenuItem {
                                text: page.t("还原选区全部修音")
                                enabled: page.view.take.role === "vocal" && page.view.rangeLast > page.view.rangeFirst
                                onTriggered: page.controller.clearSelectionEdits("pitch")
                            }
                            MenuItem {
                                text: page.t("自动适配纵轴音域")
                                onTriggered: pitchLane.resetPitchRange()
                            }
                            MenuSeparator {}
                            MenuItem {
                                enabled: false
                                text: page.t("虚线原声 · 实线渲染")
                                    + (pitchLane.hasScoreNotes ? " · " + page.t("虚框谱音") : "")
                            }
                        }
                    }

                    UiButton {
                        objectName: "chorusFocusDetail"
                        theme: page.theme
                        implicitHeight: Math.round(28 * page.theme.scale)
                        quiet: !selected
                        selected: page.timelineCollapsed
                        text: page.timelineCollapsed ? page.t("展开多轨") : page.t("专注视图")
                        ToolTip.visible: hovered
                        ToolTip.text: page.timelineCollapsed
                            ? page.t("显示上方多轨总览")
                            : page.t("收起上方多轨总览，最大化细节编辑区")
                        onClicked: page.timelineCollapsed = !page.timelineCollapsed
                    }
                }

                // Detail editors now get full remaining vertical space!
                ChorusPitchLane {
                    id: pitchLane
                    theme: page.theme
                    controller: page.controller
                    view: page.view
                    headerWidth: canvasColumn.laneHeaderWidth
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: Math.round(220 * page.theme.scale)
                    visible: page.view.mode === "pitch" && page.ready
                }
                ChorusPairLane {
                    id: pairLane
                    theme: page.theme
                    controller: page.controller
                    view: page.view
                    headerWidth: canvasColumn.laneHeaderWidth
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: Math.round(180 * page.theme.scale)
                    visible: page.view.mode === "phrase" && page.ready
                }
                TuningViewportStrip {
                    id: viewportStrip
                    theme: page.theme
                    Layout.fillWidth: true
                    implicitHeight: Math.round(26 * page.theme.scale)
                    timelineStart: page.view.first
                    timelineDuration: Math.max(0, page.view.last - page.view.first)
                    viewportStart: page.view.viewFirst
                    viewportSpan: page.view.viewSpan
                    playheadPosition: page.controller.position
                    leadingInset: canvasColumn.laneHeaderWidth + 1
                    trailingInset: 1
                    onViewportRequested: (first, span) => page.controller.viewport(first, span)
                }
            }

            // Right Inspector Panel (workflow-ordered: Auto -> Phrase -> Gain -> Track)
            Rectangle {
                id: sidePanel
                objectName: "chorusInspector"
                visible: page.view.inspector
                Layout.preferredWidth: Math.round((page.width < 1040 ? 224 : 252) * page.theme.scale)
                Layout.fillHeight: true
                color: page.theme.surface
                border.color: page.theme.line
                radius: Math.round(8 * page.theme.scale)

                ScrollView {
                    id: inspector
                    anchors.fill: parent
                    anchors.margins: Math.round(9 * page.theme.scale)
                    contentWidth: availableWidth
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    ColumnLayout {
                        width: inspector.availableWidth
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Repeater {
                                model: [{id: "batch", label: page.t("自动")},
                                        {id: "phrase", label: page.t("唱段")},
                                        {id: "gain", label: page.t("力度")},
                                        {id: "track", label: page.t("音轨")}]
                                UiButton {
                                    required property var modelData
                                    objectName: "chorusInspector-" + modelData.id
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    implicitHeight: Math.round(28 * page.theme.scale)
                                    leftPadding: Math.round(4 * page.theme.scale)
                                    rightPadding: leftPadding
                                    text: modelData.label
                                    selected: page.view.inspectorSection === modelData.id
                                    quiet: !selected
                                    onClicked: page.controller.inspectorSection(modelData.id)
                                }
                            }
                        }

                        // Tab 1: Auto batch processing (simplified with progressive disclosure)
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.view.inspectorSection === "batch"
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: page.t("一键对齐与修音")
                                color: page.theme.text
                                font.pixelSize: Math.round(13 * page.theme.scale)
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
                                spacing: 4
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
                                visible: kind.currentValue !== "pitch"
                                Caption { text: page.t("对齐力度") }
                                Slider {
                                    id: timingStrength
                                    objectName: "chorusTimingStrength"
                                    Layout.fillWidth: true
                                    from: 0
                                    to: 100
                                    value: page.view.timingStrength
                                    enabled: !page.view.busy
                                    stepSize: 1
                                    Accessible.name: page.t("对齐力度")
                                    onMoved: page.controller.setBatchStrength("timing", value)
                                }
                                Caption { text: Math.round(timingStrength.value) + "%" }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: kind.currentValue !== "timing"
                                Caption { text: page.t("修音力度") }
                                Slider {
                                    id: pitchStrength
                                    objectName: "chorusPitchStrength"
                                    Layout.fillWidth: true
                                    from: 0
                                    to: 100
                                    value: page.view.pitchStrength
                                    enabled: !page.view.busy
                                    stepSize: 1
                                    Accessible.name: page.t("修音力度")
                                    onMoved: page.controller.setBatchStrength("pitch", value)
                                }
                                Caption { text: Math.round(pitchStrength.value) + "%" }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                UiButton {
                                    objectName: "chorusBatch"
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    text: page.t("生成并应用")
                                    primary: true
                                    enabled: page.view.vocals > 0 && !page.view.busy
                                    onClicked: page.runBatch(true)
                                }
                                UiButton {
                                    objectName: "chorusAnalyze"
                                    theme: page.theme
                                    quiet: true
                                    text: page.t("仅分析")
                                    enabled: page.view.vocals > 0 && !page.view.busy
                                    onClicked: page.runBatch(false)
                                }
                            }
                            UiButton {
                                theme: page.theme
                                objectName: "chorusApplyBatch"
                                Layout.fillWidth: true
                                visible: page.view.suggestions > 0
                                text: page.t("应用已生成建议") + " · " + page.view.suggestions
                                enabled: !page.view.busy
                                onClicked: page.controller.applySuggestions(review.checked)
                            }
                            UiButton {
                                theme: page.theme
                                Layout.fillWidth: true
                                leftAligned: true
                                quiet: true
                                disclosure: page.showBatchAdvanced ? 1 : 0
                                text: page.t("高级设置")
                                onClicked: page.showBatchAdvanced = !page.showBatchAdvanced
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.showBatchAdvanced
                                spacing: 5
                                UiComboBox {
                                    id: ensemble
                                    objectName: "chorusEnsembleMode"
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    visible: range.currentValue === "whole" && scope.currentValue === "all"
                                        && kind.currentValue !== "pitch"
                                    enabled: !page.view.busy
                                    fieldLabel: page.t("整曲对齐方式")
                                    model: [
                                        {label: page.t("按同词唱段 · 日语"), value: "ja"},
                                        {label: page.t("按同词唱段 · 普通话"), value: "zh"},
                                        {label: page.t("按同词唱段 · 英语"), value: "en"},
                                        {label: page.t("按同词唱段 · 粤语"), value: "yue"},
                                        {label: page.t("按指定参照对齐"), value: ""}
                                    ]
                                    currentIndex: Math.max(0, indexOfValue(page.view.ensembleLanguage))
                                    onActivated: page.controller.setEnsembleLanguage(currentValue)
                                }
                                UiComboBox {
                                    id: pitchEngine
                                    objectName: "chorusPitchEngine"
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    visible: kind.currentValue !== "timing"
                                    enabled: !page.view.busy
                                    fieldLabel: page.t("修音引擎")
                                    model: [
                                        {label: page.t("自动 · 优先 Signalsmith"), value: "auto"},
                                        {label: "Signalsmith Stretch", value: "signalsmith"},
                                        {label: page.t("VWA 波形叠加"), value: "vwa"},
                                        {label: "Rubber Band R3", value: "r3"}
                                    ]
                                    currentIndex: Math.max(0, indexOfValue(page.view.pitchEngine))
                                    onActivated: page.controller.setPitchEngine(currentValue)
                                }
                                CheckBox {
                                    id: review
                                    text: page.t("包含存疑建议")
                                    font.pixelSize: Math.round(12 * page.theme.scale)
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.view.issues.length > 0
                                spacing: 5
                                Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.line }
                                Text {
                                    text: page.t("待核对") + "  " + page.view.issues.length
                                    color: page.theme.attention
                                    font.pixelSize: Math.round(12 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                }
                                ListView {
                                    objectName: "chorusIssueList"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: Math.min(contentHeight, Math.round(128 * page.theme.scale))
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
                                    spacing: 4
                                    UiButton { theme: page.theme; Layout.fillWidth: true; text: page.t("接受本处")
                                        enabled: page.view.canAcceptIssue; onClicked: page.controller.acceptIssue() }
                                    UiButton { theme: page.theme; Layout.fillWidth: true; quiet: true; text: page.t("保留原样")
                                        onClicked: page.controller.keepIssue() }
                                }
                            }
                        }

                        // Tab 2: Phrase precision panel
                        ChorusPrecisionPanel {
                            theme: page.theme
                            controller: page.controller
                            view: page.view
                            Layout.fillWidth: true
                            visible: page.view.inspectorSection === "phrase"
                            onImportTake: page.pickAudio("take")
                            onImportReference: referenceDialog.open()
                        }

                        // Tab 3: Syllable gain panel
                        ChorusGainPanel {
                            theme: page.theme
                            controller: page.controller
                            view: page.view
                            Layout.fillWidth: true
                            visible: page.view.inspectorSection === "gain"
                        }

                        // Tab 4: Track properties (with collapsible numeric trim)
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.view.inspectorSection === "track"
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: page.view.take.name || page.t("音轨属性")
                                color: page.theme.accent
                                font.pixelSize: Math.round(13 * page.theme.scale)
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: !!page.view.take.id
                                spacing: 5
                                Field {
                                    Layout.fillWidth: true
                                    text: page.view.take.name || ""
                                    placeholderText: page.t("音轨名称")
                                    Accessible.name: page.t("音轨名称")
                                    onEditingFinished: page.controller.trackName(page.view.selected, text)
                                }
                                Field {
                                    objectName: "chorusPart"
                                    Layout.fillWidth: true
                                    text: page.view.take.part || ""
                                    placeholderText: page.t("声部，如主唱 / 上和声")
                                    Accessible.name: page.t("声部")
                                    onEditingFinished: page.controller.trackPart(page.view.selected, text)
                                }
                                UiComboBox {
                                    id: trackRole
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    fieldLabel: page.t("音轨角色")
                                    model: [{label: page.t("待处理人声"), value: "vocal"}, {label: page.t("导唱"), value: "guide"},
                                            {label: page.t("伴奏"), value: "backing"},
                                            {label: page.t("备选"), value: "take"}]
                                    currentIndex: Math.max(0, indexOfValue(page.view.take.role))
                                    onActivated: page.controller.trackRole(page.view.selected, currentValue)
                                }
                                UiComboBox {
                                    id: timeRef
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    fieldLabel: page.t("本轨时间参照")
                                    model: [{label: page.t("工程默认参照"), value: ""}].concat(
                                        page.controller.tracks.filter(track => track.id !== page.view.selected && track.role !== "backing")
                                        .map(track => ({label: track.name, value: track.id})))
                                    currentIndex: Math.max(0, indexOfValue(page.view.take.timeRef || ""))
                                    onActivated: page.controller.bindTime(page.view.selected, currentValue)
                                }
                                UiButton {
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    quiet: page.view.guide !== page.view.selected
                                    text: page.view.guide === page.view.selected ? page.t("当前工程时间参照") : page.t("设为工程时间参照")
                                    selected: page.view.guide === page.view.selected
                                    enabled: page.view.guide !== page.view.selected
                                        && (page.view.take.role === "vocal" || page.view.take.role === "guide")
                                    onClicked: page.controller.setGuide(page.view.selected)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    visible: !!page.view.score
                                    spacing: 4
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
                                    Caption {
                                        Layout.fillWidth: true
                                        visible: (page.view.scoreRecommendations || []).length > 0
                                        text: {
                                            const rows = page.view.scoreRecommendations || []
                                            if (!rows.length) return ""
                                            return page.t("推荐谱轨") + " · " + rows[0].name + " (" + rows[0].reason + ")"
                                        }
                                    }
                                    UiButton {
                                        objectName: "chorusAutoBindScore"
                                        theme: page.theme
                                        Layout.fillWidth: true
                                        quiet: true
                                        visible: page.view.scoreLanes.length > 0
                                        text: page.t("按音区推荐绑定")
                                        enabled: !page.view.busy
                                        onClicked: page.controller.applyRecommendedScoreLanes()
                                    }
                                }
                                UiButton {
                                    theme: page.theme
                                    Layout.fillWidth: true
                                    leftAligned: true
                                    quiet: true
                                    disclosure: page.showTrackAdvanced ? 1 : 0
                                    text: page.t("起点与数值微调")
                                    onClicked: page.showTrackAdvanced = !page.showTrackAdvanced
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    visible: page.showTrackAdvanced
                                    spacing: 5
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
                                            quiet: true
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
                                            quiet: true
                                            text: page.t("音高") + " ¢"
                                            enabled: cents.acceptableInput
                                            onClicked: page.controller.editSelection("pitch", Number(cents.text))
                                        }
                                    }
                                    UiButton {
                                        theme: page.theme
                                        Layout.fillWidth: true
                                        quiet: true
                                        text: page.t("移除音轨")
                                        onClicked: page.controller.removeTrack(page.view.selected)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // 3. Transient status / progress bar (hidden when idle)
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
            UiButton { theme: page.theme; implicitHeight: Math.round(26 * page.theme.scale); text: page.t("取消"); visible: page.view.busy
                onClicked: page.controller.cancel() }
            UiButton { theme: page.theme; implicitHeight: Math.round(26 * page.theme.scale); text: page.t("打开输出目录"); visible: !!page.view.export
                onClicked: page.controller.openExport() }
        }

        // 4. Unified studio transport bar (Play, Loop, Follow, Scrub, A/B, Undo, Zoom)
        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(5 * page.theme.scale)
            UiButton {
                objectName: "chorusPlay"
                theme: page.theme
                implicitHeight: Math.round(32 * page.theme.scale)
                text: page.view.playing ? page.t("暂停") : page.t("播放")
                glyph: page.view.playing ? "pause" : "play"
                primary: true
                enabled: page.ready
                onClicked: page.controller.togglePlay()
            }
            UiButton {
                theme: page.theme
                implicitHeight: Math.round(32 * page.theme.scale)
                text: page.t("循环")
                selected: page.view.loop
                quiet: !selected
                enabled: page.ready
                ToolTip.visible: hovered
                ToolTip.text: page.t("循环播放当前选段 (L)")
                onClicked: page.controller.setLoop(!page.view.loop)
            }
            UiButton {
                objectName: "chorusFollowPlayhead"
                theme: page.theme
                implicitHeight: Math.round(32 * page.theme.scale)
                text: page.t("跟随")
                selected: !!page.view.followPlayhead
                quiet: !selected
                enabled: page.ready
                ToolTip.visible: hovered
                ToolTip.text: page.t("播放与跳转时自动翻页跟随播放头 (F)")
                onClicked: page.controller.setFollowPlayhead(!page.view.followPlayhead)
            }
            Caption {
                text: page.stamp(page.controller.position)
                Layout.minimumWidth: Math.round(62 * page.theme.scale)
            }
            Slider {
                id: seek
                objectName: "chorusSeek"
                property real seekTarget: 0
                Layout.fillWidth: true
                Layout.minimumWidth: Math.round(70 * page.theme.scale)
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
                    if (!pressed || !page.view.playing)
                        page.controller.seek(value)
                }
            }
            Rectangle {
                radius: Math.round(6 * page.theme.scale)
                color: page.theme.raised
                border.color: page.theme.line
                implicitWidth: variantRow.implicitWidth + 4
                implicitHeight: variantRow.implicitHeight + 4
                RowLayout {
                    id: variantRow
                    anchors.centerIn: parent
                    spacing: 2
                    Repeater {
                        model: ["original", "timing", "pitch", "processed"]
                        UiButton {
                            required property string modelData
                            theme: page.theme
                            implicitHeight: Math.round(26 * page.theme.scale)
                            leftPadding: Math.round(7 * page.theme.scale)
                            rightPadding: leftPadding
                            text: page.modeName(modelData)
                            selected: page.view.variant === modelData
                            quiet: !selected
                            enabled: page.ready
                            onClicked: page.controller.setVariant(modelData)
                        }
                    }
                }
            }
            UiButton {
                theme: page.theme
                implicitHeight: Math.round(32 * page.theme.scale)
                text: page.t("撤销")
                glyph: "undo"
                quiet: true
                enabled: page.view.undo
                ToolTip.visible: hovered
                ToolTip.text: page.t("撤销上一步 (Ctrl+Z)")
                onClicked: page.controller.undo()
            }
            UiButton {
                theme: page.theme
                implicitHeight: Math.round(32 * page.theme.scale)
                leftPadding: Math.round(7 * page.theme.scale)
                rightPadding: leftPadding
                text: "−"
                quiet: true
                enabled: page.ready
                ToolTip.visible: hovered
                ToolTip.text: page.t("缩小视野 (Ctrl+滚轮)")
                onClicked: page.controller.zoomViewportAt(1.35, page.controller.position)
            }
            UiButton {
                theme: page.theme
                implicitHeight: Math.round(32 * page.theme.scale)
                leftPadding: Math.round(7 * page.theme.scale)
                rightPadding: leftPadding
                text: "+"
                quiet: true
                enabled: page.ready
                ToolTip.visible: hovered
                ToolTip.text: page.t("放大视野 (Ctrl+滚轮)")
                onClicked: page.controller.zoomViewportAt(.74, page.controller.position)
            }
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
        id: referenceDialog
        objectName: "chorusReferenceDialog"
        title: page.t("导入本句参照…")
        nameFilters: [page.t("音频文件") + " (*.wav *.flac *.aif *.aiff *.mp3 *.ogg *.opus)", page.t("所有文件") + " (*)"]
        onAccepted: page.controller.importPhraseReference(selectedFile)
    }
    FileDialog {
        id: openDialog
        title: page.t("打开修音工程")
        nameFilters: [page.t("修音工程") + " (*.vwachorus)"]
        onAccepted: page.projectAction("open", selectedFile)
    }
    FileDialog {
        id: saveDialog
        title: page.t("保存修音工程")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "vwachorus"
        nameFilters: [page.t("修音工程") + " (*.vwachorus)"]
        onAccepted: page.controller.saveProject(selectedFile)
    }
    FolderDialog {
        id: exportFolder
        title: page.t("选择分轨导出目录")
        onAccepted: page.controller.exportStems(selectedFolder, page.exportWhole,
            exportMix.checked, page.exportVariant, exportSendMixdown.checked)
    }
    Dialog {
        id: discardDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: page.t("修音工程有未保存修改")
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
        id: prepScoreDialog
        objectName: "chorusPrepScoreDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        width: Math.min(page.width - 40, Math.round(460 * page.theme.scale))
        title: page.t("从准备任务导入谱轨")
        standardButtons: Dialog.Cancel
        ColumnLayout {
            width: parent.width
            spacing: 6
            Caption {
                Layout.fillWidth: true
                text: page.t("准备提取的 MIDI 先作为草稿载入，确认起点与声部后再参与批量修音。")
                wrapMode: Text.Wrap
            }
            Caption {
                Layout.fillWidth: true
                visible: (page.view.prepScores || []).length === 0
                text: page.view.busy ? page.t("正在读取准备任务谱轨…") : page.t("暂无可用的准备任务 MIDI 或工程。")
            }
            ListView {
                objectName: "chorusPrepScoreList"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(Math.round(240 * page.theme.scale), Math.max(40, contentHeight))
                visible: count > 0
                clip: true
                spacing: 4
                model: page.view.prepScores || []
                delegate: UiButton {
                    required property var modelData
                    width: ListView.view.width
                    theme: page.theme
                    leftAligned: true
                    quiet: true
                    enabled: !page.view.busy
                    text: modelData.label + (modelData.draft ? "  [" + page.t("草稿") + "]" : "")
                    onClicked: {
                        prepScoreDialog.close()
                        page.controller.importPreparationScore(modelData.path, modelData.sourceKind, modelData.label)
                    }
                }
            }
        }
    }
    Dialog {
        id: mixdownHandoffDialog
        objectName: "chorusMixdownHandoffDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        width: Math.min(page.width - 40, Math.round(400 * page.theme.scale))
        title: page.t("送入简易混音")
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            width: parent.width
            spacing: 6
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: page.t("将当前同起点人声分轨直接载入简易混音台")
                    + (page.view.hasBacking ? page.t("，并自动带入伴奏轨。") : page.t("（未导入伴奏时可在混音台再选伴奏）。"))
            }
            UiComboBox {
                id: handoffLayout
                Layout.fillWidth: true
                theme: page.theme
                fieldLabel: page.t("送入方式")
                model: [
                    {label: page.t("每条人声独立分轨") + " (" + page.view.vocals + "/" + (page.view.maxMixdownVocals || 16) + ")",
                     value: "stems"},
                    {label: page.t("合并为单轨人声合轨"), value: "merged"}
                ]
            }
            UiComboBox {
                id: handoffVariant
                Layout.fillWidth: true
                theme: page.theme
                fieldLabel: page.t("渲染版本")
                model: ["processed", "timing", "pitch", "original"].map(
                    (value) => ({label: page.modeName(value), value: value}))
            }
            UiComboBox {
                id: handoffScope
                Layout.fillWidth: true
                theme: page.theme
                fieldLabel: page.t("范围")
                model: [{label: page.t("整曲"), value: "whole"}, {label: page.t("选段"), value: "selection"}]
            }
            Caption {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: page.theme.attention
                visible: handoffLayout.currentValue === "stems"
                    && page.view.vocals > (page.view.maxMixdownVocals || 16)
                text: page.t("当前人声轨数超过简易混音单次分轨上限，请改选“合并为单轨人声合轨”。")
            }
        }
        onAboutToShow: {
            const over = page.view.vocals > (page.view.maxMixdownVocals || 16)
            handoffLayout.currentIndex = over ? 1 : 0
            const preferred = page.view.hasEdits ? "processed" : page.view.variant
            handoffVariant.currentIndex = Math.max(0, handoffVariant.indexOfValue(preferred))
            handoffScope.currentIndex = 0
        }
        onAccepted: {
            page.controller.sendToMixdown(
                handoffScope.currentValue === "whole",
                handoffVariant.currentValue,
                handoffLayout.currentValue === "merged")
        }
    }
    Dialog {
        id: exportDialog
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: page.t("分轨导出")
        standardButtons: Dialog.Ok | Dialog.Cancel
        ColumnLayout {
            Caption { text: page.t("同起点 WAV") + "  ·  32-bit float" }
            UiComboBox {
                id: exportMode
                objectName: "chorusExportVariant"
                Layout.fillWidth: true
                theme: page.theme
                fieldLabel: page.t("导出版本")
                model: ["original", "timing", "pitch", "processed"].map(
                    (value) => ({label: page.modeName(value), value: value}))
            }
            UiComboBox {
                id: exportScope
                Layout.fillWidth: true
                theme: page.theme
                fieldLabel: page.t("导出范围")
                model: [{label: page.t("整曲"), value: "whole"}, {label: page.t("选段"), value: "selection"}]
            }
            CheckBox {
                id: exportMix
                objectName: "chorusExportVocalMix"
                text: page.t("同时导出人声合轨")
                checked: true
            }
            CheckBox {
                id: exportSendMixdown
                objectName: "chorusExportSendMixdown"
                text: page.t("导出后直接送入简易混音")
                checked: false
            }
        }
        onAboutToShow: {
            exportMode.currentIndex = Math.max(0, exportMode.indexOfValue(page.view.variant))
            exportSendMixdown.checked = false
        }
        onAccepted: {
            page.exportWhole = exportScope.currentValue === "whole"
            page.exportVariant = exportMode.currentValue
            exportFolder.open()
        }
    }
}
