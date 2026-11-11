import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    objectName: "mixdownPage"
    required property QtObject theme
    required property QtObject controller

    readonly property var view: controller.view || ({})
    readonly property var tracks: controller.tracks || []
    readonly property var bands: controller.bands || []
    readonly property var eqCurve: controller.eqCurve || []
    readonly property var meters: controller.meters || ({})
    readonly property var spectrum: controller.spectrum || ({})
    readonly property var reverbCurve: controller.reverbCurve || []
    readonly property var presets: controller.presets || []

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
    readonly property bool compact: width < Math.round((theme.scale > 1.2 ? 1120 : 980) * theme.scale)
    readonly property int pageMargin: Math.round((compact ? 14 : 20) * theme.scale)
    readonly property int footerHeight: Math.round((compact ? 148 : 137) * theme.scale)
    readonly property int vocalTrackCount: page.countVocalTracks()
    readonly property int vocalTrackLimit: page.number(view.maxVocalTracks, 0)
    readonly property bool canAddTrack: vocalTrackCount < vocalTrackLimit
    readonly property bool spaceBlocked: page.dialogCount > 0 || page.focusBlocksSpace(page.Window.activeFocusItem)

    property bool seeking: false
    property real pendingPosition: 0
    property int dialogCount: 0
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
        return page.finishSeekGesture(false);
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
            if (current instanceof TextInput || current instanceof TextEdit || current instanceof TextField || current instanceof TextArea || current instanceof SpinBox || current instanceof ComboBox || current instanceof Popup || current instanceof Dialog)
                return true;
            current = current.parent;
        }
        return false;
    }

    onVisibleChanged: {
        if (!page.visible)
            page.cancelSeekGesture();
    }
    Component.onDestruction: page.cancelSeekGesture()

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
        height: Math.round(92 * page.theme.scale)
        spacing: Math.round(7 * page.theme.scale)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(9 * page.theme.scale)

            Text {
                text: "简易混音"
                color: page.theme.text
                font.pixelSize: Math.round(22 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            Rectangle {
                width: Math.round(7 * page.theme.scale)
                height: width
                radius: width / 2
                color: page.busy ? page.theme.attention : page.theme.success
            }
            Text {
                Layout.fillWidth: true
                text: page.textValue(page.view.job, "独立 WAV · 无需先创建任务")
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                elide: Text.ElideMiddle
            }
            Text {
                visible: page.resultText(page.view.result).length > 0
                text: page.view.resultStale ? "旧导出保留 · 设置已变化" : "已有导出结果"
                color: page.view.resultStale ? page.theme.attention : page.theme.success
                font.pixelSize: Math.round(11 * page.theme.scale)
            }
            UiButton {
                objectName: "mixdownInstallComponents"
                visible: !!page.view.componentMissing && !page.busy
                theme: page.theme
                primary: true
                text: "安装混音组件"
                enabled: !page.busy
                onClicked: page.controller.installMixdown()
                Accessible.description: "使用随包 manifest 与安装器离线安装已固定的混音组件"
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(7 * page.theme.scale)

            Text {
                text: "方案"
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
                Accessible.name: "混音预设"
                onActivated: {
                    if (currentValue && String(currentValue) !== "custom")
                        page.controller.applyPreset(String(currentValue));
                }
            }
            Text {
                text: page.textValue(page.view.presetName, "自定义")
                color: page.theme.muted
                font.pixelSize: Math.round(10 * page.theme.scale)
                elide: Text.ElideRight
                Layout.maximumWidth: Math.round(130 * page.theme.scale)
            }
            Item {
                Layout.fillWidth: true
            }
            UiButton {
                objectName: "mixdownSavePreset"
                theme: page.theme
                text: "追加预设…"
                enabled: !page.busy && !page.loading
                onClicked: savePresetDialog.open()
            }
            UiButton {
                objectName: "mixdownImportPresets"
                theme: page.theme
                quiet: true
                text: "导入"
                enabled: !page.busy && !page.loading
                onClicked: page.openManagedDialog(importPresetDialog)
            }
            UiButton {
                objectName: "mixdownExportPreset"
                theme: page.theme
                quiet: true
                text: "导出"
                enabled: !page.busy && !page.loading && page.currentPresetKey().length > 0
                onClicked: page.openManagedDialog(exportPresetDialog)
            }
        }
    }

    Flickable {
        id: viewport
        objectName: "mixdownViewport"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: headerLayout.bottom
        anchors.bottom: footer.top
        anchors.leftMargin: page.pageMargin
        anchors.rightMargin: page.pageMargin
        anchors.topMargin: Math.round(4 * page.theme.scale)
        anchors.bottomMargin: Math.round(8 * page.theme.scale)
        contentWidth: width
        contentHeight: Math.max(bodyGrid.implicitHeight, height)
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
        }

        GridLayout {
            id: bodyGrid
            width: viewport.width
            height: Math.max(implicitHeight, viewport.height)
            columns: 1
            rowSpacing: Math.round(12 * page.theme.scale)

            Rectangle {
                id: mixerPanel
                objectName: "mixdownMixerPanel"
                visible: !page.expanded
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(275 * page.theme.scale)
                Layout.minimumHeight: Layout.preferredHeight
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
                        Text {
                            text: "混音台"
                            color: page.theme.text
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.vocalTrackCount + " / " + page.vocalTrackLimit + " 路歌声"
                            color: page.theme.muted
                            font.pixelSize: Math.round(10 * page.theme.scale)
                        }
                        UiButton {
                            objectName: "mixdownAddTrack"
                            theme: page.theme
                            text: "+ 歌声轨"
                            enabled: page.canAddTrack && !page.busy && !page.loading
                            onClicked: page.controller.addTrack()
                        }
                    }

                    Flickable {
                        id: channelViewport
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: Math.round(185 * page.theme.scale)
                        contentWidth: channelRow.implicitWidth
                        contentHeight: height
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.horizontal: ScrollBar {
                            id: channelScroll
                            policy: ScrollBar.AsNeeded
                        }

                        Row {
                            id: channelRow
                            height: Math.max(0, channelViewport.height - (implicitWidth > channelViewport.width ? channelScroll.height : 0))
                            spacing: Math.round(8 * page.theme.scale)

                            Repeater {
                                model: page.controller.trackIds
                                Rectangle {
                                    id: channel
                                    required property int index
                                    required property string modelData
                                    objectName: "mixdownTrack_" + String(channel.trackId)
                                    readonly property string trackId: modelData
                                    readonly property var track: page.trackById(trackId)
                                    readonly property bool backing: trackId === "instrumental"
                                    readonly property string trackLabel: String(page.trackValue(track, "label", backing ? "伴奏" : "歌声 " + (index + 1)))
                                    readonly property string trackName: String(page.trackValue(track, "name", ""))
                                    readonly property string trackPath: String(page.trackValue(track, "path", ""))
                                    readonly property real gainDb: page.number(page.trackValue(track, "gain", 0), 0)
                                    readonly property real sendLevel: page.clamp(page.number(page.trackValue(track, "send", 0), 0), 0, 1)
                                    readonly property color channelAccent: backing ? page.theme.attention : page.theme.success

                                    width: Math.round(158 * page.theme.scale)
                                    height: parent.height
                                    radius: Math.round(8 * page.theme.scale)
                                    color: page.theme.base
                                    border.width: 1
                                    border.color: page.theme.line

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: Math.round(8 * page.theme.scale)
                                        spacing: Math.round(5 * page.theme.scale)

                                        RowLayout {
                                            Layout.fillWidth: true
                                            Rectangle {
                                                width: Math.round(7 * page.theme.scale)
                                                height: width
                                                radius: width / 2
                                                color: channel.channelAccent
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                text: channel.trackLabel
                                                color: page.theme.text
                                                font.pixelSize: Math.round(11 * page.theme.scale)
                                                font.weight: Font.DemiBold
                                                elide: Text.ElideRight
                                            }
                                            UiButton {
                                                visible: !channel.backing
                                                theme: page.theme
                                                quiet: true
                                                text: "×"
                                                implicitWidth: Math.round(25 * page.theme.scale)
                                                implicitHeight: Math.round(25 * page.theme.scale)
                                                enabled: !page.busy && !page.loading && page.vocalTrackCount > 1
                                                onClicked: page.controller.removeTrack(channel.trackId)
                                                Accessible.name: "移除" + channel.trackLabel
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            UiButton {
                                                Layout.fillWidth: true
                                                theme: page.theme
                                                text: channel.trackPath.length > 0 ? "更换…" : "选择…"
                                                enabled: !page.busy && !page.loading
                                                onClicked: page.chooseTrackSource(channel.trackId)
                                            }
                                            UiButton {
                                                visible: channel.trackPath.length > 0
                                                theme: page.theme
                                                quiet: true
                                                text: "清除"
                                                enabled: !page.busy && !page.loading
                                                onClicked: page.controller.clearSource(channel.trackId)
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: Math.round(19 * page.theme.scale)
                                            text: channel.trackName.length > 0 ? channel.trackName : channel.trackPath.length > 0 ? page.shortPath(channel.trackPath) : channel.backing ? "等待选择伴奏" : "可选音轨"
                                            color: page.theme.muted
                                            font.pixelSize: Math.round(9 * page.theme.scale)
                                            elide: Text.ElideRight
                                            ToolTip.visible: pathHover.hovered && truncated
                                            ToolTip.text: channel.trackPath
                                            HoverHandler {
                                                id: pathHover
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            spacing: Math.round(5 * page.theme.scale)

                                            Slider {
                                                id: gainSlider
                                                objectName: "mixdownGain_" + channel.trackId
                                                Layout.fillHeight: true
                                                Layout.preferredWidth: Math.round(30 * page.theme.scale)
                                                Layout.minimumHeight: Math.round(58 * page.theme.scale)
                                                Layout.preferredHeight: Math.round(92 * page.theme.scale)
                                                orientation: Qt.Vertical
                                                from: -60
                                                to: 12
                                                stepSize: 0.1
                                                value: page.clamp(channel.gainDb, -60, 12)
                                                enabled: !page.busy && !page.loading
                                                onMoved: page.controller.setTrack(channel.trackId, "gain", value)
                                                Accessible.name: channel.trackLabel + " 增益"
                                                Accessible.description: value.toFixed(1) + " dB"
                                                background: Rectangle {
                                                    x: Math.round((gainSlider.availableWidth - width) / 2)
                                                    y: Math.round(gainSlider.topPadding)
                                                    width: Math.round(5 * page.theme.scale)
                                                    height: gainSlider.availableHeight
                                                    radius: width / 2
                                                    color: page.theme.line
                                                    Rectangle {
                                                        width: parent.width
                                                        height: Math.max(0, parent.height * (gainSlider.value - gainSlider.from) / (gainSlider.to - gainSlider.from))
                                                        anchors.bottom: parent.bottom
                                                        radius: parent.radius
                                                        color: channel.channelAccent
                                                        opacity: 0.72
                                                    }
                                                }
                                                handle: Rectangle {
                                                    x: Math.round((gainSlider.availableWidth - width) / 2)
                                                    y: gainSlider.topPadding + gainSlider.visualPosition * (gainSlider.availableHeight - height)
                                                    width: Math.round(16 * page.theme.scale)
                                                    height: Math.round(9 * page.theme.scale)
                                                    radius: Math.round(4 * page.theme.scale)
                                                    color: page.theme.text
                                                    border.width: 1
                                                    border.color: channel.channelAccent
                                                }
                                            }

                                            MixdownMeter {
                                                Layout.fillHeight: true
                                                Layout.preferredHeight: Math.round(92 * page.theme.scale)
                                                theme: page.theme
                                                rms: page.laneValue(channel.trackId, "rms")
                                                peak: page.laneValue(channel.trackId, "peak")
                                                accentColor: channel.channelAccent
                                                accessibleLabel: channel.trackLabel + " 电平"
                                            }

                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: Math.round(4 * page.theme.scale)
                                                Text {
                                                    Layout.alignment: Qt.AlignHCenter
                                                    text: channel.gainDb <= -59.9 ? "−∞" : channel.gainDb.toFixed(1) + " dB"
                                                    color: page.theme.text
                                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                                    font.weight: Font.DemiBold
                                                }
                                                RowLayout {
                                                    Layout.alignment: Qt.AlignHCenter
                                                    UiButton {
                                                        theme: page.theme
                                                        selected: !!page.trackValue(channel.track, "muted", false)
                                                        text: "M"
                                                        implicitWidth: Math.round(30 * page.theme.scale)
                                                        implicitHeight: Math.round(28 * page.theme.scale)
                                                        enabled: !page.busy && !page.loading
                                                        onClicked: page.controller.setTrack(channel.trackId, "muted", !page.trackValue(channel.track, "muted", false))
                                                        Accessible.name: channel.trackLabel + " 静音"
                                                    }
                                                    UiButton {
                                                        theme: page.theme
                                                        selected: !!page.trackValue(channel.track, "solo", false)
                                                        text: "S"
                                                        implicitWidth: Math.round(30 * page.theme.scale)
                                                        implicitHeight: Math.round(28 * page.theme.scale)
                                                        enabled: !page.busy && !page.loading
                                                        onClicked: page.controller.setTrack(channel.trackId, "solo", !page.trackValue(channel.track, "solo", false))
                                                        Accessible.name: channel.trackLabel + " 独奏"
                                                    }
                                                }
                                                Text {
                                                    visible: !channel.backing
                                                    text: "发送 " + page.percentText(channel.sendLevel)
                                                    color: page.theme.muted
                                                    font.pixelSize: Math.round(9 * page.theme.scale)
                                                }
                                                Slider {
                                                    objectName: "mixdownSend_" + channel.trackId
                                                    visible: !channel.backing
                                                    Layout.fillWidth: true
                                                    Layout.preferredHeight: Math.round(18 * page.theme.scale)
                                                    from: 0
                                                    to: 1
                                                    stepSize: 0.01
                                                    value: channel.sendLevel
                                                    enabled: !page.busy && !page.loading
                                                    onMoved: page.controller.setTrack(channel.trackId, "send", value)
                                                    Accessible.name: channel.trackLabel + " 混响发送"
                                                    background: Rectangle {
                                                        y: Math.round((parent.height - height) / 2)
                                                        width: parent.width
                                                        height: Math.round(4 * page.theme.scale)
                                                        radius: height / 2
                                                        color: page.theme.line
                                                    }
                                                    handle: Rectangle {
                                                        x: parent.visualPosition * (parent.width - width)
                                                        y: Math.round((parent.height - height) / 2)
                                                        width: Math.round(13 * page.theme.scale)
                                                        height: width
                                                        radius: width / 2
                                                        color: page.theme.text
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            Rectangle {
                                objectName: "mixdownMonitorChannel"
                                width: Math.round(158 * page.theme.scale)
                                height: parent.height
                                radius: Math.round(8 * page.theme.scale)
                                color: page.theme.selected
                                border.width: 1
                                border.color: page.theme.selectedLine

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: Math.round(8 * page.theme.scale)
                                    spacing: Math.round(5 * page.theme.scale)
                                    Text {
                                        text: "试听输出"
                                        color: page.theme.text
                                        font.pixelSize: Math.round(11 * page.theme.scale)
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: "试听音量 · 不写入 WAV"
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(9 * page.theme.scale)
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        spacing: Math.round(7 * page.theme.scale)
                                        Slider {
                                            id: masterGainSlider
                                            objectName: "mixdownMonitorVolume"
                                            Layout.fillHeight: true
                                            Layout.preferredWidth: Math.round(30 * page.theme.scale)
                                            Layout.minimumHeight: Math.round(58 * page.theme.scale)
                                            Layout.preferredHeight: Math.round(92 * page.theme.scale)
                                            orientation: Qt.Vertical
                                            from: -60
                                            to: 0
                                            stepSize: 0.1
                                            value: page.clamp(page.number(page.view.masterGain, 0), from, to)
                                            enabled: !page.busy && !page.loading
                                            onMoved: page.controller.setOption("masterGain", value)
                                            Accessible.name: "试听音量"
                                            background: Rectangle {
                                                x: Math.round((masterGainSlider.availableWidth - width) / 2)
                                                y: Math.round(masterGainSlider.topPadding)
                                                width: Math.round(5 * page.theme.scale)
                                                height: masterGainSlider.availableHeight
                                                radius: width / 2
                                                color: page.theme.line
                                                Rectangle {
                                                    width: parent.width
                                                    height: Math.max(0, parent.height * (masterGainSlider.value - masterGainSlider.from) / (masterGainSlider.to - masterGainSlider.from))
                                                    anchors.bottom: parent.bottom
                                                    radius: parent.radius
                                                    color: page.theme.accent
                                                }
                                            }
                                            handle: Rectangle {
                                                x: Math.round((masterGainSlider.availableWidth - width) / 2)
                                                y: masterGainSlider.topPadding + masterGainSlider.visualPosition * (masterGainSlider.availableHeight - height)
                                                width: Math.round(16 * page.theme.scale)
                                                height: Math.round(9 * page.theme.scale)
                                                radius: Math.round(4 * page.theme.scale)
                                                color: page.theme.text
                                                border.color: page.theme.accent
                                            }
                                        }

                                        MixdownMeter {
                                            Layout.fillHeight: true
                                            Layout.preferredHeight: Math.round(92 * page.theme.scale)
                                            theme: page.theme
                                            rms: page.laneValue("master", "rms")
                                            peak: page.laneValue("master", "peak")
                                            accentColor: page.theme.accent
                                            accessibleLabel: "总线电平"
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: Math.round(4 * page.theme.scale)
                                            Text {
                                                Layout.alignment: Qt.AlignHCenter
                                                text: "试听音量"
                                                color: page.theme.muted
                                                font.pixelSize: Math.round(9 * page.theme.scale)
                                            }
                                            Text {
                                                Layout.alignment: Qt.AlignHCenter
                                                text: page.dbText(page.view.masterGain)
                                                color: page.theme.text
                                                font.pixelSize: Math.round(10 * page.theme.scale)
                                                font.weight: Font.DemiBold
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.view.message ? String(page.view.message) : page.busy ? "正在处理；可继续查看当前状态。" : "选择素材后，用 Space 播放或暂停。"
                        color: page.view.state === "failed" ? page.theme.error : page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        ToolTip.visible: messageHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler {
                            id: messageHover
                        }
                    }
                }
            }

            Rectangle {
                id: analysisPanel
                objectName: "mixdownAnalysisPanel"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: Math.round(325 * page.theme.scale)
                Layout.preferredHeight: Layout.minimumHeight
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
                            onClicked: page.controller.setAnalysis("eq")
                        }
                        UiButton {
                            objectName: "mixdownAnalysisGr"
                            theme: page.theme
                            selected: page.analysisMode === "gr"
                            text: "GR"
                            onClicked: page.controller.setAnalysis("gr")
                        }
                        UiButton {
                            objectName: "mixdownAnalysisReverb"
                            theme: page.theme
                            selected: page.analysisMode === "reverb"
                            text: "混响"
                            onClicked: page.controller.setAnalysis("reverb")
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Text {
                            Layout.maximumWidth: Math.round(210 * page.theme.scale)
                            text: page.meters && page.meters.source ? "实时源 · " + page.meters.source : "等待实时遥测"
                            color: page.meters && page.meters.source ? page.theme.muted : page.theme.attention
                            font.pixelSize: Math.round(10 * page.theme.scale)
                            elide: Text.ElideRight
                        }
                        UiButton {
                            objectName: "mixdownExpand"
                            theme: page.theme
                            text: page.expanded ? "返回混音台" : "放大曲线"
                            selected: page.expanded
                            onClicked: page.controller.setExpanded(!page.expanded)
                        }
                    }

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
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
                            selection: page.selectionMode
                        }

                        GrAnalysis {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            theme: page.theme
                            controller: page.controller
                            gr: page.number(page.meters.gr, NaN)
                            limiter: page.number(page.meters.limiter, NaN)
                            source: page.meters && page.meters.source ? String(page.meters.source) : ""
                            compressionEnabled: page.boolOption("compressionEnabled", false)
                            compressionThreshold: page.number(page.view.compressionThreshold, -18)
                            compressionRatio: page.number(page.view.compressionRatio, 3)
                            compressorAttack: page.number(page.view.compressorAttack, 10)
                            compressorRelease: page.number(page.view.compressorRelease, 100)
                            limiterEnabled: page.boolOption("limiterEnabled", false)
                            compressorCeiling: page.number(page.view.limiterCeiling, -1)
                        }

                        ReverbAnalysis {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            theme: page.theme
                            controller: page.controller
                            tail: page.number(page.meters.tail, NaN)
                            source: page.meters && page.meters.source ? String(page.meters.source) : ""
                            curve: page.reverbCurve
                            enabled: page.boolOption("reverbEnabled", false)
                            room: String(page.view.reverbRoom || "natural")
                            mix: page.number(page.view.reverbMix, 0)
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
        height: page.footerHeight
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
            anchors.fill: parent
            anchors.leftMargin: page.pageMargin
            anchors.rightMargin: page.pageMargin
            anchors.topMargin: Math.round(8 * page.theme.scale)
            anchors.bottomMargin: Math.round(8 * page.theme.scale)
            spacing: Math.round(6 * page.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(6 * page.theme.scale)
                UiButton {
                    objectName: "mixdownPlayPause"
                    theme: page.theme
                    primary: page.playing
                    text: page.playing ? "暂停" : "播放"
                    enabled: page.hasMedia && page.canPlay
                    onClicked: page.controller.togglePlay()
                    Accessible.description: "Space"
                }
                UiButton {
                    objectName: "mixdownLoop"
                    theme: page.theme
                    selected: !!page.view.loop
                    text: "循环"
                    onClicked: page.controller.setLoop(!page.view.loop)
                }
                UiButton {
                    objectName: "mixdownBefore"
                    theme: page.theme
                    selected: page.selectionMode === "A"
                    text: "A 处理前"
                    onClicked: page.controller.selectMix("A")
                }
                UiButton {
                    objectName: "mixdownAfter"
                    theme: page.theme
                    selected: page.selectionMode === "B"
                    text: "B 处理后"
                    onClicked: page.controller.selectMix("B")
                }
                Text {
                    text: page.timeText(page.seeking ? page.pendingPosition : page.positionMs) + " / " + page.timeText(page.durationMs)
                    color: page.theme.text
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    font.family: "Consolas"
                }
                Text {
                    Layout.fillWidth: true
                    text: page.textValue(page.view.message, page.loading ? "正在加载真实音频…" : "Space 播放 / 暂停")
                    color: page.view.state === "failed" ? page.theme.error : page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    elide: Text.ElideRight
                }
                UiButton {
                    visible: page.busy || page.loading
                    theme: page.theme
                    quiet: true
                    text: "取消"
                    onClicked: page.controller.cancel()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(6 * page.theme.scale)
                Text {
                    text: "WAV"
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: page.textValue(page.view.output, "尚未选择导出文件")
                    color: page.textValue(page.view.output, "").length > 0 ? page.theme.text : page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                    elide: Text.ElideMiddle
                    ToolTip.visible: outputHover.hovered && truncated
                    ToolTip.text: page.textValue(page.view.output, "")
                    HoverHandler {
                        id: outputHover
                    }
                }
                Text {
                    text: "采样率随伴奏"
                    color: page.theme.muted
                    font.pixelSize: Math.round(10 * page.theme.scale)
                }
                UiButton {
                    objectName: "mixdownChooseOutput"
                    theme: page.theme
                    text: "输出…"
                    enabled: !page.busy && !page.loading
                    onClicked: page.openManagedDialog(outputDialog)
                }
                UiButton {
                    objectName: "mixdownExport"
                    theme: page.theme
                    primary: true
                    text: "导出新 WAV"
                    enabled: !!page.view.canExport && !page.busy && !page.loading
                    onClicked: page.controller.exportMix()
                }
                UiButton {
                    objectName: "mixdownRegister"
                    theme: page.theme
                    text: page.view.resultStale ? "旧结果不可登记" : "登记结果"
                    enabled: !!page.view.canRegister && !page.view.resultStale && !page.busy && !page.loading
                    onClicked: page.controller.registerMix()
                }
                UiButton {
                    objectName: "mixdownOpenResult"
                    theme: page.theme
                    quiet: true
                    text: "打开结果"
                    enabled: page.resultText(page.view.result).length > 0
                    onClicked: page.controller.openResult()
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.seeking ? "待定位 " + page.timeText(page.pendingPosition) : page.loading ? "等待播放器确认定位与媒质状态" : "拖到任意位置定位；播放时钟由控制器提供"
                color: page.theme.muted
                font.pixelSize: Math.round(9 * page.theme.scale)
                elide: Text.ElideRight
            }

            Slider {
                id: seekSlider
                objectName: "mixdownProgress"
                Layout.fillWidth: true
                from: 0
                to: Math.max(1, page.durationMs)
                stepSize: 1
                enabled: page.hasMedia && !page.loading && page.durationMs > 0
                focusPolicy: Qt.StrongFocus
                Accessible.name: "混音播放进度"
                Accessible.description: page.timeText(page.positionMs) + " / " + page.timeText(page.durationMs)

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
                    border.color: page.theme.line
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

    FileDialog {
        id: sourceDialog
        property string sourceTrackId: ""
        objectName: "mixdownSourceDialog"
        title: "选择音频轨"
        fileMode: FileDialog.OpenFile
        nameFilters: ["音频文件 (*.wav *.flac *.mp3 *.m4a *.ogg *.aac *.opus *.wma *.aif *.aiff *.ape *.wv)", "所有文件 (*)"]
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
        title: "选择独立 WAV 输出"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "wav"
        nameFilters: ["WAV 音频 (*.wav)"]
        onAccepted: {
            page.noteDialogClosed();
            page.controller.chooseOutput(selectedFile);
        }
        onRejected: page.noteDialogClosed()
    }

    FileDialog {
        id: importPresetDialog
        objectName: "mixdownImportPresetDialog"
        title: "导入混音预设"
        fileMode: FileDialog.OpenFile
        nameFilters: ["VWA 混音预设 (*.json)", "所有文件 (*)"]
        onAccepted: {
            page.noteDialogClosed();
            page.controller.importPresets(selectedFile);
        }
        onRejected: page.noteDialogClosed()
    }

    FileDialog {
        id: exportPresetDialog
        objectName: "mixdownExportPresetDialog"
        title: "导出混音预设"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["VWA 混音预设 (*.json)"]
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
        title: "追加混音预设"
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - Math.round(36 * page.theme.scale), Math.round(410 * page.theme.scale))
        closePolicy: Popup.CloseOnEscape
        onOpened: {
            page.noteDialogOpened();
            presetNameField.text = page.textValue(page.view.presetName, "");
            presetNameField.forceActiveFocus();
            presetNameField.selectAll();
        }
        onClosed: page.noteDialogClosed()

        contentItem: ColumnLayout {
            spacing: Math.round(10 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: "预设会追加到当前预设库；不会覆盖素材、任务或旧导出。"
                color: page.theme.muted
                font.pixelSize: Math.round(11 * page.theme.scale)
                wrapMode: Text.Wrap
            }
            TextField {
                id: presetNameField
                Layout.fillWidth: true
                objectName: "mixdownPresetName"
                placeholderText: "预设名称"
                color: page.theme.text
                font.pixelSize: Math.round(13 * page.theme.scale)
                selectByMouse: true
                Accessible.name: "预设名称"
                background: Rectangle {
                    radius: Math.round(7 * page.theme.scale)
                    color: page.theme.base
                    border.color: presetNameField.activeFocus ? page.theme.accent : page.theme.line
                }
                onAccepted: {
                    if (presetNameField.text.trim().length > 0) {
                        page.controller.savePreset(presetNameField.text.trim());
                        savePresetDialog.close();
                    }
                }
            }
        }
        footer: RowLayout {
            spacing: Math.round(7 * page.theme.scale)
            Item {
                Layout.fillWidth: true
            }
            UiButton {
                theme: page.theme
                text: "取消"
                onClicked: savePresetDialog.close()
            }
            UiButton {
                theme: page.theme
                primary: true
                text: "追加"
                enabled: presetNameField.text.trim().length > 0
                onClicked: {
                    page.controller.savePreset(presetNameField.text.trim());
                    savePresetDialog.close();
                }
            }
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
        signal moved(real value)

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
            Layout.fillWidth: true
            from: parameter.fromValue
            to: parameter.toValue
            value: parameter.displayValue
            enabled: parameter.interactive
            stepSize: parameter.decimals > 0 ? 0.1 : 1
            onMoved: parameter.moved(value)
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

    component GrAnalysis: Item {
        id: grPage
        required property QtObject theme
        required property QtObject controller
        property real gr: NaN
        property real limiter: NaN
        property string source: ""
        property bool compressionEnabled: false
        property real compressionThreshold: -18
        property real compressionRatio: 3
        property real compressorAttack: 10
        property real compressorRelease: 100
        property bool limiterEnabled: false
        property real compressorCeiling: -1
        property bool paintQueued: false
        readonly property int gridColumns: width > Math.round(640 * theme.scale) ? 4 : 2

        onGrChanged: schedulePaint()
        onLimiterChanged: schedulePaint()
        onCompressionEnabledChanged: schedulePaint()
        onLimiterEnabledChanged: schedulePaint()

        function isLevel(value) {
            const parsed = Number(value);
            return isFinite(parsed);
        }

        function schedulePaint() {
            if (paintQueued)
                return;
            paintQueued = true;
            Qt.callLater(function () {
                grPage.paintQueued = false;
                grCanvas.requestPaint();
            });
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: Math.round(8 * grPage.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "动态与限制"
                    color: grPage.theme.text
                    font.pixelSize: Math.round(15 * grPage.theme.scale)
                    font.weight: Font.DemiBold
                }
                Item {
                    Layout.fillWidth: true
                }
                Text {
                    text: grPage.source.length > 0 ? "实测源 · " + grPage.source : "等待真实 GR 遥测"
                    color: grPage.source.length > 0 ? grPage.theme.muted : grPage.theme.attention
                    font.pixelSize: Math.round(10 * grPage.theme.scale)
                    elide: Text.ElideRight
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(130 * grPage.theme.scale)
                radius: Math.round(8 * grPage.theme.scale)
                color: grPage.theme.base
                border.color: grPage.theme.line

                Canvas {
                    id: grCanvas
                    anchors.fill: parent
                    renderTarget: Canvas.FramebufferObject
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.clearRect(0, 0, width, height);
                        ctx.globalAlpha = 1;
                        const scale = grPage.theme.scale;
                        const left = Math.round(104 * scale);
                        const right = width - Math.round(48 * scale);
                        const barWidth = Math.max(1, right - left);
                        ctx.font = Math.round(10 * scale) + "px sans-serif";
                        ctx.textBaseline = "middle";
                        ctx.textAlign = "left";

                        function row(label, value, y, color, enabled) {
                            ctx.fillStyle = grPage.theme.muted;
                            ctx.fillText(label, Math.round(12 * scale), y);
                            ctx.fillStyle = grPage.theme.line;
                            ctx.fillRect(left, y - Math.round(5 * scale), barWidth, Math.round(10 * scale));
                            if (grPage.isLevel(value)) {
                                const amount = Math.max(0, Math.min(24, Number(value)));
                                ctx.fillStyle = color;
                                ctx.globalAlpha = enabled ? 0.9 : 0.35;
                                ctx.fillRect(left, y - Math.round(5 * scale), barWidth * amount / 24, Math.round(10 * scale));
                                ctx.globalAlpha = 1;
                                ctx.fillStyle = grPage.theme.text;
                                ctx.textAlign = "right";
                                ctx.fillText(amount.toFixed(1) + " dB", width - Math.round(10 * scale), y);
                            } else {
                                ctx.fillStyle = grPage.theme.attention;
                                ctx.textAlign = "right";
                                ctx.fillText("等待测量", width - Math.round(10 * scale), y);
                            }
                            ctx.textAlign = "left";
                        }

                        row("人声压缩 GR", grPage.gr, Math.round(42 * scale), grPage.theme.success, grPage.compressionEnabled);
                        row("总线限制 GR", grPage.limiter, Math.round(92 * scale), grPage.theme.accent, grPage.limiterEnabled);
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
                        selected: grPage.compressionEnabled
                        text: grPage.compressionEnabled ? "人声压缩已启用" : "人声压缩已关闭"
                        onClicked: grPage.controller.setOption("compressionEnabled", !grPage.compressionEnabled)
                    }
                    ParameterSlider {
                        Layout.fillWidth: true
                        theme: grPage.theme
                        label: "阈值"
                        fromValue: -20
                        toValue: -8
                        displayValue: grPage.compressionThreshold
                        unit: " dB"
                        onMoved: grPage.controller.setOption("compressionThreshold", value)
                    }
                    ParameterSlider {
                        Layout.fillWidth: true
                        theme: grPage.theme
                        label: "压缩比"
                        fromValue: 1
                        toValue: 3
                        displayValue: grPage.compressionRatio
                        unit: ":1"
                        decimals: 1
                        onMoved: grPage.controller.setOption("compressionRatio", value)
                    }
                    ParameterSlider {
                        Layout.fillWidth: true
                        theme: grPage.theme
                        label: "启动"
                        fromValue: 15
                        toValue: 25
                        displayValue: grPage.compressorAttack
                        unit: " ms"
                        decimals: 1
                        onMoved: grPage.controller.setOption("compressorAttack", value)
                    }
                    ParameterSlider {
                        Layout.fillWidth: true
                        theme: grPage.theme
                        label: "释放"
                        fromValue: 120
                        toValue: 200
                        displayValue: grPage.compressorRelease
                        unit: " ms"
                        onMoved: grPage.controller.setOption("compressorRelease", value)
                    }

                    UiButton {
                        Layout.columnSpan: grPage.gridColumns
                        theme: grPage.theme
                        selected: grPage.limiterEnabled
                        enabled: false
                        text: grPage.limiterEnabled ? "最终安全限制固定启用" : "安全限制状态异常"
                        Accessible.description: "后端固定 -1 dBFS 安全限制，不提供关闭或修改上限"
                    }
                    Text {
                        Layout.columnSpan: grPage.gridColumns
                        Layout.fillWidth: true
                        text: "固定上限 " + grPage.compressorCeiling.toFixed(1) + " dBFS · 不可关闭"
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
        property real tail: NaN
        property string source: ""
        property var curve: []
        property bool enabled: false
        property string room: "natural"
        property real mix: 0
        property bool paintQueued: false

        onTailChanged: schedulePaint()
        onCurveChanged: schedulePaint()
        onEnabledChanged: schedulePaint()

        function schedulePaint() {
            if (paintQueued)
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
                    label: "小空间",
                    value: "small"
                },
                {
                    label: "自然空间",
                    value: "natural"
                },
                {
                    label: "宽空间",
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
                    text: "混响与空间"
                    color: reverbPage.theme.text
                    font.pixelSize: Math.round(15 * reverbPage.theme.scale)
                    font.weight: Font.DemiBold
                }
                Item {
                    Layout.fillWidth: true
                }
                Text {
                    text: reverbPage.source.length > 0 ? "实测源 · " + reverbPage.source : "等待真实混响遥测"
                    color: reverbPage.source.length > 0 ? reverbPage.theme.muted : reverbPage.theme.attention
                    font.pixelSize: Math.round(10 * reverbPage.theme.scale)
                    elide: Text.ElideRight
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.round(56 * reverbPage.theme.scale)
                    radius: Math.round(8 * reverbPage.theme.scale)
                    color: reverbPage.theme.base
                    border.color: reverbPage.theme.line
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: Math.round(10 * reverbPage.theme.scale)
                        Text {
                            text: "实时尾音"
                            color: reverbPage.theme.muted
                            font.pixelSize: Math.round(10 * reverbPage.theme.scale)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Text {
                            text: reverbPage.finite(reverbPage.tail) ? Number(reverbPage.tail).toFixed(1) + " dBFS" : "等待播放"
                            color: reverbPage.finite(reverbPage.tail) ? reverbPage.theme.text : reverbPage.theme.attention
                            font.pixelSize: Math.round(15 * reverbPage.theme.scale)
                            font.weight: Font.DemiBold
                        }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: Math.round(56 * reverbPage.theme.scale)
                    radius: Math.round(8 * reverbPage.theme.scale)
                    color: reverbPage.theme.base
                    border.color: reverbPage.theme.line
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: Math.round(10 * reverbPage.theme.scale)
                        Text {
                            text: "当前混合"
                            color: reverbPage.theme.muted
                            font.pixelSize: Math.round(10 * reverbPage.theme.scale)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        Text {
                            text: Math.round(Math.max(0, Math.min(1, reverbPage.mix)) * 100) + "%"
                            color: reverbPage.theme.text
                            font.pixelSize: Math.round(15 * reverbPage.theme.scale)
                            font.weight: Font.DemiBold
                        }
                    }
                }
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
                    columns: parent.width > Math.round(580 * reverbPage.theme.scale) ? 3 : 1
                    columnSpacing: Math.round(12 * reverbPage.theme.scale)
                    rowSpacing: Math.round(8 * reverbPage.theme.scale)
                    UiButton {
                        theme: reverbPage.theme
                        selected: reverbPage.enabled
                        text: reverbPage.enabled ? "混响已启用" : "混响已关闭"
                        onClicked: reverbPage.controller.setOption("reverbEnabled", !reverbPage.enabled)
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
                        Accessible.name: "混响空间"
                    }
                    ParameterSlider {
                        Layout.fillWidth: true
                        theme: reverbPage.theme
                        label: "混合"
                        fromValue: 0
                        toValue: 1
                        displayValue: reverbPage.mix
                        displayFactor: 100
                        unit: "%"
                        onMoved: reverbPage.controller.setOption("reverbMix", value / 100)
                    }
                }
            }
        }
    }
}
