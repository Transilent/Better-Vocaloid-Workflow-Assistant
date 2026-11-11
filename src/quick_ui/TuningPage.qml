import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ScrollView {
    id: page
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
    readonly property bool busy: view.busy === true
    readonly property bool hasSource: textOf(view.source).length > 0
    readonly property string activeId: textOf(view.activeTrack)
    readonly property string mode: String(view.mode || "single") === "multi" ? "multi" : "single"
    readonly property var activeTrackData: findTrack(activeId)
    readonly property int contentMargin: stacked ? 14 : 24
    readonly property real railWidth: Math.max(Math.round(330 * scale),
        Math.min(Math.round(410 * scale), availableWidth * 0.36))
    readonly property real timelineHeight: stacked
        ? Math.max(Math.round(268 * scale), Math.min(Math.round(360 * scale),
            availableHeight * 0.36))
        : Math.max(Math.round(360 * scale), Math.min(Math.round(540 * scale),
            availableHeight * 0.58))
    readonly property bool hasRange: validRange().length > 0
    readonly property bool hasLegacyDraft: hasValue(view.legacyDraft)
    readonly property bool targetEditable: editableTargets()
    readonly property bool canAddRegion: hasSource && hasRange && !busy && !hasLegacyDraft
        && targetIds().length > 0 && targetEditable && textOf(view.style).length > 0
    readonly property bool candidatePresent: hasValue(view.candidate)
    readonly property bool expressionModeKnown: textOf(view.expressionMode).length > 0
    readonly property int regionCount: regions.length

    property int retainedIndexDraft: 0
    property string selectedPresetKey: ""

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

    function syncStyleStrengthSlider(force) {
        const target = Math.round(Math.max(0, Math.min(1, numberOr(view.styleStrength, 0))) * 100)
        if (force || (!styleStrengthSlider.pressed
                      && Math.abs(styleStrengthSlider.value - target) > 0.5))
            styleStrengthSlider.value = target
    }

    function strengthText(value) {
        const strength = numberOr(value, 0)
        return String(Math.round(Math.max(0, Math.min(1, strength)) * 100)) + "%"
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

    function strengthSelected(name, value) {
        return optionKnown(name)
            && Math.abs(numberOr(optionValue(name, 0), 0) - value) < 0.005
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

    function clampViewport(start, span) {
        const origin = timelineOrigin()
        const full = timelineSpan()
        const boundedSpan = Math.max(Math.min(full, 0.5), Math.min(full,
            numberOr(span, full)))
        const boundedStart = Math.max(origin, Math.min(origin + full - boundedSpan,
            numberOr(start, origin)))
        return { start: boundedStart, span: boundedSpan }
    }

    function zoomTimeline(factor) {
        if (!hasSource)
            return
        const start = numberOr(view.viewportStart, timelineOrigin())
        const span = Math.max(0.001, numberOr(view.viewportSpan, timelineSpan()))
        const centered = clampViewport(start + span * (1 - factor) / 2,
            span * factor)
        controller.setViewport(centered.start, centered.span)
    }

    function panTimeline(fraction) {
        if (!hasSource)
            return
        const start = numberOr(view.viewportStart, timelineOrigin())
        const span = Math.max(0.001, numberOr(view.viewportSpan, timelineSpan()))
        const next = clampViewport(start + span * fraction, span)
        controller.setViewport(next.start, next.span)
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
    onViewChanged: {
        syncPresetChoice()
        syncPreciseSelection(false)
        syncStyleStrengthSlider(false)
    }
    Component.onCompleted: {
        syncRetainedDraft()
        syncPresetChoice()
        syncPreciseSelection(true)
        syncStyleStrengthSlider(true)
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

    component StrengthButton: UiButton {
        property real choiceValue: 0
        property bool knownValue: true
        property real currentValue: 0

        text: String(Math.round(choiceValue * 100)) + "%"
        selected: knownValue && Math.abs(currentValue - choiceValue) < 0.005
        leftPadding: Math.round(8 * theme.scale)
        rightPadding: leftPadding
        implicitWidth: Math.max(Math.round(48 * theme.scale),
            implicitContentWidth + leftPadding + rightPadding)
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: page.stacked ? 11 : 14

        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: page.stacked ? 15 : 22
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            spacing: Math.round(6 * page.scale)

            Text {
                Layout.fillWidth: true
                text: "逐轨调音"
                color: page.theme.text
                font.pixelSize: Math.round(25 * page.scale)
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                visible: !page.stacked || !page.hasSource
                text: page.hasSource
                    ? "按歌词定位真实音符，单轨精修或多轨共享区间；风格颜色继续标出已应用片段。"
                    : "打开 SynthV 工程后，从歌词、轨道和真实音符开始工作。"
                color: page.theme.muted
                font.pixelSize: Math.round(13 * page.scale)
                lineHeight: 1.2
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            columns: page.stacked ? 1 : 2
            columnSpacing: Math.round(14 * page.scale)
            rowSpacing: Math.round(8 * page.scale)

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Math.round(3 * page.scale)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(7 * page.scale)
                    PanelLabel {
                        theme: page.theme
                        text: page.hasSource ? page.fileName(page.view.source) : "尚未载入调音工程"
                        elide: Text.ElideMiddle
                    }
                    Text {
                        visible: page.textOf(page.view.sha256).length > 0
                        text: "SHA " + page.shortHash(page.view.sha256)
                        color: page.theme.muted
                        font.pixelSize: Math.round(10 * page.scale)
                        font.family: "Consolas"
                        textFormat: Text.PlainText
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: page.hasSource && !page.stacked
                    text: page.textOf(page.view.source)
                    color: page.theme.muted
                    font.pixelSize: Math.round(11 * page.scale)
                    elide: Text.ElideMiddle
                    textFormat: Text.PlainText
                    HoverHandler { id: sourcePathHover }
                    ToolTip.visible: sourcePathHover.hovered
                    ToolTip.text: page.view.job ? text + "\n任务：" + page.view.job : text
                }
            }

            RowLayout {
                Layout.fillWidth: page.stacked
                Layout.alignment: page.stacked ? Qt.AlignLeft : Qt.AlignRight | Qt.AlignVCenter
                spacing: Math.round(7 * page.scale)

                UiButton {
                    objectName: "tuningModeSingle"
                    theme: page.theme
                    text: "单轨精修"
                    selected: page.mode === "single"
                    enabled: !page.busy
                    onClicked: page.controller.setMode("single")
                }
                UiButton {
                    objectName: "tuningModeMulti"
                    theme: page.theme
                    text: "多轨编排"
                    selected: page.mode === "multi"
                    enabled: !page.busy
                    onClicked: page.controller.setMode("multi")
                }
                UiButton {
                    objectName: "tuningOpenProject"
                    theme: page.theme
                    text: "打开工程…"
                    primary: true
                    enabled: !page.busy
                    onClicked: projectDialog.open()
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: page.contentMargin
            Layout.rightMargin: page.contentMargin
            visible: !page.hasSource
            implicitHeight: guideText.implicitHeight + Math.round(16 * page.scale)
            radius: Math.round(8 * page.scale)
            color: page.theme.raised

            Text {
                id: guideText
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Math.round(12 * page.scale)
                anchors.rightMargin: Math.round(12 * page.scale)
                text: "01 搜索并点击歌词  ·  02 选择单轨或多轨共享区间  ·  03 应用风格色并生成候选"
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.scale)
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
        }

        Rectangle {
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
                        text: "检测到旧版全局片段"
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
                    text: "按原轨道展开旧草稿"
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
            Layout.bottomMargin: page.stacked ? 16 : 24
            columns: page.stacked ? 1 : 2
            columnSpacing: Math.round(14 * page.scale)
            rowSpacing: Math.round(12 * page.scale)

            Rectangle {
                id: workspacePanel
                objectName: "tuningWorkspacePanel"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                implicitHeight: workspaceColumn.implicitHeight + Math.round(28 * page.scale)
                radius: Math.round(10 * page.scale)
                color: page.theme.surface
                border.width: 1
                border.color: page.theme.line

                ColumnLayout {
                    id: workspaceColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(14 * page.scale)
                    spacing: Math.round(10 * page.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.scale)

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(2 * page.scale)
                            PanelLabel {
                                theme: page.theme
                                text: "轨道目标"
                            }
                            HelperText {
                                theme: page.theme
                                visible: !page.stacked
                                text: page.mode === "multi"
                                    ? "勾选轨道共享同一时间段；焦点轨独立保留。"
                                    : "点击轨道切换精修焦点；引用组与异常轨保持只读。"
                            }
                        }
                        Text {
                            text: page.targetSummary()
                            color: page.targetEditable ? page.theme.text : page.theme.attention
                            font.pixelSize: Math.round(11 * page.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }

                    Flickable {
                        objectName: "tuningTrackStrip"
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round(43 * page.scale)
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
                                delegate: UiButton {
                                    id: trackChip
                                    required property var modelData
                                    required property int index

                                    readonly property bool isActive:
                                        String(modelData.id) === page.activeId
                                    readonly property bool isSelected: page.mode === "multi"
                                        ? page.listContains(page.view.selectedTracks, modelData.id)
                                        : isActive

                                    theme: page.theme
                                    text: (isActive ? "● " : "") + page.textOf(modelData.name)
                                        + (modelData.noteCount !== undefined
                                            ? "  " + modelData.noteCount : "")
                                        + (modelData.editable === false ? "  · 只读" : "")
                                        + (page.mode === "multi" && isSelected ? "  ✓" : "")
                                    selected: isSelected
                                    enabled: !page.busy && (page.mode === "single"
                                        || modelData.editable !== false)
                                    width: Math.max(Math.round(148 * page.scale),
                                        implicitWidth)
                                    Accessible.name: page.textOf(modelData.name)
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

                    Text {
                        Layout.fillWidth: true
                        visible: page.tracks.length === 0
                        text: page.hasSource ? "工程尚未投影出可编辑演唱轨。" : "打开工程后可查看轨道、歌词与音符。"
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.tracks.length > 0 && page.mode === "multi"
                        spacing: Math.round(8 * page.scale)
                        HelperText {
                            Layout.alignment: Qt.AlignVCenter
                            theme: page.theme
                            text: "焦点轨"
                        }
                        UiComboBox {
                            objectName: "tuningFocusTrack"
                            Layout.fillWidth: true
                            Layout.maximumWidth: Math.round(360 * page.scale)
                            theme: page.theme
                            model: page.tracks
                            textRole: "name"
                            valueRole: "id"
                            currentIndex: page.trackIndex(page.activeId)
                            enabled: !page.busy && page.tracks.length > 0
                            Accessible.name: "焦点轨"
                            onActivated: function(index) {
                                if (index >= 0 && index < page.tracks.length)
                                    page.controller.focusTrack(String(page.tracks[index].id))
                            }
                        }
                        Text {
                            text: page.activeTrackData && page.activeTrackData.editable === false
                                ? "当前焦点只读" : ""
                            color: page.theme.attention
                            font.pixelSize: Math.round(11 * page.scale)
                            textFormat: Text.PlainText
                        }
                    }

                    Divider {}

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.scale)

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.scale)
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(2 * page.scale)
                                PanelLabel {
                                    theme: page.theme
                                    text: "按歌词定位"
                                }
                                HelperText {
                                    theme: page.theme
                                    text: "点选一处歌词，时间轴会跳到对应轨道和时间。"
                                }
                            }
                            Text {
                                text: page.matches.length.toString()
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(7 * page.scale)

                            ThemedTextField {
                                id: lyricSearch
                                objectName: "tuningLyricSearch"
                                Layout.fillWidth: true
                                theme: page.theme
                                placeholderText: "搜索歌词、音节或重复词…"
                                text: page.textOf(page.view.query)
                                enabled: !page.busy && page.hasSource
                                Accessible.name: "搜索歌词"
                                onTextEdited: page.controller.setQuery(text)
                                onAccepted: page.controller.setQuery(text)
                            }
                            UiButton {
                                objectName: "tuningClearLyricSearch"
                                theme: page.theme
                                text: "清空"
                                quiet: true
                                visible: page.textOf(page.view.query).length > 0
                                enabled: !page.busy
                                onClicked: page.controller.setQuery("")
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.round(54 * page.scale)
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
                                    height: Math.round(48 * page.scale)
                                    hoverEnabled: true
                                    focusPolicy: Qt.StrongFocus
                                    Accessible.name: (page.textOf(modelData.lyric).length > 0
                                        ? modelData.lyric : "空歌词") + "，"
                                        + (modelData.trackName || page.trackName(modelData.trackId))
                                        + "，" + page.formatTime(modelData.start)

                                    onClicked: page.controller.selectMatch(
                                        String(modelData.trackId), String(modelData.id))

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
                                                    ? matchRow.modelData.lyric : "[空歌词]"
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
                                                        ? "  ·  音高 " + matchRow.modelData.pitch : "")
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
                                    ? "载入工程后可搜索歌词。"
                                    : page.textOf(page.view.query).length === 0
                                        ? "输入歌词查找；重复词会以轨道和时间区分。"
                                        : "没有找到匹配歌词，可尝试短词或清空搜索。"
                                color: page.theme.muted
                                font.pixelSize: Math.round(12 * page.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }
                    }

                    Divider {}

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.scale)

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(7 * page.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(1 * page.scale)
                                PanelLabel {
                                    theme: page.theme
                                    text: page.mode === "multi"
                                        ? "多轨共享时间轴" : "当前轨时间轴"
                                }
                                HelperText {
                                    theme: page.theme
                                    text: page.hasRange
                                        ? "选区 " + page.validRange() + "  ·  " + page.targetSummary()
                                        : "拖拽时间轴建立真实音符区间，或在歌词结果中定位。"
                                }
                            }

                            UiButton {
                                objectName: "timelinePanLeft"
                                theme: page.theme
                                text: "‹"
                                quiet: true
                                enabled: page.hasSource
                                onClicked: page.panTimeline(-0.35)
                                ToolTip.visible: hovered
                                ToolTip.text: "向左平移"
                            }
                            UiButton {
                                objectName: "timelineZoomOut"
                                theme: page.theme
                                text: "−"
                                quiet: true
                                enabled: page.hasSource
                                onClicked: page.zoomTimeline(1.25)
                                ToolTip.visible: hovered
                                ToolTip.text: "缩小"
                            }
                            UiButton {
                                objectName: "timelineZoomIn"
                                theme: page.theme
                                text: "+"
                                quiet: true
                                enabled: page.hasSource
                                onClicked: page.zoomTimeline(0.8)
                                ToolTip.visible: hovered
                                ToolTip.text: "放大"
                            }
                            UiButton {
                                objectName: "timelinePanRight"
                                theme: page.theme
                                text: "›"
                                quiet: true
                                enabled: page.hasSource
                                onClicked: page.panTimeline(0.35)
                                ToolTip.visible: hovered
                                ToolTip.text: "向右平移"
                            }
                            UiButton {
                                objectName: "timelineFit"
                                theme: page.theme
                                text: "全曲"
                                quiet: true
                                enabled: page.hasSource
                                onClicked: page.fitTimelineView()
                            }
                        }

                        Rectangle {
                            id: timelineFrame
                            objectName: "tuningTimelineFrame"
                            Layout.fillWidth: true
                            Layout.preferredHeight: page.timelineHeight
                            radius: Math.round(8 * page.scale)
                            color: page.theme.base
                            border.width: 1
                            border.color: page.theme.line
                            clip: true

                            TuningTimeline {
                                anchors.fill: parent
                                anchors.margins: 1
                                theme: page.theme
                                controller: page.controller
                                view: page.view
                                tracks: page.tracks
                                notes: page.notes
                                regions: page.regions
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.scale)

                            HelperText {
                                Layout.alignment: Qt.AlignVCenter
                                theme: page.theme
                                text: "精确选区"
                            }
                            Text {
                                text: "开始"
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                            SpinBox {
                                id: selectionStartInput
                                objectName: "tuningSelectionStart"
                                Layout.preferredWidth: Math.round(108 * page.scale)
                                from: Math.round(page.selectionMinimum() * 1000)
                                to: Math.round(Math.max(page.selectionMinimum(),
                                    page.selectionMaximum()) * 1000)
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
                                Accessible.name: "选区开始时间，秒"
                            }
                            Text {
                                text: "秒"
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                            Text {
                                text: "结束"
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                            SpinBox {
                                id: selectionEndInput
                                objectName: "tuningSelectionEnd"
                                Layout.preferredWidth: Math.round(108 * page.scale)
                                from: Math.round(page.selectionMinimum() * 1000)
                                to: Math.round(Math.max(page.selectionMinimum(),
                                    page.selectionMaximum()) * 1000)
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
                                Accessible.name: "选区结束时间，秒"
                            }
                            Text {
                                text: "秒"
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.scale)
                                textFormat: Text.PlainText
                            }
                            Item { Layout.fillWidth: true }
                            UiButton {
                                objectName: "applyTuningSelection"
                                theme: page.theme
                                text: "应用时间"
                                quiet: true
                                enabled: page.hasSource && !page.busy
                                onClicked: page.commitPreciseSelection()
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.scale)

                            Rectangle {
                                implicitWidth: Math.round(9 * page.scale)
                                implicitHeight: implicitWidth
                                radius: implicitWidth / 2
                                color: page.styleColor(page.view.style)
                            }
                            Text {
                                Layout.fillWidth: true
                                text: page.targetSummary() + "  ·  "
                                    + (page.hasRange ? page.validRange() : "尚未选择区间")
                                    + "  ·  " + page.styleLabel(page.view.style)
                                    + " " + page.strengthText(page.view.styleStrength)
                                color: page.theme.text
                                font.pixelSize: Math.round(11 * page.scale)
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                            UiButton {
                                objectName: "addTuningRegion"
                                theme: page.theme
                                text: "应用为片段"
                                primary: true
                                enabled: page.canAddRegion
                                onClicked: page.controller.addRegion()
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
                Layout.alignment: Qt.AlignTop
                implicitHeight: inspectorColumn.implicitHeight + Math.round(28 * page.scale)
                radius: Math.round(10 * page.scale)
                color: page.theme.surface
                border.width: 1
                border.color: page.theme.line

                ColumnLayout {
                    id: inspectorColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(14 * page.scale)
                    spacing: Math.round(9 * page.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.scale)
                        PanelLabel {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "风格与已应用片段"
                        }
                        Rectangle {
                            implicitWidth: Math.round(10 * page.scale)
                            implicitHeight: implicitWidth
                            radius: implicitWidth / 2
                            color: page.styleColor(page.view.style)
                        }
                    }

                    UiComboBox {
                        id: stylePicker
                        objectName: "tuningStylePicker"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: page.styles
                        textRole: "label"
                        valueRole: "id"
                        currentIndex: page.styleIndex(page.view.style)
                        enabled: !page.busy && page.styles.length > 0
                        Accessible.name: "风格"
                        onActivated: function(index) {
                            if (index >= 0 && index < page.styles.length)
                                page.controller.setStyle(String(page.styles[index].id))
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(4 * page.scale)

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.scale)
                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: "风格强度"
                            }
                            Text {
                                objectName: "tuningStyleStrengthPercent"
                                text: page.strengthText(page.view.styleStrength)
                                color: page.theme.text
                                font.pixelSize: Math.round(13 * page.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }

                        Slider {
                            id: styleStrengthSlider
                            objectName: "tuningStyleStrengthSlider"
                            Layout.fillWidth: true
                            from: 0
                            to: 100
                            value: Math.round(Math.max(0,
                                Math.min(1, page.numberOr(page.view.styleStrength, 0))) * 100)
                            stepSize: 1
                            snapMode: Slider.SnapAlways
                            enabled: !page.busy && page.hasSource
                            focusPolicy: Qt.StrongFocus
                            wheelEnabled: false
                            onMoved: page.controller.setStyleStrength(value / 100)
                            Accessible.name: "风格强度"
                            Accessible.description: "范围 0% 到 100%，每次调整 1%"

                            background: Rectangle {
                                y: Math.round((parent.height - height) / 2)
                                width: parent.width
                                height: Math.round(5 * page.scale)
                                radius: height / 2
                                color: page.theme.line
                                Rectangle {
                                    width: parent.width * parent.parent.visualPosition
                                    height: parent.height
                                    radius: parent.radius
                                    color: page.theme.accent
                                }
                            }
                            handle: Rectangle {
                                x: parent.visualPosition * (parent.width - width)
                                y: Math.round((parent.height - height) / 2)
                                width: Math.round(15 * page.scale)
                                height: width
                                radius: width / 2
                                color: page.theme.text
                                border.width: 1
                                border.color: page.theme.accent
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.scale)
                            HelperText {
                                Layout.alignment: Qt.AlignVCenter
                                theme: page.theme
                                text: "快捷"
                            }
                            StrengthButton {
                                theme: page.theme
                                choiceValue: 0.3
                                knownValue: true
                                currentValue: page.numberOr(page.view.styleStrength, 0)
                                onClicked: page.controller.setStyleStrength(0.3)
                            }
                            StrengthButton {
                                theme: page.theme
                                choiceValue: 0.6
                                knownValue: true
                                currentValue: page.numberOr(page.view.styleStrength, 0)
                                onClicked: page.controller.setStyleStrength(0.6)
                            }
                            StrengthButton {
                                theme: page.theme
                                choiceValue: 0.7
                                knownValue: true
                                currentValue: page.numberOr(page.view.styleStrength, 0)
                                onClicked: page.controller.setStyleStrength(0.7)
                            }
                            StrengthButton {
                                theme: page.theme
                                choiceValue: 1.0
                                knownValue: true
                                currentValue: page.numberOr(page.view.styleStrength, 0)
                                onClicked: page.controller.setStyleStrength(1.0)
                            }
                            Item { Layout.fillWidth: true }
                            UiButton {
                                objectName: "tuningStylePitch"
                                theme: page.theme
                                text: boolOr(page.view.stylePitch, false) ? "音高 · 开" : "音高 · 关"
                                selected: boolOr(page.view.stylePitch, false)
                                enabled: !page.busy
                                onClicked: page.controller.setStylePitch(!boolOr(page.view.stylePitch, false))
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "按 1% 步进；用方向键可键盘调节，0% 不应用。"
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.hasRange
                            ? "当前：" + page.validRange() + "  ·  "
                                + page.styleLabel(page.view.style) + " "
                                + page.strengthText(page.view.styleStrength)
                                + "  ·  " + page.targetSummary()
                            : "先定位歌词或拖拽时间轴选择区间。"
                        color: page.hasRange ? page.theme.text : page.theme.muted
                        font.pixelSize: Math.round(11 * page.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round(178 * page.scale)
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
                                height: Math.round(55 * page.scale)
                                color: regionHover.hovered ? page.theme.raised : "transparent"

                                HoverHandler { id: regionHover }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: Math.round(9 * page.scale)
                                    anchors.rightMargin: Math.round(6 * page.scale)
                                    spacing: Math.round(7 * page.scale)

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
                                            text: (regionRow.modelData.trackName
                                                    || page.trackName(regionRow.modelData.trackId))
                                                + "  ·  " + (regionRow.modelData.label
                                                    || page.styleLabel(regionRow.modelData.style))
                                            color: page.theme.text
                                            font.pixelSize: Math.round(11 * page.scale)
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: page.formatTime(regionRow.modelData.start)
                                                + "–" + page.formatTime(regionRow.modelData.end)
                                                + "  ·  " + page.strengthText(regionRow.modelData.strength)
                                            color: page.theme.muted
                                            font.pixelSize: Math.round(10 * page.scale)
                                            elide: Text.ElideRight
                                            textFormat: Text.PlainText
                                        }
                                    }

                                    UiButton {
                                        theme: page.theme
                                        text: "定位"
                                        quiet: true
                                        onClicked: page.controller.selectSpan(
                                            Number(regionRow.modelData.start),
                                            Number(regionRow.modelData.end))
                                    }
                                    UiButton {
                                        objectName: "updateTuningRegion_" + String(regionRow.modelData.id)
                                        theme: page.theme
                                        text: "更新"
                                        quiet: true
                                        enabled: !page.busy && page.hasRange
                                            && page.textOf(regionRow.modelData.id).length > 0
                                        onClicked: page.controller.updateRegion(
                                            String(regionRow.modelData.id))
                                        Accessible.name: "用当前选区和风格更新此逐轨片段"
                                        ToolTip.visible: hovered
                                        ToolTip.text: "轨道按此片段固定；范围和风格取当前选区"
                                    }
                                    UiButton {
                                        theme: page.theme
                                        text: "移除"
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

                        Text {
                            anchors.centerIn: parent
                            width: parent.width - Math.round(28 * page.scale)
                            visible: page.regionCount === 0
                            horizontalAlignment: Text.AlignHCenter
                            text: page.hasSource
                                ? "还没有已应用片段。选好区间和风格后应用。"
                                : "打开工程后可查看逐轨风格片段。"
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.scale)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                    }

                    Divider {}

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.scale)
                        PanelLabel {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "候选与结果"
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

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: Math.round(6 * page.scale)
                        rowSpacing: Math.round(6 * page.scale)

                        UiButton {
                            objectName: "freezeTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "冻结预览"
                            enabled: !page.busy && boolOr(page.view.canFreeze, false)
                            onClicked: page.controller.freeze()
                        }
                        UiButton {
                            objectName: "runTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "生成候选"
                            primary: true
                            enabled: !page.busy && boolOr(page.view.canRun, false)
                            onClicked: page.controller.run()
                        }
                        UiButton {
                            objectName: "cancelTuning"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "取消当前任务"
                            enabled: page.busy
                            onClicked: page.controller.cancel()
                        }
                        UiButton {
                            objectName: "registerTuningResult"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "登记结果"
                            enabled: !page.busy && boolOr(page.view.canRegister, false)
                            onClicked: page.controller.registerResult()
                        }
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
                            text: "已过期"
                            color: page.theme.error
                            font.pixelSize: Math.round(10 * page.scale)
                            font.weight: Font.DemiBold
                            textFormat: Text.PlainText
                        }
                        UiButton {
                            objectName: "openTuningCandidate"
                            theme: page.theme
                            text: "打开候选"
                            enabled: page.candidatePresent
                            onClicked: page.controller.openCandidate()
                        }
                    }

                    HelperText {
                        Layout.fillWidth: true
                        visible: boolOr(page.view.candidateStale, false)
                        theme: page.theme
                        text: "候选基线已变化，请重新冻结、生成或明确确认后再继续。"
                    }
                    UiButton {
                        objectName: "tuningOpenMixdown"
                        theme: page.theme
                        visible: page.candidatePresent
                        text: "导入已合成的歌声…"
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
                                    ? "候选基线已明确确认。"
                                    : "候选与当前工程基线不同，需要人工确认。"
                                color: boolOr(page.view.candidateConfirmed, false)
                                    ? page.theme.success : page.theme.attention
                                font.pixelSize: Math.round(11 * page.scale)
                                font.weight: Font.DemiBold
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                UiButton {
                                    objectName: "confirmCandidateBaseline"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: "确认候选基线"
                                    primary: !boolOr(page.view.candidateConfirmed, false)
                                    enabled: !page.busy
                                        && !boolOr(page.view.candidateConfirmed, false)
                                    onClicked: page.controller.confirmCandidate(true)
                                }
                                UiButton {
                                    objectName: "unconfirmCandidateBaseline"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: "恢复待确认"
                                    enabled: !page.busy
                                        && boolOr(page.view.candidateConfirmed, false)
                                    onClicked: page.controller.confirmCandidate(false)
                                }
                            }
                        }
                    }

                    Divider {}

                    PanelLabel {
                        theme: page.theme
                        text: "工程预设"
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
                        Accessible.name: "工程预设"
                        onActivated: function(index) {
                            if (index >= 0 && index < page.presets.length) {
                                page.selectedPresetKey = String(page.presets[index].key)
                                page.controller.applyPreset(page.selectedPresetKey)
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.textOf(page.view.presetName).length > 0
                            ? "当前工程绑定：" + page.view.presetName
                            : "当前工程尚未绑定预设。"
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
                        UiButton {
                            objectName: "importTuningPresets"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "导入附加…"
                            enabled: !page.busy
                            onClicked: presetImportDialog.open()
                        }
                        UiButton {
                            objectName: "exportTuningPreset"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "导出预设…"
                            enabled: !page.busy && page.selectedPresetKey.length > 0
                            onClicked: presetExportDialog.open()
                        }
                        UiButton {
                            objectName: "saveTuningPreset"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "保存到本机库"
                            enabled: !page.busy && page.hasSource
                            onClicked: {
                                presetNameField.text = page.textOf(page.view.presetName)
                                presetNameDialog.open()
                            }
                        }
                        UiButton {
                            objectName: "fitViewFromPresetArea"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "查看全曲"
                            enabled: page.hasSource
                            onClicked: page.fitTimelineView()
                        }
                    }

                    Divider {}

                    PanelLabel {
                        theme: page.theme
                        text: "表达来源与调音选项"
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: Math.round(5 * page.scale)
                        rowSpacing: Math.round(5 * page.scale)

                        UiButton {
                            objectName: "expressionPreserve"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "保留原表现"
                            selected: page.textOf(page.view.expressionMode) === "preserve"
                            enabled: !page.busy && page.hasSource
                            onClicked: page.controller.setExpressionMode("preserve")
                        }
                        UiButton {
                            objectName: "expressionReference"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "参考音频"
                            selected: page.textOf(page.view.expressionMode) === "reference"
                            enabled: !page.busy && page.hasSource
                            onClicked: page.controller.setExpressionMode("reference")
                        }
                        UiButton {
                            objectName: "expressionCalibrated"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "校准 + 参考"
                            selected: page.textOf(page.view.expressionMode) === "calibrated"
                            enabled: !page.busy && page.hasSource
                            onClicked: page.controller.setExpressionMode("calibrated")
                        }
                        UiButton {
                            objectName: "expressionRetained"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "沿用原参数"
                            selected: page.textOf(page.view.expressionMode) === "retained"
                            enabled: !page.busy && page.hasSource
                            onClicked: page.controller.setExpressionMode("retained")
                        }
                    }

                    HelperText {
                        Layout.fillWidth: true
                        theme: page.theme
                        text: page.expressionModeKnown
                            ? "四种来源共用当前轨道与区间；外部声库渲染仍由你手动完成。"
                            : "表达来源状态待后端回读；可先选择一种真实来源。"
                    }

                    Text {
                        Layout.fillWidth: true
                        text: "焦点轨：" + (page.activeId.length > 0
                            ? page.trackName(page.activeId) : "未选择")
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
                            text: "参考音频"
                        }
                        UiButton {
                            objectName: "bindReferenceAudio"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.textOf(page.bindingField("reference", "")).length > 0
                                ? "更换参考…" : "选择参考…"
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
                            text: "参考声部"
                        }
                        UiComboBox {
                            theme: page.theme
                            Layout.fillWidth: true
                            visible: page.textOf(page.bindingField("reference", "")).length > 0
                            model: [{label: "主唱", value: "lead"}, {label: "和声", value: "backing"},
                                    {label: "合并人声", value: "vocals"}]
                            currentIndex: indexOfValue(page.bindingField("referenceVoice", ""))
                            enabled: !page.busy
                            onActivated: page.controller.setReferenceVoice(page.activeId, currentValue)
                        }

                        HelperText {
                            Layout.alignment: Qt.AlignVCenter
                            theme: page.theme
                            text: "校准配置"
                        }
                        UiButton {
                            objectName: "bindCalibrationProfile"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "选择 JSON…"
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
                            text: "沿用的原 SVP"
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(5 * page.scale)
                            SpinBox {
                                id: retainedIndexInput
                                objectName: "retainedSourceTrackIndex"
                                Layout.preferredWidth: Math.round(68 * page.scale)
                                from: 0
                                to: 255
                                value: page.retainedIndexDraft
                                editable: true
                                enabled: !page.busy && page.activeId.length > 0
                                Accessible.name: "原工程源轨索引"
                                onValueModified: page.retainedIndexDraft = value
                            }
                            UiButton {
                                objectName: "bindRetainedProject"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.textOf(page.bindingField("retained", "")).length > 0
                                    ? "更换 SVP…" : "选择 SVP…"
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
                                + "  ·  源轨索引 " + page.retainedIndexDraft
                            color: page.theme.muted
                            font.pixelSize: Math.round(10 * page.scale)
                            elide: Text.ElideMiddle
                            textFormat: Text.PlainText
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: Math.round(5 * page.scale)
                        rowSpacing: Math.round(5 * page.scale)

                        UiButton {
                            objectName: "optionReferenceDynamics"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.boolOptionText("reference_dynamics", "参考力度")
                            selected: boolOr(page.optionValue("reference_dynamics", false), false)
                            enabled: !page.busy
                            onClicked: page.toggleOption("reference_dynamics")
                        }
                        UiButton {
                            objectName: "optionReferenceTension"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.boolOptionText("reference_tension", "参考张力")
                            selected: boolOr(page.optionValue("reference_tension", false), false)
                            enabled: !page.busy
                            onClicked: page.toggleOption("reference_tension")
                        }
                        UiButton {
                            objectName: "optionReferenceBreathiness"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.boolOptionText("reference_breathiness", "参考气声")
                            selected: boolOr(page.optionValue("reference_breathiness", false), false)
                            enabled: !page.busy
                            onClicked: page.toggleOption("reference_breathiness")
                        }
                        UiButton {
                            objectName: "optionReferenceVoicing"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.boolOptionText("reference_voicing", "参考发声")
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
                            text: "动态强度"
                        }
                        StrengthButton {
                            theme: page.theme
                            choiceValue: 0.3
                            knownValue: page.optionKnown("dynamics_strength")
                            currentValue: page.numberOr(page.optionValue("dynamics_strength", 0), 0)
                            onClicked: page.controller.setTuningOption("dynamics_strength", 0.3)
                        }
                        StrengthButton {
                            theme: page.theme
                            choiceValue: 0.6
                            knownValue: page.optionKnown("dynamics_strength")
                            currentValue: page.numberOr(page.optionValue("dynamics_strength", 0), 0)
                            onClicked: page.controller.setTuningOption("dynamics_strength", 0.6)
                        }
                        StrengthButton {
                            theme: page.theme
                            choiceValue: 1.0
                            knownValue: page.optionKnown("dynamics_strength")
                            currentValue: page.numberOr(page.optionValue("dynamics_strength", 0), 0)
                            onClicked: page.controller.setTuningOption("dynamics_strength", 1.0)
                        }
                    }

                    UiComboBox {
                        objectName: "tuningTechniquePicker"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: page.techniques
                        textRole: "label"
                        valueRole: "id"
                        currentIndex: page.techniqueIndex(
                            page.optionValue("style_technique", ""))
                        enabled: !page.busy && page.techniques.length > 0
                        Accessible.name: "唱腔技法"
                        onActivated: function(index) {
                            if (index >= 0 && index < page.techniques.length)
                                page.controller.setTuningOption("style_technique",
                                    String(page.techniques[index].id))
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(5 * page.scale)
                        HelperText {
                            Layout.alignment: Qt.AlignVCenter
                            theme: page.theme
                            text: "唱腔强度"
                        }
                        StrengthButton {
                            theme: page.theme
                            choiceValue: 0.3
                            knownValue: page.optionKnown("technique_strength")
                            currentValue: page.numberOr(page.optionValue("technique_strength", 0), 0)
                            onClicked: page.controller.setTuningOption("technique_strength", 0.3)
                        }
                        StrengthButton {
                            theme: page.theme
                            choiceValue: 0.6
                            knownValue: page.optionKnown("technique_strength")
                            currentValue: page.numberOr(page.optionValue("technique_strength", 0), 0)
                            onClicked: page.controller.setTuningOption("technique_strength", 0.6)
                        }
                        StrengthButton {
                            theme: page.theme
                            choiceValue: 1.0
                            knownValue: page.optionKnown("technique_strength")
                            currentValue: page.numberOr(page.optionValue("technique_strength", 0), 0)
                            onClicked: page.controller.setTuningOption("technique_strength", 1.0)
                        }
                        UiButton {
                            objectName: "tuningTechniquePitch"
                            theme: page.theme
                            text: page.optionKnown("technique_pitch")
                                ? (boolOr(page.optionValue("technique_pitch", false), false)
                                    ? "音高 · 开" : "音高 · 关")
                                : "音高 · 待回读"
                            selected: page.optionKnown("technique_pitch")
                                && boolOr(page.optionValue("technique_pitch", false), false)
                            enabled: !page.busy
                            onClicked: page.toggleOption("technique_pitch")
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(5 * page.scale)
                        HelperText {
                            Layout.alignment: Qt.AlignVCenter
                            theme: page.theme
                            text: "表达采样"
                        }
                        UiButton {
                            objectName: "tuningDensityDense"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "加密采样"
                            selected: page.optionKnown("expression_density")
                                && String(page.optionValue("expression_density", "")) === "dense"
                            enabled: !page.busy
                            onClicked: page.controller.setTuningOption("expression_density", "dense")
                        }
                        UiButton {
                            objectName: "tuningDensityOriginal"
                            Layout.fillWidth: true
                            theme: page.theme
                            text: "原始采样"
                            selected: page.optionKnown("expression_density")
                                && String(page.optionValue("expression_density", "")) === "original"
                            enabled: !page.busy
                            onClicked: page.controller.setTuningOption("expression_density", "original")
                        }
                    }

                    HelperText {
                        Layout.fillWidth: true
                        theme: page.theme
                        text: "这里只编辑工程与参数；声库合成、真实听感和外部编辑器中的手动渲染保持独立。"
                    }
                }
            }
        }
    }

    FileDialog {
        id: projectDialog
        objectName: "tuningProjectDialog"
        title: "导入调音工程"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "SynthV 工程 (*.svp)",
            "所有文件 (*)"
        ]
        onAccepted: page.controller.chooseProject(selectedFile)
    }

    FileDialog {
        id: referenceDialog
        objectName: "tuningReferenceDialog"
        property string trackId: ""
        title: "绑定参考演唱音频"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "音频文件 (*.wav *.flac *.mp3 *.m4a *.ogg *.aac *.opus)",
            "所有文件 (*)"
        ]
        onAccepted: page.controller.bindReference(trackId, selectedFile)
    }

    FileDialog {
        id: calibrationDialog
        objectName: "tuningCalibrationDialog"
        title: "绑定校准配置"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "校准配置 (*.json)",
            "所有文件 (*)"
        ]
        onAccepted: page.controller.bindCalibration(selectedFile)
    }

    FileDialog {
        id: retainedDialog
        objectName: "tuningRetainedDialog"
        property string trackId: ""
        property int sourceTrackIndex: 0
        title: "绑定原参数 SVP"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "SynthV 工程 (*.svp)",
            "所有文件 (*)"
        ]
        onAccepted: page.controller.bindRetained(trackId, selectedFile, sourceTrackIndex)
    }

    FileDialog {
        id: presetImportDialog
        objectName: "tuningPresetImportDialog"
        title: "导入附加调音预设"
        fileMode: FileDialog.OpenFile
        nameFilters: [
            "调音预设 (*.json)",
            "所有文件 (*)"
        ]
        onAccepted: page.controller.importPresets(selectedFile)
    }

    FileDialog {
        id: presetExportDialog
        objectName: "tuningPresetExportDialog"
        title: "导出调音预设"
        fileMode: FileDialog.SaveFile
        nameFilters: [
            "调音预设 (*.json)",
            "所有文件 (*)"
        ]
        onAccepted: {
            if (page.selectedPresetKey.length > 0)
                page.controller.exportPreset(selectedFile, page.selectedPresetKey)
        }
    }

    Dialog {
        id: presetNameDialog
        objectName: "tuningPresetNameDialog"
        title: "保存到本机预设库"
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
                text: "预设名称"
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            ThemedTextField {
                id: presetNameField
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: "例如：主唱轻推 · 当前工程"
                Accessible.name: "调音预设名称"
            }
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: "名称只用于当前明确的保存或导出操作；工程、音频和声库绑定仍保留在本机。"
            }
        }

        footer: RowLayout {
            spacing: Math.round(7 * page.scale)
            Item { Layout.fillWidth: true }
            UiButton {
                theme: page.theme
                text: "取消"
                quiet: true
                onClicked: presetNameDialog.close()
            }
            UiButton {
                theme: page.theme
                text: "保存"
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
