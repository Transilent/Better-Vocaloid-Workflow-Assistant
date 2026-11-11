import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: page
    objectName: "subtitleDialog"
    required property QtObject controller
    signal neteaseRequested()
    required property QtObject theme

    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    padding: 0
    width: parent ? Math.min(parent.width - Math.round(32 * theme.scale), Math.round(1080 * theme.scale))
                  : Math.round(1080 * theme.scale)
    height: parent ? Math.min(parent.height - Math.round(32 * theme.scale), Math.round(760 * theme.scale))
                   : Math.round(760 * theme.scale)
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round((parent.height - height) / 2) : 0

    readonly property var snapshot: controller.view || ({})
    readonly property var cueRows: controller.cues || []
    readonly property var tracks: controller.midiTracks || []
    readonly property bool busySource: snapshot.sourceKind === "midi" && tracks.length > 0
    readonly property bool compact: height < Math.round(550 * theme.scale)

    function tr(source) {
        return controller && typeof controller.text === "function" ? controller.text(source) : source
    }

    function numberValue(value) {
        const input = String(value).trim().replace(",", ".")
        const parsed = input.length ? Number(input) : NaN
        return Number.isFinite(parsed) ? parsed : NaN
    }

    function trackIndex() {
        for (let index = 0; index < tracks.length; ++index) {
            if (String(tracks[index].id) === String(snapshot.selectedTrack))
                return index
        }
        return -1
    }

    function openLocal() {
        open()
    }

    function saveCurrent() {
        if (!snapshot.canExport)
            return
        saveDialog.activeFormat = String(snapshot.format || "srt")
        saveDialog.open()
    }

    onOpened: Qt.callLater(function() { importMidiButton.forceActiveFocus() })

    Overlay.modal: Rectangle {
        color: Qt.rgba(page.theme.base.r, page.theme.base.g, page.theme.base.b, 0.88)
    }

    background: Rectangle {
        color: page.theme.surface
        radius: Math.round(11 * page.theme.scale)
        border.width: 1
        border.color: page.theme.line
    }

    contentItem: ColumnLayout {
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round((page.compact ? 52 : 72) * page.theme.scale)
            color: page.theme.raised
            radius: Math.round(11 * page.theme.scale)

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.radius
                color: parent.color
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Math.round(20 * page.theme.scale)
                anchors.rightMargin: Math.round(16 * page.theme.scale)
                spacing: Math.round(10 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3

                    Text {
                        Layout.fillWidth: true
                        text: page.tr("本地字幕工具")
                        color: page.theme.text
                        font.pixelSize: Math.round(21 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !page.compact
                        text: page.tr("从已核验任务 MIDI 或本地定时 LRC 建立字幕；导出前会检查时间顺序。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                }

                UiButton {
                    objectName: "closeSubtitleDialog"
                    theme: page.theme
                    text: page.tr("关闭")
                    glyph: "close"
                    quiet: true
                    onClicked: page.close()
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: Math.round((page.compact ? 9 : 16) * page.theme.scale)
            spacing: Math.round((page.compact ? 6 : 10) * page.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(8 * page.theme.scale)

                UiButton {
                    id: importMidiButton
                    objectName: "importSubtitleMidi"
                    theme: page.theme
                    text: page.tr("导入 MIDI…")
                    primary: !page.snapshot.hasCues
                    onClicked: midiDialog.open()
                }

                UiButton {
                    objectName: "importSubtitleLrc"
                    theme: page.theme
                    text: page.tr("导入定时 LRC…")
                    onClicked: lrcDialog.open()
                }
                UiButton {
                    objectName: "importSubtitleNetease"
                    theme: page.theme
                    text: page.tr("搜索网易云时间轴…")
                    onClicked: page.neteaseRequested()
                }

                Item { Layout.fillWidth: true }
            }

            Flow {
                Layout.fillWidth: true
                visible: (page.snapshot.draftKinds || []).length > 1
                spacing: 6 * page.theme.scale
                Repeater {
                    model: page.snapshot.draftKinds || []
                    delegate: UiButton {
                        required property string modelData
                        objectName: "restoreSubtitle_" + modelData
                        theme: page.theme
                        quiet: true
                        selected: page.snapshot.sourceKind === modelData
                        text: ({midi: "MIDI 草稿", lrc: "LRC 草稿", netease: "网易云草稿"})[modelData]
                        onClicked: page.controller.restoreSource(modelData)
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: sourceColumn.implicitHeight + Math.round((page.compact ? 12 : 22) * page.theme.scale)
                color: page.theme.base
                radius: Math.round(8 * page.theme.scale)
                border.color: page.theme.line

                ColumnLayout {
                    id: sourceColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round((page.compact ? 6 : 11) * page.theme.scale)
                    spacing: Math.round(4 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            Layout.fillWidth: true
                            text: String(page.snapshot.sourceLabel || page.tr("尚未选择字幕来源"))
                            color: page.theme.text
                            font.pixelSize: Math.round(13 * page.theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideMiddle
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            visible: !!page.snapshot.sourceVerified
                            implicitWidth: verifiedText.implicitWidth + Math.round(12 * page.theme.scale)
                            implicitHeight: Math.round(22 * page.theme.scale)
                            radius: height / 2
                            color: page.theme.selected

                            Text {
                                id: verifiedText
                                anchors.centerIn: parent
                                text: page.tr("任务已核验")
                                color: page.theme.text
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                textFormat: Text.PlainText
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !page.compact && String(page.snapshot.sourceMessage || "").length > 0
                        text: String(page.snapshot.sourceMessage || "")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: page.width < Math.round(500 * page.theme.scale) ? 2 : 4
                columnSpacing: Math.round(10 * page.theme.scale)
                rowSpacing: Math.round(7 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.busySource
                    spacing: 4

                    Text {
                        text: page.tr("MIDI 声部")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        textFormat: Text.PlainText
                    }

                    UiComboBox {
                        objectName: "subtitleTrack"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: page.tracks
                        textRole: "label"
                        valueRole: "id"
                        currentIndex: page.trackIndex()
                        onActivated: page.controller.selectMidiTrack(Number(currentValue))
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        text: page.tr("整体偏移（秒）")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        textFormat: Text.PlainText
                    }

                    FieldBox {
                        id: offsetField
                        objectName: "subtitleOffset"
                        Layout.fillWidth: true
                        theme: page.theme
                        text: Number(page.snapshot.offset || 0).toFixed(3)
                        placeholderText: "0.000"
                        Accessible.name: page.tr("整体偏移秒数")
                        onAccepted: page.controller.setOffset(page.numberValue(text))
                        onEditingFinished: page.controller.setOffset(page.numberValue(text))
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        text: page.tr("导出格式")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        textFormat: Text.PlainText
                    }

                    UiComboBox {
                        objectName: "subtitleFormat"
                        Layout.fillWidth: true
                        theme: page.theme
                        model: [
                            { label: "SRT", value: "srt" },
                            { label: "WebVTT", value: "vtt" }
                        ]
                        currentIndex: String(page.snapshot.format || "srt") === "vtt" ? 1 : 0
                        onActivated: page.controller.setFormat(String(currentValue))
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Layout.alignment: Qt.AlignBottom

                    UiButton {
                        objectName: "addSubtitleCue"
                        Layout.fillWidth: true
                        theme: page.theme
                        text: page.tr("添加一行")
                        onClicked: page.controller.addCue()
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.tr("{0} 行{1}").replace("{0}", String(page.snapshot.cueCount || 0))
                            .replace("{1}", page.snapshot.dirty ? page.tr(" · 尚未导出") : "")
                        color: page.snapshot.dirty ? page.theme.attention : page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        horizontalAlignment: Text.AlignHCenter
                        textFormat: Text.PlainText
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: Math.round((page.compact ? 68 : 250) * page.theme.scale)
                color: page.theme.base
                radius: Math.round(8 * page.theme.scale)
                border.color: page.theme.line
                clip: true

                ListView {
                    id: cueList
                    objectName: "subtitleCueList"
                    anchors.fill: parent
                    anchors.margins: 1
                    clip: true
                    spacing: 1
                    model: page.cueRows
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        id: cueRow
                        required property int index
                        required property var modelData
                        width: cueList.width
                        height: Math.max(Math.round(88 * page.theme.scale), cueContent.implicitHeight + Math.round(14 * page.theme.scale))
                        color: rowHover.hovered ? page.theme.raised : "transparent"
                        property string rowError: ""

                        function commit() {
                            const start = page.numberValue(startField.text)
                            const end = page.numberValue(endField.text)
                            if (Math.abs(start - Number(cueRow.modelData.start)) < 0.0005
                                    && Math.abs(end - Number(cueRow.modelData.end)) < 0.0005
                                    && textField.text === String(cueRow.modelData.text || "")) {
                                rowError = ""
                                return
                            }
                            if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
                                rowError = page.tr("请填写有效且按先后排列的开始/结束秒数。")
                                return
                            }
                            rowError = ""
                            if (!page.controller.updateCue(index, start, end, textField.text))
                                rowError = String(page.snapshot.error || page.tr("无法更新这一行。"))
                        }

                        HoverHandler { id: rowHover }

                        RowLayout {
                            id: cueContent
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: Math.round(10 * page.theme.scale)
                            anchors.rightMargin: Math.round(10 * page.theme.scale)
                            spacing: Math.round(7 * page.theme.scale)

                            Text {
                                Layout.preferredWidth: Math.round(30 * page.theme.scale)
                                text: String(cueRow.index + 1)
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                horizontalAlignment: Text.AlignHCenter
                                textFormat: Text.PlainText
                            }

                            FieldBox {
                                id: startField
                                objectName: "subtitleStart_" + cueRow.index
                                Layout.preferredWidth: Math.round(88 * page.theme.scale)
                                theme: page.theme
                                text: Number(cueRow.modelData.start).toFixed(3)
                                Accessible.name: page.tr("第 {0} 行开始秒数").replace("{0}", String(cueRow.index + 1))
                                onEditingFinished: cueRow.commit()
                            }

                            Text {
                                text: "→"
                                color: page.theme.muted
                                font.pixelSize: Math.round(12 * page.theme.scale)
                            }

                            FieldBox {
                                id: endField
                                objectName: "subtitleEnd_" + cueRow.index
                                Layout.preferredWidth: Math.round(88 * page.theme.scale)
                                theme: page.theme
                                text: Number(cueRow.modelData.end).toFixed(3)
                                Accessible.name: page.tr("第 {0} 行结束秒数").replace("{0}", String(cueRow.index + 1))
                                onEditingFinished: cueRow.commit()
                            }

                            AreaBox {
                                id: textField
                                objectName: "subtitleText_" + cueRow.index
                                Layout.fillWidth: true
                                theme: page.theme
                                text: String(cueRow.modelData.text || "")
                                Accessible.name: page.tr("第 {0} 行字幕文字").replace("{0}", String(cueRow.index + 1))
                                onActiveFocusChanged: if (!activeFocus && !inputMethodComposing) cueRow.commit()
                            }

                            ColumnLayout {
                                spacing: Math.round(3 * page.theme.scale)

                                UiButton {
                                    objectName: "applySubtitleCue_" + cueRow.index
                                    theme: page.theme
                                    text: page.tr("更新")
                                    onClicked: cueRow.commit()
                                }

                                UiButton {
                                    objectName: "removeSubtitleCue_" + cueRow.index
                                    theme: page.theme
                                    text: page.tr("删除")
                                    quiet: true
                                    onClicked: page.controller.removeCue(cueRow.index)
                                }
                            }
                        }

                        Text {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.leftMargin: Math.round(47 * page.theme.scale)
                            anchors.rightMargin: Math.round(10 * page.theme.scale)
                            visible: cueRow.rowError.length > 0
                            text: cueRow.rowError
                            color: page.theme.error
                            font.pixelSize: Math.round(10 * page.theme.scale)
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }

                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            height: 1
                            color: page.theme.line
                        }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    width: parent.width - Math.round(40 * page.theme.scale)
                    visible: page.cueRows.length === 0
                    text: page.tr("导入任务已核验 MIDI 或本地定时 LRC 后，歌词行会出现在这里。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(13 * page.theme.scale)
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    textFormat: Text.PlainText
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(10 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3

                    Text {
                        Layout.fillWidth: true
                        visible: String(page.snapshot.error || "").length > 0
                        text: String(page.snapshot.error || "")
                        color: page.theme.error
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: String(page.snapshot.error || "").length === 0
                            && String(page.snapshot.message || "").length > 0
                        text: String(page.snapshot.message || "")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: messageHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: messageHover }
                    }
                }

                UiButton {
                    objectName: "exportSubtitle"
                    theme: page.theme
                    text: page.tr("导出字幕…")
                    primary: true
                    enabled: !!page.snapshot.canExport
                    onClicked: page.saveCurrent()
                }
            }
        }
    }

    component FieldBox: TextField {
        required property QtObject theme
        implicitHeight: Math.round(36 * theme.scale)
        leftPadding: Math.round(9 * theme.scale)
        rightPadding: leftPadding
        topPadding: Math.round(7 * theme.scale)
        bottomPadding: topPadding
        color: theme.text
        placeholderTextColor: theme.muted
        selectionColor: theme.accent
        selectedTextColor: theme.base
        font.pixelSize: Math.round(12 * theme.scale)
        selectByMouse: true
        focusPolicy: Qt.StrongFocus
        Accessible.name: text.length > 0 ? text : placeholderText
        background: Rectangle {
            radius: Math.round(6 * theme.scale)
            color: theme.surface
            border.color: parent.activeFocus ? theme.accent : theme.line
        }
    }

    component AreaBox: TextArea {
        required property QtObject theme
        implicitHeight: Math.round(58 * theme.scale)
        leftPadding: Math.round(9 * theme.scale)
        rightPadding: leftPadding
        topPadding: Math.round(7 * theme.scale)
        bottomPadding: topPadding
        color: theme.text
        placeholderTextColor: theme.muted
        selectionColor: theme.accent
        selectedTextColor: theme.base
        font.pixelSize: Math.round(12 * theme.scale)
        wrapMode: TextEdit.Wrap
        textFormat: TextEdit.PlainText
        selectByMouse: true
        persistentSelection: true
        focusPolicy: Qt.StrongFocus
        Accessible.name: placeholderText
        background: Rectangle {
            radius: Math.round(6 * theme.scale)
            color: theme.surface
            border.color: parent.activeFocus ? theme.accent : theme.line
        }
    }

    FileDialog {
        id: midiDialog
        objectName: "subtitleMidiFileDialog"
        title: page.tr("导入 MIDI")
        fileMode: FileDialog.OpenFile
        nameFilters: [page.tr("MIDI 文件") + " (*.mid *.midi)", page.tr("所有文件") + " (*)"]
        onAccepted: page.controller.loadLocal(selectedFile)
    }

    FileDialog {
        id: lrcDialog
        objectName: "subtitleLrcFileDialog"
        title: page.tr("导入定时 LRC")
        fileMode: FileDialog.OpenFile
        nameFilters: [page.tr("LRC 歌词") + " (*.lrc)", page.tr("所有文件") + " (*)"]
        onAccepted: page.controller.loadLocal(selectedFile)
    }

    FileDialog {
        id: saveDialog
        objectName: "subtitleSaveFileDialog"
        property string activeFormat: "srt"
        title: page.tr("导出字幕")
        fileMode: FileDialog.SaveFile
        defaultSuffix: activeFormat
        nameFilters: activeFormat === "vtt"
            ? [page.tr("WebVTT 字幕") + " (*.vtt)"]
            : [page.tr("SRT 字幕") + " (*.srt)"]
        onAccepted: page.controller.exportTo(selectedFile, activeFormat)
    }
}
