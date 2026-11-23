import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property QtObject controller
    required property QtObject theme
    required property var view
    signal importTake()
    readonly property bool hasAudio: !!view.take.id && view.take.role !== "backing"
    readonly property bool selection: view.rangeLast > view.rangeFirst
    readonly property bool vocal: hasAudio && view.take.role === "vocal" && selection
    readonly property var lines: view.phrase
    readonly property var record: lyricScope.currentValue === "track" ? lines.local : lines.shared
    readonly property bool canFollow: vocal && !!view.reference.id && view.reference.id !== view.selected
    property string shownSignature: ""
    property bool syncing: false

    function t(text) { return quickTranslation.text(text) }
    function syncForm() {
        const key = JSON.stringify([view.phrase.id, view.selected, view.rangeFirst, view.rangeLast, lyricScope.currentValue,
            record.text || "", record.language || "", !!record.confirmed])
        if (shownSignature === key)
            return
        shownSignature = key
        syncing = true
        const pending = controller.pendingLyricInput(lyricScope.currentValue)
        const shown = pending.language ? pending : record
        lyric.text = shown.text || ""
        if (shown.language)
            language.currentIndex = Math.max(0, language.indexOfValue(shown.language))
        syncing = false
    }
    onRecordChanged: if (visible && lyric) syncForm()
    onVisibleChanged: if (visible && lyric) syncForm()
    Component.onCompleted: syncForm()
    spacing: Math.round(6 * theme.scale)

    component Heading: Text {
        Layout.fillWidth: true
        color: panel.theme.text
        font.pixelSize: Math.round(14 * panel.theme.scale)
        font.weight: Font.DemiBold
    }
    component Note: Text {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: panel.theme.muted
        font.pixelSize: Math.round(11 * panel.theme.scale)
        textFormat: Text.PlainText
    }
    component Rule: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: panel.theme.line
    }

    Heading { text: panel.t("歌词与时间参照") }
    Note { visible: !panel.hasAudio || !panel.selection; text: panel.t("在时间轴上选中一段人声。") }
    ColumnLayout {
        Layout.fillWidth: true
        visible: panel.hasAudio && panel.selection
        spacing: 5

        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            UiComboBox {
                id: lyricScope
                objectName: "chorusLyricScope"
                theme: panel.theme
                Layout.fillWidth: true
                fieldLabel: panel.t("歌词用途")
                model: [{label: panel.t("本轨"), value: "track"},
                        {label: panel.t("同词共用"), value: "shared"}]
                onActivated: panel.syncForm()
            }
            UiComboBox {
                id: language
                objectName: "chorusLyricLanguage"
                theme: panel.theme
                Layout.fillWidth: true
                fieldLabel: panel.t("歌词语言")
                model: [{label: panel.t("日语"), value: "ja"}, {label: panel.t("普通话"), value: "zh"},
                        {label: panel.t("英语"), value: "en"}, {label: panel.t("粤语"), value: "yue"}]
                onActivated: panel.controller.lyricInput(lyricScope.currentValue, lyric.text, currentValue)
            }
        }
        TextArea {
            id: lyric
            objectName: "chorusLyricInput"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(63 * panel.theme.scale)
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            clip: true
            placeholderText: panel.t("输入歌词或读音")
            placeholderTextColor: panel.theme.muted
            color: panel.theme.text
            font.pixelSize: Math.round(13 * panel.theme.scale)
            Accessible.name: panel.t("当前唱段歌词")
            onTextChanged: if (!panel.syncing && activeFocus)
                panel.controller.lyricInput(lyricScope.currentValue, text, language.currentValue)
            background: Rectangle {
                color: panel.theme.base
                border.color: lyric.activeFocus ? panel.theme.accent : panel.theme.line
                radius: Math.round(6 * panel.theme.scale)
            }
        }
        Note {
            visible: !!panel.record.text || (lyricScope.currentValue === "track" && !!panel.lines.shared.text)
            text: panel.record.text
                ? panel.t(panel.record.confirmed ? "已核对" : "草稿 · 核对读音后确认")
                : panel.t("继承共用歌词") + "  ·  " + panel.lines.shared.text
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            UiButton {
                objectName: "chorusConfirmLyrics"
                theme: panel.theme
                Layout.fillWidth: true
                text: panel.t("确认歌词")
                enabled: !!lyric.text.trim()
                onClicked: panel.controller.setLyrics(lyricScope.currentValue, lyric.text, language.currentValue, true)
            }
            UiButton {
                theme: panel.theme
                quiet: true
                text: panel.t("清除")
                enabled: !!panel.record.text
                onClicked: panel.controller.setLyrics(lyricScope.currentValue, "", language.currentValue, false)
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            UiButton {
                objectName: "chorusRecognize"
                theme: panel.theme
                Layout.fillWidth: true
                quiet: true
                text: panel.t("识别本句")
                enabled: !panel.view.busy && !panel.lines.local.confirmed
                onClicked: { lyricScope.currentIndex = 0; panel.controller.recognizeLyrics(language.currentValue, false) }
            }
            UiButton {
                objectName: "chorusRecognizeTrack"
                theme: panel.theme
                quiet: true
                text: panel.t("识别整轨")
                enabled: !panel.view.busy
                onClicked: { lyricScope.currentIndex = 0; panel.controller.recognizeLyrics(language.currentValue, true) }
            }
        }
        UiComboBox {
            id: localRef
            objectName: "chorusPhraseReference"
            theme: panel.theme
            Layout.fillWidth: true
            fieldLabel: panel.t("本唱段时间参照")
            model: [{label: panel.t("音轨默认参照"), value: ""}].concat(
                panel.controller.tracks.filter(row => row.role !== "backing" && row.id !== panel.view.selected)
                .map(row => ({label: row.name, value: row.id})))
            currentIndex: Math.max(0, indexOfValue(panel.lines.ref))
            onActivated: panel.controller.phraseReference(currentValue)
        }
        UiComboBox {
            id: aligner
            objectName: "chorusAlignmentEngine"
            Layout.fillWidth: true
            visible: panel.vocal
            enabled: !panel.view.busy
            theme: panel.theme
            fieldLabel: panel.t("唱段对齐引擎")
            model: [{label: panel.t("自动 · 优先 TiFA"), value: "auto"},
                    {label: "TiFA", value: "tifa"}, {label: "HubertFA", value: "hubertfa"}]
            currentIndex: Math.max(0, indexOfValue(panel.view.alignmentEngine))
            onActivated: panel.controller.setAlignmentEngine(currentValue)
        }
        Note {
            visible: panel.vocal && panel.view.alignmentEngine === "tifa" && !panel.controller.alignmentReady("tifa")
            text: panel.t("缺少 TiFA 模型。可使用自动或 HubertFA。")
        }
        RowLayout {
            Layout.fillWidth: true
            visible: panel.vocal
            spacing: 4
            UiButton {
                objectName: "chorusAnalyzePhrase"
                theme: panel.theme
                Layout.fillWidth: true
                text: panel.t("分析本句")
                enabled: !panel.view.busy && panel.canFollow
                onClicked: {
                    panel.controller.setMode("phrase")
                    panel.controller.analyze("timing", "track", false, false, false, 100)
                }
            }
            UiButton {
                objectName: "chorusAddAnchor"
                theme: panel.theme
                text: panel.t("添锚点")
                quiet: true
                onClicked: {
                    panel.controller.setMode("phrase")
                    panel.controller.addTimeAnchor(panel.controller.position)
                }
            }
        }
        Note {
            objectName: "chorusTimingEvidence"
            visible: !!panel.view.timingDetail
            text: panel.t(panel.view.timingDetail)
        }
        UiButton {
            theme: panel.theme
            Layout.fillWidth: true
            quiet: true
            visible: panel.vocal && panel.view.take.timing.some(row => row.start < panel.view.rangeLast
                                                                   && row.end > panel.view.rangeFirst)
            text: panel.t("还原选段时间")
            onClicked: panel.controller.clearSelectionEdits("timing")
        }
        UiButton {
            objectName: "chorusApplyPhrase"
            theme: panel.theme
            Layout.fillWidth: true
            visible: !!panel.view.canApplyTiming
            text: panel.t("应用本句对齐")
            enabled: !panel.view.busy
            onClicked: panel.controller.acceptPhraseTiming()
        }
    }

    Rule { visible: panel.vocal }
    ColumnLayout {
        Layout.fillWidth: true
        visible: panel.vocal
        spacing: 5
        Heading { text: panel.t("唱段精修") }
        RowLayout {
            Layout.fillWidth: true
            UiButton {
                objectName: "chorusFollowContour"
                theme: panel.theme
                text: panel.t("跟走势")
                Layout.fillWidth: true
                enabled: panel.canFollow && !panel.view.busy
                ToolTip.visible: hovered
                ToolTip.text: panel.t("保留本声部音区，借用参照起伏")
                onClicked: {
                    panel.controller.setMode("pitch")
                    panel.controller.followReference("contour", 70)
                }
            }
            UiButton {
                objectName: "chorusFollowAbsolute"
                theme: panel.theme
                text: panel.t("跟音高")
                enabled: panel.canFollow && !panel.view.busy
                ToolTip.visible: hovered
                ToolTip.text: panel.t("使用参照的实测音高")
                onClicked: {
                    panel.controller.setMode("pitch")
                    panel.controller.followReference("absolute", 100)
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Note { Layout.fillWidth: false; text: panel.t("增益") }
            Slider {
                id: phraseGain
                objectName: "chorusPhraseGain"
                Layout.fillWidth: true
                from: -18
                to: 12
                stepSize: .5
                Accessible.name: panel.t("唱段增益")
                Binding { target: phraseGain; property: "value"; value: panel.view.gain; when: !phraseGain.pressed }
                property real requested: 0
                onMoved: {
                    requested = value
                    if (!pressed)
                        panel.controller.editSelection("gain", value)
                }
                onPressedChanged: {
                    if (pressed)
                        requested = value
                    else if (requested !== panel.view.gain)
                        panel.controller.editSelection("gain", requested)
                }
            }
            Note { Layout.fillWidth: false; text: phraseGain.value.toFixed(1) + " dB" }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            UiComboBox {
                id: take
                objectName: "chorusCompTrack"
                theme: panel.theme
                Layout.fillWidth: true
                fieldLabel: panel.t("此段使用的录音")
                model: [{label: panel.t("主录音"), value: ""}].concat(
                    panel.controller.tracks.filter(row => row.role === "take")
                    .map(row => ({label: row.name, value: row.id})))
                currentIndex: Math.max(0, indexOfValue(panel.view.comp))
                onActivated: panel.controller.chooseTake(currentValue)
            }
            UiButton { theme: panel.theme; text: "+ " + panel.t("备选"); quiet: true
                enabled: !panel.view.busy; onClicked: panel.importTake() }
        }
    }
}
