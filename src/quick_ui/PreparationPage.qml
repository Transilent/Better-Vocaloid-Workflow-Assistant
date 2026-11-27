import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Item {
    id: page
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message
    }
    objectName: "preparationPage"
    required property QtObject theme
    required property QtObject controller
    signal subtitleRequested()

    readonly property var prep: controller.preparation || ({})
    readonly property var task: controller.preparationTask || ({})
    readonly property bool narrow: width < (theme.scale > 1.2 ? 1040 : 820)
    readonly property bool editLocked: controller.preparationBusy || controller.workBusy
    readonly property bool roformerSelected: prep.voice_mode === "roformer"
        || (prep.voice_mode === "dual" && prep.separator_model === "roformer")
    readonly property string taskState: task && task.state ? String(task.state) : "idle"
    readonly property bool taskRunning: taskState === "running" || taskState === "cancelling"
    readonly property bool taskSucceeded: taskState === "succeeded" || taskState === "done" || taskState === "completed"
    readonly property bool taskPartial: taskState === "partial"
    readonly property bool taskFailed: taskState === "failed" || taskState === "cancelled" || taskState === "interrupted"
    readonly property bool taskRecoverable: taskFailed || taskPartial
    readonly property bool taskHasResult: taskSucceeded || taskPartial
    readonly property real taskTotal: task && task.total !== undefined ? Number(task.total) : 0
    readonly property real taskCompleted: task && task.completed !== undefined ? Number(task.completed) : 0
    readonly property int contentMargin: narrow ? 12 : 16
    readonly property var presetChoices: [{name: controller.text("选择工作流预设"), key: ""}]
        .concat(controller.preparationPresets || [])
    readonly property bool selectedPresetRemovable: {
        const index = preparationPreset.currentIndex - 1
        const values = controller.preparationPresets || []
        return index >= 0 && index < values.length && values[index].removable === true
    }

    readonly property bool usesBackingLyrics: field("target") !== "separation"
        && Boolean(field("recognize_lyrics")) && field("voice_mode") !== "single"
    readonly property bool englishLyrics: field("language") === "en"
    readonly property var componentCheck: controller.preparationSources.view.componentCheck || ({})
    readonly property string inputBlocker: page.sourceBlocker()
    property bool showOutputOptions: false
    property bool showMusicPath: false
    property bool showTaskDetails: false

    function sourceBlocker() {
        if (field("input_mode") === "local" && !field("music_path"))
            return controller.text("先选择本地音频。")
        if (field("input_mode") === "bilibili" && !String(field("source")).trim())
            return controller.text("先填写 B站来源。")
        if (field("voice_mode") === "import" && (!stemPath("lead") || !stemPath("backing") || !stemPath("instrumental")))
            return controller.text("请补齐主唱、和声与伴奏 WAV。")
        return ""
    }

    function checkLine() {
        const check = page.componentCheck
        if (check.state === "checking") return controller.text("正在检查所选组件…")
        if (check.state === "passed") return controller.text("所选组件检查通过")
        if (check.state === "error") return controller.text(check.error || "检查失败")
        if (check.state === "issues") return (check.issues || []).join(" · ")
        return controller.text("所选组件 · 待检查")
    }

    function field(name) {
        return prep[name] === undefined || prep[name] === null ? "" : prep[name]
    }

    function indexInModel(options, value) {
        // Passing model explicitly makes each currentIndex binding depend on
        // the model, so it cannot be evaluated against an empty first frame.
        if (!options || options.length === undefined)
            return 0
        for (let index = 0; index < options.length; ++index) {
            const candidate = options[index] && options[index].value !== undefined
                ? options[index].value : options[index]
            if (String(candidate) === String(value))
                return index
        }
        return 0
    }

    function listHas(values, value) {
        if (!values || values.length === undefined)
            return false
        for (let index = 0; index < values.length; ++index) {
            if (String(values[index]) === String(value))
                return true
        }
        return false
    }

    function chooseVoiceMode(value) {
        if (value === "roformer") {
            controller.setPreparationField("separator_model", "roformer")
            controller.setPreparationField("voice_mode", "roformer")
        } else if (value === "dual") {
            controller.setPreparationField("separator_model", "dual")
            controller.setPreparationField("voice_mode", "dual")
        } else {
            controller.setPreparationField("voice_mode", value)
        }
    }

    function chooseSeparatorModel(value) {
        controller.setPreparationField("separator_model", value)
        if (value === "roformer")
            controller.setPreparationField("voice_mode", "roformer")
        else if (field("voice_mode") === "roformer")
            controller.setPreparationField("voice_mode", "dual")
    }

    function stemPath(key) {
        const stems = prep.imported_stems || ({})
        return stems[key] || ""
    }

    function fileName(value) {
        if (!value)
            return ""
        const parts = String(value).replace(/\\/g, "/").split("/")
        return parts[parts.length - 1]
    }

    function formatElapsed(value) {
        if (value === undefined || value === null || value === "")
            return ""
        const seconds = Number(value)
        if (!isFinite(seconds) || seconds < 0)
            return String(value)
        const whole = Math.floor(seconds)
        const minutes = Math.floor(whole / 60)
        const remainder = whole % 60
        return minutes > 0 ? minutes + ":" + String(remainder).padStart(2, "0") : remainder + " " + controller.text("秒")
    }

    function statusColor() {
        if (taskFailed)
            return theme.error
        if (taskSucceeded || taskPartial)
            return theme.success
        if (taskRunning)
            return theme.accent
        return theme.muted
    }

    function statusTitle() {
        if (taskState === "running")
            return controller.text("正在准备")
        if (taskState === "cancelling")
            return controller.text("正在取消")
        if (taskSucceeded)
            return controller.text("准备完成")
        if (taskState === "failed")
            return controller.text("准备失败")
        if (taskState === "cancelled")
            return controller.text("已取消")
        if (taskState === "interrupted")
            return controller.text("任务已中断")
        if (taskPartial)
            return controller.text("本轮阶段完成")
        return controller.text("下一步")
    }

    function separatorSummary() {
        if (field("voice_mode") === "import")
            return controller.text("导入外部分轨")
        if (field("voice_mode") === "single")
            return controller.text("Karaoke 2 · 合并人声")
        if (roformerSelected)
            return "BS-RoFormer · " + String(field("roformer_device") || "auto").toUpperCase()
        return controller.text("Karaoke 2 · 主唱与和声")
    }

    component LabelText: Text {
        id: label
        required property QtObject theme
        color: label.theme.text
        font.pixelSize: Math.round(13 * label.theme.scale)
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    component HelperText: Text {
        id: helper
        required property QtObject theme
        color: helper.theme.muted
        font.pixelSize: Math.round(12 * helper.theme.scale)
        lineHeight: 1.18
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    component ThemedTextField: TextField {
        id: input
        required property QtObject theme

        // A TextField's content metrics can change while the parent lays out.
        // Give the layout a stable row height before the first native frame.
        implicitHeight: Math.round(38 * input.theme.scale)
        leftPadding: Math.round(10 * input.theme.scale)
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

    component ThemedTextArea: TextArea {
        id: area
        required property QtObject theme

        color: area.theme.text
        placeholderTextColor: area.theme.muted
        font.pixelSize: Math.round(13 * area.theme.scale)
        selectByMouse: true
        persistentSelection: true
        wrapMode: TextEdit.Wrap
        textFormat: TextEdit.PlainText
        focusPolicy: Qt.StrongFocus
        selectionColor: area.theme.accent
        selectedTextColor: area.theme.base
        topPadding: Math.round(10 * area.theme.scale)
        bottomPadding: topPadding
        leftPadding: Math.round(11 * area.theme.scale)
        rightPadding: leftPadding
        Accessible.name: placeholderText

        background: Rectangle {
            radius: Math.round(7 * area.theme.scale)
            color: area.enabled ? area.theme.surface : area.theme.base
            border.width: 1
            border.color: area.activeFocus ? area.theme.accent : area.theme.line
            opacity: area.enabled ? 1 : 0.58
        }
    }

    component CheckOption: CheckBox {
        id: option
        required property QtObject theme

        implicitHeight: Math.max(Math.round(34 * option.theme.scale), implicitContentHeight + Math.round(8 * option.theme.scale))
        leftPadding: 0
        rightPadding: 0
        topPadding: Math.round(3 * option.theme.scale)
        bottomPadding: topPadding
        spacing: Math.round(8 * option.theme.scale)
        font.pixelSize: Math.round(13 * option.theme.scale)
        focusPolicy: Qt.StrongFocus
        Accessible.name: text

        indicator: Rectangle {
            implicitWidth: Math.round(18 * option.theme.scale)
            implicitHeight: implicitWidth
            x: option.leftPadding
            y: (option.height - height) / 2
            radius: Math.round(4 * option.theme.scale)
            color: option.checked ? option.theme.accent : option.theme.surface
            border.width: 1
            border.color: option.activeFocus || option.visualFocus ? option.theme.accent : option.checked ? option.theme.accent : option.theme.line
            opacity: option.enabled ? 1 : 0.5

            Text {
                anchors.centerIn: parent
                visible: option.checked
                text: "✓"
                color: option.theme.base
                font.pixelSize: Math.round(12 * option.theme.scale)
                font.weight: Font.Bold
                textFormat: Text.PlainText
            }
        }

        contentItem: Text {
            leftPadding: option.indicator.width + option.spacing
            text: option.text
            color: option.enabled ? option.theme.text : option.theme.muted
            font: option.font
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
    }

    component SectionCard: Rectangle {
        id: card
        required property QtObject theme
        property string title
        property string subtitle
        property string icon: ""
        property string stepNumber: ""
        default property alias contentData: contentColumn.data

        implicitHeight: cardColumn.implicitHeight + Math.round(28 * card.theme.scale)
        color: card.theme.surface
        radius: card.theme.radiusLg !== undefined ? card.theme.radiusLg : Math.round(12 * card.theme.scale)
        border.width: 1
        border.color: card.theme.lineSubtle !== undefined ? card.theme.lineSubtle : card.theme.line
        clip: false

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: card.theme.highlightTop !== undefined ? card.theme.highlightTop : "transparent"
        }

        ColumnLayout {
            id: cardColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Math.round(16 * card.theme.scale)
            spacing: Math.round(11 * card.theme.scale)

            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(8 * card.theme.scale)

                Rectangle {
                    visible: card.stepNumber.length > 0
                    Layout.preferredWidth: Math.round(22 * card.theme.scale)
                    Layout.preferredHeight: Layout.preferredWidth
                    Layout.alignment: Qt.AlignVCenter
                    radius: Math.round(Layout.preferredWidth / 2)
                    color: Qt.rgba(card.theme.accent.r, card.theme.accent.g, card.theme.accent.b, 0.16)
                    border.width: 1
                    border.color: Qt.rgba(card.theme.accent.r, card.theme.accent.g, card.theme.accent.b, 0.36)

                    Text {
                        anchors.centerIn: parent
                        text: card.stepNumber
                        color: card.theme.accent
                        font.pixelSize: Math.round(11 * card.theme.scale)
                        font.weight: Font.Bold
                        textFormat: Text.PlainText
                    }
                }

                Loader {
                    active: card.icon.length > 0 && card.stepNumber.length === 0
                    visible: active
                    Layout.preferredWidth: Math.round(16 * card.theme.scale)
                    Layout.preferredHeight: Layout.preferredWidth
                    Layout.alignment: Qt.AlignVCenter
                    sourceComponent: UiIcon { glyph: card.icon; tint: card.theme.accent }
                }

                Text {
                    Layout.fillWidth: true
                    text: card.title
                    color: card.theme.text
                    font.pixelSize: Math.round(16 * card.theme.scale)
                    font.weight: Font.DemiBold
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                }
            }

            Text {
                Layout.fillWidth: true
                visible: card.subtitle.length > 0
                text: card.subtitle
                color: card.theme.muted
                font.pixelSize: Math.round(12 * card.theme.scale)
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }

            ColumnLayout {
                id: contentColumn
                Layout.fillWidth: true
                spacing: Math.round(10 * card.theme.scale)
            }
        }
    }

    PreparationSourcesDialog {
        id: sourceTools
        theme: page.theme
        controller: page.controller.preparationSources
        onLyricQuerySubmitted: query => page.controller.setPreparationField("lyrics_lookup", query)
        onComponentsRequested: page.controller.navigate("components")
    }

    UiDialog {
        id: savePreparationPresetDialog
        objectName: "preparationPresetNameDialog"
        theme: page.theme
        title: page.controller.text("保存到本机预设库")
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 420, Math.round(430 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)

        contentItem: ColumnLayout {
            spacing: Math.round(8 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.controller.text("预设名称")
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.theme.scale)
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            ThemedTextField {
                id: preparationPresetNameField
                objectName: "preparationPresetName"
                Layout.fillWidth: true
                theme: page.theme
                placeholderText: page.controller.text("例如：中文双轨 · 当前输出")
                Accessible.name: page.controller.text("准备预设名称")
            }
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.controller.text("只保存处理参数和工作流步骤；歌曲、歌词、路径与设备设置不会写入预设。")
            }
        }

        footer: RowLayout {
            spacing: Math.round(7 * page.theme.scale)
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "cancelPreparationPresetSave"
                theme: page.theme
                text: page.controller.text("取消")
                quiet: true
                onClicked: savePreparationPresetDialog.close()
            }
            UiButton {
                objectName: "confirmPreparationPresetSave"
                theme: page.theme
                text: page.controller.text("保存")
                primary: true
                enabled: preparationPresetNameField.text.trim().length > 0 && !page.editLocked
                onClicked: {
                    if (page.controller.savePreparationPreset(preparationPresetNameField.text.trim()))
                        savePreparationPresetDialog.close()
                }
            }
        }
    }

    UiDialog {
        id: removePreparationPresetDialog
        objectName: "preparationPresetRemoveDialog"
        theme: page.theme
        property string presetKey: ""
        property string presetLabel: ""
        title: page.controller.text("移除自定义预设")
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 420, Math.round(430 * page.theme.scale))
        padding: Math.round(18 * page.theme.scale)

        contentItem: ColumnLayout {
            spacing: Math.round(8 * page.theme.scale)
            Text {
                Layout.fillWidth: true
                text: page.controller.text("移除预设“") + removePreparationPresetDialog.presetLabel + "”？"
                color: page.theme.text
                font.pixelSize: Math.round(13 * page.theme.scale)
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
            }
            HelperText {
                Layout.fillWidth: true
                theme: page.theme
                text: page.controller.text("只会从当前预设列表移除；本机不可变历史文件与其他预设不会改动。")
            }
        }

        footer: RowLayout {
            spacing: Math.round(7 * page.theme.scale)
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "cancelPreparationPresetRemove"
                theme: page.theme
                text: page.controller.text("取消")
                quiet: true
                onClicked: removePreparationPresetDialog.close()
            }
            UiButton {
                objectName: "confirmPreparationPresetRemove"
                theme: page.theme
                text: page.controller.text("移除")
                primary: true
                onClicked: {
                    if (page.controller.removePreparationPreset(removePreparationPresetDialog.presetKey)) {
                        removePreparationPresetDialog.close()
                        preparationPreset.currentIndex = 0
                    }
                }
            }
        }
    }

    FileDialog {
        id: musicDialog
        title: page.controller.text("选择本地音频")
        nameFilters: [
            page.controller.text("音频文件") + " (*.wav *.flac *.mp3 *.m4a *.ogg *.aac *.opus *.wma *.aif *.aiff *.ape *.wv)",
            page.controller.text("所有文件") + " (*)"
        ]
        onAccepted: page.controller.chooseMusic(selectedFile)
    }

    FileDialog {
        id: stemDialog
        property string stemKey: "lead"
        title: page.controller.text("选择外部分轨 WAV")
        nameFilters: [page.controller.text("WAV 音频") + " (*.wav)", page.controller.text("所有文件") + " (*)"]
        onAccepted: page.controller.chooseStem(stemKey, selectedFile)
    }

    FolderDialog {
        id: outputDialog
        title: page.controller.text("选择工程输出目录")
        onAccepted: page.controller.chooseOutput(selectedFolder)
    }

    FileDialog {
        id: lyricsDialog
        property string voice: "lead"
        title: voice === "lead"
            ? page.controller.text("导入主唱歌词")
            : page.controller.text("导入和声歌词")
        nameFilters: [
            page.controller.text("歌词文件") + " (*.lrc *.yrc *.txt)",
            page.controller.text("所有文件") + " (*)"
        ]
        onAccepted: page.controller.importLyrics(voice, selectedFile)
    }

    ScrollView {
        id: prepScroll
        objectName: "preparationContentScroll"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: footer.top
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical: UiScrollBar { theme: page.theme }

        ColumnLayout {
            width: prepScroll.availableWidth
            spacing: page.narrow ? 9 : 10

            // Unified compact header + inline preset bar
            GridLayout {
                Layout.fillWidth: true
                Layout.topMargin: Math.round(12 * page.theme.scale)
                Layout.leftMargin: page.contentMargin
                Layout.rightMargin: page.contentMargin
                columns: page.narrow ? 1 : 2
                columnSpacing: Math.round(10 * page.theme.scale)
                rowSpacing: Math.round(6 * page.theme.scale)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(10 * page.theme.scale)
                    Text {
                        text: page.controller.text("准备工作流")
                        color: page.theme.text
                        font.pixelSize: Math.round(18 * page.theme.scale)
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: !page.narrow
                        text: page.controller.text("01 素材 → 02 歌词与声部 → 03 输出")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                }

                RowLayout {
                    Layout.fillWidth: page.narrow
                    spacing: Math.round(5 * page.theme.scale)

                    UiComboBox {
                        id: preparationPreset
                        objectName: "preparationPreset"
                        Layout.fillWidth: page.narrow
                        Layout.preferredWidth: Math.round(192 * page.theme.scale)
                        Layout.preferredHeight: Math.round(30 * page.theme.scale)
                        theme: page.theme
                        model: page.presetChoices
                        textRole: "name"; valueRole: "key"
                        currentIndex: 0
                        enabled: !page.editLocked && (page.controller.preparationPresets || []).length > 0
                        Accessible.name: page.controller.text("工作流预设")
                        onActivated: index => {
                            if (index > 0)
                                page.controller.applyPreparationPreset(valueAt(index))
                        }
                    }

                    UiButton {
                        objectName: "savePreparationPreset"
                        theme: page.theme
                        implicitHeight: Math.round(30 * page.theme.scale)
                        quiet: true
                        text: page.controller.text("保存预设")
                        leadingGlyph: "save"
                        leadingIconSize: 14
                        enabled: !page.editLocked
                        Accessible.name: page.controller.text("保存当前准备设置为预设")
                        onClicked: {
                            const index = preparationPreset.currentIndex - 1
                            const values = page.controller.preparationPresets || []
                            preparationPresetNameField.text = index >= 0 && index < values.length
                                ? String(values[index].preset_name || "") : ""
                            savePreparationPresetDialog.open()
                        }
                    }

                    UiButton {
                        objectName: "removePreparationPreset"
                        visible: page.selectedPresetRemovable
                        theme: page.theme
                        implicitHeight: Math.round(30 * page.theme.scale)
                        text: page.controller.text("移除")
                        quiet: true
                        enabled: !page.editLocked && page.selectedPresetRemovable
                        Accessible.name: page.controller.text("移除所选自定义准备预设")
                        onClicked: {
                            const index = preparationPreset.currentIndex - 1
                            const values = page.controller.preparationPresets || []
                            if (index < 0 || index >= values.length)
                                return
                            removePreparationPresetDialog.presetKey = String(values[index].key || "")
                            removePreparationPresetDialog.presetLabel = String(values[index].name || "")
                            removePreparationPresetDialog.open()
                        }
                    }
                }
            }

            GridLayout {
                id: workspaceGrid
                objectName: "preparationWorkspace"
                Layout.fillWidth: true
                Layout.leftMargin: page.contentMargin
                Layout.rightMargin: page.contentMargin
                columns: page.narrow ? 1 : 2
                columnSpacing: Math.round(14 * page.theme.scale)
                rowSpacing: Math.round(12 * page.theme.scale)

                ColumnLayout {
                    objectName: "preparationSourceAndLyrics"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.alignment: Qt.AlignTop
                    spacing: Math.round(12 * page.theme.scale)
                    SectionCard {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        theme: page.theme
                        stepNumber: "1"
                        icon: "music_note"
                        title: page.controller.text("音乐来源")
                        subtitle: page.controller.text("使用本地音频，或从 B站视频下载。")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            UiButton {
                                objectName: "sourceLocal"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("本地音频")
                                selected: page.field("input_mode") === "local"
                                enabled: !page.editLocked
                                onClicked: {
                                    if (page.field("input_mode") !== "local")
                                        page.controller.setPreparationField("input_mode", "local")
                                }
                            }

                            UiButton {
                                objectName: "sourceBilibili"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("B站视频")
                                selected: page.field("input_mode") === "bilibili"
                                enabled: !page.editLocked
                                onClicked: {
                                    if (page.field("input_mode") !== "bilibili")
                                        page.controller.setPreparationField("input_mode", "bilibili")
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: sourceSummary.implicitHeight + Math.round(20 * page.theme.scale)
                            color: localMusicDropArea.containsDrag ? page.theme.selected : page.theme.raised
                            radius: page.theme.radiusMd !== undefined ? page.theme.radiusMd : Math.round(8 * page.theme.scale)
                            border.width: 1
                            border.color: localMusicDropArea.containsDrag
                                ? page.theme.accent
                                : (page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line)

                            ColumnLayout {
                                id: sourceSummary
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: Math.round(10 * page.theme.scale)
                                spacing: Math.round(5 * page.theme.scale)

                                Text {
                                    Layout.fillWidth: true
                                    text: page.controller.text("当前素材")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(11 * page.theme.scale)
                                    textFormat: Text.PlainText
                                }

                                Text {
                                    objectName: "selectedMusic"
                                    Layout.fillWidth: true
                                    text: page.field("input_mode") === "local"
                                        ? (page.field("music_name") || page.controller.text("未选择本地音频"))
                                        : (page.field("source") || page.controller.text("尚未填写 B站地址"))
                                    color: page.theme.text
                                    font.pixelSize: Math.round(14 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideMiddle
                                    textFormat: Text.PlainText
                                    ToolTip.visible: musicHover.hovered && ToolTip.text.length > 0
                                    ToolTip.text: page.field("input_mode") === "local" ? page.field("music_path") : page.field("source")
                                    HoverHandler { id: musicHover }
                                }
                            }

                            DropArea {
                                id: localMusicDropArea
                                objectName: "localMusicDropArea"
                                anchors.fill: parent
                                enabled: !page.editLocked
                                onEntered: function(drag) {
                                    if (drag.hasUrls || String(drag.text || "").trim().length > 0)
                                        drag.accept(Qt.CopyAction)
                                }
                                onDropped: function(drop) {
                                    const urls = drop.urls || []
                                    if (urls.length > 0) {
                                        for (let index = 0; index < urls.length; ++index) {
                                            if (urls[index] && urls[index].isLocalFile()) {
                                                page.controller.setPreparationField("input_mode", "local")
                                                page.controller.setPreparationMusicPath(urls[index].toLocalFile())
                                                drop.acceptProposedAction()
                                                return
                                            }
                                        }
                                    }
                                    const text = String(drop.text || "").trim().replace(/^"|"$/g, "")
                                    if (text.length > 0) {
                                        page.controller.setPreparationField("input_mode", "local")
                                        page.controller.setPreparationMusicPath(text)
                                        drop.acceptProposedAction()
                                    }
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.field("input_mode") === "local" && page.showMusicPath
                            spacing: Math.round(5 * page.theme.scale)

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("本地音频路径")
                            }

                            ThemedTextField {
                                id: musicPathInput
                                objectName: "localMusicPath"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.field("music_path")
                                placeholderText: page.controller.text("粘贴文件路径，或拖入上方素材卡片")
                                enabled: !page.editLocked
                                onEditingFinished: {
                                    const value = text.trim()
                                    if (value !== String(page.field("music_path")))
                                        page.controller.setPreparationMusicPath(value)
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("路径在确认后校验；歌曲路径不会写入重启设置。")
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            visible: page.field("input_mode") === "local"
                            spacing: Math.round(8 * page.theme.scale)

                            UiButton {
                                objectName: "chooseMusic"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("选择本地音频")
                                enabled: !page.editLocked
                                onClicked: {
                                    if (page.field("input_mode") !== "local")
                                        page.controller.setPreparationField("input_mode", "local")
                                    musicDialog.open()
                                }
                            }

                            UiButton {
                                objectName: "preparationMusicPathToggle"
                                theme: page.theme
                                quiet: true
                                text: page.controller.text(page.showMusicPath ? "收起路径" : "粘贴路径…")
                                onClicked: {
                                    page.showMusicPath = !page.showMusicPath
                                    if (page.showMusicPath)
                                        musicPathInput.forceActiveFocus()
                                }
                            }

                            UiButton {
                                objectName: "clearMusic"
                                visible: page.field("input_mode") === "local" && String(page.field("music_path")).length > 0
                                theme: page.theme
                                text: page.controller.text("移除")
                                quiet: true
                                enabled: !page.editLocked
                                onClicked: page.controller.chooseMusic("")
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.field("input_mode") === "bilibili"
                            spacing: Math.round(5 * page.theme.scale)

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("B站视频地址")
                            }

                            ThemedTextField {
                                id: bilibiliSourceField
                                objectName: "bilibiliSource"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.field("source")
                                placeholderText: page.controller.text("粘贴 BV 号或视频链接")
                                enabled: !page.editLocked
                                onTextEdited: page.controller.setPreparationField("source", text)
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                UiButton {
                                    objectName: "bilibiliDownloadOptions"
                                    theme: page.theme
                                    text: page.vwaTr("下载与账号…")
                                    enabled: !page.editLocked
                                    onClicked: sourceTools.openPane("download")
                                }
                                HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.preparationSources.view.selectedDownload
                                        ? page.vwaTr("复用 ") + page.controller.preparationSources.view.selectedDownload
                                        : page.vwaTr("保留原视频，也可复用同 BV 的已核验素材。")
                                }
                            }
                        }
                    }

                    SectionCard {
                        Layout.fillWidth: true
                        theme: page.theme
                        stepNumber: "2"
                        icon: "lyrics"
                        title: page.controller.text("歌词与语言")
                        subtitle: page.controller.text("已有歌词可直接填入或导入；留空时按所选语言从人声音频识别。")

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            ThemedTextField {
                                id: lyricsLookupField
                                objectName: "neteaseSong"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.field("lyrics_lookup")
                                placeholderText: page.vwaTr("歌名、歌手、歌曲 ID 或链接")
                                enabled: !page.editLocked && Boolean(page.field("recognize_lyrics"))
                                onTextEdited: page.controller.setPreparationField("lyrics_lookup", text)
                                onAccepted: sourceTools.openPane("lyrics", text)
                            }
                            UiButton {
                                objectName: "browseNeteaseVersions"
                                theme: page.theme
                                text: page.vwaTr("搜索网易云")
                                enabled: lyricsLookupField.enabled
                                onClicked: sourceTools.openPane("lyrics", lyricsLookupField.text)
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.narrow ? 1 : 2
                            columnSpacing: Math.round(9 * page.theme.scale)
                            rowSpacing: Math.round(7 * page.theme.scale)

                            UiButton {
                                objectName: "importLeadLyrics"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("导入主唱歌词文件")
                                enabled: !page.editLocked && Boolean(page.field("recognize_lyrics"))
                                onClicked: {
                                    lyricsDialog.voice = "lead"
                                    lyricsDialog.open()
                                }
                            }

                            UiButton {
                                objectName: "importBackingLyrics"
                                Layout.fillWidth: true
                                visible: page.usesBackingLyrics
                                theme: page.theme
                                text: page.controller.text("导入和声歌词文件")
                                enabled: !page.editLocked && Boolean(page.field("recognize_lyrics"))
                                onClicked: {
                                    lyricsDialog.voice = "backing"
                                    lyricsDialog.open()
                                }
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.narrow ? 1 : 2
                            columnSpacing: Math.round(12 * page.theme.scale)
                            rowSpacing: Math.round(5 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("主唱歌词")
                                }

                                ThemedTextArea {
                                    id: lyricsEditor
                                    objectName: "lyricsInput"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: Math.round(150 * Math.max(1, page.theme.scale))
                                    theme: page.theme
                                    text: page.field("lyrics")
                                    placeholderText: page.controller.text("填写、粘贴主唱原文歌词；留空时自动识别。")
                                    enabled: !page.editLocked && Boolean(page.field("recognize_lyrics"))
                                    onTextChanged: {
                                        if (activeFocus && !inputMethodComposing && text !== page.field("lyrics"))
                                            page.controller.setPreparationField("lyrics", text)
                                    }
                                    onInputMethodComposingChanged: {
                                        if (!inputMethodComposing && activeFocus && text !== page.field("lyrics"))
                                            page.controller.setPreparationField("lyrics", text)
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.usesBackingLyrics
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("和声歌词")
                                }

                                ThemedTextArea {
                                    id: backingLyricsEditor
                                    objectName: "backingLyricsInput"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: Math.round(150 * Math.max(1, page.theme.scale))
                                    theme: page.theme
                                    text: page.field("backing_lyrics")
                                    placeholderText: page.controller.text("可选；填写和声原文歌词，留空时独立识别。")
                                    enabled: !page.editLocked && Boolean(page.field("recognize_lyrics"))
                                    onTextChanged: {
                                        if (activeFocus && !inputMethodComposing && text !== page.field("backing_lyrics"))
                                            page.controller.setPreparationField("backing_lyrics", text)
                                    }
                                    onInputMethodComposingChanged: {
                                        if (!inputMethodComposing && activeFocus && text !== page.field("backing_lyrics"))
                                            page.controller.setPreparationField("backing_lyrics", text)
                                    }
                                }
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            visible: !page.usesBackingLyrics && Boolean(page.field("backing_lyrics"))
                            text: page.controller.text("当前方案不使用和声歌词，已保留原文。")
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.narrow ? 1 : 3
                            columnSpacing: Math.round(12 * page.theme.scale)
                            rowSpacing: Math.round(8 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("歌词语言")
                                }

                                UiComboBox {
                                    objectName: "lyricsLanguage"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: page.controller.preparationLanguages
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("language"))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("language", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.field("language") === "zh"
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("中文歌词格式")
                                }

                                UiComboBox {
                                    objectName: "zhLyricMode"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: page.controller.text("汉字"), value: "hanzi"},
                                        {label: page.controller.text("拼音"), value: "pinyin"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("zh_lyric_mode"))
                                    enabled: !page.editLocked && Boolean(page.field("recognize_lyrics"))
                                    onActivated: page.controller.setPreparationField("zh_lyric_mode", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("歌词处理")
                                }

                                CheckOption {
                                    objectName: "recognizeLyrics"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.controller.text("识别歌词并写入 MIDI")
                                    checked: Boolean(page.field("recognize_lyrics"))
                                    enabled: !page.editLocked
                                    onToggled: {
                                        if (checked !== Boolean(page.field("recognize_lyrics")))
                                            page.controller.setPreparationField("recognize_lyrics", checked)
                                    }
                                }

                                HelperText {
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: !page.field("recognize_lyrics")
                                        ? page.controller.text("关闭识别时不提交歌词文本。")
                                        : page.englishLyrics ? page.controller.text("参考歌词请保留单词间的空格，数字请按实际唱法拼写。")
                                        : page.controller.text("有可靠歌词时优先填写，可减少识别并参与对齐。")
                                }
                            }
                        }
                    }
                }

                ColumnLayout {
                    objectName: "preparationPlanInspector"
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: page.narrow
                    Layout.minimumWidth: page.narrow ? 0 : Math.round(320 * page.theme.scale)
                    Layout.preferredWidth: Math.round(335 * page.theme.scale)
                    Layout.maximumWidth: page.narrow ? 16777215 : Math.round(355 * page.theme.scale)
                    spacing: Math.round(12 * page.theme.scale)
                    SectionCard {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        theme: page.theme
                        stepNumber: "3"
                        icon: "nav_processing"
                        title: page.controller.text("分离与分轨")
                        subtitle: page.controller.text("选择内置分离、BS-RoFormer，或直接导入已有分轨。")

                        LabelText {
                            theme: page.theme
                            text: page.controller.text("声部路线")
                        }

                        UiComboBox {
                            objectName: "voiceMode"
                            Layout.fillWidth: true
                            theme: page.theme
                            fieldLabel: page.controller.text("声部路线")
                            model: [
                                {label: page.controller.text("内置 · 主唱与和声双轨"), value: "dual"},
                                {label: page.controller.text("内置 · 合并人声单轨"), value: "single"},
                                {label: page.controller.text("BS-RoFormer · 双轨"), value: "roformer"},
                                {label: page.controller.text("导入外部分轨"), value: "import"}
                            ]
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: page.indexInModel(model, page.roformerSelected ? "roformer" : page.field("voice_mode"))
                            enabled: !page.editLocked
                            onActivated: page.chooseVoiceMode(currentValue)
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)

                            Text {
                                objectName: "selectedSeparator"
                                Layout.fillWidth: true
                                text: page.separatorSummary()
                                color: page.theme.text
                                font.pixelSize: Math.round(13 * page.theme.scale)
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                                ToolTip.visible: separatorHover.hovered && truncated
                                ToolTip.text: text
                                HoverHandler { id: separatorHover }
                            }

                            UiButton {
                                objectName: "openComponents"
                                theme: page.theme
                                text: page.controller.text("模型与组件")
                                quiet: true
                                onClicked: page.controller.navigate("components")
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.field("voice_mode") === "dual" || page.field("voice_mode") === "roformer"
                            spacing: Math.round(5 * page.theme.scale)

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("自动分离模型")
                            }

                            UiComboBox {
                                objectName: "separatorModel"
                                Layout.fillWidth: true
                                theme: page.theme
                                model: [
                                    {label: page.controller.text("Karaoke 2 · 内置"), value: "dual"},
                                    {label: page.controller.text("BS-RoFormer · 可选组件"), value: "roformer"}
                                ]
                                textRole: "label"
                                valueRole: "value"
                                currentIndex: page.indexInModel(model, page.field("separator_model"))
                                enabled: !page.editLocked
                                onActivated: page.chooseSeparatorModel(currentValue)
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            visible: page.roformerSelected
                            columns: 1
                            columnSpacing: Math.round(10 * page.theme.scale)
                            rowSpacing: Math.round(7 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("RoFormer 设备")
                                }

                                UiComboBox {
                                    objectName: "roformerDevice"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: page.controller.text("自动 · WebGPU 优先"), value: "auto"},
                                        {label: "CPU", value: "cpu"},
                                        {label: "GPU / WebGPU", value: "webgpu"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("roformer_device"))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("roformer_device", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("分离路径")
                                }

                                UiComboBox {
                                    objectName: "separationFlow"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: page.controller.text("先取总人声，再拆分主唱与和声"), value: "cascade"},
                                        {label: page.controller.text("从原曲独立提取主唱（实验）"), value: "independent"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("separation_flow"))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("separation_flow", currentValue)
                                }
                            }
                        }

                        HelperText {
                            Layout.fillWidth: true
                            visible: page.roformerSelected
                            theme: page.theme
                            text: page.controller.text("RoFormer 需要先在模型与组件中安装。自动模式优先使用 WebGPU（支持 AMD / NVIDIA / Intel 显卡）；遇到显存或驱动问题时可在失败后改用 CPU。")
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.field("voice_mode") === "import"
                            spacing: Math.round(8 * page.theme.scale)

                            Repeater {
                                model: [
                                    {key: "lead", label: page.controller.text("主唱 WAV")},
                                    {key: "backing", label: page.controller.text("和声 WAV")},
                                    {key: "instrumental", label: page.controller.text("伴奏 WAV")}
                                ]

                                delegate: ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: Math.round(4 * page.theme.scale)

                                    LabelText {
                                        theme: page.theme
                                        text: modelData.label
                                    }

                                    GridLayout {
                                        Layout.fillWidth: true
                                        columns: 1
                                        columnSpacing: Math.round(7 * page.theme.scale)
                                        rowSpacing: Math.round(5 * page.theme.scale)

                                        Text {
                                            objectName: "selectedStem_" + modelData.key
                                            Layout.fillWidth: true
                                            text: page.fileName(page.stemPath(modelData.key)) || page.controller.text("未选择 WAV")
                                            color: page.stemPath(modelData.key) ? page.theme.text : page.theme.muted
                                            font.pixelSize: Math.round(12 * page.theme.scale)
                                            elide: Text.ElideMiddle
                                            textFormat: Text.PlainText
                                            verticalAlignment: Text.AlignVCenter
                                            ToolTip.visible: stemHover.hovered && truncated
                                            ToolTip.text: page.stemPath(modelData.key)
                                            HoverHandler { id: stemHover }
                                        }

                                        RowLayout {
                                            Layout.alignment: Qt.AlignRight
                                            spacing: Math.round(6 * page.theme.scale)

                                            UiButton {
                                                objectName: "chooseStem_" + modelData.key
                                                theme: page.theme
                                                text: page.controller.text("选择")
                                                enabled: !page.editLocked
                                                onClicked: {
                                                    stemDialog.stemKey = modelData.key
                                                    stemDialog.open()
                                                }
                                            }

                                            UiButton {
                                                objectName: "clearStem_" + modelData.key
                                                visible: Boolean(page.stemPath(modelData.key))
                                                theme: page.theme
                                                text: page.controller.text("清除")
                                                quiet: true
                                                enabled: !page.editLocked
                                                onClicked: page.controller.chooseStem(modelData.key, "")
                                            }
                                        }
                                    }
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("导入模式需要主唱、和声、伴奏三个完整 WAV。")
                            }
                        }
                    }

                    SectionCard {
                        Layout.fillWidth: true
                        theme: page.theme
                        stepNumber: "4"
                        icon: "target"
                        title: page.controller.text("工作目标")

                        UiComboBox {
                            objectName: "workTarget"
                            Layout.fillWidth: true
                            theme: page.theme
                            fieldLabel: page.controller.text("工作目标")
                            model: [
                                {label: page.controller.text("仅分离音频"), value: "separation"},
                                {label: page.controller.text("MIDI 与音频"), value: "midi"},
                                {label: page.controller.text("MIDI、音频与字幕"), value: "subtitles"}
                            ]
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: page.indexInModel(model, page.field("target"))
                            enabled: !page.editLocked
                            onActivated: page.controller.setPreparationField("target", currentValue)
                        }

                        UiButton {
                            objectName: "preparationOutputOptions"
                            Layout.fillWidth: true
                            theme: page.theme
                            quiet: true
                            leftAligned: true
                            text: page.controller.text("输出目录与格式")
                            disclosure: page.showOutputOptions ? 1 : 0
                            Accessible.checkable: true
                            Accessible.checked: disclosure === 1
                            onClicked: page.showOutputOptions = !page.showOutputOptions
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.showOutputOptions
                            spacing: Math.round(8 * page.theme.scale)
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.field("target") !== "separation"
                                spacing: Math.round(7 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("工程格式")
                                }

                                HelperText {
                                    objectName: "preparationProjectFormatHint"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    visible: !(page.field("project_formats") || []).length
                                    text: page.controller.text("工程格式未选。将生成 MIDI，勾选格式后可同时导出工程。")
                                }

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    columnSpacing: Math.round(12 * page.theme.scale)
                                    rowSpacing: Math.round(2 * page.theme.scale)

                                    Repeater {
                                        model: [
                                            {value: "ustx", label: "USTX", hint: page.controller.text("OpenUtau 工程")},
                                            {value: "vsqx", label: "VSQX", hint: page.controller.text("VOCALOID 工程")},
                                            {value: "svp", label: "SVP", hint: page.controller.text("Synthesizer V 工程")}
                                        ]

                                        delegate: CheckOption {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: modelData.label
                                            checked: page.listHas(page.field("project_formats"), modelData.value)
                                            enabled: !page.editLocked
                                            ToolTip.visible: hovered
                                            ToolTip.text: modelData.hint
                                            onToggled: page.controller.setPreparationExportFormat(
                                                "project_formats", modelData.value, checked)
                                        }
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(7 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("音频格式")
                                }

                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: page.narrow ? 2 : 4
                                    columnSpacing: Math.round(12 * page.theme.scale)
                                    rowSpacing: Math.round(2 * page.theme.scale)

                                    Repeater {
                                        model: ["wav", "flac", "mp3", "ogg"]

                                        delegate: CheckOption {
                                            required property string modelData
                                            Layout.fillWidth: true
                                            theme: page.theme
                                            text: modelData.toUpperCase()
                                            checked: page.listHas(page.field("audio_formats"), modelData)
                                            enabled: !page.editLocked
                                            onToggled: page.controller.setPreparationExportFormat(
                                                "audio_formats", modelData, checked)
                                        }
                                    }
                                }
                            }

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("输出目录")
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: page.narrow ? 1 : 2
                                columnSpacing: Math.round(8 * page.theme.scale)
                                rowSpacing: Math.round(7 * page.theme.scale)

                                ThemedTextField {
                                    id: outputDirectoryField
                                    objectName: "outputDirectory"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    text: page.field("output_directory")
                                    placeholderText: page.controller.text("留空使用设置中的默认目录")
                                    enabled: !page.editLocked
                                    onTextEdited: page.controller.setPreparationField("output_directory", text)
                                }

                                UiButton {
                                    objectName: "chooseOutput"
                                    Layout.fillWidth: page.narrow
                                    theme: page.theme
                                    text: page.controller.text("选择目录")
                                    enabled: !page.editLocked
                                    onClicked: outputDialog.open()
                                }
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.field("target") === "separation"
                                    ? page.controller.text("仅分离模式不会生成 MIDI、工程或字幕。")
                                    : page.controller.text("工程、音频和字幕写入任务目录；移动工程时请保留相对引用的伴奏。")
                            }
                        }

                        LabelText {
                            theme: page.theme
                            text: page.controller.text("更多工具")
                        }
                        UiButton {
                            objectName: "openLocalSubtitleTool"
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("打开字幕工具…")
                            onClicked: page.subtitleRequested()
                        }
                    }
                }
            }

            Rectangle {
                objectName: "advancedOptionsPanel"
                Layout.fillWidth: true
                Layout.leftMargin: page.contentMargin
                Layout.rightMargin: page.contentMargin
                implicitHeight: advancedColumn.implicitHeight + Math.round(20 * page.theme.scale)
                color: page.theme.surface
                radius: Math.round(10 * page.theme.scale)
                border.width: 1
                border.color: page.theme.line

                ColumnLayout {
                    id: advancedColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(10 * page.theme.scale)
                    spacing: Math.round(10 * page.theme.scale)

                    UiButton {
                        objectName: "advancedToggle"
                        Layout.fillWidth: true
                        theme: page.theme
                        text: page.controller.text("高级处理选项")
                        disclosure: Boolean(page.field("advanced_expanded")) ? 1 : 0
                        Accessible.checkable: true
                        Accessible.checked: disclosure === 1
                        quiet: true
                        selected: Boolean(page.field("advanced_expanded"))
                        leftAligned: true
                        onClicked: page.controller.setPreparationField("advanced_expanded", !Boolean(page.field("advanced_expanded")))
                    }

                    ColumnLayout {
                        id: advancedBody
                        Layout.fillWidth: true
                        visible: Boolean(page.field("advanced_expanded"))
                        spacing: Math.round(10 * page.theme.scale)

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 1
                            color: page.theme.line
                        }

                        HelperText {
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.controller.text("这些选项适合需要固定设备、速度或工程细节时使用。")
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.narrow ? 1 : 3
                            columnSpacing: Math.round(12 * page.theme.scale)
                            rowSpacing: Math.round(8 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("内置分离设备")
                                }

                                UiComboBox {
                                    objectName: "separationDevice"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: "CPU", value: "cpu"},
                                        {label: "WebGPU", value: "webgpu"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("separation_device"))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("separation_device", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("MIDI 提取设备")
                                }

                                UiComboBox {
                                    objectName: "midiDevice"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: "CPU", value: "cpu"},
                                        {label: "WebGPU", value: "webgpu"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("midi_device"))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("midi_device", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("WebGPU 显卡")
                                }

                                UiComboBox {
                                    objectName: "dmlAdapter"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: page.controller.text("自动选择"), value: "auto"},
                                        {label: page.controller.text("适配器") + " 0", value: 0},
                                        {label: page.controller.text("适配器") + " 1", value: 1},
                                        {label: page.controller.text("适配器") + " 2", value: 2},
                                        {label: page.controller.text("适配器") + " 3", value: 3},
                                        {label: page.controller.text("适配器") + " 4", value: 4},
                                        {label: page.controller.text("适配器") + " 5", value: 5},
                                        {label: page.controller.text("适配器") + " 6", value: 6},
                                        {label: page.controller.text("适配器") + " 7", value: 7}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.field("dml_adapter"))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("dml_adapter", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("CPU 推理线程")
                                }

                                UiComboBox {
                                    objectName: "cpuThreads"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: page.controller.text("自动 · 引擎默认"), value: 0},
                                        {label: "2", value: 2},
                                        {label: "4", value: 4},
                                        {label: "8", value: 8},
                                        {label: "16", value: 16}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, Number(page.field("cpu_threads")))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("cpu_threads", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("MIDI 后端")
                                }

                                UiComboBox {
                                    objectName: "midiBackend"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: "Vocal2MIDI v1", value: "v1"},
                                        {label: "Vocal2MIDI v2", value: "v2"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.englishLyrics ? "v2" : page.field("midi_backend"))
                                    enabled: !page.editLocked && !page.englishLyrics
                                    onActivated: page.controller.setPreparationField("midi_backend", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("歌词对齐")
                                }

                                UiComboBox {
                                    objectName: "alignmentEngine"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: "HubertFA · " + page.controller.text("内置"), value: "hfa"},
                                        {label: "TiFA", value: "tifa"}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, page.englishLyrics ? "hfa" : page.field("alignment_engine"))
                                    enabled: !page.editLocked && !page.englishLyrics
                                    onActivated: page.controller.setPreparationField("alignment_engine", currentValue)
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(5 * page.theme.scale)

                                LabelText {
                                    theme: page.theme
                                    text: page.controller.text("提取步数")
                                }

                                UiComboBox {
                                    objectName: "midiSteps"
                                    Layout.fillWidth: true
                                    theme: page.theme
                                    model: [
                                        {label: page.controller.text("8 步"), value: 8},
                                        {label: page.controller.text("16 步"), value: 16},
                                        {label: page.controller.text("32 步"), value: 32}
                                    ]
                                    textRole: "label"
                                    valueRole: "value"
                                    currentIndex: page.indexInModel(model, Number(page.field("midi_steps")))
                                    enabled: !page.editLocked
                                    onActivated: page.controller.setPreparationField("midi_steps", currentValue)
                                }
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(5 * page.theme.scale)

                            LabelText {
                                theme: page.theme
                                text: page.controller.text("固定 BPM")
                            }

                            ThemedTextField {
                                id: bpmEditor
                                objectName: "bpmField"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.field("bpm")
                                placeholderText: page.controller.text("留空使用默认 120 BPM")
                                enabled: !page.editLocked
                                onTextEdited: page.controller.setPreparationField("bpm", text)
                            }

                            HelperText {
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("未填写时不自动测速；留空使用 120 BPM 作为时间基准。")
                            }
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.narrow ? 1 : 2
                            columnSpacing: Math.round(12 * page.theme.scale)
                            rowSpacing: Math.round(3 * page.theme.scale)

                            CheckOption {
                                objectName: "includeInstrumental"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("工程引用伴奏")
                                checked: Boolean(page.field("include_instrumental"))
                                enabled: !page.editLocked
                                onToggled: {
                                    if (checked !== Boolean(page.field("include_instrumental")))
                                        page.controller.setPreparationField("include_instrumental", checked)
                                }
                            }

                            CheckOption {
                                objectName: "exportPitch"
                                Layout.fillWidth: true
                                theme: page.theme
                                text: page.controller.text("写入连续音高线")
                                checked: Boolean(page.field("export_pitch"))
                                enabled: !page.editLocked
                                onToggled: {
                                    if (checked !== Boolean(page.field("export_pitch")))
                                        page.controller.setPreparationField("export_pitch", checked)
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                objectName: "preparationTaskPanel"
                visible: page.taskState !== "idle" && (page.showTaskDetails || page.taskRecoverable)
                Layout.fillWidth: true
                Layout.leftMargin: page.contentMargin
                Layout.rightMargin: page.contentMargin
                Layout.bottomMargin: Math.round(24 * page.theme.scale)
                implicitHeight: taskColumn.implicitHeight + Math.round(28 * page.theme.scale)
                color: page.theme.surface
                radius: Math.round(10 * page.theme.scale)
                border.width: 1
                border.color: page.taskRunning ? page.theme.accent
                    : page.taskFailed ? page.theme.error
                    : page.taskSucceeded ? page.theme.success
                    : page.theme.line

                ColumnLayout {
                    id: taskColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(14 * page.theme.scale)
                    spacing: Math.round(10 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(9 * page.theme.scale)

                        Rectangle {
                            Layout.preferredWidth: Math.round(8 * page.theme.scale)
                            Layout.preferredHeight: Layout.preferredWidth
                            Layout.alignment: Qt.AlignVCenter
                            radius: Layout.preferredWidth / 2
                            color: page.statusColor()
                        }

                        Text {
                            Layout.fillWidth: true
                            text: page.statusTitle()
                            color: page.statusColor()
                            font.pixelSize: Math.round(16 * page.theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }

                        Text {
                            visible: page.taskRunning && page.task.current !== undefined && String(page.task.current).length > 0
                            text: page.task.current !== undefined ? page.controller.text(String(page.task.current)) : ""
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.task && String(page.task.message || "").length > 0
                        text: page.task && page.task.message
                            ? page.controller.text(String(page.task.recovery_title || page.task.message)) : ""
                        color: page.taskFailed ? page.theme.error : page.theme.text
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.task && String(page.task.job || "").length > 0 && !page.taskRunning
                        text: page.task && page.task.job ? page.controller.text("任务目录") + " · " + String(page.task.job) : ""
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: taskPathHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: taskPathHover }
                    }

                    HelperText {
                        Layout.fillWidth: true
                        visible: page.taskState === "idle"
                        theme: page.theme
                        text: page.controller.text("开始后离开准备页不会结束任务；返回时可从同一快照继续查看进度。")
                    }

                    HelperText {
                        Layout.fillWidth: true
                        visible: page.taskFailed && String(page.task.recovery_action || "").length > 0
                        theme: page.theme
                        text: page.controller.text(String(page.task.recovery_message || ""))
                    }

                    Repeater {
                        model: page.task.lyric_notices || []
                        delegate: HelperText {
                            required property var modelData
                            Layout.fillWidth: true
                            theme: page.theme
                            text: page.controller.text(modelData.voice === "lead" ? "主唱"
                                : modelData.voice === "backing" ? "和声" : "人声") + " · "
                                + (modelData.state === "unavailable"
                                    ? page.controller.text("英文歌词未能对齐，已保留 MIDI 音符。")
                                    : page.controller.text("{0} 个片段只有音符，请在对齐报告中核对。")
                                        .replace("{0}", modelData.chunks))
                        }
                    }

                    HelperText {
                        Layout.fillWidth: true
                        visible: Number(page.task.vsqx_pronunciation_reviews) > 0
                        theme: page.theme
                        text: page.controller.text("VSQX 有 {0} 处英文发音需核对。歌词和位置见导出回执。")
                            .replace("{0}", page.task.vsqx_pronunciation_reviews)
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.taskFailed && !!page.task.recovery_title && !!page.task.message
                        text: String(page.task.message || "")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: 8 * page.theme.scale
                        visible: (page.task.stages || []).length > 0
                        Repeater {
                            model: page.task.stages || []
                            delegate: Label {
                                required property var modelData
                                color: modelData.state === "done" ? page.theme.accent : page.theme.muted
                                font.pixelSize: 12 * page.theme.scale
                                text: (modelData.state === "done" ? "✓ " : modelData.key === page.task.current ? "• " : "")
                                      + page.controller.text(modelData.label)
                            }
                        }
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 148 * page.theme.scale
                        visible: String(page.task.log_tail || "").length > 0
                        TextArea {
                            objectName: "preparationLogTail"
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.PlainText
                            color: page.theme.muted
                            text: String(page.task.log_tail || "")
                        }
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.theme.scale)

                        UiButton {
                            objectName: "retryPreparationCpu"
                            visible: page.taskFailed && String(page.task.recovery_action || "") === "cpu"
                            theme: page.theme
                            text: page.controller.text("使用 CPU 重试")
                            enabled: !page.editLocked && Boolean(page.task.can_retry)
                            onClicked: page.controller.retryPreparation(true)
                        }

                        UiButton {
                            objectName: "openComponentsRecovery"
                            visible: page.taskFailed && String(page.task.recovery_action || "") === "components"
                            theme: page.theme
                            text: page.controller.text("检查模型与组件")
                            onClicked: page.controller.navigate("components")
                        }

                        UiButton {
                            theme: page.theme
                            visible: page.taskFailed && page.task.recovery_action === "storage"
                            text: page.controller.text("前往设置与支持")
                            onClicked: page.controller.navigate("updates")
                        }

                        UiButton {
                            theme: page.theme
                            visible: page.taskFailed && page.task.recovery_action === "subtitles"
                            text: page.controller.text("打开字幕工具")
                            onClicked: page.controller.openSubtitleTool()
                        }

                        UiButton {
                            objectName: "refreshPreparationLog"
                            visible: String(page.task.job || "").length > 0
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("读取最近日志")
                            onClicked: page.controller.refreshPreparationLog()
                        }

                        UiButton {
                            theme: page.theme
                            quiet: true
                            visible: page.taskFailed
                            text: page.controller.text("本机诊断")
                            onClicked: page.controller.support.showDiagnostics()
                        }

                        Item {
                            visible: !page.narrow
                            Layout.fillWidth: true
                        }

                        UiButton {
                            objectName: "openComponentsFromPreparation"
                            theme: page.theme
                            text: page.controller.text("模型与组件")
                            quiet: true
                            onClicked: page.controller.navigate("components")
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        id: footer
        objectName: "preparationFooter"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Math.max(Math.round(66 * page.theme.scale), footerRow.implicitHeight + Math.round(18 * page.theme.scale))
        color: page.theme.surface

        Rectangle {
            width: parent.width
            height: 1
            color: page.theme.line
        }

        RowLayout {
            id: footerRow
            anchors.fill: parent
            anchors.leftMargin: page.contentMargin
            anchors.rightMargin: page.contentMargin
            anchors.topMargin: Math.round(8 * page.theme.scale)
            anchors.bottomMargin: Math.round(8 * page.theme.scale)
            spacing: Math.round(8 * page.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: Math.round(4 * page.theme.scale)

                Text {
                    id: footerStatus
                    objectName: "preparationFooterStatus"
                    Layout.fillWidth: true
                    text: page.taskState !== "idle"
                        ? page.statusTitle() + (page.task.message ? " · "
                            + page.controller.text(String(page.task.recovery_title || page.task.message)) : "")
                        : page.inputBlocker || page.controller.text(page.componentCheck.state === "issues"
                            ? "先处理组件问题。" : "素材已选，可以检查或开始。")
                    color: page.taskState !== "idle" ? page.statusColor() : page.theme.text
                    elide: Text.ElideRight
                    font.pixelSize: Math.round(13 * page.theme.scale)
                    font.weight: Font.DemiBold
                    textFormat: Text.PlainText
                    ToolTip.visible: footerStatusHover.hovered && truncated
                    ToolTip.text: text
                    HoverHandler { id: footerStatusHover }
                }

                Text {
                    id: componentStatus
                    objectName: "preparationComponentStatus"
                    Layout.fillWidth: true
                    visible: !page.taskRunning
                    text: page.taskState !== "idle" && page.inputBlocker ? page.inputBlocker : page.checkLine()
                    color: page.componentCheck.state === "issues" || page.componentCheck.state === "error"
                        ? page.theme.attention : page.theme.muted
                    elide: Text.ElideRight
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    textFormat: Text.PlainText
                    ToolTip.visible: componentStatusHover.hovered && truncated
                    ToolTip.text: text
                    HoverHandler { id: componentStatusHover }
                }

                Text {
                    objectName: "preparationStageCount"
                    Layout.fillWidth: true
                    visible: page.taskRunning && page.taskTotal > 0
                    text: page.controller.text("已完成 {0} / {1} 个步骤")
                        .replace("{0}", page.taskCompleted).replace("{1}", page.taskTotal)
                    color: page.theme.muted
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    textFormat: Text.PlainText
                }
            }

            UiButton {
                objectName: "preparationTaskDetails"
                theme: page.theme
                quiet: true
                visible: page.taskState !== "idle"
                text: page.controller.text(page.showTaskDetails ? "收起任务详情" : "任务详情")
                onClicked: {
                    page.showTaskDetails = !page.showTaskDetails
                    if (page.showTaskDetails || page.taskRecoverable)
                        Qt.callLater(() => prepScroll.contentItem.contentY = Math.max(0, prepScroll.contentItem.contentHeight - prepScroll.availableHeight))
                }
            }

            UiButton {
                objectName: "checkPreparationComponents"
                theme: page.theme
                text: page.vwaTr("检查组件…")
                leadingGlyph: "check"
                leadingIconSize: 14
                enabled: !page.editLocked
                visible: !page.taskRunning
                onClicked: sourceTools.openPane("check")
            }

            UiButton {
                objectName: "startPreparation"
                theme: page.theme
                text: page.taskState === "idle" ? page.controller.text("开始准备")
                    : page.controller.text("用当前设置新建任务")
                leadingGlyph: "play"
                leadingIconSize: 14
                primary: !page.taskHasResult && !(page.taskRecoverable && page.task.can_retry)
                visible: !page.taskRunning
                enabled: !page.editLocked && !page.inputBlocker && page.componentCheck.state !== "issues"
                onClicked: page.controller.startPreparation()
            }

            UiButton {
                objectName: "cancelPreparation"
                theme: page.theme
                primary: true
                text: page.taskState === "cancelling"
                    ? page.controller.text("正在取消…")
                    : page.controller.text("取消任务")
                visible: page.taskRunning
                enabled: page.taskState === "running" && page.controller.preparationBusy
                onClicked: page.controller.cancelPreparation()
            }

            UiButton {
                objectName: "retryPreparation"
                visible: page.taskRecoverable && Boolean(page.task.can_retry)
                theme: page.theme
                primary: !page.taskHasResult
                text: page.taskPartial ? page.controller.text("继续任务") : page.controller.text("重试")
                enabled: !page.editLocked
                onClicked: page.controller.retryPreparation(false)
            }

            UiButton {
                objectName: "preparationChooseWorkshopVocal"
                theme: page.theme
                text: page.controller.text("为工坊选人声")
                quiet: true
                visible: page.taskHasResult
                enabled: !page.controller.workBusy
                onClicked: page.controller.chooseWorkshopVocal(true)
            }
            UiButton {
                objectName: "viewPreparationResult"
                theme: page.theme
                text: page.controller.text("查看准备结果")
                primary: true
                visible: page.taskHasResult
                enabled: !page.controller.workBusy
                onClicked: page.controller.viewPreparationResult()
            }
        }
    }
}
