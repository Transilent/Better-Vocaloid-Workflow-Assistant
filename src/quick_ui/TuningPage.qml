import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ScrollView {
    id: page
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
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

    readonly property real scale: theme.scale || 1
    readonly property bool stacked: availableWidth < (scale > 1.2 ? 1180 : 1040)
    readonly property bool compact: availableWidth < Math.round(720 * scale)
    readonly property bool draftConflict: textOf(view.draftConflict).length > 0
    readonly property bool busy: view.busy === true || draftConflict
    readonly property bool hasSource: textOf(view.source).length > 0
    readonly property bool sessionExists: boolOr(view.sessionExists, false)
    readonly property bool candidateOpenable: candidatePresent
        && (boolOr(view.candidateVerified, false) || boolOr(view.candidateHistorical, false))
    readonly property string activeId: textOf(view.activeTrack)
    readonly property string mode: String(view.mode || "single") === "multi" ? "multi" : "single"
    readonly property var activeTrackData: findTrack(activeId)
    readonly property int contentMargin: stacked ? 12 : 18
    readonly property int editorPadding: Math.round(10 * scale)
    readonly property int editorGap: Math.round(6 * scale)
    readonly property real railWidth: Math.round(246 * scale)
    readonly property real minimumRollHeight: Math.round((stacked ? 170 : 220) * scale)
    readonly property bool hasRange: validRange().length > 0
    readonly property bool hasLegacyDraft: hasValue(view.legacyDraft)
    readonly property bool targetEditable: editableTargets()
    readonly property bool canAddRegion: hasSource && hasRange && !busy && !hasLegacyDraft
        && targetIds().length > 0 && targetEditable
        && (textOf(view.style) !== "none" || textOf(view.style_technique) !== "none")
    readonly property bool candidatePresent: hasValue(view.candidate)
    readonly property bool expressionModeKnown: textOf(view.expressionMode).length > 0
    readonly property int regionCount: regions.length

    property int retainedIndexDraft: 0
    property string selectedPresetKey: ""
    property string inspectorTab: "style"
    property bool showStyleDetails: false
    property bool editorFocus: false
    signal calibrationRequested()

    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical.policy: ScrollBar.AsNeeded

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
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.undoRegions()
    }

    Shortcut {
        objectName: "tuningRedoShortcut"
        sequence: "Ctrl+Y"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && boolOr(page.view.canRedoRegions, false)
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.redoRegions()
    }

    Shortcut {
        objectName: "tuningRedoShiftShortcut"
        sequence: "Ctrl+Shift+Z"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && boolOr(page.view.canRedoRegions, false)
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.redoRegions()
    }

    Shortcut {
        objectName: "tuningDeleteRegionShortcut"
        sequence: "Delete"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.busy && page.textOf(page.view.selectedRegion).length > 0
            && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
        onActivated: page.controller.removeSelectedRegion()
    }

    Shortcut {
        objectName: "tuningEscapeShortcut"
        sequence: "Escape"
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.focusBlocksEditorShortcut(page.Window.activeFocusItem)
            && (page.editorFocus || page.hasRange)
        onActivated: {
            if (page.editorFocus)
                page.editorFocus = false
            else
                page.controller.clearSelection()
        }
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
        const names = {performance_style:"风格", style_strength:"风格强度", style_pitch:"附加音高",
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

    component PanelLabel: Text {
        id: label
        required property QtObject theme
        color: label.theme.text
        font.pixelSize: Math.round(13 * label.theme.scale)
        font.weight: Font.DemiBold
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    component HelperText: Text {
        id: helper
        required property QtObject theme
        color: helper.theme.muted
        font.pixelSize: Math.round(12 * helper.theme.scale)
        lineHeight: 1.18
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    component Divider: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: page.theme.line
    }

    component ThemedTextField: TextField {
        id: input
        required property QtObject theme

        implicitHeight: Math.round(38 * input.theme.scale)
        leftPadding: Math.round(11 * input.theme.scale)
        rightPadding: leftPadding
        topPadding: Math.round(8 * input.theme.scale)
        bottomPadding: topPadding
        color: input.theme.text
        placeholderTextColor: input.theme.muted
        font.pixelSize: Math.round(13 * input.theme.scale)
        selectByMouse: true
        focusPolicy: Qt.StrongFocus
        selectionColor: input.theme.accent
        selectedTextColor: input.theme.base
        Accessible.name: placeholderText

        background: Rectangle {
            radius: Math.round(7 * input.theme.scale)
            color: input.enabled ? input.theme.surface : input.theme.base
            border.width: 1
            border.color: input.activeFocus ? input.theme.accent : input.theme.line
            opacity: input.enabled ? 1 : 0.58
        }
    }

    component EditorButton: UiButton {
        implicitHeight: Math.round(30 * theme.scale)
        leftPadding: Math.round(7 * theme.scale)
        rightPadding: leftPadding
        topPadding: 0
        bottomPadding: 0
    }

    component RecipeButton: EditorButton {
        id: recipe
        required property color tint
        required property string fullLabel
        Accessible.name: fullLabel
        Accessible.checkable: true
        Accessible.checked: selected
        ToolTip.visible: hovered || visualFocus
        ToolTip.text: fullLabel
        background: Rectangle {
            radius: Math.round(6 * recipe.theme.scale)
            color: Qt.rgba(recipe.tint.r, recipe.tint.g, recipe.tint.b,
                           recipe.selected ? 0.52 : recipe.hovered ? 0.30 : 0.17)
            border.color: recipe.selected || recipe.visualFocus ? recipe.tint : recipe.theme.line
            border.width: recipe.selected || recipe.visualFocus ? 2 : 1
            Rectangle {
                anchors.left: parent.left
                anchors.leftMargin: 5
                anchors.verticalCenter: parent.verticalCenter
                width: 5; height: 5; radius: 2.5
                color: recipe.tint
            }
        }
    }

    component StrengthRow: RowLayout {
        id: strengthRow
        required property string label
        required property string sliderName
        required property real amount
        property string valueName: ""
        signal edited(real amount)
        Layout.fillWidth: true
        spacing: page.editorGap
        HelperText {
            theme: page.theme
            Layout.minimumWidth: Math.round(46 * page.scale)
            text: strengthRow.label
        }
        PercentSlider {
            objectName: strengthRow.sliderName
            Layout.fillWidth: true
            strength: strengthRow.amount
            enabled: !page.busy && page.hasSource && strengthRow.enabled
            Accessible.name: strengthRow.label
            onMoved: strengthRow.edited(value / 100)
        }
        HelperText {
            objectName: strengthRow.valueName
            theme: page.theme
            Layout.minimumWidth: Math.round(33 * page.scale)
            horizontalAlignment: Text.AlignRight
            text: page.strengthText(strengthRow.amount)
        }
    }

    component PercentSlider: Slider {
        id: slider
        required property real strength
        readonly property int percent: Math.round(Math.max(0, Math.min(1, strength)) * 100)

        implicitHeight: Math.round(30 * page.scale)
        from: 0
        to: 100
        value: percent
        stepSize: 1
        snapMode: Slider.SnapAlways
        focusPolicy: Qt.StrongFocus
        wheelEnabled: false
        Accessible.description: page.vwaTr("范围 0% 到 100%，每次调整 1%")

        background: Rectangle {
            x: slider.leftPadding + slider.handle.width / 2
            y: Math.round((slider.height - height) / 2)
            width: Math.max(0, slider.availableWidth - slider.handle.width)
            height: Math.round(4 * page.scale)
            radius: height / 2
            color: page.theme.line
            Rectangle {
                width: parent.width * slider.visualPosition
                height: parent.height
                radius: parent.radius
                color: page.theme.accent
            }
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: Math.round((slider.height - height) / 2)
            width: Math.round(15 * page.scale)
            height: width
            radius: width / 2
            color: slider.visualFocus ? page.theme.accent : page.theme.text
            border.width: slider.visualFocus ? 2 : 1
            border.color: slider.visualFocus ? page.theme.text : page.theme.accent
            opacity: slider.enabled ? 1 : 0.5
        }
    }

    ColumnLayout {
        width: page.availableWidth
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
                PanelLabel {
                    Layout.fillWidth: true
                    theme: page.theme
                    text: page.vwaTr("调音专注编辑 · 时间轴与参数栏")
                }
                EditorButton {
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
            columns: page.availableWidth < Math.round(480 * page.scale) ? 1 : 2
            columnSpacing: page.editorPadding
            rowSpacing: page.editorGap

            RowLayout {
                Layout.fillWidth: true
                spacing: page.editorPadding
                Text {
                    text: page.vwaTr("逐轨调音")
                    color: page.theme.text
                    font.pixelSize: Math.round(19 * page.scale)
                    font.weight: Font.DemiBold
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

            RowLayout {
                spacing: page.editorGap
                EditorButton {
                    objectName: "tuningModeSingle"
                    theme: page.theme
                    text: page.vwaTr("单轨精修")
                    selected: page.mode === "single"
                    enabled: !page.busy
                    onClicked: page.controller.setMode("single")
                }
                EditorButton {
                    objectName: "tuningModeMulti"
                    theme: page.theme
                    text: page.vwaTr("多轨编排")
                    selected: page.mode === "multi"
                    enabled: !page.busy
                    onClicked: page.controller.setMode("multi")
                }
                EditorButton {
                    objectName: "openVoiceCalibration"
                    theme: page.theme
                    text: page.vwaTr("声库校准…")
                    onClicked: page.calibrationRequested()
                }
                EditorButton {
                    objectName: "tuningOpenProject"
                    theme: page.theme
                    text: page.vwaTr("打开工程…")
                    primary: true
                    enabled: !page.busy
                    onClicked: projectDialog.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.columnSpan: projectToolbar.columns
                spacing: page.editorGap
                EditorButton {
                    objectName: "tuningOpenTask"
                    theme: page.theme
                    text: page.vwaTr("从任务载入…")
                    enabled: !page.busy
                    onClicked: {
                        page.controller.refreshTuningTasks()
                        taskPickerDialog.open()
                    }
                }
                EditorButton {
                    objectName: "tuningReloadProject"
                    theme: page.theme
                    text: page.vwaTr("重新读取当前工程")
                    enabled: !page.busy && boolOr(page.view.canReload, false)
                    onClicked: page.controller.reloadProject()
                }
                EditorButton {
                    objectName: "tuningResumeSession"
                    theme: page.theme
                    text: page.vwaTr("继续会话…")
                    enabled: !page.busy
                    onClicked: sessionDialog.open()
                }
                EditorButton {
                    objectName: "tuningChooseSessionLocation"
                    theme: page.theme
                    text: page.vwaTr("会话位置…")
                    visible: !page.textOf(page.view.job).length
                    enabled: !page.busy && boolOr(page.view.canChooseSessionLocation, false)
                    onClicked: sessionLocationDialog.open()
                }
                EditorButton {
                    objectName: "tuningEditorFocus"
                    theme: page.theme
                    text: page.vwaTr("专注编辑")
                    enabled: page.hasSource && !page.draftConflict
                    onClicked: page.toggleEditorFocus()
                }
                Text {
                    objectName: "tuningSessionLocation"
                    Layout.fillWidth: true
                    text: page.textOf(page.view.sessionLocation)
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.scale)
                    elide: Text.ElideMiddle
                    textFormat: Text.PlainText
                    HoverHandler { id: sessionLocationHover }
                    ToolTip.visible: sessionLocationHover.hovered && text.length > 0
                    ToolTip.text: text + page.vwaTr("\n候选目录：") + page.textOf(page.view.sessionOutput)
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
                HelperText {
                    Layout.fillWidth: true
                    theme: page.theme
                    text: page.vwaTr("共享草稿有更新。本地编辑已存成副本，请核对后继续。")
                }
                EditorButton {
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
                    PanelLabel {
                        theme: page.theme
                        text: page.vwaTr("检测到旧版全局片段")
                    }
                    HelperText {
                        theme: page.theme
                        text: page.legacyText()
                    }
                    HelperText {
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
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            columns: page.stacked ? 1 : 2
            columnSpacing: page.editorPadding
            rowSpacing: page.editorPadding

            Rectangle {
                id: workspacePanel
                objectName: "tuningWorkspacePanel"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: Math.max(chromeHeight + page.minimumRollHeight,
                    Math.min(Math.round(920 * page.scale), page.availableHeight
                        - (projectToolbar.visible ? projectToolbar.height : editorFocusBar.height)
                        - 3 * page.editorPadding
                        - (legacyDraftBanner.visible ? legacyDraftBanner.height + page.editorPadding : 0)))
                readonly property real chromeHeight: editorTools.implicitHeight
                    + selectionToolbar.implicitHeight + viewportStrip.implicitHeight
                    + 2 * page.editorPadding + 3 * page.editorGap
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
                                        delegate: EditorButton {
                                            id: trackChip
                                            required property var modelData
                                            required property int index

                                            readonly property bool isActive:
                                                String(modelData.id) === page.activeId
                                            readonly property bool isSelected: page.mode === "multi"
                                                ? page.listContains(page.view.selectedTracks, modelData.id)
                                                : isActive

                                            theme: page.theme
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
                                            Accessible.name: text + (isActive ? page.vwaTr("，焦点轨") : "")
                                            Rectangle {
                                                x: Math.round(6 * page.scale)
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: Math.round(7 * page.scale)
                                                height: width
                                                radius: width / 2
                                                color: timelineRoll.trackColor(trackChip.index)
                                            }
                                            ToolTip.visible: hovered
                                                && page.textOf(modelData.reason).length > 0
                                            ToolTip.text: page.textOf(modelData.reason)
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
                                displayText: "焦点：" + (currentText || "—")
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
                            Layout.fillWidth: true
                            spacing: page.editorGap

                            ThemedTextField {
                                id: lyricSearch
                                objectName: "tuningLyricSearch"
                                implicitHeight: Math.round(30 * page.scale)
                                topPadding: 0
                                bottomPadding: 0
                                Layout.fillWidth: true
                                theme: page.theme
                                placeholderText: page.vwaTr("搜索歌词…")
                                text: page.textOf(page.view.query)
                                enabled: !page.busy && page.hasSource
                                Accessible.name: page.vwaTr("搜索歌词")
                                onTextEdited: page.controller.setQuery(text)
                                onAccepted: page.controller.setQuery(text)
                            }
                            EditorButton {
                                objectName: "tuningClearLyricSearch"
                                theme: page.theme
                                text: page.vwaTr("清空")
                                quiet: true
                                visible: page.textOf(page.view.query).length > 0
                                enabled: !page.busy
                                onClicked: page.controller.setQuery("")
                            }
                            EditorButton {
                                objectName: "timelineFit"
                                theme: page.theme
                                text: page.vwaTr("全曲")
                                quiet: true
                                enabled: page.hasSource
                                onClicked: page.fitTimelineView()
                            }
                            EditorButton {
                                objectName: "tuningSnapToNotes"
                                theme: page.theme
                                text: page.vwaTr("吸附")
                                quiet: true
                                selected: !!page.view.snapEnabled
                                enabled: page.hasSource
                                Accessible.checkable: true
                                Accessible.checked: selected
                                ToolTip.visible: hovered || visualFocus
                                ToolTip.text: page.vwaTr("吸附到附近音符边界 · Alt 临时跳过")
                                onClicked: page.controller.setSnapEnabled(!page.view.snapEnabled)
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
                                ScrollBar.horizontal: ScrollBar {}
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

                                    onClicked: {
                                        page.controller.selectMatch(String(modelData.trackId),
                                            String(modelData.id))
                                        timelineRoll.revealPitch(modelData.pitch,
                                            modelData.trackId, modelData.id)
                                    }

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
                            onRegionFocused: page.inspectorTab = "style"
                        }
                    }

                    TuningViewportStrip {
                        id: viewportStrip
                        Layout.fillWidth: true
                        Layout.leftMargin: timelineRoll.x + timelineRoll.plotLeft
                        Layout.rightMargin: Math.max(0, timelineFrame.width
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
                            EditorButton {
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
                Layout.preferredHeight: page.stacked ? Math.round(560 * page.scale) : workspacePanel.height
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
                            model: [{key:"style", label:page.vwaTr("风格")}, {key:"source", label:page.vwaTr("来源")},
                                    {key:"presets", label:page.vwaTr("预设")}, {key:"result", label:page.vwaTr("结果")}]
                            EditorButton {
                                required property var modelData
                                objectName: "tuningInspector_" + modelData.key
                                Layout.fillWidth: true
                                theme: page.theme
                                quiet: true
                                text: modelData.label
                                selected: page.inspectorTab === modelData.key
                                onClicked: page.inspectorTab = modelData.key
                            }
                        }
                    }
                    Divider {}
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
                        }
                        ColumnLayout {
                            width: inspectorScroll.availableWidth
                            spacing: page.editorGap
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.inspectorTab === "style"
                                spacing: page.editorGap
                                RowLayout {
                                    Layout.fillWidth: true
                                    PanelLabel { Layout.fillWidth: true; theme: page.theme; text: page.vwaTr("风格") }
                                    EditorButton {
                                        objectName: "tuningStylePitch"
                                        theme: page.theme
                                        text: page.vwaTr("附加音高")
                                        quiet: true
                                        selected: !!page.view.stylePitch
                                        enabled: !page.busy && page.hasSource
                                        onClicked: page.controller.setStylePitch(!page.view.stylePitch)
                                    }
                                }
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    uniformCellWidths: true
                                    columnSpacing: page.editorGap
                                    rowSpacing: page.editorGap
                                    Repeater {
                                        model: page.styles
                                        RecipeButton {
                                            required property var modelData
                                            objectName: "tuningStyle_" + String(modelData.id)
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: String(modelData.short || modelData.label)
                                            fullLabel: String(modelData.label)
                                            tint: modelData.color || page.theme.muted
                                            selected: String(page.view.style) === String(modelData.id)
                                            enabled: !page.busy && page.hasSource
                                            onClicked: page.controller.setStyle(String(modelData.id))
                                        }
                                    }
                                }
                                StrengthRow {
                                    label: page.vwaTr("风格强度")
                                    sliderName: "tuningStyleStrengthSlider"
                                    valueName: "tuningStyleStrengthPercent"
                                    amount: page.numberOr(page.view.styleStrength, 0)
                                    onEdited: amount => page.controller.setStyleStrength(amount)
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    PanelLabel { Layout.fillWidth: true; theme: page.theme; text: page.vwaTr("唱腔") }
                                    EditorButton {
                                        objectName: "tuningTechniquePitch"
                                        theme: page.theme
                                        text: page.vwaTr("倚滑与收腔")
                                        quiet: true
                                        selected: !!page.view.technique_pitch
                                        enabled: !page.busy && page.hasSource && page.view.style_technique !== "none"
                                        onClicked: page.toggleOption("technique_pitch")
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
                                        RecipeButton {
                                            required property var modelData
                                            objectName: "tuningTechnique_" + String(modelData.id)
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: String(modelData.short || modelData.label)
                                            fullLabel: String(modelData.label)
                                            tint: modelData.color || page.theme.muted
                                            selected: String(page.view.style_technique) === String(modelData.id)
                                            enabled: !page.busy && page.hasSource
                                            onClicked: page.controller.setTuningOption("style_technique", String(modelData.id))
                                        }
                                    }
                                }
                                StrengthRow {
                                    label: page.vwaTr("唱腔强度")
                                    sliderName: "tuningTechniqueStrengthSlider"
                                    amount: page.numberOr(page.view.technique_strength, 0)
                                    enabled: page.view.style_technique !== "none"
                                    onEdited: amount => page.controller.setTuningOption("technique_strength", amount)
                                }
                                EditorButton {
                                    objectName: "tuningStyleDetailToggle"
                                    theme: page.theme
                                    text: page.showStyleDetails ? page.vwaTr("收起风格细节") : page.vwaTr("起音、持续、衔接、收尾…")
                                    quiet: true
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
                                        StrengthRow {
                                            required property var modelData
                                            label: modelData.label
                                            sliderName: "tuningStyleDetail_" + modelData.key
                                            amount: page.numberOr((page.view.styleDetail || {})[modelData.key], 1)
                                            onEdited: amount => page.controller.setStyleDetail(modelData.key, amount)
                                        }
                                    }
                                }
                                Divider {}
                                EditorButton {
                                    objectName: "addTuningRegion"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.vwaTr("应用为片段")
                                    primary: true
                                    enabled: page.canAddRegion
                                    onClicked: page.controller.addRegion()
                                }
                                CheckBox {
                                    objectName: "tuningAutoApplySelection"
                                    text: page.vwaTr("框选即应用")
                                    font.pixelSize: Math.round(12 * page.scale)
                                    checked: !!page.view.autoApplySelection
                                    onClicked: page.controller.setAutoApplySelection(checked)
                                    ToolTip.visible: hovered || visualFocus
                                    ToolTip.text: page.vwaTr("拖选松手后应用当前风格和唱腔并保存草稿；点音符或输入时间仍只选择。同轨后添加、移动、调整或更新的整组风格覆盖旧片段，其他轨道不受影响。")
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    PanelLabel { Layout.fillWidth: true; theme: page.theme; text: page.vwaTr("片段") }
                                    EditorButton {
                                        objectName: "undoTuningRegions"
                                        theme: page.theme; text: page.vwaTr("撤销"); quiet: true
                                        enabled: !page.busy && !!page.view.canUndoRegions
                                        onClicked: page.controller.undoRegions()
                                    }
                                    EditorButton {
                                        objectName: "redoTuningRegions"
                                        theme: page.theme; text: page.vwaTr("重做"); quiet: true
                                        enabled: !page.busy && !!page.view.canRedoRegions
                                        onClicked: page.controller.redoRegions()
                                    }
                                    EditorButton {
                                        objectName: "clearTuningRegions"
                                        theme: page.theme; text: page.vwaTr("清空…"); quiet: true
                                        enabled: !page.busy && page.regionCount > 0
                                        onClicked: clearRegionsDialog.open()
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
                                        ScrollBar.vertical: ScrollBar {}

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

                                                EditorButton {
                                                    theme: page.theme
                                                    glyph: "edit"
                                                    text: page.vwaTr("编辑片段")
                                                    quiet: true
                                                    enabled: !page.busy
                                                    onClicked: page.controller.editRegion(String(regionRow.modelData.id))
                                                }
                                                EditorButton {
                                                    objectName: "updateTuningRegion_" + String(regionRow.modelData.id)
                                                    theme: page.theme
                                                    glyph: "check"
                                                    text: page.vwaTr("更新片段")
                                                    quiet: true
                                                    enabled: !page.busy && page.hasRange
                                                        && page.textOf(regionRow.modelData.id).length > 0
                                                    onClicked: page.controller.updateRegion(
                                                        String(regionRow.modelData.id))
                                                    Accessible.name: page.vwaTr("用当前选区和风格更新此逐轨片段")
                                                    ToolTip.text: page.vwaTr("轨道按此片段固定；范围和风格取当前选区")
                                                }
                                                EditorButton {
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
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.inspectorTab === "source"
                                spacing: page.editorGap
                                PanelLabel {
                                    theme: page.theme
                                    text: page.vwaTr("表达来源与调音选项")
                                }

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    columnSpacing: Math.round(5 * page.scale)
                                    rowSpacing: Math.round(5 * page.scale)

                                    EditorButton {
                                        objectName: "expressionPreserve"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("保留原表现")
                                        selected: page.textOf(page.view.expressionMode) === "preserve"
                                        enabled: !page.busy && page.hasSource
                                        onClicked: page.controller.setExpressionMode("preserve")
                                    }
                                    EditorButton {
                                        objectName: "expressionReference"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("参考音频")
                                        selected: page.textOf(page.view.expressionMode) === "reference"
                                        enabled: !page.busy && page.hasSource
                                        onClicked: page.controller.setExpressionMode("reference")
                                    }
                                    EditorButton {
                                        objectName: "expressionCalibrated"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("校准 + 参考")
                                        selected: page.textOf(page.view.expressionMode) === "calibrated"
                                        enabled: !page.busy && page.hasSource
                                        onClicked: page.controller.setExpressionMode("calibrated")
                                    }
                                    EditorButton {
                                        objectName: "expressionRetained"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("沿用原参数")
                                        selected: page.textOf(page.view.expressionMode) === "retained"
                                        enabled: !page.busy && page.hasSource
                                        onClicked: page.controller.setExpressionMode("retained")
                                    }
                                }

                                HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.expressionModeKnown
                                        ? page.vwaTr("四种来源共用当前轨道与区间；外部声库渲染仍由你手动完成。")
                                        : page.vwaTr("表达来源状态待后端回读；可先选择一种真实来源。")
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

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: page.compact ? 1 : 2
                                    columnSpacing: Math.round(6 * page.scale)
                                    rowSpacing: Math.round(6 * page.scale)

                                    HelperText {
                                        Layout.alignment: Qt.AlignVCenter
                                        theme: page.theme
                                        text: page.vwaTr("参考音频")
                                        visible: page.expressionSourceAllows(["reference", "calibrated"])
                                    }
                                    EditorButton {
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
                                            referenceDialog.trackId = page.activeId
                                            referenceDialog.open()
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
                                    HelperText {
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

                                    HelperText {
                                        Layout.alignment: Qt.AlignVCenter
                                        theme: page.theme
                                        text: page.vwaTr("校准配置")
                                        visible: page.view.expressionMode === "calibrated"
                                    }
                                    EditorButton {
                                        objectName: "bindCalibrationProfile"
                                        visible: page.view.expressionMode === "calibrated"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("选择 JSON…")
                                        enabled: !page.busy && page.hasSource
                                            && page.textOf(page.view.expressionMode) === "calibrated"
                                        onClicked: calibrationDialog.open()
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        Layout.columnSpan: page.compact ? 1 : 2
                                        visible: page.textOf(page.view.calibration).length > 0
                                        text: page.fileName(page.view.calibration)
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.scale)
                                        elide: Text.ElideMiddle
                                        textFormat: Text.PlainText
                                    }

                                    HelperText {
                                        Layout.alignment: Qt.AlignVCenter
                                        theme: page.theme
                                        text: page.vwaTr("沿用的原 SVP")
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
                                        EditorButton {
                                            objectName: "bindRetainedProject"
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: page.textOf(page.bindingField("retained", "")).length > 0
                                                ? page.vwaTr("更换 SVP…") : page.vwaTr("选择 SVP…")
                                            enabled: !page.busy && page.activeId.length > 0
                                                && page.activeTrackData !== null
                                                && page.activeTrackData.editable !== false
                                                && page.textOf(page.view.expressionMode) === "retained"
                                            onClicked: {
                                                retainedDialog.trackId = page.activeId
                                                retainedDialog.sourceTrackIndex = page.retainedIndexDraft
                                                retainedDialog.open()
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
                                    visible: page.expressionSourceAllows(["reference", "calibrated"])
                                    columns: 2
                                    columnSpacing: Math.round(5 * page.scale)
                                    rowSpacing: Math.round(5 * page.scale)

                                    EditorButton {
                                        objectName: "optionReferenceDynamics"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.boolOptionText("reference_dynamics", page.vwaTr("参考力度"))
                                        selected: boolOr(page.optionValue("reference_dynamics", false), false)
                                        enabled: !page.busy
                                        onClicked: page.toggleOption("reference_dynamics")
                                    }
                                    EditorButton {
                                        objectName: "optionReferenceTension"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.boolOptionText("reference_tension", page.vwaTr("参考张力"))
                                        selected: boolOr(page.optionValue("reference_tension", false), false)
                                        enabled: !page.busy
                                        onClicked: page.toggleOption("reference_tension")
                                    }
                                    EditorButton {
                                        objectName: "optionReferenceBreathiness"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.boolOptionText("reference_breathiness", page.vwaTr("参考气声"))
                                        selected: boolOr(page.optionValue("reference_breathiness", false), false)
                                        enabled: !page.busy
                                        onClicked: page.toggleOption("reference_breathiness")
                                    }
                                    EditorButton {
                                        objectName: "optionReferenceVoicing"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.boolOptionText("reference_voicing", page.vwaTr("参考发声"))
                                        selected: boolOr(page.optionValue("reference_voicing", false), false)
                                        enabled: !page.busy
                                        onClicked: page.toggleOption("reference_voicing")
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(5 * page.scale)
                                    HelperText {
                                        Layout.alignment: Qt.AlignVCenter
                                        theme: page.theme
                                        text: page.vwaTr("动态强度")
                                    }
                                    PercentSlider {
                                        Layout.fillWidth: true
                                        objectName: "tuningDynamicsStrengthSlider"
                                        strength: page.numberOr(page.optionValue("dynamics_strength", 0), 0)
                                        enabled: !page.busy && page.optionKnown("dynamics_strength")
                                        Accessible.name: page.vwaTr("动态强度")
                                        onMoved: page.controller.setTuningOption("dynamics_strength", value / 100)
                                    }
                                    HelperText {
                                        theme: page.theme
                                        Layout.minimumWidth: Math.round(34 * page.scale)
                                        horizontalAlignment: Text.AlignRight
                                        text: page.optionKnown("dynamics_strength")
                                            ? page.strengthText(page.optionValue("dynamics_strength", 0)) : "—"
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(5 * page.scale)
                                    HelperText {
                                        Layout.alignment: Qt.AlignVCenter
                                        theme: page.theme
                                        text: page.vwaTr("表达采样")
                                    }
                                    EditorButton {
                                        objectName: "tuningDensityDense"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("加密采样")
                                        selected: page.optionKnown("expression_density")
                                            && String(page.optionValue("expression_density", "")) === "dense"
                                        enabled: !page.busy
                                        onClicked: page.controller.setTuningOption("expression_density", "dense")
                                    }
                                    EditorButton {
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

                                HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.vwaTr("这里只编辑工程与参数；声库合成、真实听感和外部编辑器中的手动渲染保持独立。")
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.inspectorTab === "presets"
                                spacing: page.editorGap
                                PanelLabel {
                                    theme: page.theme
                                    text: page.vwaTr("工程预设")
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
                                    Accessible.name: page.vwaTr("工程预设")
                                    onActivated: function(index) {
                                        if (index >= 0 && index < page.presets.length) {
                                            page.selectedPresetKey = String(page.presets[index].key)
                                            page.controller.dismissPresetReview()
                                        }
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    EditorButton {
                                        objectName: "previewTuningPreset"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("查看差异")
                                        enabled: page.hasSource && !page.busy && page.selectedPresetKey.length > 0
                                        onClicked: page.controller.previewPreset(page.selectedPresetKey)
                                    }
                                    EditorButton {
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
                                    HelperText {
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: parent.review.name + (parent.review.brushOnly ? page.vwaTr(" · 仅下一片段") : page.vwaTr(" · 绑定当前工程"))
                                    }
                                    Repeater {
                                        model: parent.review.changes || []
                                        HelperText {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: page.presetFieldName(modelData.field) + "："
                                                + page.presetValue(modelData.field, modelData.before) + " → "
                                                + page.presetValue(modelData.field, modelData.after)
                                        }
                                    }
                                    HelperText {
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        visible: (parent.review.missing || []).length > 0
                                        text: (parent.review.missing || []).join("\n")
                                    }
                                    EditorButton {
                                        objectName: "applyReviewedTuningPreset"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: parent.review.brushOnly ? page.vwaTr("应用到下一片段") : page.vwaTr("确认绑定预设")
                                        enabled: !page.busy
                                        primary: true
                                        onClicked: page.controller.confirmPreset()
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
                                    EditorButton {
                                        objectName: "importTuningPresets"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("导入附加…")
                                        enabled: !page.busy
                                        onClicked: presetImportDialog.open()
                                    }
                                    EditorButton {
                                        objectName: "exportTuningPreset"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("导出预设…")
                                        enabled: !page.busy && page.selectedPresetKey.length > 0
                                        onClicked: presetExportDialog.open()
                                    }
                                    EditorButton {
                                        objectName: "saveTuningPreset"
                                        Layout.fillWidth: true
                                        theme: page.theme
                                        text: page.vwaTr("保存到本机库")
                                        enabled: !page.busy && page.hasSource
                                        onClicked: {
                                            presetNameField.text = page.textOf(page.view.presetName)
                                            presetNameDialog.open()
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
                                    PanelLabel {
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
                                        visible: boolOr(page.view.candidateStale, false)
                                        text: page.vwaTr("已过期")
                                        color: page.theme.error
                                        font.pixelSize: Math.round(10 * page.scale)
                                        font.weight: Font.DemiBold
                                        textFormat: Text.PlainText
                                    }
                                    EditorButton {
                                        objectName: "openTuningCandidate"
                                        theme: page.theme
                                        text: page.vwaTr("打开候选")
                                        enabled: page.candidateOpenable && !page.view.busy
                                        onClicked: page.controller.openCandidate()
                                    }
                                }

                                HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    visible: page.candidatePresent
                                    text: {
                                        if (boolOr(page.view.candidateHistorical, false)) {
                                            const reason = page.textOf(page.view.message)
                                            return page.vwaTr("这是安全保留的历史候选；当前完整回执核验失败，因此不能登记。")
                                                + (reason.length > 0 ? "\n" + reason : "")
                                        }
                                        const report = page.view.candidateDetails || {}
                                        const state = {applied:page.vwaTr("已处理"), partial:page.vwaTr("部分处理"), unchanged:page.vwaTr("无变化")}[report.status] || ""
                                        return state + " · " + (report.tracks || 0) + page.vwaTr(" 轨")
                                            + (report.notesPreserved ? page.vwaTr(" · 音符与时值保留") : "")
                                            + (report.pitchChanged ? page.vwaTr("\n已修改局部音高") : "")
                                            + "\nSHA " + page.shortHash(report.sha256)
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: page.editorGap
                                    visible: !!page.view.candidateVerified
                                    EditorButton {
                                        objectName: "saveTuningCandidate"
                                        theme: page.theme
                                        text: page.vwaTr("另存 SVP…")
                                        enabled: !page.view.busy
                                        onClicked: candidateSaveDialog.open()
                                    }
                                    EditorButton {
                                        objectName: "openTuningReceipt"
                                        theme: page.theme
                                        text: boolOr(page.view.candidateHistorical, false) ? page.vwaTr("查看原回执") : page.vwaTr("核验回执")
                                        visible: !!page.view.candidateVerified
                                            || page.textOf(page.view.candidateReceipt).length > 0
                                        enabled: !page.view.busy
                                        onClicked: page.controller.openResultReceipt()
                                    }
                                }
                                HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    visible: !!page.view.savedCopy
                                    text: page.vwaTr("另存：") + page.fileName(page.view.savedCopy)
                                }

                                HelperText {
                                    Layout.fillWidth: true
                                    visible: boolOr(page.view.candidateStale, false)
                                    theme: page.theme
                                    text: page.vwaTr("候选基线已变化，请重新冻结、生成或明确确认后再继续。")
                                }
                                EditorButton {
                                    objectName: "tuningOpenMixdown"
                                    theme: page.theme
                                    visible: page.candidatePresent
                                    text: page.vwaTr("导入已合成的歌声…")
                                    enabled: !page.busy
                                    onClicked: page.controller.openMixdown()
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    visible: boolOr(page.view.candidateNeedsConfirmation, false)
                                    implicitHeight: confirmLayout.implicitHeight + Math.round(20 * page.scale)
                                    radius: Math.round(8 * page.scale)
                                    color: page.theme.raised
                                    border.width: 1
                                    border.color: boolOr(page.view.candidateConfirmed, false)
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
                                            text: boolOr(page.view.candidateConfirmed, false)
                                                ? page.vwaTr("候选基线已明确确认。")
                                                : page.vwaTr("候选与当前工程基线不同，需要人工确认。")
                                            color: boolOr(page.view.candidateConfirmed, false)
                                                ? page.theme.success : page.theme.attention
                                            font.pixelSize: Math.round(11 * page.scale)
                                            font.weight: Font.DemiBold
                                            wrapMode: Text.Wrap
                                            textFormat: Text.PlainText
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            EditorButton {
                                                objectName: "confirmCandidateBaseline"
                                                Layout.fillWidth: true
                                                theme: page.theme
                                                text: page.vwaTr("确认候选基线")
                                                primary: !boolOr(page.view.candidateConfirmed, false)
                                                enabled: !page.busy
                                                    && !boolOr(page.view.candidateConfirmed, false)
                                                onClicked: page.controller.confirmCandidate(true)
                                            }
                                            EditorButton {
                                                objectName: "unconfirmCandidateBaseline"
                                                Layout.fillWidth: true
                                                theme: page.theme
                                                text: page.vwaTr("恢复待确认")
                                                enabled: !page.busy
                                                    && boolOr(page.view.candidateConfirmed, false)
                                                onClicked: page.controller.confirmCandidate(false)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Divider {}
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

                        EditorButton {
                            objectName: "freezeTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("冻结预览")
                            enabled: !page.busy && boolOr(page.view.canFreeze, false)
                            onClicked: { page.inspectorTab = "result"; page.controller.freeze() }
                        }
                        EditorButton {
                            objectName: "runTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("生成候选")
                            primary: true
                            enabled: !page.busy && boolOr(page.view.canRun, false)
                            onClicked: { page.inspectorTab = "result"; page.controller.run() }
                        }
                        EditorButton {
                            objectName: "cancelTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("取消任务")
                            visible: page.view.busy === true
                            enabled: visible
                            onClicked: page.controller.cancel()
                        }
                        EditorButton {
                            objectName: "chooseDraftRecovery"
                            theme: page.theme
                            text: page.vwaTr("草稿副本…")
                            visible: page.hasSource && !page.draftConflict
                            enabled: !page.busy
                            onClicked: draftFile.open()
                        }
                        EditorButton {
                            objectName: "reloadLatestTuningDraft"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("载入最新草稿")
                            visible: page.hasSource && !page.draftConflict
                            enabled: !page.busy && boolOr(page.view.canReloadDraft, false)
                            onClicked: page.controller.reloadLatestDraft()
                        }
                        EditorButton {
                            objectName: "saveTuningDraftRevision"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.vwaTr("保存新修订")
                            visible: page.hasSource && !page.draftConflict
                            enabled: !page.busy && boolOr(page.view.canSaveDraftRevision, false)
                            onClicked: page.controller.saveDraftRevision()
                        }
                        EditorButton {
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

    Dialog {
        id: draftRecovery
        objectName: "tuningDraftRecoveryDialog"
        title: page.vwaTr("核对调音草稿")
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
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
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.textOf(page.view.message)
            }
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!draftRecovery.review.path
                text: page.vwaTr("共享版本 ") + page.textOf(draftRecovery.review.revision) + "：" + page.textOf(draftRecovery.review.shared)
            }
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!draftRecovery.review.path
                text: page.vwaTr("本地副本：") + page.textOf(draftRecovery.review.local) + "\n"
                    + page.fileName(draftRecovery.review.path)
            }
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                visible: !!draftRecovery.review.path
                text: page.vwaTr("载入共享草稿会保留本地副本。恢复本地编辑前，也会为当前共享版本另存副本。")
            }
        }
        footer: DialogButtonBox {
            EditorButton {
                theme: page.theme
                text: draftRecovery.review.path ? page.vwaTr("稍后处理") : page.vwaTr("关闭")
                enabled: !page.view.busy
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
                onClicked: draftRecovery.close()
            }
            EditorButton {
                objectName: "loadSharedTuningDraft"
                theme: page.theme
                text: page.vwaTr("载入共享草稿")
                enabled: !page.view.busy && !!draftRecovery.review.path
                DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                onClicked: page.controller.resolveDraftRecovery(false)
            }
            EditorButton {
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
        title: page.vwaTr("另存已核验的 SVP")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "svp"
        nameFilters: [page.vwaTr("Synthesizer V 工程 (*.svp)")]
        onAccepted: page.controller.exportCandidate(selectedFile)
    }

    Dialog {
        id: clearRegionsDialog
        objectName: "tuningClearRegionsDialog"
        title: page.vwaTr("清空风格片段")
        modal: true
        anchors.centerIn: parent
        width: Math.min(page.width - 28, Math.round(375 * page.scale))
        standardButtons: Dialog.Cancel | Dialog.Ok
        onAccepted: page.controller.clearRegions()
        contentItem: HelperText {
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
            page.vwaTr("SynthV 工程 (*.svp)"),
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

    Dialog {
        id: taskPickerDialog
        objectName: "tuningTaskPickerDialog"
        title: page.vwaTr("从任务与结果载入工程")
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, Math.round(580 * page.scale))
        closePolicy: Popup.CloseOnEscape
        property string jobId: ""
        property string artifactId: ""
        readonly property var taskRows: page.controller.tuningTasks || []
        readonly property var projectRows: page.controller.tuningTaskProjects || []

        contentItem: ColumnLayout {
            spacing: Math.round(9 * page.scale)
            HelperText {
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
            HelperText {
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
        title: page.vwaTr("绑定原参数 SVP")
        fileMode: FileDialog.OpenFile
        nameFilters: [
            page.vwaTr("SynthV 工程 (*.svp)"),
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

    Dialog {
        id: presetNameDialog
        objectName: "tuningPresetNameDialog"
        title: page.vwaTr("保存到本机预设库")
        parent: Overlay.overlay
        modal: true
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
            ThemedTextField {
                id: presetNameField
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: page.vwaTr("例如：主唱轻推 · 当前工程")
                Accessible.name: page.vwaTr("调音预设名称")
            }
            HelperText {
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
