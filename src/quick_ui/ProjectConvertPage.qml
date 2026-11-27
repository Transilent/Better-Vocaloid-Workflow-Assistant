import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    objectName: "projectConvertPage"
    required property QtObject theme
    required property QtObject controller

    readonly property var view: controller.view || ({})
    readonly property var catalog: controller.catalog || ({})
    readonly property var src: view.source || ({})
    readonly property var receipt: view.receipt || ({})
    readonly property real s: theme.scale || 1.0
    readonly property bool sideBySide: width >= Math.round(700 * s)
    property bool showExtendedFormats: false
    property bool dropHover: false

    function vwaTr(text) {
        return quickTranslation ? quickTranslation.text(text) : text
    }

    function formatDuration(sec) {
        const total = Math.max(0, Math.round(Number(sec || 0)))
        const m = Math.floor(total / 60)
        const r = total % 60
        return m + ":" + (r < 10 ? "0" + r : String(r))
    }

    function formatBytes(n) {
        const val = Number(n || 0)
        if (val < 1024) return val + " B"
        if (val < 1024 * 1024) return (val / 1024).toFixed(1) + " KB"
        return (val / (1024 * 1024)).toFixed(2) + " MB"
    }

    FileDialog {
        id: sourceFileDialog
        title: page.vwaTr("选择需要互转的人声工程")
        nameFilters: [page.catalog.openFileFilter || "所有文件 (*)"]
        onAccepted: {
            if (selectedFile)
                page.controller.loadSource(selectedFile)
        }
    }

    FolderDialog {
        id: outputFolderDialog
        title: page.vwaTr("选择输出文件夹")
        onAccepted: {
            if (selectedFolder)
                page.controller.setOutputDir(selectedFolder)
        }
    }

    // Whole-page drop support when a source is already loaded
    DropArea {
        anchors.fill: parent
        enabled: !!page.view.hasSource && !page.view.busy
        onEntered: drag => {
            if (drag.hasUrls) {
                drag.accept(Qt.CopyAction)
                page.dropHover = true
            }
        }
        onExited: page.dropHover = false
        onDropped: drop => {
            page.dropHover = false
            if (drop.hasUrls && drop.urls && drop.urls.length > 0) {
                drop.accept(Qt.CopyAction)
                page.controller.loadSource(drop.urls[0])
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Math.round(20 * page.s)
        spacing: Math.round(14 * page.s)

        // Header bar
        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(10 * page.s)

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Math.round(3 * page.s)
                Text {
                    objectName: "projectConvertHeading"
                    Layout.fillWidth: true
                    text: page.vwaTr("工程互转")
                    color: page.theme.text
                    font.pixelSize: page.theme.fontDisplay
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                }
                Text {
                    Layout.fillWidth: true
                    text: page.vwaTr("在主流歌声合成工程之间安全互转，并支持按工程时间轴导出 LRC / SRT 歌词。")
                    color: page.theme.muted
                    font.pixelSize: page.theme.fontSm
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                }
            }

            UiButton {
                objectName: "convertPickSourceTop"
                visible: !!page.view.hasSource
                theme: page.theme
                enabled: !page.view.busy
                leadingGlyph: "folder"
                leadingIconSize: 14
                text: page.vwaTr("更换源工程")
                onClicked: sourceFileDialog.open()
            }
            UiButton {
                objectName: "convertClearSource"
                visible: !!page.view.hasSource
                theme: page.theme
                quiet: true
                enabled: !page.view.busy
                text: page.vwaTr("清空")
                onClicked: page.controller.clearSource()
            }
        }

        // Empty state landing drop stage
        UiDropStage {
            objectName: "convertDropStage"
            visible: !page.view.hasSource
            Layout.fillWidth: true
            Layout.fillHeight: true
            theme: page.theme
            iconGlyph: "nav_convert"
            title: page.vwaTr("拖入或选择一个人声工程开始互转")
            subtitle: page.vwaTr("拖入后将先体检轨道、BPM 与音高曲线，再由你挑选轨道与目标格式，绝不覆盖源文件。")
            dropHint: page.vwaTr("松手立即体检工程结构")
            formatTags: [
                "SVP / SV2", "USTX", "VPR", "VSQX",
                "ACEP / ACEP2", "UFData", "MIDI", "LRC / SRT",
                "UST", "SVIP", "CCS", "DS / DSPX", "MusicXML"
            ]
            primaryText: page.vwaTr("选择工程文件")
            primaryIcon: "folder"
            primaryEnabled: !page.view.busy
            onPrimaryClicked: sourceFileDialog.open()
            onFilesDropped: urls => {
                if (urls && urls.length > 0)
                    page.controller.loadSource(urls[0])
            }
        }

        // Empty-state error / runtime notice banner (visible when dropping corrupt/unsupported files)
        Rectangle {
            objectName: "convertEmptyNotice"
            visible: !page.view.hasSource && ((page.view.notice || "").length > 0 || !page.view.runtimeAvailable)
            Layout.fillWidth: true
            implicitHeight: emptyNoticeRow.implicitHeight + Math.round(16 * page.s)
            radius: page.theme.radiusMd
            color: page.theme.raised
            border.width: 1
            border.color: (page.view.error || !page.view.runtimeAvailable) ? page.theme.error : page.theme.line

            RowLayout {
                id: emptyNoticeRow
                anchors.fill: parent
                anchors.margins: Math.round(10 * page.s)
                spacing: Math.round(8 * page.s)
                UiIcon {
                    width: Math.round(15 * page.s)
                    height: width
                    glyph: "info_circle"
                    tint: (page.view.error || !page.view.runtimeAvailable) ? page.theme.error : page.theme.attention
                }
                Text {
                    Layout.fillWidth: true
                    text: !page.view.runtimeAvailable
                        ? page.vwaTr("缺少工程互转运行库（dependencies/export），请在完整安装环境中运行。")
                        : (page.view.busy ? (page.view.busyDetail || page.vwaTr("处理中…")) : (page.view.notice || ""))
                    color: (page.view.error || !page.view.runtimeAvailable) ? page.theme.error : page.theme.text
                    font.pixelSize: page.theme.fontSm
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }
            }
        }

        // Active responsive converter workspace (2-column wide, 1-column stacked on narrow/high-DPI)
        GridLayout {
            visible: !!page.view.hasSource
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: page.sideBySide ? 2 : 1
            columnSpacing: Math.round(14 * page.s)
            rowSpacing: Math.round(12 * page.s)

            // Left column: source preflight summary + track selection + precheck advisories
            UiCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: page.sideBySide ? 44 : 1
                Layout.preferredHeight: page.sideBySide ? -1 : 40
                Layout.minimumWidth: page.sideBySide ? Math.round(260 * page.s) : 0
                theme: page.theme
                highlighted: page.dropHover

                Flickable {
                    anchors.fill: parent
                    anchors.margins: Math.round(14 * page.s)
                    contentWidth: width
                    contentHeight: leftCol.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    clip: true
                    ScrollBar.vertical: UiScrollBar { theme: page.theme; policy: ScrollBar.AsNeeded }

                    ColumnLayout {
                        id: leftCol
                        width: parent.width
                        spacing: Math.round(12 * page.s)

                        // Source identity card
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.s)
                            Rectangle {
                                radius: Math.round(6 * page.s)
                                color: page.theme.selected
                                border.color: page.theme.selectedLine
                                implicitWidth: fmtBadgeTxt.implicitWidth + Math.round(14 * page.s)
                                implicitHeight: fmtBadgeTxt.implicitHeight + Math.round(6 * page.s)
                                Text {
                                    id: fmtBadgeTxt
                                    anchors.centerIn: parent
                                    text: page.src.formatShort || ""
                                    color: page.theme.text
                                    font.pixelSize: page.theme.fontXs
                                    font.weight: Font.DemiBold
                                    font.family: page.theme.monoFont
                                    textFormat: Text.PlainText
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text {
                                    objectName: "convertSourceFileName"
                                    Layout.fillWidth: true
                                    text: page.src.fileName || ""
                                    color: page.theme.text
                                    font.pixelSize: page.theme.fontTitle
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideMiddle
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: [
                                        page.src.formatVersion ? ("v" + page.src.formatVersion) : "",
                                        page.src.tempoSummary || "",
                                        page.src.meterSummary || "",
                                        page.formatDuration(page.src.durationSeconds),
                                        (page.src.totalNotes || 0) + " " + page.vwaTr("音符")
                                    ].filter(s => s.length > 0).join(" · ")
                                    color: page.theme.muted
                                    font.pixelSize: page.theme.fontXs
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.lineSubtle }

                        // Track list header & quick selectors
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.s)
                            Text {
                                Layout.fillWidth: true
                                text: page.vwaTr("轨道筛选") + "（" + (page.view.selectedVocalCount || 0) + "/" + (page.src.vocalTrackCount || 0) + "）"
                                color: page.theme.text
                                font.pixelSize: page.theme.fontBody
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                            UiButton {
                                objectName: "convertSelectAllVocals"
                                theme: page.theme
                                quiet: true
                                enabled: !page.view.busy && (page.src.vocalTrackCount || 0) > 1
                                implicitHeight: Math.round(26 * page.s)
                                text: page.vwaTr("全选人声")
                                onClicked: page.controller.selectAllVocals()
                            }
                            UiButton {
                                objectName: "convertSelectLeadOnly"
                                theme: page.theme
                                quiet: true
                                enabled: !page.view.busy && (page.src.vocalTrackCount || 0) > 1
                                implicitHeight: Math.round(26 * page.s)
                                text: page.vwaTr("仅主唱轨")
                                onClicked: page.controller.selectLeadOnly()
                            }
                        }

                        // Track rows
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.s)
                            Repeater {
                                model: page.view.tracks || []
                                delegate: Rectangle {
                                    required property var modelData
                                    required property int index
                                    readonly property bool isVocal: modelData.kind === "vocal"
                                    Layout.fillWidth: true
                                    implicitHeight: trackRow.implicitHeight + Math.round(14 * page.s)
                                    radius: page.theme.radiusMd
                                    color: isVocal && modelData.selected ? page.theme.selected : page.theme.raised
                                    border.width: 1
                                    border.color: isVocal && modelData.selected ? page.theme.selectedLine : page.theme.lineSubtle

                                    RowLayout {
                                        id: trackRow
                                        anchors.fill: parent
                                        anchors.leftMargin: Math.round(10 * page.s)
                                        anchors.rightMargin: Math.round(10 * page.s)
                                        anchors.topMargin: Math.round(7 * page.s)
                                        anchors.bottomMargin: Math.round(7 * page.s)
                                        spacing: Math.round(8 * page.s)

                                        UiCheckBox {
                                            objectName: "convertTrackCheck_" + (isVocal ? modelData.vocalIndex : index)
                                            theme: page.theme
                                            visible: isVocal
                                            checked: !!modelData.selected
                                            enabled: !page.view.busy
                                            onToggled: page.controller.toggleVocalTrack(modelData.vocalIndex)
                                        }

                                        UiIcon {
                                            visible: !isVocal
                                            width: Math.round(15 * page.s)
                                            height: width
                                            glyph: "waveform"
                                            tint: page.theme.muted
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2
                                            Text {
                                                Layout.fillWidth: true
                                                text: modelData.name || ""
                                                color: page.theme.text
                                                font.pixelSize: page.theme.fontBody
                                                font.weight: isVocal ? Font.DemiBold : Font.Normal
                                                elide: Text.ElideRight
                                                textFormat: Text.PlainText
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                text: isVocal
                                                    ? [
                                                        (modelData.noteCount || 0) + " " + page.vwaTr("音符"),
                                                        modelData.hasLyrics ? page.vwaTr("含歌词") : page.vwaTr("无歌词"),
                                                        modelData.hasPitch ? page.vwaTr("含音高曲线") : page.vwaTr("仅音符")
                                                    ].join(" · ")
                                                    : (page.vwaTr("伴奏轨") + (modelData.audioPath ? (" · " + modelData.audioPath) : ""))
                                                color: page.theme.muted
                                                font.pixelSize: page.theme.fontXs
                                                elide: Text.ElideMiddle
                                                textFormat: Text.PlainText
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Precheck advisories
                        Rectangle {
                            visible: (page.view.warnings || []).length > 0
                            Layout.fillWidth: true
                            implicitHeight: warnCol.implicitHeight + Math.round(18 * page.s)
                            radius: page.theme.radiusMd
                            color: page.theme.raised
                            border.width: 1
                            border.color: page.theme.line

                            ColumnLayout {
                                id: warnCol
                                anchors.fill: parent
                                anchors.margins: Math.round(10 * page.s)
                                spacing: Math.round(5 * page.s)
                                RowLayout {
                                    spacing: Math.round(6 * page.s)
                                    UiIcon {
                                        width: Math.round(14 * page.s)
                                        height: width
                                        glyph: "info_circle"
                                        tint: page.theme.attention
                                    }
                                    Text {
                                        text: page.vwaTr("转换前体检提示")
                                        color: page.theme.text
                                        font.pixelSize: page.theme.fontSm
                                        font.weight: Font.DemiBold
                                        textFormat: Text.PlainText
                                    }
                                }
                                Repeater {
                                    model: page.view.warnings || []
                                    delegate: Text {
                                        required property string modelData
                                        Layout.fillWidth: true
                                        text: "• " + modelData
                                        color: page.theme.muted
                                        font.pixelSize: page.theme.fontXs
                                        wrapMode: Text.Wrap
                                        lineHeight: 1.3
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Right column: target format, options, output location, action & receipt
            UiCard {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: page.sideBySide ? 56 : 1
                Layout.preferredHeight: page.sideBySide ? -1 : 60
                Layout.minimumWidth: page.sideBySide ? Math.round(320 * page.s) : 0
                theme: page.theme

                Flickable {
                    anchors.fill: parent
                    anchors.margins: Math.round(14 * page.s)
                    contentWidth: width
                    contentHeight: rightCol.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    clip: true
                    ScrollBar.vertical: UiScrollBar { theme: page.theme; policy: ScrollBar.AsNeeded }

                    ColumnLayout {
                        id: rightCol
                        width: parent.width
                        spacing: Math.round(14 * page.s)

                        // Target format groups
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.s)

                            Text {
                                text: page.vwaTr("目标格式")
                                color: page.theme.text
                                font.pixelSize: page.theme.fontBody
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }

                            Text {
                                text: page.vwaTr("常用工程格式")
                                color: page.theme.muted
                                font.pixelSize: page.theme.fontXs
                                textFormat: Text.PlainText
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: Math.round(6 * page.s)
                                Repeater {
                                    model: page.catalog.outputCoreFormats || []
                                    delegate: UiButton {
                                        required property var modelData
                                        objectName: "convertTarget_" + modelData.id
                                        theme: page.theme
                                        enabled: !page.view.busy
                                        selected: page.view.targetFormat === modelData.id
                                        quiet: !selected
                                        text: modelData.shortLabel + " (" + modelData.primaryExt + ")"
                                        onClicked: page.controller.setTargetFormat(modelData.id)
                                    }
                                }
                            }

                            Text {
                                Layout.topMargin: Math.round(2 * page.s)
                                text: page.vwaTr("时间轴歌词导出")
                                color: page.theme.muted
                                font.pixelSize: page.theme.fontXs
                                textFormat: Text.PlainText
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: Math.round(6 * page.s)
                                Repeater {
                                    model: page.catalog.outputLyricsFormats || []
                                    delegate: UiButton {
                                        required property var modelData
                                        objectName: "convertTarget_" + modelData.id
                                        theme: page.theme
                                        enabled: !page.view.busy
                                        selected: page.view.targetFormat === modelData.id
                                        quiet: !selected
                                        leadingGlyph: "lyrics"
                                        leadingIconSize: 13
                                        text: modelData.shortLabel + " (" + modelData.primaryExt + ")"
                                        onClicked: page.controller.setTargetFormat(modelData.id)
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: Math.round(2 * page.s)
                                Text {
                                    text: page.vwaTr("更多扩展格式（UST / SVIP / CCS / DS / DSPX / MusicXML）")
                                    color: page.theme.muted
                                    font.pixelSize: page.theme.fontXs
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                                UiButton {
                                    objectName: "convertToggleExtended"
                                    theme: page.theme
                                    quiet: true
                                    implicitHeight: Math.round(24 * page.s)
                                    text: page.showExtendedFormats ? page.vwaTr("收起") : page.vwaTr("展开")
                                    onClicked: page.showExtendedFormats = !page.showExtendedFormats
                                }
                            }
                            Flow {
                                visible: page.showExtendedFormats || ((page.view.targetSpec || {}).group === "extended")
                                Layout.fillWidth: true
                                spacing: Math.round(6 * page.s)
                                Repeater {
                                    model: page.catalog.outputExtendedFormats || []
                                    delegate: UiButton {
                                        required property var modelData
                                        objectName: "convertTarget_" + modelData.id
                                        theme: page.theme
                                        enabled: !page.view.busy
                                        selected: page.view.targetFormat === modelData.id
                                        quiet: !selected
                                        text: modelData.shortLabel + " (" + modelData.primaryExt + ")"
                                        onClicked: page.controller.setTargetFormat(modelData.id)
                                    }
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                visible: !!((page.view.targetSpec || {}).note)
                                text: (page.view.targetSpec || {}).note || ""
                                color: page.theme.muted
                                font.pixelSize: page.theme.fontXs
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.lineSubtle }

                        // 4 focused conversion options
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(10 * page.s)

                            Text {
                                text: page.vwaTr("转换策略")
                                color: page.theme.text
                                font.pixelSize: page.theme.fontBody
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }

                            // 1) Pitch mode
                            RowLayout {
                                visible: !!((page.view.targetSpec || {}).supportsPitch)
                                Layout.fillWidth: true
                                spacing: Math.round(8 * page.s)
                                Text {
                                    Layout.preferredWidth: Math.round(84 * page.s)
                                    text: page.vwaTr("音高曲线")
                                    color: page.theme.text
                                    font.pixelSize: page.theme.fontSm
                                    textFormat: Text.PlainText
                                }
                                UiButton {
                                    objectName: "convertPitchKeep"
                                    theme: page.theme
                                    enabled: !page.view.busy
                                    selected: page.view.pitchMode === "keep"
                                    quiet: !selected
                                    text: page.vwaTr("保留音高")
                                    onClicked: page.controller.setPitchMode("keep")
                                }
                                UiButton {
                                    objectName: "convertPitchStrip"
                                    theme: page.theme
                                    enabled: !page.view.busy
                                    selected: page.view.pitchMode === "strip"
                                    quiet: !selected
                                    text: page.vwaTr("仅保留干净音符")
                                    onClicked: page.controller.setPitchMode("strip")
                                }
                                Item { Layout.fillWidth: true }
                            }

                            // 2) Lyric mode
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(8 * page.s)
                                Text {
                                    Layout.preferredWidth: Math.round(84 * page.s)
                                    text: page.vwaTr("歌词与续唱")
                                    color: page.theme.text
                                    font.pixelSize: page.theme.fontSm
                                    textFormat: Text.PlainText
                                }
                                UiButton {
                                    objectName: "convertLyricAdapt"
                                    theme: page.theme
                                    enabled: !page.view.busy
                                    selected: page.view.lyricMode === "adapt"
                                    quiet: !selected
                                    text: page.vwaTr("自动适配目标格式")
                                    onClicked: page.controller.setLyricMode("adapt")
                                }
                                UiButton {
                                    objectName: "convertLyricRaw"
                                    theme: page.theme
                                    enabled: !page.view.busy
                                    selected: page.view.lyricMode === "raw"
                                    quiet: !selected
                                    text: page.vwaTr("原样保留")
                                    onClicked: page.controller.setLyricMode("raw")
                                }
                                Item { Layout.fillWidth: true }
                            }

                            // 3 & 4) Instrumental + Split tracks checkboxes
                            Flow {
                                Layout.fillWidth: true
                                spacing: Math.round(14 * page.s)
                                UiCheckBox {
                                    objectName: "convertIncludeInstrumental"
                                    theme: page.theme
                                    visible: !!((page.view.targetSpec || {}).supportsInstrumental) && (page.src.instrumentalTrackCount || 0) > 0
                                    checked: !!page.view.includeInstrumental
                                    enabled: !page.view.busy
                                    text: page.vwaTr("保留伴奏轨引用")
                                    onToggled: page.controller.setIncludeInstrumental(checked)
                                }
                                UiCheckBox {
                                    objectName: "convertSplitTracks"
                                    theme: page.theme
                                    visible: (page.view.selectedVocalCount || 0) > 1
                                    checked: !!page.view.splitTracks
                                    enabled: !page.view.busy
                                    text: page.vwaTr("按人声轨拆分为独立文件")
                                    onToggled: page.controller.setSplitTracks(checked)
                                }
                            }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.lineSubtle }

                        // Output folder & planned file preview
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.s)
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(8 * page.s)
                                Text {
                                    text: page.vwaTr("输出目录")
                                    color: page.theme.text
                                    font.pixelSize: page.theme.fontSm
                                    font.weight: Font.DemiBold
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.view.outputDir || ""
                                    color: page.theme.muted
                                    font.pixelSize: page.theme.fontXs
                                    font.family: page.theme.monoFont
                                    elide: Text.ElideMiddle
                                    textFormat: Text.PlainText
                                }
                                UiButton {
                                    objectName: "convertChooseOutputDir"
                                    theme: page.theme
                                    quiet: true
                                    enabled: !page.view.busy
                                    implicitHeight: Math.round(28 * page.s)
                                    text: page.vwaTr("更改目录")
                                    onClicked: outputFolderDialog.open()
                                }
                            }
                            Text {
                                visible: (page.view.plannedOutputs || []).length > 0
                                Layout.fillWidth: true
                                text: page.vwaTr("预计生成：") + (page.view.plannedOutputs || []).map(o => o.fileName).join("、")
                                color: page.theme.muted
                                font.pixelSize: page.theme.fontXs
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        // Primary Convert action row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(10 * page.s)
                            UiButton {
                                objectName: "convertStartButton"
                                theme: page.theme
                                primary: true
                                enabled: !!page.view.canConvert
                                leadingGlyph: "nav_convert"
                                leadingIconSize: 15
                                leftPadding: Math.round(18 * page.s)
                                rightPadding: leftPadding
                                text: page.view.busy ? page.vwaTr("正在转换…") : page.vwaTr("开始转换")
                                onClicked: page.controller.startConvert()
                            }
                            UiButton {
                                objectName: "convertCancelButton"
                                visible: !!page.view.busy
                                theme: page.theme
                                text: page.vwaTr("取消")
                                onClicked: page.controller.cancel()
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: (page.view.notice || "").length > 0 || !page.view.runtimeAvailable
                                text: !page.view.runtimeAvailable
                                    ? page.vwaTr("缺少工程互转运行库（dependencies/export），请在完整安装环境中运行。")
                                    : (page.view.busy ? (page.view.busyDetail || page.vwaTr("处理中…")) : (page.view.notice || ""))
                                color: (page.view.error || !page.view.runtimeAvailable) ? page.theme.error : page.theme.muted
                                font.pixelSize: page.theme.fontXs
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                        }

                        // Verified conversion receipt card
                        ProjectConvertReceiptCard {
                            visible: !!page.view.hasReceipt
                            Layout.fillWidth: true
                            theme: page.theme
                            controller: page.controller
                            receipt: page.receipt
                            s: page.s
                        }
                    }
                }
            }
        }
    }
}
