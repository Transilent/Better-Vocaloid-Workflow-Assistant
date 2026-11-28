import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" && quickTranslation ? quickTranslation.text(message) : message
    }
    objectName: "tuningPage"
    required property QtObject theme
    required property QtObject controller

    readonly property var view: controller.view || ({})
    readonly property var tracks: controller.tracks || []
    readonly property var styles: controller.styles || []
    readonly property var regions: controller.regions || []
    readonly property var matches: controller.matches || []
    readonly property var notes: controller.notes || []
    readonly property var presets: controller.presets || []
    readonly property var bindings: controller.bindings || []
    readonly property var techniques: controller.techniques || []
    readonly property var currentNote: view.selectedNote || ({})
    readonly property var noteDetails: currentNote.details || []
    readonly property int selectedNoteCount: (view.selectedNoteIds || []).length
    readonly property string noteScope: textOf(view.sha256) + "|" + textOf(currentNote.id)
    readonly property string styleRegionId: textOf(view.selectedRegion)
    readonly property var styleRegion: regions.find(row => row.id === styleRegionId)
    readonly property var styleBrush: view.styleBrush || ({})
    readonly property var lyricContext: view.lyricContext || ({})
    readonly property bool wholeTimelineVisible: numberOr(view.viewportSpan, 0) >= timelineSpan() - .001
    readonly property bool canReturnToWord: textOf(lyricContext.lyric).length > 0
        && currentWordSpan() < timelineSpan() - .001

    readonly property real availableWidth: Math.max(0, width)
    readonly property real availableHeight: Math.max(0, height)
    readonly property real scale: theme.scale || 1
    readonly property bool stacked: availableWidth < (scale > 1.2 ? 1180 : 1040)
    readonly property bool compact: availableWidth < Math.round(720 * scale)
    readonly property bool draftConflict: textOf(view.draftConflict).length > 0
    readonly property bool busy: view.busy === true || draftConflict
    readonly property bool hasSource: textOf(view.source).length > 0
    readonly property bool ustxProject: view.projectSuffix === "ustx"
    readonly property bool vprProject: view.projectSuffix === "vpr"
    readonly property bool vsqxProject: view.projectSuffix === "vsqx"
    readonly property bool acepProject: view.projectSuffix === "acep"
    readonly property bool ufdataProject: view.projectSuffix === "ufdata"
    readonly property bool nativeStyleProject: ustxProject || vprProject || vsqxProject || acepProject || ufdataProject
    readonly property var projectFilters: ({
        svp: "Synthesizer V 工程 (*.svp)", ustx: "OpenUtau 工程 (*.ustx)",
        vpr: "VOCALOID 工程 (*.vpr)", vsqx: "VOCALOID VSQX 工程 (*.vsqx)",
        acep: "ACE Studio 工程 (*.acep)",
        ufdata: "ACE Studio 交换谱 (*.ufdata)"
    })
    readonly property bool sessionExists: boolOr(view.sessionExists, false)
    readonly property bool candidateOpenable: candidatePresent
        && (boolOr(view.candidateVerified, false) || boolOr(view.candidateHistorical, false))
    readonly property string activeId: textOf(view.activeTrack)
    readonly property string mode: String(view.mode || "single") === "multi" ? "multi" : "single"
    readonly property var activeTrackData: findTrack(activeId)
    readonly property int contentMargin: stacked ? 10 : 12
    readonly property int editorPadding: Math.round(7 * scale)
    readonly property int editorGap: Math.round(6 * scale)
    readonly property real railWidth: Math.round(246 * scale)
    readonly property real minimumRollHeight: Math.round((mode === "multi"
        ? (stacked ? 220 : 280) : (stacked ? 170 : 220)) * scale)
    readonly property bool hasRange: validRange().length > 0
    readonly property bool hasLegacyDraft: hasValue(view.legacyDraft)
    readonly property bool targetEditable: editableTargets()
    readonly property bool canAddRegion: hasSource && hasRange && !busy && !hasLegacyDraft
        && targetIds().length > 0 && targetEditable
        && (textOf(view.style) !== "none" || !!view.hasStyleRecipe || textOf(view.style_technique) !== "none")
    readonly property bool candidatePresent: hasValue(view.candidate)
    readonly property bool expressionModeKnown: textOf(view.expressionMode).length > 0
    readonly property int regionCount: regions.length

    property int retainedIndexDraft: 0
    property string selectedPresetKey: ""
    property string inspectorTab: "style"
    property bool showStyleDetails: false
    property int noteScopeVersion: 0
    property bool editorFocus: false
    signal workshopRequested(string pane)

    clip: true
    onNoteScopeChanged: noteScopeVersion += 1

    function hasValue(value) {
        return value !== undefined && value !== null && value !== ""
    }

    function textOf(value) {
        return value === undefined || value === null ? "" : String(value)
    }

    function numberOr(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback
        const parsed = Number(value)
        return isFinite(parsed) ? parsed : fallback
    }

    function boolOr(value, fallback) {
        return value === undefined || value === null ? fallback : Boolean(value)
    }

    function listContains(values, value) {
        if (!values || values.length === undefined)
            return false
        for (let index = 0; index < values.length; ++index) {
            if (String(values[index]) === String(value))
                return true
        }
        return false
    }

    function toggleEditorFocus() {
        if (hasSource)
            editorFocus = !editorFocus
    }

    function focusBlocksEditorShortcut(item) {
        if (lyricBatchDialog.visible || issuesDialog.visible)
            return true
        let current = item
        while (current) {
            if (current instanceof TextInput || current instanceof TextEdit
                    || current instanceof TextField || current instanceof TextArea
                    || current instanceof SpinBox || current instanceof ComboBox
                    || current instanceof Popup || current instanceof Dialog)
                return true
            current = current.parent
        }
        return false
    }

    Shortcut {
        objectName: "tuningUndoShortcut"
        sequence: "Ctrl+Z"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && boolOr(page.view.canUndoRegions, false)
            && !timelineRoll.gestureType && !parameterLane.gesture
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.undoRegions()
    }

    Shortcut {
        objectName: "tuningRedoShortcut"
        sequence: "Ctrl+Y"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && boolOr(page.view.canRedoRegions, false)
            && !timelineRoll.gestureType && !parameterLane.gesture
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.redoRegions()
    }

    Shortcut {
        objectName: "tuningRedoShiftShortcut"
        sequence: "Ctrl+Shift+Z"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && boolOr(page.view.canRedoRegions, false)
            && !timelineRoll.gestureType && !parameterLane.gesture
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.redoRegions()
    }

    Shortcut {
        objectName: "tuningDeleteRegionShortcut"
        sequence: "Delete"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && page.textOf(page.view.selectedRegion).length > 0
            && !timelineRoll.gestureType && !parameterLane.gesture
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.removeSelectedRegion()
    }

    Shortcut {
        sequence: "Ctrl+F"
        enabled: page.visible && page.hasSource && !page.busy && !lyricBatchDialog.visible && !issuesDialog.visible
        onActivated: { lyricSearch.forceActiveFocus(Qt.ShortcutFocusReason); lyricSearch.selectAll() }
    }

    Shortcut {
        objectName: "tuningEscapeShortcut"
        sequence: "Escape"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
            && (page.editorFocus || page.hasRange || !!timelineRoll.gestureType || !!parameterLane.gesture)
        onActivated: {
            if (parameterLane.gesture)
                parameterLane.resetPointer()
            else if (timelineRoll.gestureType)
                timelineRoll.clearGesture()
            else if (page.hasRange)
                timelineRoll.clearSelection()
            else
                page.editorFocus = false
        }
    }

    function editNoteField(key, value) {
        const note = currentNote
        if (!note.id || selectedNoteCount !== 1 || !view.noteTool)
            return
        if (controller.editNoteGeometry(note.id, key === "end" ? "resize" : "move",
                key === "pitch" ? note.start : value, key === "pitch" ? value : note.pitch, key === "start"))
            timelineRoll.locateNote(currentNote)
    }

    function findTrack(trackId) {
        for (let index = 0; index < tracks.length; ++index) {
            if (String(tracks[index].id) === String(trackId))
                return tracks[index]
        }
        return null
    }

    function trackIndex(trackId) {
        for (let index = 0; index < tracks.length; ++index) {
            if (String(tracks[index].id) === String(trackId))
                return index
        }
        return -1
    }

    function trackName(trackId) {
        const track = findTrack(trackId)
        if (track && textOf(track.name).length > 0)
            return textOf(track.name)
        return textOf(trackId)
    }

    function targetIds() {
        const result = []
        if (mode === "multi") {
            const selected = view.selectedTracks || []
            for (let index = 0; index < selected.length; ++index)
                result.push(String(selected[index]))
        } else if (activeId.length > 0) {
            result.push(activeId)
        }
        return result
    }

    function targetNames() {
        const ids = targetIds()
        const result = []
        for (let index = 0; index < ids.length; ++index)
            result.push(trackName(ids[index]))
        return result
    }

    function editableTargets() {
        const ids = targetIds()
        if (ids.length === 0)
            return false
        for (let index = 0; index < ids.length; ++index) {
            const track = findTrack(ids[index])
            if (!track || track.editable === false)
                return false
        }
        return true
    }

    function targetSummary() {
        const ids = targetIds()
        if (ids.length === 0)
            return "未选择修改目标"
        const names = targetNames()
        return mode === "multi"
            ? "目标：" + names.join("、") + "（" + ids.length + " 轨）"
            : "目标：" + names[0]
    }

    function sessionStatusText() {
        if (!hasSource)
            return "未载入工程"
        if (textOf(view.job).length > 0)
            return "任务会话"
        if (sessionExists)
            return "会话已就绪"
        return "首次生成时创建会话"
    }

    function currentLyricText() {
        const lyric = textOf(lyricContext.lyric)
        if (lyric.length === 0)
            return page.vwaTr("当前词：点击歌词或搜索定位")
        return page.vwaTr("当前词：") + lyric + " · " + trackName(lyricContext.trackId)
            + " · " + formatTime(lyricContext.start) + "–" + formatTime(lyricContext.end)
    }

    function lyricMatchPositionText() {
        const count = numberOr(view.lyricMatchCount, matches.length)
        const index = numberOr(view.lyricMatchIndex, -1)
        return count > 0 && index >= 0 ? String(index + 1) + "/" + String(count) : "—"
    }

    function rangeValues() {
        const start = numberOr(view.start, Number.NaN)
        const end = numberOr(view.end, Number.NaN)
        return { start: start, end: end }
    }

    function validRange() {
        const range = rangeValues()
        if (!isFinite(range.start) || !isFinite(range.end) || range.end <= range.start)
            return ""
        return formatTime(range.start) + "–" + formatTime(range.end)
    }

    function formatTime(value) {
        const seconds = numberOr(value, Number.NaN)
        if (!isFinite(seconds))
            return "—"
        const negative = seconds < 0
        const absolute = Math.abs(seconds)
        const whole = Math.floor(absolute)
        const minutes = Math.floor(whole / 60)
        const rest = whole % 60
        const tenths = Math.floor((absolute - whole) * 10)
        return (negative ? "-" : "") + String(minutes).padStart(2, "0") + ":"
            + String(rest).padStart(2, "0") + "." + String(tenths)
    }

    function selectionMinimum() {
        return numberOr(view.timelineStart, 0)
    }

    function selectionMaximum() {
        return selectionMinimum() + Math.max(0, numberOr(view.duration, 0))
    }

    function syncPreciseSelection(force) {
        if (force || !selectionStartInput.activeFocus)
            selectionStartInput.value = Math.round(numberOr(view.start, selectionMinimum()) * 1000)
        if (force || !selectionEndInput.activeFocus)
            selectionEndInput.value = Math.round(numberOr(view.end, selectionMaximum()) * 1000)
    }

    function commitPreciseSelection() {
        if (!hasSource || busy)
            return
        controller.selectSpan(Number(selectionStartInput.value) / 1000,
            Number(selectionEndInput.value) / 1000)
        syncPreciseSelection(true)
    }

    function strengthText(value) {
        const strength = numberOr(value, 0)
        return String(Math.round(Math.max(0, Math.min(1, strength)) * 100)) + "%"
    }

    function presetFieldName(field) {
        const names = {performance_style:"风格", style_recipe:"外部风格", style_strength:"风格强度", style_pitch:"附加音高",
            style_detail:"风格细节", style_technique:"唱腔", technique_strength:"唱腔强度",
            technique_pitch:"唱腔音高", dynamics_strength:"动态强度", expression_mode:"表达来源",
            expression_density:"表达采样", reference_dynamics:"参考力度", reference_tension:"参考张力",
            reference_breathiness:"参考气声", reference_voicing:"参考发声", track_indices:"目标轨道"}
        return names[field] || field
    }

    function presetValue(field, value) {
        if (field === "performance_style")
            return styleLabel(value)
        if (field === "style_technique") {
            const item = techniqueFor(value)
            return item ? item.label : textOf(value)
        }
        if (field.endsWith("_strength"))
            return strengthText(value)
        if (field === "style_detail" && value)
            return [value.onset, value.sustain, value.connection, value.release].map(strengthText).join(" / ")
        if (typeof value === "boolean")
            return value ? "开启" : "关闭"
        if (field === "expression_mode")
            return {preserve:"保留原表现", reference:"参考音频", calibrated:"校准 + 参考", retained:"沿用原参数"}[value] || value
        if (field === "expression_density")
            return value === "dense" ? "加密采样" : "原始采样"
        return value === null || value === undefined ? "—" : String(value)
    }

    function styleIndex(styleId) {
        const wanted = textOf(styleId)
        for (let index = 0; index < styles.length; ++index) {
            if (String(styles[index].id) === wanted)
                return index
        }
        return -1
    }

    function styleFor(styleId) {
        const index = styleIndex(styleId)
        return index >= 0 ? styles[index] : null
    }

    function styleLabel(styleId) {
        const style = styleFor(styleId)
        return style ? textOf(style.label) : textOf(styleId)
    }

    function styleColor(styleId) {
        const style = styleFor(styleId)
        return style && style.color ? style.color : theme.accent
    }

    function presetIndex(name) {
        const wanted = textOf(name)
        for (let index = 0; index < presets.length; ++index) {
            if (String(presets[index].key) === wanted
                    || String(presets[index].label) === wanted)
                return index
        }
        return -1
    }

    function presetKeyIndex(key) {
        const wanted = textOf(key)
        for (let index = 0; index < presets.length; ++index) {
            if (String(presets[index].key) === wanted)
                return index
        }
        return -1
    }

    function presetKeyForName(name) {
        const wanted = textOf(name)
        for (let index = 0; index < presets.length; ++index) {
            if (String(presets[index].label) === wanted)
                return String(presets[index].key)
        }
        return ""
    }

    function syncPresetChoice() {
        if (selectedPresetKey.length > 0 && presetKeyIndex(selectedPresetKey) >= 0)
            return
        selectedPresetKey = presetKeyForName(view.presetName)
    }

    function expressionSourceAllows(modes) {
        const current = textOf(view.expressionMode)
        for (let index = 0; index < modes.length; ++index) {
            if (current === modes[index])
                return true
        }
        return false
    }

    function expressionSourceReason(mode) {
        return textOf((view.expressionSourceReasons || {})[mode])
    }

    function techniqueIndex(techniqueId) {
        const wanted = textOf(techniqueId)
        for (let index = 0; index < techniques.length; ++index) {
            if (String(techniques[index].id) === wanted)
                return index
        }
        return -1
    }

    function techniqueFor(techniqueId) {
        const index = techniqueIndex(techniqueId)
        return index >= 0 ? techniques[index] : null
    }

    function bindingFor(trackId) {
        for (let index = 0; index < bindings.length; ++index) {
            if (String(bindings[index].trackId) === String(trackId))
                return bindings[index]
        }
        return null
    }

    function activeBinding() {
        return activeId.length > 0 ? bindingFor(activeId) : null
    }

    function bindingField(name, fallback) {
        const binding = activeBinding()
        return binding && binding[name] !== undefined && binding[name] !== null
            ? binding[name] : fallback
    }

    function optionSource() {
        const nested = view.tuningOptions || view.expressionOptions || ({})
        return nested && typeof nested === "object" ? nested : ({})
    }

    function optionValue(name, fallback) {
        const nested = optionSource()
        if (nested[name] !== undefined && nested[name] !== null)
            return nested[name]
        if (view[name] !== undefined && view[name] !== null)
            return view[name]
        return fallback
    }

    function optionKnown(name) {
        const nested = optionSource()
        return (nested[name] !== undefined && nested[name] !== null)
            || (view[name] !== undefined && view[name] !== null)
    }

    function boolOptionText(name, label) {
        if (!optionKnown(name))
            return label + " · 待回读"
        return label + (boolOr(optionValue(name, false), false) ? " · 开" : " · 关")
    }

    function toggleOption(name) {
        controller.setTuningOption(name, !boolOr(optionValue(name, false), false))
    }

    function fileName(value) {
        const source = textOf(value)
        if (source.length === 0)
            return ""
        let slash = source.lastIndexOf("/")
        const backslash = source.lastIndexOf("\\")
        if (backslash > slash)
            slash = backslash
        return slash >= 0 ? source.slice(slash + 1) : source
    }

    function shortHash(value) {
        const hash = textOf(value)
        return hash.length > 12 ? hash.slice(0, 12) + "…" : hash
    }

    function candidateText() {
        const candidate = view.candidate
        if (typeof candidate === "string")
            return fileName(candidate)
        if (candidate && typeof candidate === "object") {
            const fields = ["summary", "title", "path", "id"]
            for (let index = 0; index < fields.length; ++index) {
                if (candidate[fields[index]] !== undefined
                        && candidate[fields[index]] !== null
                        && String(candidate[fields[index]]).length > 0) {
                    return fields[index] === "path"
                        ? fileName(candidate[fields[index]])
                        : String(candidate[fields[index]])
                }
            }
            try {
                return JSON.stringify(candidate)
            } catch (error) {
                return "候选已生成"
            }
        }
        return ""
    }

    function legacyText() {
        const legacy = view.legacyDraft
        if (typeof legacy === "string")
            return legacy
        if (legacy && typeof legacy === "object") {
            const fields = ["summary", "message", "description", "label"]
            for (let index = 0; index < fields.length; ++index) {
                if (legacy[fields[index]] !== undefined
                        && legacy[fields[index]] !== null
                        && String(legacy[fields[index]]).length > 0)
                    return String(legacy[fields[index]])
            }
            try {
                return JSON.stringify(legacy)
            } catch (error) {
                return "检测到需要转换的 v1 片段"
            }
        }
        return ""
    }

    function legacyMissingTrackHint() {
        const legacy = view.legacyDraft
        if (legacy && typeof legacy === "object") {
            const missing = legacy.missingTracks || legacy.missing_tracks
            if (missing && missing.length !== undefined && missing.length > 0)
                return "部分旧片段缺少轨道身份；转换时将使用当前勾选轨道作为目标。"
        }
        return "转换保留旧风格，并把每一轨的选区对齐到完整音符。"
    }

    function statusColor() {
        if (busy)
            return theme.accent
        const state = textOf(view.state).toLowerCase()
        if (state.indexOf("fail") >= 0 || state.indexOf("error") >= 0
                || state.indexOf("stale") >= 0)
            return theme.error
        if (state.indexOf("done") >= 0 || state.indexOf("ready") >= 0
                || state.indexOf("success") >= 0 || state.indexOf("confirmed") >= 0)
            return theme.success
        return theme.muted
    }

    function statusLabel() {
        switch (String(view.state || "")) {
        case "loading": return "读取工程"
        case "ready": return "编辑中"
        case "freezing": return "计算预览"
        case "frozen": return "可生成"
        case "running": return "生成中"
        case "checking": return "核验中"
        case "done": return "候选就绪"
        case "registered": return "已登记"
        case "registering": return "登记中"
        case "partial": return "待核对"
        case "failed": return "需要处理"
        case "cancelled": return "已取消"
        case "preset": return "读取预设"
        default: return "空闲"
        }
    }

    function timelineOrigin() {
        return numberOr(view.timelineStart, 0)
    }

    function timelineSpan() {
        return Math.max(0.001, numberOr(view.duration, 1))
    }

    function fitTimelineView() {
        if (hasSource)
            controller.setViewport(timelineOrigin(), timelineSpan())
    }

    function currentWordSpan() {
        const start = numberOr(lyricContext.start, 0);
        return Math.max(4, (numberOr(lyricContext.end, start) - start) * 3);
    }

    function returnToCurrentWord() {
        if (canReturnToWord) {
            const span = currentWordSpan();
            controller.setViewport(numberOr(lyricContext.start, timelineOrigin()) - span * .22, span);
        }
    }

    function syncRetainedDraft() {
        const index = numberOr(bindingField("retainedIndex", 0), 0)
        retainedIndexDraft = Math.max(0, Math.round(index))
    }

    onActiveIdChanged: syncRetainedDraft()
    onPresetsChanged: syncPresetChoice()
    onVisibleChanged: if (!page.visible) page.editorFocus = false
    onViewChanged: {
        syncPresetChoice()
        syncPreciseSelection(false)
    }
    Component.onCompleted: {
        syncRetainedDraft()
        syncPresetChoice()
        syncPreciseSelection(true)
    }


    property alias timelineRoll: timelineRoll
    property alias lyricBatchDialog: tuningDialogs.lyricBatchDialog
    property alias issuesDialog: tuningDialogs.issuesDialog
    property alias draftRecovery: tuningDialogs.draftRecovery
    property alias draftFile: tuningDialogs.draftFile
    property alias candidateSaveDialog: tuningDialogs.candidateSaveDialog
    property alias clearRegionsDialog: tuningDialogs.clearRegionsDialog
    property alias projectDialog: tuningDialogs.projectDialog
    property alias sessionDialog: tuningDialogs.sessionDialog
    property alias sessionLocationDialog: tuningDialogs.sessionLocationDialog
    property alias taskPickerDialog: tuningDialogs.taskPickerDialog
    property alias referenceDialog: tuningDialogs.referenceDialog
    property alias calibrationDialog: tuningDialogs.calibrationDialog
    property alias retainedDialog: tuningDialogs.retainedDialog
    property alias presetImportDialog: tuningDialogs.presetImportDialog
    property alias presetExportDialog: tuningDialogs.presetExportDialog
    property alias presetNameDialog: tuningDialogs.presetNameDialog
    property alias presetNameField: tuningDialogs.presetNameField

    ColumnLayout {
        anchors.fill: parent
        spacing: page.editorPadding

        Rectangle {
            id: editorFocusBar
            objectName: "tuningEditorFocusBar"
            Layout.fillWidth: true
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            Layout.topMargin: page.editorPadding
            visible: page.editorFocus
            implicitHeight: focusBarRow.implicitHeight + 12 * page.scale
            radius: Math.round(7 * page.scale)
            color: page.theme.surface
            border.width: 1
            border.color: page.theme.line
            RowLayout {
                id: focusBarRow
                anchors.fill: parent
                anchors.margins: 6 * page.scale
                spacing: page.editorGap
                TuningControls.PanelLabel {
                    Layout.fillWidth: true
                    theme: page.theme
                    text: page.vwaTr("调音专注编辑 · 时间轴与参数栏")
                }
                TuningControls.EditorButton {
                    objectName: "exitTuningEditorFocus"
                    theme: page.theme
                    text: page.vwaTr("退出专注 (Esc)")
                    onClicked: page.editorFocus = false
                }
            }
        }

        GridLayout {
            id: projectToolbar
            visible: !page.editorFocus
            Layout.fillWidth: true
            Layout.topMargin: page.editorPadding
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            columns: page.availableWidth < Math.round(760 * page.scale) ? 1 : 2
            columnSpacing: page.editorPadding
            rowSpacing: page.editorGap

            RowLayout {
                Layout.fillWidth: true
                spacing: page.editorPadding
                Text {
                    text: page.vwaTr("逐轨调音")
                    color: page.theme.text
                    font.pixelSize: Math.round(17 * page.scale)
                    font.weight: Font.DemiBold
                    textFormat: Text.PlainText
                }
                Text {
                    objectName: "tuningProjectFormat"
                    visible: text.length > 0
                    text: page.textOf(page.view.projectFormat)
                    color: page.theme.muted
                    font.pixelSize: Math.round(11 * page.scale)
                    textFormat: Text.PlainText
                }
                Text {
                    Layout.fillWidth: true
                    text: page.hasSource ? page.fileName(page.view.source) : page.vwaTr("尚未载入调音工程")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.scale)
                    elide: Text.ElideMiddle
                    textFormat: Text.PlainText
                    HoverHandler { id: sourcePathHover }
                    ToolTip.visible: page.hasSource && sourcePathHover.hovered
                    ToolTip.text: page.textOf(page.view.source)
                        + (page.view.sha256 ? "\nSHA " + page.shortHash(page.view.sha256) : "")
                        + (page.view.job ? page.vwaTr("\n任务：") + page.view.job : "")
                }
            }

            Flow {
                Layout.fillWidth: projectToolbar.columns === 1
                Layout.alignment: projectToolbar.columns === 1 ? Qt.AlignLeft : Qt.AlignRight
                spacing: page.editorGap
                Rectangle {
                    radius: Math.round(7 * page.scale)
                    color: page.theme.raised
                    border.color: page.theme.line
                    implicitWidth: tuningModePillRow.implicitWidth + 4
                    implicitHeight: tuningModePillRow.implicitHeight + 4
                    RowLayout {
                        id: tuningModePillRow
                        anchors.fill: parent
                        anchors.margins: 2
                        spacing: 2
                        TuningControls.EditorButton {
                            objectName: "tuningModeSingle"
                            theme: page.theme
                            implicitHeight: Math.round(26 * page.scale)
                            quiet: !selected
                            text: page.vwaTr("单轨精修")
                            leadingGlyph: "nav_tuning"
                            leadingIconSize: 14
                            selected: page.mode === "single"
                            enabled: !page.busy
                            onClicked: page.controller.setMode("single")
                        }
                        TuningControls.EditorButton {
                            objectName: "tuningModeMulti"
                            theme: page.theme
                            implicitHeight: Math.round(26 * page.scale)
                            quiet: !selected
                            text: page.vwaTr("多轨编排")
                            leadingGlyph: "layers"
                            leadingIconSize: 14
                            selected: page.mode === "multi"
                            enabled: !page.busy
                            onClicked: page.controller.setMode("multi")
                        }
                    }
                }
                TuningControls.EditorButton {
                    objectName: "tuningOpenProject"
                    theme: page.theme
                    text: page.vwaTr("打开工程…")
                    primary: true
                    enabled: !page.busy
                    onClicked: projectDialog.open()
                }
                TuningControls.EditorButton {
                    id: workspaceMenuButton
                    objectName: "tuningWorkspaceMenu"
                    theme: page.theme
                    text: page.vwaTr("工程与恢复…")
                    enabled: !page.busy || !page.hasSource
                    onClicked: workspaceMenu.open()
                    UiMenu {
                        id: workspaceMenu
                        theme: page.theme
                        y: workspaceMenuButton.height
                        width: Math.round(222 * page.scale)
                        MenuItem {
                            objectName: "tuningOpenTask"
                            text: page.vwaTr("从任务载入…")
                            enabled: !page.busy
                            onTriggered: {
                                page.controller.refreshTuningTasks()
                                taskPickerDialog.open()
                            }
                        }
                        MenuSeparator {}
                        MenuItem {
                            objectName: "tuningReloadProject"
                            text: page.vwaTr("重新读取当前工程")
                            enabled: !page.busy && boolOr(page.view.canReload, false)
                            onTriggered: page.controller.reloadProject()
                        }
                        MenuItem {
                            objectName: "tuningResumeSession"
                            text: page.vwaTr("继续会话…")
                            enabled: !page.busy
                            onTriggered: sessionDialog.open()
                        }
                        MenuItem {
                            objectName: "tuningChooseSessionLocation"
                            text: page.vwaTr("会话位置…")
                            visible: !page.textOf(page.view.job).length
                            enabled: !page.busy && boolOr(page.view.canChooseSessionLocation, false)
                            onTriggered: sessionLocationDialog.open()
                        }
                        MenuSeparator {}
                        MenuItem {
                            objectName: "reloadLatestTuningDraft"
                            text: page.vwaTr("载入最新草稿")
                            visible: page.hasSource && !page.draftConflict
                            enabled: !page.busy && boolOr(page.view.canReloadDraft, false)
                            onTriggered: page.controller.reloadLatestDraft()
                        }
                        MenuItem {
                            objectName: "saveTuningDraftRevision"
                            text: page.vwaTr("保存新修订")
                            visible: page.hasSource && !page.draftConflict
                            enabled: !page.busy && boolOr(page.view.canSaveDraftRevision, false)
                            onTriggered: page.controller.saveDraftRevision()
                        }
                        MenuItem {
                            objectName: "chooseDraftRecovery"
                            text: page.vwaTr("草稿副本…")
                            visible: page.hasSource && !page.draftConflict
                            enabled: !page.busy
                            onTriggered: draftFile.open()
                        }
                    }
                }
                TuningControls.EditorButton {
                    objectName: "tuningEditorFocus"
                    theme: page.theme
                    text: page.vwaTr("专注编辑")
                    enabled: page.hasSource && !page.draftConflict
                    onClicked: page.toggleEditorFocus()
                }
                Text {
                    id: sessionChip
                    objectName: "tuningSessionLocation"
                    width: Math.min(Math.round(96 * page.scale), Math.max(0, parent.width))
                    height: Math.round(28 * page.scale); verticalAlignment: Text.AlignVCenter
                    text: page.vwaTr(page.sessionStatusText())
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.scale)
                    elide: Text.ElideMiddle
                    textFormat: Text.PlainText
                    Accessible.name: page.vwaTr("会话状态：") + text
                    HoverHandler { id: sessionLocationHover }
                    ToolTip.visible: sessionLocationHover.hovered
                    ToolTip.text: page.vwaTr("会话位置：") + page.textOf(page.view.sessionLocation)
                        + page.vwaTr("\n候选目录：") + page.textOf(page.view.sessionOutput)
                }
            }
        }

        Rectangle {
            objectName: "tuningDraftConflict"
            Layout.fillWidth: true
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            visible: page.draftConflict
            implicitHeight: conflictRow.implicitHeight + 14 * page.scale
            radius: 7 * page.scale
            color: page.theme.raised
            border.color: page.theme.attention
            RowLayout {
                id: conflictRow
                anchors.fill: parent
                anchors.margins: 7 * page.scale
                TuningControls.HelperText {
                    Layout.fillWidth: true
                    theme: page.theme
                    text: page.vwaTr("共享草稿有更新。本地编辑已存成副本，请核对后继续。")
                }
                TuningControls.EditorButton {
                    objectName: "reviewDraftConflict"
                    theme: page.theme
                    text: page.vwaTr("核对草稿")
                    enabled: !page.view.busy
                    onClicked: draftRecovery.openReview("")
                }
            }
        }

        Rectangle {
            id: legacyDraftBanner
            objectName: "legacyDraftBanner"
            Layout.fillWidth: true
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            visible: page.hasLegacyDraft
            implicitHeight: legacyLayout.implicitHeight + Math.round(24 * page.scale)
            radius: Math.round(9 * page.scale)
            color: page.theme.raised
            border.width: 1
            border.color: page.theme.attention

            RowLayout {
                id: legacyLayout
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(12 * page.scale)
                spacing: Math.round(10 * page.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(3 * page.scale)
                    TuningControls.PanelLabel {
                        theme: page.theme
                        text: page.vwaTr("检测到旧版全局片段")
                    }
                    TuningControls.HelperText {
                        theme: page.theme
                        text: page.legacyText()
                    }
                    TuningControls.HelperText {
                        theme: page.theme
                        text: page.legacyMissingTrackHint()
                    }
                }
                UiButton {
                    objectName: "migrateLegacy"
                    theme: page.theme
                    text: page.vwaTr("按原轨道展开旧草稿")
                    primary: true
                    enabled: !page.busy
                    onClicked: page.controller.migrateLegacy()
                }
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            Layout.bottomMargin: page.contentMargin
            columns: page.stacked ? 1 : 2
            columnSpacing: page.editorPadding
            rowSpacing: page.editorPadding

            Rectangle {
                id: workspacePanel
                objectName: "tuningWorkspacePanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: chromeHeight + page.minimumRollHeight
                readonly property real chromeHeight: editorTools.implicitHeight
                    + selectionToolbar.implicitHeight + viewportStrip.implicitHeight
                    + 2 * page.editorPadding + 3 * page.editorGap
                    + (parameterLane.visible ? parameterLane.implicitHeight + page.editorGap : 0)
                radius: Math.round(8 * page.scale)
                color: page.theme.surface
                border.width: 1
                border.color: page.theme.line

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: page.editorPadding
                    spacing: page.editorGap

                    ColumnLayout {
                        id: editorTools
                        Layout.fillWidth: true
                        spacing: page.editorGap

                        RowLayout {
                            Layout.fillWidth: true
                            visible: page.tracks.length > 0
                            spacing: page.editorGap
                            Text {
                                objectName: "tuningTargetScopeLabel"
                                text: page.mode === "multi" ? page.vwaTr("批量目标")
                                    : page.vwaTr("当前轨")
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                            Flickable {
                                objectName: "tuningTrackStrip"
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.round(30 * page.scale)
                                clip: true
                                contentWidth: Math.max(width, trackStripRow.implicitWidth)
                                contentHeight: height
                                flickableDirection: Flickable.HorizontalFlick
                                boundsBehavior: Flickable.StopAtBounds
                                visible: page.tracks.length > 0

                                Row {
                                    id: trackStripRow
                                    height: parent.height
                                    spacing: Math.round(7 * page.scale)

                                    Repeater {
                                        model: page.tracks
                                        delegate: TuningControls.EditorButton {
                                            id: trackChip
                                            required property var modelData
                                            required property int index

                                            readonly property bool isActive:
                                                String(modelData.id) === page.activeId
                                            readonly property bool isSelected: page.mode === "multi"
                                                ? page.listContains(page.view.selectedTracks, modelData.id)
                                                : isActive

                                            theme: page.theme
                                            objectName: (page.mode === "multi"
                                                ? "tuningTrackTarget_" : "tuningTrackFocus_")
                                                + String(modelData.id)
                                            text: page.textOf(modelData.name)
                                                + (modelData.noteCount !== undefined
                                                    ? "  " + modelData.noteCount : "")
                                                + (modelData.editable === false ? page.vwaTr("  · 只读") : "")
                                                + (page.mode === "multi" && isSelected ? "  ✓" : "")
                                            selected: isSelected
                                            enabled: !page.busy && (page.mode === "single"
                                                || modelData.editable !== false)
                                            width: Math.max(Math.round(80 * page.scale),
                                                Math.min(Math.round(236 * page.scale), implicitWidth))
                                            leftPadding: Math.round(19 * page.scale)
                                            Accessible.name: text
                                                + (page.mode === "multi"
                                                    ? (isSelected ? page.vwaTr("，批量目标")
                                                        : page.vwaTr("，未选为批量目标"))
                                                    : page.vwaTr("，当前轨"))
                                                + (isActive ? page.vwaTr("，焦点轨") : "")
                                            Rectangle {
                                                x: Math.round(6 * page.scale)
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: Math.round(7 * page.scale)
                                                height: width
                                                radius: width / 2
                                                color: timelineRoll.trackColor(trackChip.index)
                                            }
                                            ToolTip.visible: hovered
                                            ToolTip.text: page.mode === "multi"
                                                ? page.vwaTr("点击切换批量目标；焦点在泳道或右侧焦点框切换。")
                                                    + (page.textOf(modelData.reason).length > 0
                                                        ? "\n" + page.textOf(modelData.reason) : "")
                                                : page.vwaTr("点击设为当前精修轨。")
                                                    + (page.textOf(modelData.reason).length > 0
                                                        ? "\n" + page.textOf(modelData.reason) : "")
                                            onClicked: {
                                                if (page.mode === "single")
                                                    page.controller.focusTrack(String(modelData.id))
                                                else
                                                    page.controller.toggleTrack(String(modelData.id))
                                            }
                                        }
                                    }
                                }
                            }
                            UiComboBox {
                                objectName: "tuningFocusTrack"
                                Layout.preferredWidth: Math.round(137 * page.scale)
                                implicitHeight: Math.round(30 * page.scale)
                                visible: page.mode === "multi"
                                theme: page.theme
                                model: page.tracks
                                textRole: "name"
                                displayText: page.vwaTr("焦点：") + (currentText || "—")
                                valueRole: "id"
                                currentIndex: page.trackIndex(page.activeId)
                                enabled: !page.busy && page.tracks.length > 0
                                Accessible.name: page.vwaTr("焦点轨")
                                onActivated: function(index) {
                                    if (index >= 0 && index < page.tracks.length)
                                        page.controller.focusTrack(String(page.tracks[index].id))
                                }
                            }
                        }

                        RowLayout {
                            objectName: "tuningCurrentLyricRow"
                            Layout.fillWidth: true
                            visible: page.mode === "single" && page.hasSource
                            spacing: page.editorGap
                            Rectangle {
                                Layout.alignment: Qt.AlignVCenter
                                implicitWidth: Math.round(7 * page.scale)
                                implicitHeight: implicitWidth
                                radius: width / 2
                                color: page.activeTrackData
                                    ? timelineRoll.trackColor(page.trackIndex(page.activeId))
                                    : page.theme.muted
                            }
                            Text {
                                objectName: "tuningCurrentLyricContext"
                                Layout.fillWidth: true
                                text: page.currentLyricText()
                                color: page.theme.text
                                font.pixelSize: Math.round(11 * page.scale)
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                                ToolTip.visible: truncated && lyricHover.hovered
                                ToolTip.text: text
                                HoverHandler { id: lyricHover }
                            }
                            Text {
                                objectName: "tuningHandPitchLegend"
                                visible: !!page.view.pitchTool || timelineRoll.manualCurves.length > 0
                                text: page.view.pitchTool === "erase" ? page.vwaTr("擦除手绘") : page.vwaTr("手绘音高偏移")
                                color: timelineRoll.handColor
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                                ToolTip.visible: handLegendHover.hovered
                                ToolTip.text: page.vwaTr("手绘覆盖风格的音高偏移，与原工程音高叠加。这里不显示合成后的音高。")
                                HoverHandler { id: handLegendHover }
                            }
                            Text {
                                objectName: "tuningLyricOccurrencePosition"
                                text: page.lyricMatchPositionText()
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.scale)
                                textFormat: Text.PlainText
                            }
                            TuningControls.EditorButton {
                                objectName: "tuningPreviousLyricOccurrence"
                                theme: page.theme
                                text: page.vwaTr("上一处")
                                quiet: true
                                enabled: !page.busy && boolOr(page.view.canPreviousLyricMatch, false)
                                onClicked: page.controller.stepLyricMatch(-1)
                            }
                            TuningControls.EditorButton {
                                objectName: "tuningNextLyricOccurrence"
                                theme: page.theme
                                text: page.vwaTr("下一处")
                                quiet: true
                                enabled: !page.busy && boolOr(page.view.canNextLyricMatch, false)
                                onClicked: page.controller.stepLyricMatch(1)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: page.editorGap

                            // 1. Primary editing tools grouped in one segmented pill
                            Rectangle {
                                radius: Math.round(7 * page.scale)
                                color: page.theme.raised
                                border.color: page.theme.line
                                Layout.preferredWidth: tuningToolsRow.implicitWidth + 4
                                Layout.minimumWidth: Layout.preferredWidth
                                Layout.preferredHeight: tuningToolsRow.implicitHeight + 4
                                implicitWidth: Layout.preferredWidth
                                implicitHeight: Layout.preferredHeight
                                RowLayout {
                                    id: tuningToolsRow
                                    anchors.fill: parent
                                    anchors.margins: 2
                                    spacing: 2
                                    TuningControls.EditorButton {
                                        objectName: "tuningNoteTool"
                                        theme: page.theme
                                        implicitHeight: Math.round(26 * page.scale)
                                        text: page.vwaTr("改音符")
                                        leadingGlyph: "music_note"
                                        leadingIconSize: 14
                                        quiet: !selected
                                        selected: !!page.view.noteTool
                                        enabled: page.hasSource && !page.busy && !page.textOf(page.view.scoreEditReason)
                                        Accessible.checkable: true
                                        Accessible.checked: selected
                                        Accessible.description: ToolTip.text
                                        ToolTip.visible: hovered || visualFocus
                                        ToolTip.text: page.textOf(page.view.scoreEditReason)
                                            || page.vwaTr("拖动音符可移动，拖右端可拉伸。空白处框选音符，Ctrl 单击增减选择。")
                                        onClicked: {
                                            page.controller.setNoteTool(true)
                                            if (page.currentNote.id)
                                                page.inspectorTab = "note"
                                        }
                                    }
                                    TuningControls.EditorButton {
                                        objectName: "tuningStyleTool"
                                        theme: page.theme
                                        implicitHeight: Math.round(26 * page.scale)
                                        text: page.vwaTr("框选风格")
                                        leadingGlyph: "nav_workshop"
                                        leadingIconSize: 14
                                        quiet: !selected
                                        selected: !!page.view.styleTool
                                        enabled: page.hasSource && !page.busy && !page.view.formatReadOnly
                                        Accessible.checkable: true
                                        Accessible.checked: selected
                                        Accessible.description: ToolTip.text
                                        ToolTip.visible: hovered || visualFocus
                                        ToolTip.text: page.vwaTr("拖出风格区间。按住 Shift 拖动只选区。")
                                        onClicked: {
                                            if (page.mode === "single")
                                                page.controller.setNoteTool(false)
                                            page.inspectorTab = "style"
                                        }
                                    }
                                    TuningControls.EditorButton {
                                        objectName: "tuningPitchDrawTool"
                                        theme: page.theme
                                        implicitHeight: Math.round(26 * page.scale)
                                        quiet: !selected
                                        glyph: "edit"
                                        text: page.vwaTr("手绘音高")
                                        selected: page.view.pitchTool === "draw"
                                        visible: page.hasSource && page.mode === "single"
                                        enabled: !page.busy && !page.textOf(page.view.pitchEditReason)
                                        Accessible.checkable: true
                                        Accessible.checked: selected
                                        Accessible.description: ToolTip.text
                                        ToolTip.text: page.textOf(page.view.pitchEditReason)
                                            || page.vwaTr("沿一个音符画音高偏移，松手保存。")
                                        onClicked: selected && !page.view.scoreEditReason
                                            ? page.controller.setNoteTool(true)
                                            : page.controller.setPitchTool(selected ? "style" : "draw")
                                    }
                                    TuningControls.EditorButton {
                                        objectName: "tuningPitchEraseTool"
                                        theme: page.theme
                                        implicitHeight: Math.round(26 * page.scale)
                                        quiet: !selected
                                        glyph: "eraser"
                                        text: page.vwaTr("擦除手绘")
                                        selected: page.view.pitchTool === "erase"
                                        visible: page.hasSource && page.mode === "single"
                                        enabled: !page.busy && !page.textOf(page.view.pitchEditReason)
                                            && (selected || (page.view.pitchStrokeCount || 0) > 0)
                                        Accessible.checkable: true
                                        Accessible.checked: selected
                                        ToolTip.text: page.vwaTr("点笔触擦掉一整段，拖动擦掉局部。原曲线和风格保留。")
                                        onClicked: selected && !page.view.scoreEditReason
                                            ? page.controller.setNoteTool(true)
                                            : page.controller.setPitchTool(selected ? "style" : "erase")
                                    }
                                }
                            }

                            // 2. View & editing toggles
                            TuningControls.EditorButton {
                                objectName: "tuningParameterToggle"
                                theme: page.theme
                                glyph: "sliders"
                                quiet: !selected
                                text: page.vwaTr("参数轨")
                                selected: parameterLane.visible
                                enabled: page.hasSource && page.mode === "single"
                                Accessible.checkable: true
                                Accessible.checked: selected
                                onClicked: page.controller.setParameterOpen(!page.view.parameterOpen)
                            }
                            TuningControls.EditorButton {
                                objectName: "tuningSnapToNotes"
                                theme: page.theme
                                text: page.vwaTr("吸附")
                                quiet: !selected
                                selected: !!page.view.snapEnabled
                                enabled: page.hasSource
                                Accessible.checkable: true
                                Accessible.checked: selected
                                ToolTip.visible: hovered || visualFocus
                                ToolTip.text: page.view.pitchTool === "draw"
                                    ? page.vwaTr("手绘每 10 音分吸附 · Alt 临时跳过")
                                    : page.vwaTr("吸附到附近音符边界 · Alt 临时跳过")
                                onClicked: page.controller.setSnapEnabled(!page.view.snapEnabled)
                            }
                            TuningControls.EditorButton {
                                objectName: "timelineFit"
                                theme: page.theme
                                readonly property bool returning: page.wholeTimelineVisible && page.canReturnToWord
                                text: returning ? page.vwaTr("回当前词") : page.vwaTr("全曲")
                                Layout.minimumWidth: Math.max(implicitWidth, Math.round(58 * page.scale))
                                quiet: true
                                enabled: page.hasSource
                                ToolTip.visible: hovered || visualFocus
                                ToolTip.text: returning ? page.vwaTr("回到当前词附近，只调整视野")
                                    : page.vwaTr("显示全曲，只调整视野")
                                onClicked: returning ? page.returnToCurrentWord() : page.fitTimelineView()
                            }

                            // 3. Lyric search & QA tools on the right
                            TuningControls.ThemedTextField {
                                id: lyricSearch
                                objectName: "tuningLyricSearch"
                                implicitHeight: Math.round(30 * page.scale)
                                topPadding: 0
                                bottomPadding: 0
                                Layout.fillWidth: true
                                Layout.minimumWidth: Math.round(92 * page.scale)
                                theme: page.theme
                                placeholderText: page.vwaTr("搜索歌词…")
                                text: page.textOf(page.view.query)
                                enabled: !page.busy && page.hasSource
                                Accessible.name: page.vwaTr("搜索歌词")
                                onTextEdited: page.controller.setQuery(text)
                                onAccepted: {
                                    page.controller.setQuery(text)
                                    if (page.view.canNextLyricMatch) page.controller.stepLyricMatch(1)
                                    timelineRoll.forceActiveFocus(Qt.OtherFocusReason)
                                }
                            }
                            TuningControls.EditorButton {
                                objectName: "tuningClearLyricSearch"
                                theme: page.theme
                                text: page.vwaTr("清空")
                                quiet: true
                                visible: page.textOf(page.view.query).length > 0
                                enabled: !page.busy
                                onClicked: page.controller.setQuery("")
                            }
                            TuningControls.EditorButton {
                                objectName: "tuningLyricBatch"
                                theme: page.theme
                                quiet: !lyricBatchDialog.peeking
                                glyph: lyricBatchDialog.peeking ? "" : "lyrics"
                                text: lyricBatchDialog.peeking ? page.vwaTr("继续贴词") : page.vwaTr("贴词")
                                enabled: page.hasSource && !page.busy && !page.textOf(page.view.scoreEditReason)
                                ToolTip.visible: hovered
                                ToolTip.text: page.vwaTr("整句贴词 · 先框选音符。按住 Shift 可只选区。")
                                onClicked: lyricBatchDialog.open()
                            }
                            TuningControls.EditorButton {
                                objectName: "tuningEditingIssues"
                                theme: page.theme
                                quiet: true
                                glyph: "issues"
                                text: page.vwaTr("检查")
                                enabled: page.hasSource && !page.busy
                                ToolTip.visible: hovered
                                ToolTip.text: page.vwaTr("检查编辑问题")
                                onClicked: {
                                    issuesDialog.review = page.controller.editingIssues()
                                    issuesDialog.open()
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.round(44 * page.scale)
                            visible: page.textOf(page.view.query).length > 0
                            radius: Math.round(8 * page.scale)
                            color: page.theme.base
                            border.width: 1
                            border.color: page.theme.line
                            clip: true

                            ListView {
                                id: matchList
                                objectName: "tuningLyricMatches"
                                anchors.fill: parent
                                anchors.margins: 1
                                clip: true
                                model: page.matches
                                orientation: ListView.Horizontal
                                spacing: Math.round(2 * page.scale)
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.horizontal: UiScrollBar { theme: page.theme }
                                WheelHandler {
                                    target: null
                                    onWheel: function(event) {
                                        const x = event.angleDelta.x || event.angleDelta.y
                                        if (x) {
                                            matchList.contentX = Math.max(0, Math.min(matchList.contentWidth - matchList.width,
                                                matchList.contentX - x))
                                            event.accepted = true
                                        }
                                    }
                                }

                                delegate: ItemDelegate {
                                    id: matchRow
                                    required property var modelData
                                    width: Math.min(Math.round(205 * page.scale), matchList.width)
                                    height: Math.round(41 * page.scale)
                                    hoverEnabled: true
                                    focusPolicy: Qt.StrongFocus
                                    Accessible.name: (page.textOf(modelData.lyric).length > 0
                                        ? modelData.lyric : page.vwaTr("空歌词")) + "，"
                                        + (modelData.trackName || page.trackName(modelData.trackId))
                                        + "，" + page.formatTime(modelData.start)

                                    onClicked: page.controller.selectMatch(String(modelData.trackId), String(modelData.id))

                                    contentItem: RowLayout {
                                        spacing: Math.round(8 * page.scale)

                                        Rectangle {
                                            Layout.alignment: Qt.AlignVCenter
                                            implicitWidth: Math.round(3 * page.scale)
                                            implicitHeight: Math.round(28 * page.scale)
                                            radius: width / 2
                                            color: page.theme.accent
                                            opacity: matchRow.hovered ? 1 : 0.58
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: Math.round(1 * page.scale)
                                            Text {
                                                Layout.fillWidth: true
                                                text: page.textOf(matchRow.modelData.lyric).length > 0
                                                    ? matchRow.modelData.lyric : page.vwaTr("[空歌词]")
                                                color: page.theme.text
                                                font.pixelSize: Math.round(13 * page.scale)
                                                font.weight: Font.DemiBold
                                                elide: Text.ElideRight
                                                textFormat: Text.PlainText
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                text: (matchRow.modelData.trackName
                                                        || page.trackName(matchRow.modelData.trackId))
                                                    + "  ·  " + page.formatTime(matchRow.modelData.start)
                                                    + "–" + page.formatTime(matchRow.modelData.end)
                                                    + (matchRow.modelData.pitch !== undefined
                                                        && matchRow.modelData.pitch !== null
                                                        ? page.vwaTr("  ·  音高 ") + matchRow.modelData.pitch : "")
                                                color: page.theme.muted
                                                font.pixelSize: Math.round(10 * page.scale)
                                                elide: Text.ElideRight
                                                textFormat: Text.PlainText
                                            }
                                        }
                                    }

                                    background: Rectangle {
                                        color: matchRow.hovered ? page.theme.hover
                                            : matchRow.visualFocus ? page.theme.selected : "transparent"
                                    }
                                }
                            }

                            Text {
                                anchors.centerIn: parent
                                width: parent.width - Math.round(32 * page.scale)
                                visible: page.matches.length === 0
                                horizontalAlignment: Text.AlignHCenter
                                text: !page.hasSource
                                    ? page.vwaTr("载入工程后可搜索歌词。")
                                    : page.textOf(page.view.query).length === 0
                                        ? page.vwaTr("输入歌词查找；重复词会以轨道和时间区分。")
                                        : page.vwaTr("没有找到匹配歌词，可尝试短词或清空搜索。")
                                color: page.theme.muted
                                font.pixelSize: Math.round(12 * page.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }
                    }

                    Rectangle {
                        id: timelineFrame
                        objectName: "tuningTimelineFrame"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: page.minimumRollHeight
                        Layout.preferredHeight: page.minimumRollHeight
                        radius: Math.round(6 * page.scale)
                        color: page.theme.base
                        border.width: 1
                        border.color: page.theme.line
                        clip: true

                        TuningTimeline {
                            id: timelineRoll
                            anchors.fill: parent
                            anchors.margins: 1
                            theme: page.theme
                            controller: page.controller
                            view: page.view
                            tracks: page.tracks
                            notes: page.notes
                            regions: page.regions
                            compact: page.stacked
                            inspectionTime: parameterLane.inspectionTime
                            inspectPointer: parameterLane.visible
                            onNoteFocused: if (!page.editorFocus) page.inspectorTab = "note"
                            onRegionFocused: page.inspectorTab = "style"
                        }

                        Rectangle {
                            objectName: "tuningEmptyState"
                            anchors.fill: parent
                            anchors.margins: 1
                            visible: !page.hasSource
                            z: 10
                            color: page.theme.base
                            Accessible.role: Accessible.Pane
                            Accessible.name: page.vwaTr("打开调音工程")
                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.LeftButton
                                onClicked: {}
                            }
                            ColumnLayout {
                                anchors.centerIn: parent
                                width: Math.min(parent.width - Math.round(36 * page.scale),
                                    Math.round(430 * page.scale))
                                spacing: Math.round(8 * page.scale)
                                Text {
                                    Layout.fillWidth: true
                                    text: page.vwaTr("还没有打开调音工程")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(17 * page.scale)
                                    font.weight: Font.DemiBold
                                    horizontalAlignment: Text.AlignHCenter
                                    textFormat: Text.PlainText
                                }
                                TuningControls.HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.vwaTr("打开调音工程后，可在单轨卷帘逐句精修，或切到多轨泳道比较重复歌词。")
                                    horizontalAlignment: Text.AlignHCenter
                                }
                                RowLayout {
                                    Layout.alignment: Qt.AlignHCenter
                                    spacing: page.editorGap
                                    TuningControls.EditorButton {
                                        objectName: "emptyOpenTuningProject"
                                        theme: page.theme
                                        text: page.vwaTr("打开工程…")
                                        primary: true
                                        enabled: !page.busy
                                        onClicked: projectDialog.open()
                                    }
                                    TuningControls.EditorButton {
                                        objectName: "emptyOpenTuningTask"
                                        theme: page.theme
                                        text: page.vwaTr("从任务载入…")
                                        enabled: !page.busy
                                        onClicked: {
                                            page.controller.refreshTuningTasks()
                                            taskPickerDialog.open()
                                        }
                                    }
                                    TuningControls.EditorButton {
                                        objectName: "emptyResumeTuningSession"
                                        theme: page.theme
                                        text: page.vwaTr("继续会话…")
                                        quiet: true
                                        enabled: !page.busy
                                        onClicked: sessionDialog.open()
                                    }
                                }
                            }
                        }
                    }

                    TuningParameterLane {
                        id: parameterLane
                        Layout.fillWidth: true
                        Layout.preferredHeight: implicitHeight
                        visible: page.hasSource && page.mode === "single" && !!page.view.parameterOpen
                        theme: page.theme
                        controller: page.controller
                        view: page.view
                        roll: timelineRoll
                    }

                    TuningViewportStrip {
                        id: viewportStrip
                        Layout.fillWidth: true
                        leadingInset: timelineRoll.x + timelineRoll.plotLeft
                        trailingInset: Math.max(0, timelineFrame.width
                            - timelineRoll.x - timelineRoll.plotRight)
                        theme: page.theme
                        timelineStart: page.timelineOrigin()
                        timelineDuration: page.numberOr(page.view.duration, 0)
                        viewportStart: page.numberOr(page.view.viewportStart, timelineStart)
                        viewportSpan: page.numberOr(page.view.viewportSpan, timelineDuration)
                        onViewportRequested: (start, span) => page.controller.setViewport(start, span)
                    }

                    GridLayout {
                        id: selectionToolbar
                        Layout.fillWidth: true
                        columns: width < Math.round(520 * page.scale) ? 1 : 3
                        columnSpacing: page.editorGap
                        rowSpacing: page.editorGap

                        RowLayout {
                            spacing: page.editorGap
                            Text {
                                text: page.vwaTr("开始")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                            SpinBox {
                                id: selectionStartInput
                                objectName: "tuningSelectionStart"
                                Layout.minimumWidth: Math.round(142 * page.scale)
                                Layout.preferredWidth: Layout.minimumWidth
                                font.pixelSize: Math.round(13 * page.scale)
                                from: Math.round(page.selectionMinimum() * 1000)
                                to: Math.round(Math.max(page.selectionMinimum(),
                                    page.selectionMaximum()) * 1000)
                                Layout.preferredHeight: Math.round(30 * page.scale)
                                stepSize: 1
                                editable: true
                                live: false
                                enabled: page.hasSource && !page.busy
                                textFromValue: function(value) {
                                    return (Number(value) / 1000).toFixed(3)
                                }
                                valueFromText: function(text) {
                                    const parsed = Number(String(text).trim().replace(",", "."))
                                    return isFinite(parsed) ? Math.round(parsed * 1000) : value
                                }
                                onValueModified: page.commitPreciseSelection()
                                Accessible.name: page.vwaTr("选区开始时间，秒")
                            }
                            Text {
                                text: page.vwaTr("结束")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                            SpinBox {
                                id: selectionEndInput
                                objectName: "tuningSelectionEnd"
                                Layout.minimumWidth: Math.round(142 * page.scale)
                                Layout.preferredWidth: Layout.minimumWidth
                                font.pixelSize: Math.round(13 * page.scale)
                                from: Math.round(page.selectionMinimum() * 1000)
                                to: Math.round(Math.max(page.selectionMinimum(),
                                    page.selectionMaximum()) * 1000)
                                Layout.preferredHeight: Math.round(30 * page.scale)
                                stepSize: 1
                                editable: true
                                live: false
                                enabled: page.hasSource && !page.busy
                                textFromValue: function(value) {
                                    return (Number(value) / 1000).toFixed(3)
                                }
                                valueFromText: function(text) {
                                    const parsed = Number(String(text).trim().replace(",", "."))
                                    return isFinite(parsed) ? Math.round(parsed * 1000) : value
                                }
                                onValueModified: page.commitPreciseSelection()
                                Accessible.name: page.vwaTr("选区结束时间，秒")
                            }
                            TuningControls.EditorButton {
                                objectName: "applyTuningSelection"
                                theme: page.theme
                                text: page.vwaTr("应用时间")
                                quiet: true
                                enabled: page.hasSource && !page.busy
                                onClicked: page.commitPreciseSelection()
                            }
                        }
                    }
                }
            }

            Rectangle {
                id: inspectorPanel
                objectName: "tuningInspectorPanel"
                Layout.fillWidth: page.stacked
                Layout.preferredWidth: page.stacked ? 0 : page.railWidth
                Layout.minimumWidth: page.stacked ? 0 : page.railWidth
                Layout.fillHeight: !page.stacked
                Layout.preferredHeight: page.stacked ? Math.round(560 * page.scale) : -1
                Layout.alignment: Qt.AlignTop
                radius: Math.round(8 * page.scale)
                color: page.theme.surface
                border.color: page.theme.line
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(8 * page.scale)
                    spacing: page.editorGap
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Repeater {
                            model: [{key:"style", label:page.vwaTr("风格")}, {key:"note", label:page.vwaTr("音符")},
                                    {key:"source", label:page.vwaTr("来源")},
                                    {key:"presets", label:page.vwaTr("预设")}, {key:"result", label:page.vwaTr("结果")}]
                            TuningControls.EditorButton {
                                required property var modelData
                                objectName: "tuningInspector_" + modelData.key
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                theme: page.theme
                                quiet: true
                                text: modelData.label
                                selected: page.inspectorTab === modelData.key
                                onClicked: page.inspectorTab = modelData.key
                            }
                        }
                    }
                    TuningControls.Divider {}
                    TuningControls.HelperText {
                        objectName: "tuningFormatReadOnly"
                        Layout.fillWidth: true
                        theme: page.theme
                        visible: !!page.view.formatReadOnly
                        text: page.textOf(page.view.trackReadOnlyReason || page.view.formatNotice)
                    }
                    ScrollView {
                        id: inspectorScroll
                        objectName: "tuningInspectorScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        contentWidth: availableWidth
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical.policy: ScrollBar.AsNeeded
                        Connections {
                            target: page
                            function onInspectorTabChanged() { inspectorScroll.contentItem.contentY = 0 }
                            function onStyleRegionIdChanged() {
                                if (page.styleRegionId && page.inspectorTab === "style")
                                    inspectorScroll.contentItem.contentY = 0
                            }
                        }
                        ColumnLayout {
                            width: inspectorScroll.availableWidth
                            spacing: page.editorGap
                            TuningInspectorNoteStyle { page: page }
                            TuningInspectorSourceResult { page: page }
                        }
                    }
                    TuningControls.Divider {}
                    Text {
                        Layout.fillWidth: true
                        visible: page.inspectorTab !== "result" && page.view.state === "failed"
                        text: page.textOf(page.view.message)
                        color: page.theme.error
                        font.pixelSize: Math.round(11 * page.scale)
                        elide: Text.ElideRight
                        maximumLineCount: 2
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                        ToolTip.visible: noticeHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: noticeHover }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: Math.round(6 * page.scale)
                        rowSpacing: Math.round(6 * page.scale)

                        TuningControls.EditorButton {
                            objectName: "freezeTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("冻结预览")
                            enabled: !page.busy && boolOr(page.view.canFreeze, false)
                            onClicked: { page.inspectorTab = "result"; page.controller.freeze() }
                        }
                        TuningControls.EditorButton {
                            objectName: "runTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("生成候选")
                            primary: true
                            enabled: !page.busy && boolOr(page.view.canRun, false)
                            onClicked: { page.inspectorTab = "result"; page.controller.run() }
                        }
                        TuningControls.EditorButton {
                            objectName: "cancelTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("取消任务")
                            visible: page.view.busy === true
                            enabled: visible
                            onClicked: page.controller.cancel()
                        }
                        TuningControls.EditorButton {
                            objectName: "registerTuningResult"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("登记结果")
                            visible: !!page.view.canRegister
                            enabled: !page.busy && boolOr(page.view.canRegister, false)
                            onClicked: page.controller.registerResult()
                        }
                    }

                }
            }
        }
    }


    TuningDialogs {
        id: tuningDialogs
        page: page
    }
}
