import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
    }
    objectName: "mixdownPage"
    required property QtObject theme
    required property QtObject controller

    readonly property var view: controller.view || ({})
    readonly property var tracks: controller.tracks || []
    readonly property var bands: controller.bands || []
    readonly property var eqCurve: controller.eqCurve || []
    readonly property var meters: controller.meters || ({})
    readonly property var grHistory: controller.grHistory || ({})
    readonly property var reverbTelemetry: meters && meters.reverb ? meters.reverb : ({})
    readonly property bool compressorMeasured: !!meters && meters.compressor_available === true
    readonly property bool compressorEnabledMeasured: !!meters && meters.compressor_enabled === true
    readonly property bool limiterMeasured: !!meters && meters.limiter_available === true
    readonly property bool limiterEnabledMeasured: !!meters && meters.limiter_enabled === true
    readonly property var spectrum: controller.spectrum || ({})
    readonly property var reverbCurve: controller.reverbCurve || []
    readonly property var presets: controller.presets || []
    readonly property var exportProgress: view.exportProgress || ({})
    readonly property string resultPath: page.textValue(view.result, "")
    readonly property bool hasResult: resultPath.length > 0
    readonly property bool resultExpired: hasResult && !!view.resultStale
    readonly property bool taskResult: page.textValue(view.job, "").length > 0
    readonly property bool registeredMix: hasResult && !resultExpired && !!view.canComposeVideo
        && String(view.videoHandoffMode || "") === "registered"
    readonly property bool independentMix: hasResult && !resultExpired && !!view.canComposeVideo
        && String(view.videoHandoffMode || "") === "independent"
    readonly property bool taskNeedsRegistration: hasResult && !resultExpired && taskResult
        && !!view.canRegister && !registeredMix && !independentMix
    readonly property bool exportTargetReusesResult: hasResult
        && page.sameLocalPath(view.output, resultPath)
    readonly property string deliveryState: {
        const state = String(view.state || "");
        if (state === "exporting")
            return "exporting";
        if (state === "registering")
            return "registering";
        if (resultExpired)
            return "expired";
        if (registeredMix)
            return "registered";
        if (independentMix)
            return "independent";
        if (taskNeedsRegistration)
            return "awaiting";
        if (hasResult)
            return "review";
        if (state === "failed" || state === "error")
            return "error";
        if (!!view.componentMissing)
            return "blocked";
        return "empty";
    }
    readonly property string primaryHandoffAction: {
        if (deliveryState === "exporting" || deliveryState === "registering")
            return "none";
        if (deliveryState === "registered" || deliveryState === "independent")
            return "compose";
        if (deliveryState === "awaiting")
            return "register";
        return "export";
    }

    readonly property string analysisMode: String(view.analysis || "eq").toLowerCase()
    readonly property string selectionMode: String(view.selection || "B").toUpperCase() === "A" ? "A" : "B"
    readonly property bool hasMedia: !!view.hasMedia
    readonly property bool canPlay: !!view.canPlay
    readonly property bool playing: !!view.playing
    readonly property bool loading: !!view.loading
    readonly property bool busy: !!view.busy
    readonly property bool expanded: !!view.expanded
    readonly property int durationMs: Math.max(0, page.number(view.duration, 0))
    readonly property real positionMs: page.clamp(page.number(controller ? controller.position : 0, 0), 0, Math.max(1, durationMs))
    readonly property bool compact: width / theme.scale < 1010 || height / theme.scale < 575
    readonly property int pageMargin: Math.round((compact ? 14 : 20) * theme.scale)
    readonly property int vocalTrackCount: page.countVocalTracks()
    readonly property int vocalTrackLimit: page.number(view.maxVocalTracks, 0)
    readonly property bool canAddTrack: vocalTrackCount < vocalTrackLimit
    readonly property bool spaceBlocked: page.dialogCount > 0 || page.focusBlocksSpace(page.Window.activeFocusItem)

    property bool seeking: false
    property real pendingPosition: 0
    property int dialogCount: 0
    property string compactPanel: "analysis"
    property bool showCompactEqDetails: false
    property bool showCompactPresets: false
    property bool showPreviewRange: false
    function number(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback;
        const parsed = Number(value);
        return isFinite(parsed) ? parsed : fallback;
    }

    function clamp(value, low, high) {
        return Math.max(low, Math.min(high, value));
    }

    function textValue(value, fallback) {
        return value === undefined || value === null || String(value).length === 0 ? (fallback === undefined ? "" : fallback) : String(value);
    }

    function boolOption(name, fallback) {
        const value = page.view ? page.view[name] : undefined;
        return value === undefined || value === null ? fallback : !!value;
    }

    function trackValue(track, key, fallback) {
        if (!track || track[key] === undefined || track[key] === null)
            return fallback;
        return track[key];
    }

    function countVocalTracks() {
        let count = 0;
        for (let index = 0; index < page.tracks.length; ++index) {
            const track = page.tracks[index];
            if (track && String(track.id).startsWith("vocal-"))
                ++count;
        }
        return count;
    }

    function trackById(id) {
        for (const track of page.tracks) {
            if (track.id === id)
                return track;
        }
        return ({});
    }

    function laneMeter(id) {
        const lanes = page.meters && page.meters.lanes ? page.meters.lanes : [];
        const wanted = String(id);
        for (let index = 0; index < lanes.length; ++index) {
            if (lanes[index] && String(lanes[index].id) === wanted)
                return lanes[index];
        }
        return null;
    }

    function laneValue(id, key) {
        const lane = page.laneMeter(id);
        if (!lane || lane[key] === undefined || lane[key] === null)
            return NaN;
        const value = Number(lane[key]);
        return isFinite(value) ? value : NaN;
    }

    function finite(value) {
        if (value === undefined || value === null || value === "")
            return false;
        const parsed = Number(value);
        return isFinite(parsed);
    }

    function dbText(value, unit) {
        return page.finite(value) ? Number(value).toFixed(1) + (unit || " dB") : "—";
    }

    function percentText(value) {
        return page.finite(value) ? Math.round(page.clamp(Number(value), 0, 1) * 100) + "%" : "—";
    }

    function timeText(milliseconds) {
        const total = Math.max(0, Math.floor(page.number(milliseconds, 0) / 1000));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const seconds = total % 60;
        const two = function (value) {
            return String(value).padStart(2, "0");
        };
        return hours > 0 ? hours + ":" + two(minutes) + ":" + two(seconds) : two(minutes) + ":" + two(seconds);
    }

    function shortPath(value) {
        const raw = page.textValue(value, "");
        if (raw.length === 0)
            return "";
        const normalized = raw.replace(/\\/g, "/");
        const pieces = normalized.split("/");
        return pieces.length > 1 ? pieces[pieces.length - 1] : raw;
    }

    function resultText(value) {
        if (value === undefined || value === null || value === "")
            return "";
        if (typeof value === "string")
            return value;
        if (value.path !== undefined)
            return String(value.path);
        if (value.output_path !== undefined)
            return String(value.output_path);
        return String(value);
    }

    function presetIndex(name) {
        const wanted = String(name || "");
        if (wanted.length === 0)
            return -1;
        for (let index = 0; index < page.presets.length; ++index) {
            const item = page.presets[index] || ({});
            if (String(item.key) === wanted || String(item.label) === wanted)
                return index;
        }
        return -1;
    }

    function currentPresetKey() {
        const index = presetCombo.currentIndex;
        if (index < 0 || index >= page.presets.length)
            return "";
        const key = String(page.presets[index].key || "");
        return key === "custom" ? "" : key;
    }

    function chooseTrackSource(trackId) {
        if (page.busy || page.loading || !trackId)
            return;
        sourceDialog.sourceTrackId = String(trackId);
        page.openManagedDialog(sourceDialog);
    }

    function openManagedDialog(dialog) {
        page.noteDialogOpened();
        dialog.open();
    }

    function seekPositionAt(x) {
        if (!page.hasMedia || page.durationMs <= 0)
            return 0;
        return page.clamp(x / Math.max(1, seekSlider.width), 0, 1) * page.durationMs;
    }

    function beginSeekGesture(position) {
        if (page.seeking || !page.hasMedia || page.loading || page.durationMs <= 0)
            return false;
        page.pendingPosition = page.clamp(position, 0, page.durationMs);
        page.seeking = true;
        page.controller.beginSeek();
        return true;
    }

    function updateSeekGesture(position) {
        if (page.seeking)
            page.pendingPosition = page.clamp(position, 0, page.durationMs);
    }

    function finishSeekGesture(commit) {
        if (!page.seeking)
            return false;
        const target = page.clamp(page.pendingPosition, 0, page.durationMs);
        page.seeking = false;
        if (commit) {
            page.controller.commitSeek(target);
        } else if (page.controller && typeof page.controller.cancelSeek === "function") {
            page.controller.cancelSeek();
        }
        return true;
    }

    function cancelSeekGesture() {
        if (keyboardSeekTimer && keyboardSeekTimer.running)
            keyboardSeekTimer.stop();
        return page.finishSeekGesture(false);
    }

    function keyboardSeek(event) {
        if (event.key === Qt.Key_Escape && page.seeking) {
            page.cancelSeekGesture();
            event.accepted = true;
            return;
        }
        if (!seekSlider.enabled || (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)))
            return;
        let target = page.seeking ? page.pendingPosition : page.positionMs;
        const delta = (event.modifiers & Qt.ShiftModifier) ? 100 : 500;
        if (event.key === Qt.Key_Left || event.key === Qt.Key_Down)
            target -= delta;
        else if (event.key === Qt.Key_Right || event.key === Qt.Key_Up)
            target += delta;
        else if (event.key === Qt.Key_PageDown)
            target -= 5000;
        else if (event.key === Qt.Key_PageUp)
            target += 5000;
        else if (event.key === Qt.Key_Home)
            target = 0;
        else if (event.key === Qt.Key_End)
            target = page.durationMs;
        else
            return;
        if (seekGrab.pressed)
            return;
        if (page.seeking)
            page.updateSeekGesture(target);
        else if (!page.beginSeekGesture(target))
            return;
        keyboardSeekTimer.restart();
        event.accepted = true;
    }
    function noteDialogOpened() {
        page.dialogCount += 1;
    }

    function noteDialogClosed() {
        page.dialogCount = Math.max(0, page.dialogCount - 1);
    }

    function focusBlocksSpace(item) {
        let current = item;
        while (current) {
            if (String(current.objectName || "") === "mixdownEqPlot")
                return true
            if (current instanceof TextInput || current instanceof TextEdit || current instanceof TextField || current instanceof TextArea || current instanceof SpinBox || current instanceof ComboBox || current instanceof Popup || current instanceof Dialog)
                return true;
            current = current.parent;
        }
        return false;
    }

    function previewTimeText(milliseconds) {
        return (Number(milliseconds || 0) / 1000).toFixed(3);
    }

    function exportPhaseText(phase) {
        const labels = {prepare: "准备", decode: "解码", mix: "混音", encode: "编码",
                        loudness: "响度分析", done: "完成", cancelled: "已取消", failed: "失败"};
        return labels[String(phase || "")] || String(phase || "处理中");
    }

    function deliveryTitle() {
        switch (page.deliveryState) {
        case "exporting": return page.vwaTr("正在导出 WAV");
        case "registering": return page.vwaTr("正在登记任务");
        case "expired": return page.vwaTr("导出已过期");
        case "registered": return page.vwaTr("任务已核验");
        case "independent": return page.vwaTr("独立 WAV 有效");
        case "awaiting": return page.vwaTr("任务待登记");
        case "review": return page.vwaTr("导出结果待检查");
        case "error": return page.vwaTr("当前操作有阻塞");
        case "blocked": return page.vwaTr("混音组件未就绪");
        default: return page.vwaTr("尚未导出");
        }
    }

    function deliveryColor() {
        switch (page.deliveryState) {
        case "registered":
        case "independent":
            return page.theme.success;
        case "expired":
        case "awaiting":
        case "review":
        case "blocked":
            return page.theme.attention;
        case "error":
            return page.theme.error;
        case "exporting":
        case "registering":
            return page.theme.accent;
        default:
            return page.theme.muted;
        }
    }

    function handoffActionLabel() {
        if (page.primaryHandoffAction === "compose") {
            if (page.independentMix)
                return page.compact ? page.vwaTr("进入") : page.vwaTr("进入视频合成");
            return page.compact ? page.vwaTr("合成") : page.vwaTr("合成视频");
        }
        if (page.primaryHandoffAction === "register")
            return page.compact ? page.vwaTr("登记") : page.vwaTr("登记到任务");
        if (page.exportTargetReusesResult)
            return page.compact ? page.vwaTr("选择路径") : page.vwaTr("选择新路径");
        return page.compact ? page.vwaTr("导出") : page.vwaTr("导出新 WAV");
    }

    function handoffActionEnabled() {
        if (page.primaryHandoffAction === "compose")
            return !!page.view.canComposeVideo && !page.busy;
        if (page.primaryHandoffAction === "register")
            return !!page.view.canRegister && !page.resultExpired && !page.busy && !page.loading;
        if (page.exportTargetReusesResult)
            return !page.busy && !page.loading;
        return !!page.view.canExport && !page.busy && !page.loading;
    }

    function sameLocalPath(left, right) {
        const normalizedLeft = String(left || "").trim().replace(/\\/g, "/").toLowerCase();
        const normalizedRight = String(right || "").trim().replace(/\\/g, "/").toLowerCase();
        return normalizedLeft.length > 0 && normalizedLeft === normalizedRight;
    }

    function commitPreviewRange() {
        if (!page.hasMedia || page.loading)
            return;
        page.controller.setPreviewRange(Number(previewStartInput.value) / 1000,
                                        Number(previewDurationInput.value) / 1000);
    }

    function reviewPreset(key) {
        if (key && key !== "custom" && page.controller.previewPreset(key, false, false))
            presetReviewDialog.open();
    }

    function selectAnalysis(mode) {
        page.compactPanel = "analysis";
        page.controller.setAnalysis(mode);
    }

    onAnalysisModeChanged: if (analysisScroll) analysisScroll.contentY = 0

    function presetFieldName(field) {
        const labels = {"processing.eq_enabled": "人声 EQ", "processing.eq_bands": "EQ 频段",
            "processing.compression_enabled": "压缩", "processing.compression_strength": "压缩强度",
            "processing.reverb_enabled": "混响", "processing.reverb_profile": "混响空间",
            "processing.reverb_mix": "混响量", "processing.reverb_predelay_ms": "预延迟",
            "processing.width": "总输出宽度", "tracks": "歌声声像与发送"};
        return labels[field] || field;
    }

    function presetValueText(field, value) {
        if (field === "processing.eq_bands")
            return value.map((band, i) => "B" + (i + 1) + " " + (band.enabled ? "启用" : "旁路") + " "
                             + band.freq_hz + " Hz / " + band.gain_db + " dB / Q " + band.q).join("\n");
        if (field === "tracks")
            return value.map((track, i) => "歌声 " + (i + 1) + " · 声像 " + Math.round(track.pan * 100)
                             + " / 发送 " + page.percentText(track.reverb_send)).join("\n");
        if (typeof value === "boolean")
            return value ? "启用" : "关闭";
        if (field === "processing.compression_strength" || field === "processing.reverb_mix")
            return page.percentText(value);
        if (field === "processing.reverb_predelay_ms")
            return String(value) + " ms";
        return String(value);
    }

    onVisibleChanged: {
        if (!page.visible)
            page.cancelSeekGesture();
    }
    Component.onDestruction: page.cancelSeekGesture()
    Timer {
        id: keyboardSeekTimer
        interval: 85
        onTriggered: page.finishSeekGesture(true)
    }

    Connections {
        target: page.Window.window
        function onActiveChanged() {
            if (!target.active)
                page.cancelSeekGesture();
        }
    }
    Shortcut {
        objectName: "mixdownSpaceShortcut"
        sequence: "Space"
        autoRepeat: false
        context: Qt.WindowShortcut
        enabled: page.visible && !page.spaceBlocked && page.hasMedia && page.canPlay
        onActivated: page.controller.togglePlay()
    }
    component MixShortcut: Shortcut {
        context: Qt.WindowShortcut
        autoRepeat: false
        enabled: page.visible && !page.spaceBlocked
    }
    MixShortcut { sequence: "Alt+1"; onActivated: page.selectAnalysis("eq") }
    MixShortcut { sequence: "Alt+2"; onActivated: page.selectAnalysis("gr") }
    MixShortcut { sequence: "Alt+3"; onActivated: page.selectAnalysis("reverb") }
    MixShortcut { sequence: "Ctrl+Shift+A"; enabled: page.visible && !page.spaceBlocked && page.hasMedia && page.canPlay; onActivated: page.controller.selectMix("A") }
    MixShortcut { sequence: "Ctrl+Shift+B"; enabled: page.visible && !page.spaceBlocked && page.hasMedia && page.canPlay; onActivated: page.controller.selectMix("B") }
    MixShortcut { sequence: "Ctrl+Shift+L"; enabled: page.visible && !page.spaceBlocked && page.hasMedia && page.canPlay; onActivated: page.controller.setLoop(!page.view.loop) }

    Rectangle {
        anchors.fill: parent
        color: page.theme.base
    }

    ColumnLayout {
        id: headerLayout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: page.pageMargin
        anchors.rightMargin: page.pageMargin
        anchors.topMargin: page.pageMargin
        height: implicitHeight
        spacing: Math.round(7 * page.theme.scale)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(9 * page.theme.scale)

            Text {
                text: page.vwaTr("简易混音")
                color: page.theme.text
                font.pixelSize: Math.round(22 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Rectangle {
                visible: !page.compact
                width: Math.round(7 * page.theme.scale)
                height: width
                radius: width / 2
                color: page.busy ? page.theme.attention : page.theme.success
            }
            Text {
                Layout.fillWidth: true
                visible: !page.compact
                text: page.textValue(page.view.job, page.vwaTr("独立 WAV · 无需先创建任务"))
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                elide: Text.ElideMiddle
            }
            UiButton {
                objectName: "mixdownShowMixer"
                visible: page.compact && !page.expanded
                theme: page.theme
                quiet: true
                selected: page.compactPanel === "mixer"
                text: page.vwaTr("混音台")
                onClicked: page.compactPanel = "mixer"
            }
            UiButton {
                objectName: "mixdownShowAnalysis"
                visible: page.compact && !page.expanded
                theme: page.theme
                quiet: true
                selected: page.compactPanel === "analysis"
                text: page.vwaTr("曲线")
                Accessible.name: page.vwaTr("曲线与效果")
                onClicked: page.compactPanel = "analysis"
            }
            UiButton {
                objectName: "mixdownShowPresets"
                visible: page.compact && !page.expanded
                theme: page.theme
                quiet: true
                selected: page.showCompactPresets
                text: page.vwaTr("预设")
                onClicked: page.showCompactPresets = !page.showCompactPresets
            }
            UiButton {
                objectName: "mixdownAddTrackCompact"
                visible: page.compact && !page.expanded && page.compactPanel === "mixer"
                theme: page.theme
                glyph: "plus"
                text: page.vwaTr("添加歌声轨")
                enabled: page.canAddTrack && !page.busy && !page.loading
                onClicked: page.controller.addTrack()
            }
            Item { visible: page.compact; Layout.fillWidth: true }
            Text {
                visible: page.resultText(page.view.result).length > 0
                text: page.view.resultStale ? page.vwaTr("旧结果已过期") : page.vwaTr("已有导出结果")
                color: page.view.resultStale ? page.theme.attention : page.theme.success
                font.pixelSize: Math.round(11 * page.theme.scale)
            }
            UiButton {
                objectName: "mixdownInstallComponents"
                visible: !!page.view.componentMissing && !page.busy
                theme: page.theme
                primary: true
                text: page.vwaTr("安装混音组件")
                enabled: !page.busy
                onClicked: page.controller.installMixdown()
                Accessible.description: page.vwaTr("使用随包 manifest 与安装器离线安装已固定的混音组件")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: !page.expanded && (!page.compact || page.showCompactPresets)
            spacing: Math.round(7 * page.theme.scale)

            Text {
                text: page.vwaTr("方案")
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
            }
            UiComboBox {
                id: presetCombo
                objectName: "mixdownPreset"
                Layout.fillWidth: true
                Layout.maximumWidth: Math.round(250 * page.theme.scale)
                theme: page.theme
                model: page.presets
                currentIndex: page.presetIndex(page.view.presetName)
                enabled: page.presets.length > 0 && !page.busy && !page.loading
                Accessible.name: page.vwaTr("混音预设")
                onActivated: page.reviewPreset(String(currentValue || ""))
            }
            Text {
                visible: !page.compact
                text: page.vwaTr("当前 · ") + page.textValue(page.view.presetName, page.vwaTr("自定义"))
                color: page.theme.muted
                font.pixelSize: Math.round(10 * page.theme.scale)
                elide: Text.ElideRight
                Layout.maximumWidth: Math.round(130 * page.theme.scale)
            }
            Item {
                Layout.fillWidth: true
            }
            UiButton {
                objectName: "mixdownUndoPreset"
                theme: page.theme
                quiet: true
                visible: !!page.view.canUndoPreset
                enabled: !page.busy && !page.loading
                text: page.vwaTr("撤销应用")
                onClicked: page.controller.undoPreset()
            }
            UiButton {
                objectName: "mixdownSavePreset"
                theme: page.theme
                text: page.vwaTr("另存预设…")
                enabled: !page.busy && !page.loading
                onClicked: savePresetDialog.open()
            }
            UiButton {
                objectName: "mixdownImportPresets"
                theme: page.theme
                quiet: true
                text: page.vwaTr("导入预设")
                enabled: !page.busy && !page.loading
                onClicked: page.openManagedDialog(importPresetDialog)
            }
            UiButton {
                objectName: "mixdownExportPreset"
                theme: page.theme
                quiet: true
                text: page.vwaTr("导出预设")
                enabled: !page.busy && !page.loading && page.currentPresetKey().length > 0
                onClicked: page.openManagedDialog(exportPresetDialog)
            }
        }
    }

    RowLayout {
        id: workspace
        objectName: "mixdownViewport"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: headerLayout.bottom
        anchors.bottom: footer.top
        anchors.leftMargin: page.pageMargin
        anchors.rightMargin: page.pageMargin
        anchors.topMargin: Math.round(8 * page.theme.scale)
        anchors.bottomMargin: Math.round(8 * page.theme.scale)
        spacing: Math.round(12 * page.theme.scale)

        Rectangle {
            id: mixerPanel
            objectName: "mixdownMixerPanel"
            visible: !page.expanded && (!page.compact || page.compactPanel === "mixer")
            Layout.fillWidth: page.compact
            Layout.fillHeight: true
            Layout.preferredWidth: page.compact ? workspace.width
                : Math.round(page.clamp(workspace.width * .35, 372 * page.theme.scale, 590 * page.theme.scale))
            Layout.minimumWidth: 0
            Layout.minimumHeight: 0
            radius: Math.round(9 * page.theme.scale)
            color: page.theme.surface
            border.color: page.theme.line
            clip: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Math.round(10 * page.theme.scale)
                spacing: Math.round(8 * page.theme.scale)
                RowLayout {
                    Layout.fillWidth: true
                    visible: !page.compact
                    Text {
                        text: page.vwaTr("混音台")
                        color: page.theme.text
                        font.pixelSize: Math.round(14 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.vocalTrackCount + " / " + page.vocalTrackLimit + page.vwaTr(" 路歌声")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                    }
                    UiButton {
                        objectName: "mixdownAddTrack"
                        theme: page.theme
                        glyph: "plus"
                        text: page.vwaTr("添加歌声轨")
                        enabled: page.canAddTrack && !page.busy && !page.loading
                        onClicked: page.controller.addTrack()
                    }
                }

                Flickable {
                    id: channelViewport
                    objectName: "mixdownChannelViewport"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: Math.max(width, channelRow.implicitWidth)
                    contentHeight: channelRow.height
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: contentWidth > width || contentHeight > height
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    Row {
                        id: channelRow
                        height: Math.max(channelViewport.height, Math.round((page.compact ? 174 : 330) * page.theme.scale))
                        spacing: Math.round(5 * page.theme.scale)
                        readonly property int count: page.controller.trackIds.length + 1
                        readonly property real stripWidth: Math.floor(page.clamp(
                            (channelViewport.width - spacing * (count - 1)) / count,
                            72 * page.theme.scale, 132 * page.theme.scale))

                        Repeater {
                            model: page.controller.trackIds
                            ChannelStrip {
                                required property string modelData
                                required property int index
                                trackId: modelData
                                order: index
                                width: channelRow.stripWidth
                                height: channelRow.height
                            }
                        }
                        ChannelStrip {
                            trackId: "master"
                            width: channelRow.stripWidth
                            height: channelRow.height
                        }
                    }
                }

                ParameterSlider {
                    objectName: "mixdownWidth"
                    Layout.fillWidth: true
                    theme: page.theme
                    label: page.vwaTr("立体声宽度")
                    fromValue: 0
                    toValue: 2
                    displayValue: page.number(page.view.width, 1)
                    decimals: 2
                    step: 0.01
                    unit: "×"
                    resetValue: 1
                    interactive: !page.busy && !page.loading
                    onMoved: amount => page.controller.setOption("width", amount)
                    onResetRequested: page.controller.setOption("width", 1)
                    Accessible.name: page.vwaTr("立体声宽度")
                }
            }
        }

        Rectangle {
            id: analysisPanel
            objectName: "mixdownAnalysisPanel"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            Layout.minimumWidth: 0
            visible: page.expanded || !page.compact || page.compactPanel === "analysis"
            clip: true
            radius: Math.round(10 * page.theme.scale)
            color: page.theme.surface
            border.width: 1
            border.color: page.theme.line

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Math.round(11 * page.theme.scale)
                spacing: Math.round(8 * page.theme.scale)

                RowLayout {
                    Layout.fillWidth: true
                    UiButton {
                        objectName: "mixdownAnalysisEq"
                        theme: page.theme
                        selected: page.analysisMode === "eq"
                        text: "EQ"
                        ToolTip.visible: hovered
                        ToolTip.text: page.vwaTr("EQ 曲线 · Alt+1")
                            onClicked: page.selectAnalysis("eq")
                    }
                    UiButton {
                        objectName: "mixdownAnalysisGr"
                        theme: page.theme
                        selected: page.analysisMode === "gr"
                        text: page.vwaTr("动态 GR")
                        ToolTip.visible: hovered
                        ToolTip.text: page.vwaTr("压缩与限制器增益衰减 · Alt+2")
                            onClicked: page.selectAnalysis("gr")
                    }
                    UiButton {
                        objectName: "mixdownAnalysisReverb"
                        theme: page.theme
                        selected: page.analysisMode === "reverb"
                        text: page.vwaTr("混响")
                        ToolTip.visible: hovered
                        ToolTip.text: page.vwaTr("混响空间 · Alt+3")
                            onClicked: page.selectAnalysis("reverb")
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        visible: !page.compact
                        Layout.maximumWidth: Math.round(210 * page.theme.scale)
                        text: page.meters && page.meters.source ? page.vwaTr("实测中") : page.vwaTr("等待实测")
                        color: page.meters && page.meters.source ? page.theme.muted : page.theme.attention
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        elide: Text.ElideRight
                    }
                    UiButton {
                        objectName: "mixdownCompactEqDetails"
                        visible: page.compact && page.analysisMode === "eq"
                        theme: page.theme
                        glyph: "sliders"
                        text: page.showCompactEqDetails ? page.vwaTr("隐藏 EQ 参数") : page.vwaTr("显示 EQ 参数")
                        selected: page.showCompactEqDetails
                        onClicked: {
                            page.showCompactEqDetails = !page.showCompactEqDetails;
                            Qt.callLater(function() {
                                analysisScroll.contentY = page.showCompactEqDetails
                                    ? Math.max(0, analysisScroll.contentHeight - analysisScroll.height) : 0;
                            });
                        }
                    }
                    UiButton {
                        objectName: "mixdownExpand"
                        theme: page.theme
                            text: page.expanded ? page.vwaTr("还原混音布局") : page.vwaTr("放大曲线")
                        glyph: page.expanded ? "restore" : "expand"
                        selected: page.expanded
                        onClicked: page.controller.setExpanded(!page.expanded)
                    }
                }

                Flickable {
                    id: analysisScroll
                    objectName: "mixdownAnalysisViewport"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    contentWidth: width
                    contentHeight: Math.max(height, Math.round(analysisContentMinimum * page.theme.scale))
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: contentHeight > height
                    readonly property int analysisContentMinimum: page.analysisMode === "reverb" ? 426
                        : page.analysisMode === "gr" ? 486
                        : page.compact ? (page.showCompactEqDetails ? 440 : 112) : 408
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    StackLayout {
                        width: analysisScroll.width
                        height: analysisScroll.contentHeight
                        currentIndex: page.analysisMode === "gr" ? 1 : page.analysisMode === "reverb" ? 2 : 0

                        MixdownEq {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            theme: page.theme
                            controller: page.controller
                            bands: page.bands
                            eqCurve: page.eqCurve
                            spectrum: page.spectrum
                            maxHz: page.number(page.view.maxHz, 20000)
                            showBandDetails: !page.compact || page.showCompactEqDetails
                            showPlotHeader: !page.compact
                            minimumPlotHeight: Math.round((page.compact ? 112 : 230) * page.theme.scale)
                        }

                        GrAnalysis {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            theme: page.theme
                            controller: page.controller
                            gr: page.number(page.meters.gr, NaN)
                            grPeak: page.number(page.meters.gr_peak, NaN)
                            limiter: page.number(page.meters.limiter, NaN)
                            limiterPeak: page.number(page.meters.limiter_peak, NaN)
                            source: page.meters && page.meters.source ? String(page.meters.source) : ""
                            compressorMeasured: page.compressorMeasured
                            compressionEnabled: page.compressorEnabledMeasured
                            configuredCompressionEnabled: page.boolOption("compressionEnabled", false)
                            limiterMeasured: page.limiterMeasured
                            limiterEnabled: page.limiterEnabledMeasured
                            processingSelection: page.selectionMode
                            history: page.grHistory
                            compressionStrength: page.number(page.view.compressionStrength, 0)
                            compressionThreshold: page.number(page.view.compressionThreshold, -18)
                            compressionRatio: page.number(page.view.compressionRatio, 3)
                            compressorAttack: page.number(page.view.compressorAttack, 10)
                            compressorRelease: page.number(page.view.compressorRelease, 100)
                            compressorCeiling: page.number(page.view.limiterCeiling, -1)
                        }

                        ReverbAnalysis {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            theme: page.theme
                            controller: page.controller
                            telemetryAvailable: page.reverbTelemetry && page.reverbTelemetry.available === true
                            sendLevel: page.number(page.reverbTelemetry.send_rms, NaN)
                            sendPeak: page.number(page.reverbTelemetry.send_peak, NaN)
                            returnLevel: page.number(page.reverbTelemetry.return_rms, NaN)
                            returnPeak: page.number(page.reverbTelemetry.return_peak, NaN)
                            dryLevel: page.number(page.reverbTelemetry.dry_rms, NaN)
                            wetFraction: page.number(page.reverbTelemetry.wet_fraction, NaN)
                            edcAvailable: page.reverbTelemetry && page.reverbTelemetry.edc_available === true
                            edcMeasured: page.reverbTelemetry && page.reverbTelemetry.edc_measured === true
                            edcReason: page.reverbTelemetry && page.reverbTelemetry.edc_reason ? String(page.reverbTelemetry.edc_reason) : ""
                            source: page.meters && page.meters.source ? String(page.meters.source) : ""
                            curve: page.reverbCurve
                            effectEnabled: page.reverbTelemetry && page.reverbTelemetry.enabled === true
                            configuredEnabled: page.boolOption("reverbEnabled", false)
                            processingSelection: page.selectionMode
                            room: String(page.view.reverbRoom || "natural")
                            mix: page.number(page.view.reverbMix, 0)
                            predelay: page.number(page.view.reverbPredelay, 0)
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        id: footer
        objectName: "mixdownFooter"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: transport.implicitHeight + Math.round(12 * page.theme.scale)
        color: page.theme.surface
        border.width: 0
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 1
            color: page.theme.line
        }

        ColumnLayout {
            id: transport
            anchors.fill: parent
            anchors.leftMargin: page.pageMargin
            anchors.rightMargin: page.pageMargin
            anchors.topMargin: Math.round(6 * page.theme.scale)
            anchors.bottomMargin: Math.round(6 * page.theme.scale)
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(6 * page.theme.scale)
                UiButton {
                    objectName: "mixdownPlayPause"
                    theme: page.theme
                    primary: page.playing
                    text: page.playing ? page.vwaTr("暂停") : page.vwaTr("播放")
                    glyph: page.playing ? "pause" : "play"
                    enabled: page.hasMedia && page.canPlay
                    onClicked: page.controller.togglePlay()
                    Accessible.description: "Space"
                    ToolTip.text: (page.playing ? page.vwaTr("暂停试听") : page.vwaTr("播放试听")) + page.vwaTr(" · 空格")
                }
                UiButton {
                    objectName: "mixdownLoop"
                    theme: page.theme
                    selected: !!page.view.loop
                    text: page.vwaTr("循环")
                    glyph: "loop"
                    Accessible.checkable: true
                    Accessible.checked: !!page.view.loop
                    ToolTip.visible: hovered
                    ToolTip.text: page.vwaTr("循环所选试听范围 · Ctrl+Shift+L")
                    onClicked: page.controller.setLoop(!page.view.loop)
                }
                UiButton {
                    objectName: "mixdownPreviewRangeToggle"
                    theme: page.theme
                    quiet: true
                    selected: page.showPreviewRange
                    text: page.compact ? page.vwaTr("范围") : page.vwaTr("试听范围")
                    Accessible.checkable: true
                    Accessible.checked: page.showPreviewRange
                    onClicked: page.showPreviewRange = !page.showPreviewRange
                }
                UiButton {
                    objectName: "mixdownBefore"
                    theme: page.theme
                    selected: page.selectionMode === "A"
                    text: page.compact ? "A" : page.vwaTr("A 处理前")
                    Accessible.name: page.vwaTr("A 处理前")
                    ToolTip.visible: hovered
                    ToolTip.text: page.vwaTr("切换并试听处理前 · Ctrl+Shift+A")
                    onClicked: page.controller.selectMix("A")
                }
                UiButton {
                    objectName: "mixdownAfter"
                    theme: page.theme
                    selected: page.selectionMode === "B"
                    text: page.compact ? "B" : page.vwaTr("B 处理后")
                    Accessible.name: page.vwaTr("B 处理后")
                    ToolTip.visible: hovered
                    ToolTip.text: page.vwaTr("切换并试听处理后 · Ctrl+Shift+B")
                    onClicked: page.controller.selectMix("B")
                }
                Text {
                    text: page.timeText(page.seeking ? page.pendingPosition : page.positionMs) + " / " + page.timeText(page.durationMs)
                    color: page.theme.text
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    font.family: "Consolas"
                }
                Item {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                }
                UiButton {
                    visible: page.busy || page.loading
                    theme: page.theme
                    quiet: true
                    text: page.vwaTr("取消")
                    onClicked: page.controller.cancel()
                }
            }

            RowLayout {
                id: handoffRow
                objectName: "mixdownHandoff"
                Layout.fillWidth: true
                spacing: Math.round(7 * page.theme.scale)

                Rectangle {
                    objectName: "mixdownDeliveryStatus"
                    Layout.preferredWidth: Math.round(128 * page.theme.scale)
                    Layout.preferredHeight: Math.round(26 * page.theme.scale)
                    radius: height / 2
                    color: page.theme.base
                    border.color: page.deliveryColor()
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Math.round(8 * page.theme.scale)
                        anchors.rightMargin: Math.round(9 * page.theme.scale)
                        spacing: Math.round(6 * page.theme.scale)
                        Rectangle {
                            Layout.preferredWidth: Math.round(6 * page.theme.scale)
                            Layout.preferredHeight: Layout.preferredWidth
                            radius: width / 2
                            color: page.deliveryColor()
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.deliveryTitle()
                            color: page.theme.text
                            font.pixelSize: Math.round(10 * page.theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }
                }
                Text {
                    objectName: "mixdownDeliveryMessage"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: page.textValue(page.view.message,
                        page.loading ? page.vwaTr("正在加载真实音频…") : page.vwaTr("Space 播放 / 暂停"))
                    color: page.deliveryState === "error" ? page.theme.error : page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                    ToolTip.visible: handoffMessageHover.hovered && truncated
                    ToolTip.text: text
                    HoverHandler { id: handoffMessageHover }
                }
                UiButton {
                    id: outputMenuButton
                    objectName: "mixdownOutputMenu"
                    theme: page.theme
                    quiet: true
                    text: page.vwaTr("导出选项")
                    ToolTip.text: page.vwaTr("输出位置、打开 WAV 或所在文件夹")
                    onClicked: outputMenu.open()
                    Menu {
                        id: outputMenu
                        y: outputMenuButton.height
                        width: Math.round(284 * page.theme.scale)
                        MenuItem {
                            objectName: "mixdownChooseOutput"
                            text: page.vwaTr("选择 WAV 导出位置…")
                            enabled: !page.busy && !page.loading
                            onTriggered: page.openManagedDialog(outputDialog)
                        }
                        MenuSeparator {}
                        MenuItem {
                            text: page.vwaTr("当前输出：") + page.shortPath(page.view.output)
                            enabled: false
                        }
                        MenuItem {
                            objectName: "mixdownOpenResult"
                            text: page.resultExpired ? page.vwaTr("打开上次导出的 WAV") : page.vwaTr("打开导出的 WAV")
                            enabled: page.hasResult
                            onTriggered: page.controller.openResult()
                        }
                        MenuItem {
                            objectName: "mixdownOpenResultDirectory"
                            text: page.hasResult ? page.vwaTr("打开结果所在文件夹") : page.vwaTr("打开输出文件夹")
                            enabled: page.textValue(page.hasResult ? page.resultPath : page.view.output, "").length > 0
                            onTriggered: page.controller.openOutputFolder()
                        }
                    }
                }
                UiButton {
                    objectName: "mixdownExport"
                    theme: page.theme
                    visible: page.primaryHandoffAction === "export"
                    text: page.handoffActionLabel()
                    primary: true
                    enabled: page.handoffActionEnabled()
                    ToolTip.text: page.exportTargetReusesResult
                        ? page.vwaTr("当前目标等于上次结果；请选择新文件名，现有 WAV 不会被覆盖")
                        : page.vwaTr("导出新的 WAV 文件")
                    onClicked: {
                        if (page.exportTargetReusesResult)
                            page.openManagedDialog(outputDialog);
                        else
                            page.controller.exportMix();
                    }
                }
                UiButton {
                    objectName: "mixdownRegister"
                    theme: page.theme
                    visible: page.primaryHandoffAction === "register"
                    text: page.handoffActionLabel()
                    primary: true
                    enabled: page.handoffActionEnabled()
                    onClicked: page.controller.registerMix()
                }
                UiButton {
                    objectName: "mixdownComposeVideo"
                    theme: page.theme
                    visible: page.primaryHandoffAction === "compose"
                    text: page.handoffActionLabel()
                    primary: true
                    enabled: page.handoffActionEnabled()
                    onClicked: page.controller.composeVideo()
                }
            }

            RowLayout {
                id: exportProgressRow
                Layout.fillWidth: true
                visible: String(page.view.state || "") === "exporting"
                spacing: Math.round(7 * page.theme.scale)

                Text {
                    Layout.preferredWidth: Math.round(62 * page.theme.scale)
                    text: page.exportPhaseText(page.exportProgress.phase)
                    color: page.theme.text
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    font.weight: Font.DemiBold
                    textFormat: Text.PlainText
                }
                ProgressBar {
                    objectName: "mixdownExportProgress"
                    Layout.fillWidth: true
                    from: 0
                    to: 1
                    value: page.clamp(page.number(page.exportProgress.fraction, 0), 0, 1)
                    indeterminate: false
                    Accessible.name: page.vwaTr("导出进度")
                }
                Text {
                    Layout.preferredWidth: Math.round(40 * page.theme.scale)
                    horizontalAlignment: Text.AlignRight
                    text: Math.round(page.number(page.exportProgress.fraction, 0) * 100) + "%"
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    font.family: "Consolas"
                }
            }

            RowLayout {
                id: previewRangeRow
                Layout.fillWidth: true
                visible: page.showPreviewRange
                spacing: Math.round(7 * page.theme.scale)

                Text {
                    text: page.vwaTr("试听范围")
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                }
                Text {
                    text: page.vwaTr("开始")
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                }
                SpinBox {
                    id: previewStartInput
                    objectName: "mixdownPreviewStart"
                    Layout.preferredWidth: Math.round(112 * page.theme.scale)
                    from: 0
                    to: Math.max(1, page.durationMs)
                    stepSize: 100
                    editable: true
                    enabled: page.hasMedia && !page.loading
                    textFromValue: value => page.previewTimeText(value)
                    valueFromText: text => Math.round(Number(String(text).trim().replace(",", ".")) * 1000)
                    onValueModified: page.commitPreviewRange()
                    Accessible.name: page.vwaTr("试听起点，秒")
                    Binding {
                        target: previewStartInput
                        property: "value"
                        when: !previewStartInput.activeFocus
                        value: Math.round(page.number(page.view.previewStart, 0) * 1000)
                        restoreMode: Binding.RestoreBindingOrValue
                    }
                }
                Text {
                    text: page.vwaTr("时长")
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                }
                SpinBox {
                    id: previewDurationInput
                    objectName: "mixdownPreviewDuration"
                    Layout.preferredWidth: Math.round(104 * page.theme.scale)
                    from: 2000
                    to: 30000
                    stepSize: 500
                    editable: true
                    enabled: page.hasMedia && !page.loading
                    textFromValue: value => page.previewTimeText(value)
                    valueFromText: text => Math.round(Number(String(text).trim().replace(",", ".")) * 1000)
                    onValueModified: page.commitPreviewRange()
                    Accessible.name: page.vwaTr("试听范围时长，秒")
                    Binding {
                        target: previewDurationInput
                        property: "value"
                        when: !previewDurationInput.activeFocus
                        value: Math.round(page.number(page.view.previewDuration, 12) * 1000)
                        restoreMode: Binding.RestoreBindingOrValue
                    }
                }
                UiButton {
                    objectName: "mixdownPreviewRangeReset"
                    theme: page.theme
                    quiet: true
                    text: page.vwaTr("重置 0–12 秒")
                    enabled: page.hasMedia && !page.loading
                    onClicked: page.controller.setPreviewRange(0, 12)
                }
                Text {
                    Layout.fillWidth: true
                    text: page.view.loop ? page.vwaTr("循环仅播放此范围") : page.vwaTr("启用循环后重复此范围")
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    elide: Text.ElideRight
                }
            }

            Slider {
                id: seekSlider
                objectName: "mixdownProgress"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(48 * page.theme.scale)
                Layout.minimumHeight: Layout.preferredHeight
                from: 0
                to: Math.max(1, page.durationMs)
                stepSize: 1
                enabled: page.hasMedia && !page.loading && page.durationMs > 0
                focusPolicy: Qt.StrongFocus
                Accessible.name: page.vwaTr("混音播放进度")
                Accessible.description: page.timeText(page.positionMs) + " / " + page.timeText(page.durationMs)
                    + page.vwaTr("。方向键定位，Shift 精调，Home / End 到开头或结尾。")
                Keys.onPressed: event => page.keyboardSeek(event)

                Binding {
                    target: seekSlider
                    property: "value"
                    value: page.seeking ? page.pendingPosition : page.positionMs
                    restoreMode: Binding.RestoreBindingOrValue
                }
                background: Rectangle {
                    x: 0
                    y: Math.round((parent.height - height) / 2)
                    width: parent.width
                    height: Math.round(7 * page.theme.scale)
                    radius: height / 2
                    color: page.theme.base
                    border.color: seekSlider.activeFocus ? page.theme.accent : page.theme.line
                    Rectangle {
                        width: parent.width * (parent.parent ? parent.parent.visualPosition : 0)
                        height: parent.height
                        radius: parent.radius
                        color: page.theme.accent
                    }
                }
                MouseArea {
                    id: seekGrab
                    objectName: "mixdownProgressGrab"
                    anchors.fill: parent
                    enabled: seekSlider.enabled
                    acceptedButtons: Qt.LeftButton
                    hoverEnabled: true
                    preventStealing: true
                    z: 10
                    cursorShape: Qt.PointingHandCursor
                    onPressed: function (mouse) {
                        mouse.accepted = true;
                        seekSlider.forceActiveFocus(Qt.MouseFocusReason);
                        if (keyboardSeekTimer.running)
                            page.cancelSeekGesture();
                        page.beginSeekGesture(page.seekPositionAt(mouse.x));
                    }
                    onPositionChanged: function (mouse) {
                        if (pressed)
                            page.updateSeekGesture(page.seekPositionAt(mouse.x));
                    }
                    onReleased: page.finishSeekGesture(true)
                    onCanceled: page.cancelSeekGesture()
                    onEnabledChanged: {
                        if (!enabled)
                            page.cancelSeekGesture();
                    }
                }
                handle: Rectangle {
                    x: parent.visualPosition * (parent.width - width)
                    y: Math.round((parent.height - height) / 2)
                    width: Math.round(18 * page.theme.scale)
                    height: width
                    radius: width / 2
                    color: page.theme.text
                    border.width: 2
                    border.color: page.theme.accent
                }
            }
        }
    }

    Dialog {
        id: trackSettingsDialog
        objectName: "mixdownTrackSettings"
        property string trackId: ""
        readonly property var track: page.trackById(trackId)
        title: page.vwaTr("轨道参数 · ") + page.trackValue(track, "label", "")
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - 28, Math.round(390 * page.theme.scale))
        closePolicy: Popup.CloseOnEscape
        onOpened: page.noteDialogOpened()
        onClosed: page.noteDialogClosed()
        contentItem: ColumnLayout {
            spacing: Math.round(8 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.textValue(page.trackValue(trackSettingsDialog.track, "name", ""), page.vwaTr("尚未选择素材"))
                color: page.theme.muted
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
            }
            TrackNumber {
                theme: page.theme
                inputName: "mixdownTrackGainValue"
                label: page.vwaTr("精确增益 (dB)")
                lower: -60
                upper: 12
                digits: 1
                currentValue: page.number(page.trackValue(trackSettingsDialog.track, "gain", 0), 0)
                onCommit: value => page.controller.setTrack(trackSettingsDialog.trackId, "gain", value)
            }
            TrackNumber {
                theme: page.theme
                inputName: "mixdownTrackPan"
                label: page.vwaTr("声像（−100 左 / 0 中 / +100 右）")
                lower: -100
                upper: 100
                digits: 0
                currentValue: 100 * page.number(page.trackValue(trackSettingsDialog.track, "pan", 0), 0)
                onCommit: value => page.controller.setTrack(trackSettingsDialog.trackId, "pan", value / 100)
            }
            TrackNumber {
                visible: trackSettingsDialog.trackId !== "instrumental"
                theme: page.theme
                inputName: "mixdownTrackOffset"
                label: page.vwaTr("位置偏移（秒，正值延后）")
                lower: -30
                upper: 30
                digits: 3
                currentValue: page.number(page.trackValue(trackSettingsDialog.track, "offset", 0), 0)
                onCommit: value => page.controller.setTrack(trackSettingsDialog.trackId, "offset", value)
            }
            ParameterSlider {
                visible: page.compact && trackSettingsDialog.trackId !== "instrumental"
                Layout.fillWidth: true
                theme: page.theme
                label: page.vwaTr("混响发送")
                displayValue: page.number(page.trackValue(trackSettingsDialog.track, "send", 0), 0)
                displayFactor: 100
                step: 0.01
                unit: "%"
                onMoved: (amount) => page.controller.setTrack(trackSettingsDialog.trackId, "send", amount)
            }
            RowLayout {
                Layout.fillWidth: true
                UiButton {
                    theme: page.theme
                    quiet: true
                    text: page.vwaTr("清除素材")
                    enabled: page.textValue(page.trackValue(trackSettingsDialog.track, "path", ""), "").length > 0
                        && !page.busy && !page.loading
                    onClicked: {
                        page.controller.clearSource(trackSettingsDialog.trackId);
                        trackSettingsDialog.close();
                    }
                }
                UiButton {
                    theme: page.theme
                    quiet: true
                    visible: trackSettingsDialog.trackId !== "instrumental"
                    enabled: page.vocalTrackCount > 1 && !page.busy && !page.loading
                    text: page.vwaTr("移除歌声轨")
                    onClicked: {
                        page.controller.removeTrack(trackSettingsDialog.trackId);
                        trackSettingsDialog.close();
                    }
                }
                Item { Layout.fillWidth: true }
            }
        }
        footer: DialogButtonBox {
            UiButton {
                theme: page.theme
                text: page.vwaTr("完成")
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
                onClicked: trackSettingsDialog.close()
            }
        }
    }

    Dialog {
        id: presetReviewDialog
        objectName: "mixdownPresetReview"
        readonly property var plan: page.view.presetPreview || ({})
        title: page.vwaTr("预览混音预设")
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - 28, Math.round(570 * page.theme.scale))
        closePolicy: Popup.CloseOnEscape
        onOpened: page.noteDialogOpened()
        onClosed: {
            page.noteDialogClosed();
            page.controller.discardPresetPreview();
        }
        contentItem: ColumnLayout {
            spacing: Math.round(6 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                color: page.theme.text
                text: presetReviewDialog.plan.name || page.textValue(page.view.message, page.vwaTr("需要重新预览。"))
                wrapMode: Text.Wrap
                font.pixelSize: Math.round(14 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            CheckBox {
                objectName: "mixdownPresetIncludeWidth"
                visible: !!presetReviewDialog.plan.hasWidth
                text: page.vwaTr("同时应用总输出宽度")
                checked: !!presetReviewDialog.plan.includeWidth
                onClicked: page.controller.previewPreset(presetReviewDialog.plan.key, checked, tracksScope.checked)
            }
            CheckBox {
                id: tracksScope
                objectName: "mixdownPresetIncludeTracks"
                visible: !!presetReviewDialog.plan.hasTracks
                text: page.vwaTr("同时应用歌声声像与混响发送")
                checked: !!presetReviewDialog.plan.includeTracks
                onClicked: page.controller.previewPreset(presetReviewDialog.plan.key, !!presetReviewDialog.plan.includeWidth, checked)
            }
            Text {
                Layout.fillWidth: true
                text: page.vwaTr("不更换素材，不改变静音、独奏、增益或位置偏移。") +
                    (presetReviewDialog.plan.adjusted ? page.vwaTr(" 数值已按当前采样率与控件精度调整。") : "")
                color: page.theme.muted
                wrapMode: Text.Wrap
                font.pixelSize: Math.round(11 * page.theme.scale)
            }
            ScrollView {
                id: presetDifferences
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(Math.round(220 * page.theme.scale), Math.round(page.height * .32))
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    width: presetDifferences.availableWidth
                    spacing: Math.round(6 * page.theme.scale)
                    Text {
                        visible: changesList.count === 0
                        text: page.vwaTr("所选范围内没有参数差异。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                    }
                    Repeater {
                        id: changesList
                        model: presetReviewDialog.plan.changes || []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Text {
                                text: page.presetFieldName(modelData.field)
                                color: page.theme.text
                                font.pixelSize: Math.round(12 * page.theme.scale)
                                font.weight: Font.DemiBold
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    text: page.vwaTr("当前 · ") + page.presetValueText(modelData.field, modelData.before)
                                    color: page.theme.muted
                                    wrapMode: Text.Wrap
                                    font.pixelSize: Math.round(11 * page.theme.scale)
                                }
                                Text {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    text: page.vwaTr("应用后 · ") + page.presetValueText(modelData.field, modelData.after)
                                    color: page.theme.text
                                    wrapMode: Text.Wrap
                                    font.pixelSize: Math.round(11 * page.theme.scale)
                                }
                            }
                        }
                    }
                }
            }
        }
        footer: DialogButtonBox {
            UiButton {
                theme: page.theme
                text: page.vwaTr("取消")
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
                onClicked: presetReviewDialog.close()
            }
            UiButton {
                objectName: "mixdownConfirmPreset"
                theme: page.theme
                primary: true
                text: page.vwaTr("应用预设")
                enabled: !!presetReviewDialog.plan.key && !page.busy
                DialogButtonBox.buttonRole: DialogButtonBox.ApplyRole
                onClicked: {
                    if (page.controller.confirmPreset())
                        presetReviewDialog.close();
                }
            }
        }
    }

    FileDialog {
        id: sourceDialog
        property string sourceTrackId: ""
        objectName: "mixdownSourceDialog"
        title: page.vwaTr("选择音频轨")
        fileMode: FileDialog.OpenFile
        nameFilters: [page.vwaTr("音频文件 (*.wav *.flac *.mp3 *.m4a *.ogg *.aac *.opus *.wma *.aif *.aiff *.ape *.wv)"), page.vwaTr("所有文件 (*)")]
        onAccepted: {
            page.noteDialogClosed();
            if (sourceTrackId.length > 0)
                page.controller.chooseSource(sourceTrackId, selectedFile);
        }
        onRejected: page.noteDialogClosed()
    }

    FileDialog {
        id: outputDialog
        objectName: "mixdownOutputDialog"
        title: page.vwaTr("选择独立 WAV 输出")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "wav"
        nameFilters: [page.vwaTr("WAV 音频 (*.wav)")]
        onAccepted: {
            page.noteDialogClosed();
            page.controller.chooseOutput(selectedFile);
        }
        onRejected: page.noteDialogClosed()
    }

    FileDialog {
        id: importPresetDialog
        objectName: "mixdownImportPresetDialog"
        title: page.vwaTr("导入混音预设")
        fileMode: FileDialog.OpenFile
        nameFilters: [page.vwaTr("VWA 混音预设 (*.json)"), page.vwaTr("所有文件 (*)")]
        onAccepted: {
            page.noteDialogClosed();
            page.controller.importPresets(selectedFile);
        }
        onRejected: page.noteDialogClosed()
    }

    FileDialog {
        id: exportPresetDialog
        objectName: "mixdownExportPresetDialog"
        title: page.vwaTr("导出混音预设")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [page.vwaTr("VWA 混音预设 (*.json)")]
        onAccepted: {
            page.noteDialogClosed();
            const key = page.currentPresetKey();
            if (key.length > 0)
                page.controller.exportPreset(selectedFile, key);
        }
        onRejected: page.noteDialogClosed()
    }

    Dialog {
        id: savePresetDialog
        objectName: "mixdownSavePresetDialog"
        title: page.vwaTr("追加混音预设")
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - Math.round(36 * page.theme.scale), Math.round(410 * page.theme.scale))
        closePolicy: Popup.CloseOnEscape
        onOpened: {
            page.noteDialogOpened();
            presetNameField.text = page.textValue(page.view.presetName, "");
            saveWidth.checked = false;
            presetNameField.forceActiveFocus();
            presetNameField.selectAll();
        }
        onClosed: page.noteDialogClosed()

        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.vwaTr("预设会追加到当前预设库；不会覆盖素材、任务或旧导出。")
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            TextField {
                id: presetNameField
                Layout.fillWidth: true
                objectName: "mixdownPresetName"
                placeholderText: page.vwaTr("预设名称")
                color: page.theme.text
                font.pixelSize: Math.round(13 * page.theme.scale)
                selectByMouse: true
                Accessible.name: page.vwaTr("预设名称")
                background: Rectangle {
                    radius: Math.round(7 * page.theme.scale)
                    color: page.theme.base
                    border.color: presetNameField.activeFocus ? page.theme.accent : page.theme.line
                }
                onAccepted: {
                    if (presetNameField.text.trim().length > 0
                            && page.controller.savePreset(presetNameField.text.trim(), saveWidth.checked))
                        savePresetDialog.close();
                }
            }
            CheckBox {
                id: saveWidth
                text: page.vwaTr("同时保存总输出宽度")
            }
        }
        footer: RowLayout {
            spacing: Math.round(7 * page.theme.scale)
            Item {
                Layout.fillWidth: true
            }
            UiButton {
                theme: page.theme
                text: page.vwaTr("取消")
                onClicked: savePresetDialog.close()
            }
            UiButton {
                theme: page.theme
                primary: true
                text: page.vwaTr("追加")
                enabled: presetNameField.text.trim().length > 0
                onClicked: {
                    if (page.controller.savePreset(presetNameField.text.trim(), saveWidth.checked))
                        savePresetDialog.close();
                }
            }
        }
    }

    component ChannelStrip: Rectangle {
        id: channel
        required property string trackId
        property int order: 0
        readonly property bool monitor: trackId === "master"
        readonly property bool backing: trackId === "instrumental"
        readonly property var track: page.trackById(trackId)
        readonly property string label: monitor ? page.vwaTr("试听输出") : String(page.trackValue(track, "label", backing ? page.vwaTr("伴奏") : page.vwaTr("歌声 ") + (order + 1)))
        readonly property string sourcePath: String(page.trackValue(track, "path", ""))
        readonly property color accent: monitor ? page.theme.accent : backing ? page.theme.attention
            : order === 0 ? page.theme.success : order === 1 ? page.theme.accent : page.theme.text
        readonly property real gainDb: monitor ? page.number(page.view.masterGain, 0)
            : page.number(page.trackValue(track, "gain", 0), 0)
        readonly property real panValue: page.number(page.trackValue(track, "pan", 0), 0)
        readonly property int panPercent: Math.round(panValue * 100)
        readonly property real offsetSeconds: page.number(page.trackValue(track, "offset", 0), 0)
        readonly property int offsetMilliseconds: Math.round(offsetSeconds * 1000)
        readonly property string panReadout: panPercent < 0 ? "L" + Math.abs(panPercent) : "R" + panPercent
        readonly property string offsetReadout: (offsetMilliseconds > 0 ? "+" : "")
            + offsetMilliseconds + "ms"
        readonly property bool hasPan: !monitor && Math.abs(panValue) > 0.0001
        readonly property bool hasOffset: !monitor && !backing && Math.abs(offsetSeconds) >= 0.0005
        readonly property bool hasReadout: hasPan || hasOffset
        readonly property string readoutText: [hasPan ? panReadout : "",
            hasOffset ? offsetReadout : ""].filter(value => value.length > 0).join(" / ")
        readonly property string readoutAccessibleName: {
            const fields = [];
            if (hasPan)
                fields.push(page.vwaTr("声像 ") + panReadout);
            if (hasOffset)
                fields.push(page.vwaTr("位置偏移 ") + offsetReadout);
            return label + " · " + fields.join(page.vwaTr("，"))
                + page.vwaTr("。点击打开精确轨道参数");
        }
        readonly property bool editable: !page.busy && !page.loading
        function openTrackSettings() {
            trackSettingsDialog.trackId = trackId;
            trackSettingsDialog.open();
        }
        objectName: monitor ? "mixdownMonitorChannel" : "mixdownTrack_" + trackId
        radius: Math.round(6 * page.theme.scale)
        color: monitor ? page.theme.selected : page.theme.base
        border.color: monitor ? page.theme.selectedLine : page.theme.line

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Math.round(5 * page.theme.scale)
            height: Math.round(2 * page.theme.scale)
            radius: height / 2
            color: channel.accent
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: Math.round(5 * page.theme.scale)
            anchors.rightMargin: Math.round(5 * page.theme.scale)
            anchors.topMargin: Math.round(11 * page.theme.scale)
            anchors.bottomMargin: Math.round(6 * page.theme.scale)
            spacing: Math.round((page.compact ? 3 : 4) * page.theme.scale)

            Text {
                Layout.fillWidth: true
                text: channel.label
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.theme.scale)
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                textFormat: Text.PlainText
                ToolTip.visible: page.compact && channelHeaderHover.hovered
                    && (channel.sourcePath.length > 0 || channel.monitor)
                ToolTip.text: channel.monitor ? page.vwaTr("仅影响试听音量，不写入 WAV") : channel.sourcePath
                HoverHandler { id: channelHeaderHover }
            }
            Text {
                visible: !page.compact
                Layout.fillWidth: true
                text: channel.monitor ? page.vwaTr("仅影响试听") : page.textValue(page.trackValue(channel.track, "name", ""), page.vwaTr("未选素材"))
                color: page.theme.muted
                font.pixelSize: Math.round(10 * page.theme.scale)
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideMiddle
                textFormat: Text.PlainText
                ToolTip.visible: sourceHover.hovered && channel.sourcePath.length > 0
                ToolTip.text: channel.sourcePath
                HoverHandler { id: sourceHover }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(20 * page.theme.scale)
                TrackReadoutButton {
                    objectName: "mixdownTrackReadout_" + channel.trackId
                    anchors.fill: parent
                    visible: channel.hasReadout
                    theme: page.theme
                    text: channel.readoutText
                    Accessible.name: channel.readoutAccessibleName
                    ToolTip.text: Accessible.name
                    onClicked: channel.openTrackSettings()
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round((page.compact ? 26 : 30) * page.theme.scale)
                RowLayout {
                    anchors.centerIn: parent
                    spacing: Math.round(3 * page.theme.scale)
                    visible: !channel.monitor
                    StripButton {
                        objectName: "mixdownChooseSource_" + channel.trackId
                        theme: page.theme
                        glyph: "folder"
                        text: (channel.sourcePath.length ? page.vwaTr("更换") : page.vwaTr("选择")) + channel.label + page.vwaTr("素材")
                        enabled: channel.editable
                        onClicked: page.chooseTrackSource(channel.trackId)
                    }
                    StripButton {
                        objectName: "mixdownTrackSettings_" + channel.trackId
                        theme: page.theme
                        glyph: "sliders"
                        text: channel.label + page.vwaTr("轨道参数")
                        Accessible.description: channel.backing ? page.vwaTr("精确增益和声像") : page.vwaTr("精确增益、声像和时间偏移")
                        enabled: channel.editable
                        onClicked: channel.openTrackSettings()
                    }
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                Layout.fillHeight: true
                Layout.minimumHeight: Math.round((page.compact ? 62 : 116) * page.theme.scale)
                spacing: Math.round(4 * page.theme.scale)
                Slider {
                    id: fader
                    objectName: channel.monitor ? "mixdownMonitorVolume" : "mixdownGain_" + channel.trackId
                    Layout.preferredWidth: Math.round(28 * page.theme.scale)
                    Layout.fillHeight: true
                    orientation: Qt.Vertical
                    from: -60
                    to: channel.monitor ? 0 : 12
                    stepSize: 0.1
                    value: page.clamp(channel.gainDb, from, to)
                    enabled: channel.editable
                    focusPolicy: Qt.StrongFocus
                    onMoved: {
                        if (channel.monitor)
                            page.controller.setOption("masterGain", value);
                        else
                            page.controller.setTrack(channel.trackId, "gain", value);
                    }
                    Accessible.name: channel.label + page.vwaTr("增益")
                    Accessible.description: value.toFixed(1) + page.vwaTr(" dB；双击恢复 0 dB")
                    TapHandler {
                        acceptedButtons: Qt.LeftButton
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onDoubleTapped: function(event) {
                            if (channel.monitor)
                                page.controller.setOption("masterGain", 0);
                            else
                                page.controller.setTrack(channel.trackId, "gain", 0);
                            event.accepted = true;
                        }
                    }
                    background: Rectangle {
                        x: Math.round((fader.width - width) / 2)
                        y: Math.round(fader.topPadding)
                        width: Math.round(5 * page.theme.scale)
                        height: fader.availableHeight
                        radius: width / 2
                        color: page.theme.line
                        Rectangle {
                            width: parent.width
                            height: parent.height * fader.position
                            anchors.bottom: parent.bottom
                            radius: parent.radius
                            color: channel.accent
                            opacity: 0.7
                        }
                    }
                    handle: Rectangle {
                        x: Math.round((fader.width - width) / 2)
                        y: fader.topPadding + fader.visualPosition * (fader.availableHeight - height)
                        width: Math.round(20 * page.theme.scale)
                        height: Math.round(11 * page.theme.scale)
                        radius: Math.round(3 * page.theme.scale)
                        color: page.theme.text
                        border.color: channel.accent
                    }
                }
                MixdownMeter {
                    Layout.fillHeight: true
                    Layout.preferredWidth: Math.round(22 * page.theme.scale)
                    theme: page.theme
                    rms: page.laneValue(channel.trackId, "rms")
                    peak: page.laneValue(channel.trackId, "peak")
                    accentColor: channel.accent
                    accessibleLabel: channel.label + page.vwaTr("电平")
                }
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: page.dbText(channel.gainDb)
                color: page.theme.text
                font.pixelSize: Math.round(11 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round((page.compact ? 26 : 30) * page.theme.scale)
                RowLayout {
                    anchors.centerIn: parent
                    spacing: Math.round(3 * page.theme.scale)
                    visible: !channel.monitor
                    Repeater {
                        model: [{field: "mute", label: page.vwaTr("静音"), short: "M"}, {field: "solo", label: page.vwaTr("独奏"), short: "S"}]
                        StripButton {
                            required property var modelData
                            theme: page.theme
                            text: modelData.short
                            selected: !!page.trackValue(channel.track, modelData.field, false)
                            enabled: channel.editable
                            Accessible.name: channel.label + modelData.label
                            Accessible.checkable: true
                            Accessible.checked: selected
                            ToolTip.visible: hovered || visualFocus
                            ToolTip.text: channel.label + " · " + modelData.label
                            onClicked: page.controller.setTrack(channel.trackId, modelData.field, !selected)
                        }
                    }
                }
            }

            Loader {
                visible: !page.compact
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(38 * page.theme.scale)
                active: visible && !channel.monitor && !channel.backing
                sourceComponent: ParameterSlider {
                    objectName: "mixdownSend_" + channel.trackId
                    theme: page.theme
                    label: page.vwaTr("发送")
                    displayValue: page.number(page.trackValue(channel.track, "send", 0), 0)
                    displayFactor: 100
                    unit: "%"
                    step: 0.01
                    interactive: channel.editable
                    onMoved: (amount) => page.controller.setTrack(channel.trackId, "send", amount)
                }
            }
        }
    }

    component StripButton: UiButton {
        implicitWidth: Math.round(29 * theme.scale)
        implicitHeight: Math.round((page.compact ? 26 : 30) * theme.scale)
        leftPadding: 0
        topPadding: 0
        bottomPadding: 0
        quiet: true
        font.pixelSize: Math.round(11 * theme.scale)
    }

    component TrackReadoutButton: UiButton {
        implicitHeight: Math.round(20 * theme.scale)
        leftPadding: Math.round(4 * theme.scale)
        rightPadding: leftPadding
        topPadding: 0
        bottomPadding: 0
        quiet: true
        font.pixelSize: Math.round(9 * theme.scale)
    }

    component TrackNumber: RowLayout {
        id: field
        required property QtObject theme
        required property string label
        required property string inputName
        required property real lower
        required property real upper
        required property int digits
        required property real currentValue
        readonly property int factor: Math.pow(10, digits)
        signal commit(real value)
        Text {
            Layout.fillWidth: true
            text: field.label
            color: field.theme.text
            wrapMode: Text.Wrap
            font.pixelSize: Math.round(12 * field.theme.scale)
        }
        SpinBox {
            id: spin
            objectName: field.inputName
            Layout.preferredWidth: Math.round(116 * field.theme.scale)
            from: Math.round(field.lower * field.factor)
            to: Math.round(field.upper * field.factor)
            value: Math.round(field.currentValue * field.factor)
            stepSize: field.digits === 3 ? 10 : 1
            editable: true
            validator: DoubleValidator {
                bottom: field.lower
                top: field.upper
                decimals: field.digits
                notation: DoubleValidator.StandardNotation
                locale: spin.locale.name
            }
            textFromValue: function (value, locale) { return (value / field.factor).toLocaleString(locale, "f", field.digits); }
            valueFromText: function (text, locale) { return Math.round(Number.fromLocaleString(locale, text) * field.factor); }
            onValueModified: field.commit(value / field.factor)
            Accessible.name: field.label
        }
    }

    component ParameterSlider: ColumnLayout {
        id: parameter
        required property QtObject theme
        property string label: ""
        property real fromValue: 0
        property real toValue: 1
        property real displayValue: 0
        property real displayFactor: 1
        property bool interactive: true
        property string unit: ""
        property int decimals: 0
        property real step: 1
        property real resetValue: Number.NaN
        signal moved(real value)
        signal resetRequested()

        spacing: Math.round(2 * parameter.theme.scale)

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: parameter.label
                color: parameter.theme.muted
                font.pixelSize: Math.round(10 * parameter.theme.scale)
                elide: Text.ElideRight
            }
            Text {
                text: (Number(parameter.displayValue) * parameter.displayFactor).toFixed(parameter.decimals) + parameter.unit
                color: parameter.theme.text
                font.pixelSize: Math.round(10 * parameter.theme.scale)
            }
        }
        Slider {
            id: slider
            objectName: parameter.objectName.length > 0 ? parameter.objectName + "Slider" : ""
            Layout.fillWidth: true
            from: parameter.fromValue
            to: parameter.toValue
            value: parameter.displayValue
            enabled: parameter.interactive
            stepSize: parameter.step
            snapMode: Slider.SnapAlways
            onMoved: parameter.moved(value)
            TapHandler {
                acceptedButtons: Qt.LeftButton
                gesturePolicy: TapHandler.ReleaseWithinBounds
                enabled: parameter.interactive && isFinite(parameter.resetValue)
                onDoubleTapped: function(event) {
                    parameter.resetRequested();
                    event.accepted = true;
                }
            }
            Accessible.name: parameter.label
            background: Rectangle {
                y: Math.round((parent.height - height) / 2)
                width: parent.width
                height: Math.round(4 * parameter.theme.scale)
                radius: height / 2
                color: parameter.theme.line
                Rectangle {
                    width: parent.width * parent.parent.visualPosition
                    height: parent.height
                    radius: parent.radius
                    color: parameter.theme.accent
                }
            }
            handle: Rectangle {
                x: parent.visualPosition * (parent.width - width)
                y: Math.round((parent.height - height) / 2)
                width: Math.round(13 * parameter.theme.scale)
                height: width
                radius: width / 2
                color: parameter.theme.text
            }
        }
    }

    component SignalReadout: ColumnLayout {
        id: readout
        required property QtObject theme
        property string label: ""
        property string emptyLabel: "等待实测"
        property string unit: " dBFS"
        property real rms: NaN
        property real peak: NaN
        property real minimum: -72
        property real maximum: 0
        property color ink: theme.success
        readonly property bool measured: isFinite(rms) || isFinite(peak)

        function fraction(value) {
            return isFinite(value) ? Math.min(1, Math.max(0, (value - minimum) / (maximum - minimum))) : 0;
        }

        Layout.fillWidth: true
        spacing: 4 * theme.scale
        Text {
            Layout.fillWidth: true
            text: readout.label
            color: readout.theme.text
            font.pixelSize: 12 * readout.theme.scale
            elide: Text.ElideRight
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 7 * readout.theme.scale
            radius: height / 2
            color: readout.theme.line
            Rectangle {
                width: parent.width * readout.fraction(readout.rms)
                height: parent.height
                radius: height / 2
                color: readout.ink
            }
            Rectangle {
                visible: isFinite(readout.peak)
                width: 2
                height: parent.height
                x: Math.min(parent.width - width, Math.max(0, parent.width * readout.fraction(readout.peak) - width))
                color: readout.theme.text
            }
        }
        Text {
            Layout.fillWidth: true
            text: readout.measured
                ? (isFinite(readout.rms) ? "RMS " + readout.rms.toFixed(1) + readout.unit : "")
                    + (isFinite(readout.peak) ? page.vwaTr("  ·  峰值 ") + readout.peak.toFixed(1) + readout.unit : "")
                : readout.emptyLabel
            color: readout.measured ? readout.theme.muted : readout.theme.attention
            font.pixelSize: 11 * readout.theme.scale
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
    }

    component GrAnalysis: Item {
        id: grPage
        required property QtObject theme
        required property QtObject controller
        property real gr: NaN
        property real limiter: NaN
        property real grPeak: NaN
        property real limiterPeak: NaN
        property bool compressorMeasured: false
        property bool limiterMeasured: false
        property bool configuredCompressionEnabled: false
        property string processingSelection: "B"
        property var history: ({})
        property string source: ""
        property bool compressionEnabled: false
        property real compressionStrength: 0
        property real compressionThreshold: -18
        property real compressionRatio: 3
        property real compressorAttack: 10
        property real compressorRelease: 100
        property bool limiterEnabled: false
        property real compressorCeiling: -1
        readonly property int gridColumns: width > Math.round(640 * theme.scale) ? 4 : 2

        onHistoryChanged: if (visible && !historyPaint.running) historyPaint.start()
        onVisibleChanged: if (visible) grCanvas.requestPaint()
        Timer {
            id: historyPaint
            interval: 120
            repeat: false
            onTriggered: if (grPage.visible) grCanvas.requestPaint()
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: Math.round(8 * grPage.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: page.vwaTr("动态与限制")
                    color: grPage.theme.text
                    font.pixelSize: Math.round(15 * grPage.theme.scale)
                    font.weight: Font.DemiBold
                }
                Item {
                    Layout.fillWidth: true
                }
                Text {
                    text: grPage.source.length > 0 ? page.vwaTr("实测源 · ") + grPage.source : page.vwaTr("等待真实 GR 遥测")
                    color: grPage.source.length > 0 ? grPage.theme.muted : grPage.theme.attention
                    font.pixelSize: Math.round(10 * grPage.theme.scale)
                    elide: Text.ElideRight
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 18 * grPage.theme.scale
                SignalReadout {
                    objectName: "mixdownCompressionMeasurement"
                    theme: grPage.theme
                    label: page.vwaTr("人声压缩")
                    unit: " dB"
                    minimum: 0
                    maximum: 24
                    rms: grPage.processingSelection === "B" && grPage.compressionEnabled ? grPage.gr : NaN
                    peak: grPage.processingSelection === "B" && grPage.compressionEnabled ? grPage.grPeak : NaN
                    emptyLabel: grPage.processingSelection === "A" ? "A · 旁路"
                        : grPage.compressorMeasured && !grPage.compressionEnabled ? "压缩旁路" : "等待真实 GR"
                }
                SignalReadout {
                    objectName: "mixdownLimiterMeasurement"
                    theme: grPage.theme
                    label: page.vwaTr("总线限制")
                    unit: " dB"
                    minimum: 0
                    maximum: 24
                    rms: grPage.limiterMeasured ? grPage.limiter : NaN
                    peak: grPage.limiterMeasured ? grPage.limiterPeak : NaN
                    ink: grPage.theme.accent
                }
            }
            Text {
                Layout.fillWidth: true
                text: page.vwaTr("峰值衰减 · 最近 30 秒 · 绿：压缩  紫：限制")
                color: grPage.theme.muted
                font.pixelSize: 11 * grPage.theme.scale
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 86 * grPage.theme.scale
                radius: 8 * grPage.theme.scale
                color: grPage.theme.base
                border.color: grPage.theme.line
                Canvas {
                    id: grCanvas
                    objectName: "mixdownGrHistoryCanvas"
                    anchors.fill: parent
                    anchors.margins: 5 * grPage.theme.scale
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        const h = grPage.history;
                        const end = Number(h.positionMs || 0) + 100;
                        const span = Number(h.windowMs || 30000);
                        ctx.strokeStyle = grPage.theme.line;
                        ctx.lineWidth = 1;
                        for (let row = 1; row <= 3; ++row) {
                            const y = row * height / 4;
                            ctx.beginPath();
                            ctx.moveTo(0, y);
                            ctx.lineTo(width, y);
                            ctx.stroke();
                        }
                        function draw(rows, ink) {
                            let previous = -Infinity;
                            ctx.beginPath();
                            ctx.lineWidth = 1.5 * grPage.theme.scale;
                            ctx.strokeStyle = ink;
                            for (const row of rows) {
                                const x = width * (1 - (end - row.ms) / span);
                                const y = 2 + Math.min(24, Math.max(0, row.db)) / 24 * Math.max(1, height - 4);
                                if (row.ms - previous > 150) {
                                    ctx.moveTo(x, y);
                                    ctx.lineTo(x + 1, y);
                                } else {
                                    ctx.lineTo(x, y);
                                }
                                previous = row.ms;
                            }
                            ctx.stroke();
                        }
                        if (grPage.processingSelection === "B")
                            draw(h.compressor || [], grPage.theme.success);
                        draw(h.limiter || [], grPage.theme.accent);
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Math.round(8 * grPage.theme.scale)
                color: grPage.theme.base
                border.color: grPage.theme.line

                GridLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(10 * grPage.theme.scale)
                    columns: grPage.gridColumns
                    columnSpacing: Math.round(12 * grPage.theme.scale)
                    rowSpacing: Math.round(9 * grPage.theme.scale)

                    UiButton {
                        Layout.columnSpan: grPage.gridColumns
                        theme: grPage.theme
                        selected: grPage.configuredCompressionEnabled
                        text: grPage.configuredCompressionEnabled ? page.vwaTr("人声压缩已启用") : page.vwaTr("人声压缩已关闭")
                        onClicked: grPage.controller.setOption("compressionEnabled", !grPage.configuredCompressionEnabled)
                    }
                    ParameterSlider {
                        objectName: "mixdownCompressionStrength"
                        Layout.columnSpan: grPage.gridColumns
                        Layout.fillWidth: true
                        theme: grPage.theme
                        label: page.vwaTr("压缩强度")
                        displayValue: grPage.compressionStrength
                        displayFactor: 100
                        unit: "%"
                        step: 0.01
                        interactive: grPage.configuredCompressionEnabled
                        onMoved: (amount) => grPage.controller.setOption("compressionStrength", amount)
                    }
                    Text {
                        Layout.columnSpan: grPage.gridColumns
                        Layout.fillWidth: true
                        text: page.vwaTr("随强度变化：阈值 ") + grPage.compressionThreshold.toFixed(1) + page.vwaTr(" dB · 压缩比 ")
                            + grPage.compressionRatio.toFixed(2) + page.vwaTr(":1 · 启动 ")
                            + grPage.compressorAttack.toFixed(1) + page.vwaTr(" ms · 释放 ")
                            + grPage.compressorRelease.toFixed(0) + " ms"
                        color: grPage.theme.muted
                        wrapMode: Text.Wrap
                        font.pixelSize: Math.round(10 * grPage.theme.scale)
                    }
                    Text {
                        Layout.columnSpan: grPage.gridColumns
                        Layout.fillWidth: true
                        text: grPage.limiterMeasured && grPage.limiterEnabled
                            ? page.vwaTr("总线安全限制 · ") + grPage.compressorCeiling.toFixed(1) + " dBFS"
                            : page.vwaTr("总线限制 · 等待实测")
                        color: grPage.theme.muted
                        font.pixelSize: Math.round(10 * grPage.theme.scale)
                    }
                }
            }
        }
    }

    component ReverbAnalysis: Item {
        id: reverbPage
        required property QtObject theme
        required property QtObject controller
        property bool telemetryAvailable: false
        property real sendLevel: NaN
        property real sendPeak: NaN
        property real returnLevel: NaN
        property real returnPeak: NaN
        property real dryLevel: NaN
        property real wetFraction: NaN
        property bool edcAvailable: false
        property bool edcMeasured: false
        property string edcReason: ""
        property bool configuredEnabled: false
        property string processingSelection: "B"
        property string source: ""
        property var curve: []
        property bool effectEnabled: false
        property string room: "natural"
        property real mix: 0
        property real predelay: 0
        property bool paintQueued: false

        onCurveChanged: schedulePaint()
        onVisibleChanged: if (visible) schedulePaint()

        function schedulePaint() {
            if (!visible || paintQueued)
                return;
            paintQueued = true;
            Qt.callLater(function () {
                reverbPage.paintQueued = false;
                reverbCanvas.requestPaint();
            });
        }

        function roomOptions() {
            return [
                {
                    label: page.vwaTr("小空间"),
                    value: "small"
                },
                {
                    label: page.vwaTr("自然空间"),
                    value: "natural"
                },
                {
                    label: page.vwaTr("宽空间"),
                    value: "wide"
                }
            ];
        }

        function roomIndex(value) {
            const options = roomOptions();
            for (let index = 0; index < options.length; ++index) {
                if (options[index].value === String(value))
                    return index;
            }
            return 1;
        }

        function finite(value) {
            const parsed = Number(value);
            return isFinite(parsed);
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: Math.round(8 * reverbPage.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: page.vwaTr("混响与空间")
                    color: reverbPage.theme.text
                    font.pixelSize: Math.round(15 * reverbPage.theme.scale)
                    font.weight: Font.DemiBold
                }
                Item {
                    Layout.fillWidth: true
                }
                Text {
                    text: reverbPage.source.length > 0 ? page.vwaTr("实测源 · ") + reverbPage.source : page.vwaTr("等待真实混响遥测")
                    color: reverbPage.source.length > 0 ? reverbPage.theme.muted : reverbPage.theme.attention
                    font.pixelSize: Math.round(10 * reverbPage.theme.scale)
                    elide: Text.ElideRight
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 18 * reverbPage.theme.scale
                SignalReadout {
                    objectName: "mixdownReverbSend"
                    theme: reverbPage.theme
                    label: page.vwaTr("实时发送")
                    rms: reverbPage.processingSelection === "B" && reverbPage.effectEnabled ? reverbPage.sendLevel : NaN
                    peak: reverbPage.processingSelection === "B" && reverbPage.effectEnabled ? reverbPage.sendPeak : NaN
                    emptyLabel: reverbPage.processingSelection === "A" ? "A · 旁路"
                        : reverbPage.telemetryAvailable && !reverbPage.effectEnabled ? "混响旁路" : "等待实测"
                }
                SignalReadout {
                    objectName: "mixdownReverbReturn"
                    theme: reverbPage.theme
                    label: page.vwaTr("实时返回")
                    rms: reverbPage.processingSelection === "B" && reverbPage.effectEnabled ? reverbPage.returnLevel : NaN
                    peak: reverbPage.processingSelection === "B" && reverbPage.effectEnabled ? reverbPage.returnPeak : NaN
                    ink: reverbPage.theme.accent
                    emptyLabel: reverbPage.processingSelection === "A" ? "A · 旁路"
                        : reverbPage.telemetryAvailable && !reverbPage.effectEnabled ? "混响旁路" : "等待实测"
                }
            }
            Text {
                Layout.fillWidth: true
                text: page.vwaTr("空间单位脉冲衰减（EDC）") + (reverbPage.edcAvailable && reverbPage.edcMeasured ? page.vwaTr(" · 已测量") : "")
                color: reverbPage.theme.muted
                font.pixelSize: 11 * reverbPage.theme.scale
                ToolTip.visible: edcHover.hovered && !!reverbPage.edcReason
                ToolTip.text: reverbPage.edcReason
                HoverHandler { id: edcHover }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: Math.round(180 * reverbPage.theme.scale)
                radius: Math.round(8 * reverbPage.theme.scale)
                color: reverbPage.theme.base
                border.color: reverbPage.theme.line
                Canvas {
                    id: reverbCanvas
                    anchors.fill: parent
                    renderTarget: Canvas.FramebufferObject
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        ctx.globalAlpha = 1;
                        if (width < 20 || height < 20)
                            return;
                        const scale = reverbPage.theme.scale;
                        const left = Math.round(42 * scale);
                        const right = width - Math.round(12 * scale);
                        const top = Math.round(18 * scale);
                        const bottom = height - Math.round(25 * scale);
                        const plotWidth = Math.max(1, right - left);
                        const plotHeight = Math.max(1, bottom - top);
                        ctx.lineWidth = 1;
                        for (let line = 0; line <= 4; ++line) {
                            const y = top + plotHeight * line / 4;
                            ctx.strokeStyle = reverbPage.theme.line;
                            ctx.globalAlpha = 0.65;
                            ctx.beginPath();
                            ctx.moveTo(left, y);
                            ctx.lineTo(right, y);
                            ctx.stroke();
                        }
                        ctx.globalAlpha = 1;
                        ctx.font = Math.round(9 * scale) + "px sans-serif";
                        ctx.fillStyle = reverbPage.theme.muted;
                        ctx.textAlign = "left";
                        ctx.fillText("1.0", Math.round(8 * scale), top + 3);
                        ctx.fillText("0.0", Math.round(8 * scale), bottom);
                        ctx.textAlign = "center";
                        ctx.fillText("0", left, bottom + Math.round(14 * scale));
                        ctx.fillText("归一化时间", left + plotWidth / 2, bottom + Math.round(14 * scale));
                        ctx.fillText("1", right, bottom + Math.round(14 * scale));

                        if (reverbPage.curve && reverbPage.curve.length > 0) {
                            ctx.beginPath();
                            let started = false;
                            for (let index = 0; index < reverbPage.curve.length; ++index) {
                                const point = reverbPage.curve[index];
                                if (!point)
                                    continue;
                                const nx = Number(point.x);
                                const ny = Number(point.y);
                                if (!isFinite(nx) || !isFinite(ny))
                                    continue;
                                const x = left + Math.max(0, Math.min(1, nx)) * plotWidth;
                                const y = bottom - Math.max(0, Math.min(1, ny)) * plotHeight;
                                if (!started) {
                                    ctx.moveTo(x, y);
                                    started = true;
                                } else {
                                    ctx.lineTo(x, y);
                                }
                            }
                            if (started) {
                                ctx.strokeStyle = reverbPage.theme.accent;
                                ctx.lineWidth = Math.max(2, 2 * scale);
                                ctx.stroke();
                            }
                        } else {
                            ctx.fillStyle = reverbPage.theme.attention;
                            ctx.textAlign = "center";
                            ctx.font = Math.round(11 * scale) + "px sans-serif";
                            ctx.fillText("尚未测得单位脉冲衰减", left + plotWidth / 2, top + plotHeight * 0.38);
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.round(126 * reverbPage.theme.scale)
                radius: Math.round(8 * reverbPage.theme.scale)
                color: reverbPage.theme.base
                border.color: reverbPage.theme.line
                GridLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(10 * reverbPage.theme.scale)
                    columns: parent.width > Math.round(620 * reverbPage.theme.scale) ? 4 : 2
                    columnSpacing: Math.round(12 * reverbPage.theme.scale)
                    rowSpacing: Math.round(8 * reverbPage.theme.scale)
                    UiButton {
                        objectName: "mixdownToggleReverb"
                        theme: reverbPage.theme
                        selected: reverbPage.configuredEnabled
                        text: reverbPage.configuredEnabled ? page.vwaTr("混响已启用") : page.vwaTr("混响已关闭")
                        onClicked: reverbPage.controller.setOption("reverbEnabled", !reverbPage.configuredEnabled)
                    }
                    UiComboBox {
                        Layout.fillWidth: true
                        theme: reverbPage.theme
                        model: reverbPage.roomOptions()
                        currentIndex: reverbPage.roomIndex(reverbPage.room)
                        onActivated: {
                            if (currentValue && String(currentValue) !== "custom")
                                reverbPage.controller.setOption("reverbRoom", currentValue);
                        }
                        Accessible.name: page.vwaTr("混响空间")
                    }
                    ParameterSlider {
                        objectName: "mixdownReverbMix"
                        Layout.fillWidth: true
                        theme: reverbPage.theme
                        label: page.vwaTr("混合")
                        fromValue: 0
                        toValue: 1
                        displayValue: reverbPage.mix
                        displayFactor: 100
                        unit: "%"
                        step: 0.01
                        interactive: reverbPage.configuredEnabled
                        onMoved: (amount) => reverbPage.controller.setOption("reverbMix", amount)
                    }
                    ParameterSlider {
                        objectName: "mixdownReverbPredelay"
                        Layout.fillWidth: true
                        theme: reverbPage.theme
                        label: page.vwaTr("预延迟")
                        toValue: 120
                        displayValue: reverbPage.predelay
                        unit: " ms"
                        interactive: reverbPage.configuredEnabled
                        onMoved: (milliseconds) => reverbPage.controller.setOption("reverbPredelay", milliseconds)
                    }
                }
            }
        }
    }
}
