import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "settingsPage"
    required property QtObject theme
    required property QtObject controller

    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    focus: true

    readonly property var values: controller.settings || ({})
    readonly property var updateInfo: controller.startup || ({})
    readonly property var diagnostics: controller.diagnostics || ({})
    readonly property bool narrow: availableWidth < (theme.scale > 1.2 ? 840 : 680)
    readonly property int pageMargin: Math.round((narrow ? 16 : 24) * theme.scale)
    readonly property string communityNumber: controller.qqGroup || "490619616"
    readonly property string currentUpdateState: String(updateInfo.update_state || "idle").toLowerCase()
    readonly property bool workBusy: !!controller.workBusy
    readonly property bool downloading: currentUpdateState === "downloading"
    readonly property bool applying: currentUpdateState === "applying"
    property bool suppressDiagnosticsClose: false

    function tr(source) {
        return controller && typeof controller.text === "function" ? controller.text(source) : source
    }

    function setting(name, fallback) {
        const current = page.values ? page.values[name] : undefined
        return current === undefined || current === null ? fallback : current
    }

    function changeSetting(name, value) {
        if (controller && typeof controller.setSetting === "function")
            controller.setSetting(name, value)
    }

    function indexOfValue(options, value) {
        if (!options)
            return 0
        for (let index = 0; index < options.length; ++index) {
            if (String(options[index].value) === String(value))
                return index
        }
        return 0
    }

    function chooseDirectory(url) {
        if (!url)
            return
        if (controller && typeof controller.chooseOutput === "function")
            controller.chooseOutput(url)
        else
            changeSetting("output_directory", url.toLocalFile())
    }

    function delayOptions() {
        return [
            { label: tr("下次启动"), value: 0 },
            { label: tr("一小时后"), value: 3600 },
            { label: tr("一天后"), value: 86400 },
            { label: tr("七天后"), value: 604800 },
            { label: tr("三十天后"), value: 2592000 },
            { label: tr("忽略此版本"), value: -1 }
        ]
    }

    function updateStateLabel(state) {
        switch (String(state || "idle").toLowerCase()) {
        case "checking": return tr("正在检查更新")
        case "available":
        case "update_available": return tr("发现新版本")
        case "downloading": return tr("正在下载更新")
        case "ready":
        case "downloaded": return tr("更新已下载")
        case "applying": return tr("正在重启并安装")
        case "current":
        case "up_to_date": return tr("已是最新版本")
        case "failed":
        case "error": return tr("更新检查失败")
        case "cancelled": return tr("更新已暂缓")
        default: return tr("尚未检查更新")
        }
    }

    function updateStateColor(state) {
        switch (String(state || "idle").toLowerCase()) {
        case "available":
        case "update_available":
        case "ready":
        case "downloaded": return theme.accent
        case "current":
        case "up_to_date": return theme.success
        case "failed":
        case "error": return theme.error
        default: return theme.muted
        }
    }

    function updatePrimaryLabel() {
        if (updateInfo.apply_ready)
            return tr("重启并安装")
        if (downloading || applying)
            return updateStateLabel(currentUpdateState)
        if (updateInfo.can_update)
            return tr("立即更新")
        if (String(updateInfo.url || "").length > 0)
            return tr("打开发布页")
        return tr("立即更新")
    }

    function updatePrimaryAction() {
        if (updateInfo.apply_ready)
            controller.applyUpdate()
        else if (updateInfo.can_update)
            controller.downloadUpdate()
        else if (String(updateInfo.url || "").length > 0)
            controller.openRelease()
        else
            controller.checkForUpdates()
    }

    function updateCanDefer() {
        return String(updateInfo.version || "").length > 0 && !downloading && !applying
    }

    function formatVersion(value) {
        const version = String(value || "").trim()
        if (version.length === 0)
            return tr("未知")
        return version.charAt(0).toLowerCase() === "v" ? version : "v" + version
    }

    function findDescendant(item, objectName) {
        if (!item)
            return null
        if (String(item.objectName || "") === objectName)
            return item
        const children = item.children || []
        for (let index = 0; index < children.length; ++index) {
            const found = findDescendant(children[index], objectName)
            if (found)
                return found
        }
        return null
    }

    function syncDiagnostics() {
        if (diagnostics.open && !diagnosticsDialog.opened)
            diagnosticsDialog.open()
        else if (!diagnostics.open && diagnosticsDialog.opened) {
            suppressDiagnosticsClose = true
            diagnosticsDialog.close()
            suppressDiagnosticsClose = false
        }
    }

    FolderDialog {
        id: outputDialog
        title: page.tr("选择任务输出目录")
        onAccepted: page.chooseDirectory(selectedFolder)
    }

    Dialog {
        id: diagnosticsDialog
        objectName: "diagnosticsDialog"
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: 0
        x: parent ? Math.max(Math.round(12 * page.theme.scale), (parent.width - width) / 2) : 12
        y: parent ? Math.max(Math.round(12 * page.theme.scale), (parent.height - height) / 2) : 12
        width: Math.min(parent ? parent.width - Math.round(24 * page.theme.scale) : Math.round(680 * page.theme.scale),
                        Math.round(680 * page.theme.scale))
        height: Math.min(parent ? parent.height - Math.round(24 * page.theme.scale) : Math.round(560 * page.theme.scale),
                         Math.round(560 * page.theme.scale))

        Overlay.modal: Rectangle {
            color: Qt.rgba(page.theme.base.r, page.theme.base.g, page.theme.base.b, 0.86)
        }
        background: Rectangle {
            color: page.theme.surface
            radius: Math.round(10 * page.theme.scale)
            border.color: page.theme.line
        }
        contentItem: ColumnLayout {
            spacing: 0
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: diagnosticHeader.implicitHeight + Math.round(24 * page.theme.scale)
                color: page.theme.raised
                radius: Math.round(10 * page.theme.scale)
                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: parent.radius
                    color: parent.color
                }
                ColumnLayout {
                    id: diagnosticHeader
                    anchors.fill: parent
                    anchors.leftMargin: Math.round(18 * page.theme.scale)
                    anchors.rightMargin: Math.round(18 * page.theme.scale)
                    spacing: 2
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("本机诊断摘要")
                        color: page.theme.text
                        font.pixelSize: Math.round(18 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("仅显示控制器提供的摘要，不读取私人日志正文或用户文件。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                    }
                }
            }
            ScrollView {
                id: diagnosticsScroll
                objectName: "diagnosticsScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: Math.round(18 * page.theme.scale)
                Layout.rightMargin: Math.round(18 * page.theme.scale)
                Layout.topMargin: Math.round(12 * page.theme.scale)
                Layout.bottomMargin: Math.round(8 * page.theme.scale)
                contentWidth: availableWidth
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                background: Rectangle {
                    color: page.theme.base
                    radius: Math.round(7 * page.theme.scale)
                    border.color: page.theme.line
                }
                TextArea {
                    objectName: "diagnosticsText"
                    width: diagnosticsScroll.availableWidth
                    text: String(page.diagnostics.text || "").length > 0
                        ? String(page.diagnostics.text)
                        : page.tr("诊断摘要暂不可用。")
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    wrapMode: TextEdit.WrapAnywhere
                    textFormat: TextEdit.PlainText
                    color: page.theme.text
                    selectionColor: page.theme.accent
                    selectedTextColor: page.theme.base
                    font.family: "Consolas"
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    leftPadding: Math.round(10 * page.theme.scale)
                    rightPadding: Math.round(10 * page.theme.scale)
                    topPadding: Math.round(9 * page.theme.scale)
                    bottomPadding: Math.round(9 * page.theme.scale)
                    background: Rectangle { color: "transparent" }
                    Accessible.name: page.tr("本机诊断摘要正文")
                }
            }
            Flow {
                Layout.fillWidth: true
                Layout.leftMargin: Math.round(18 * page.theme.scale)
                Layout.rightMargin: Math.round(18 * page.theme.scale)
                Layout.bottomMargin: Math.round(14 * page.theme.scale)
                spacing: Math.round(7 * page.theme.scale)
                UiButton {
                    objectName: "copyDiagnostics"
                    theme: page.theme
                    text: page.tr("复制摘要")
                    enabled: String(page.diagnostics.text || "").length > 0
                    onClicked: page.controller.copyDiagnostics()
                }
                UiButton {
                    objectName: "diagnosticsFeedback"
                    theme: page.theme
                    quiet: true
                    text: page.tr("GitHub 反馈")
                    onClicked: page.controller.openFeedback()
                }
                UiButton {
                    objectName: "closeDiagnostics"
                    theme: page.theme
                    quiet: true
                    text: page.tr("关闭")
                    onClicked: page.controller.closeDiagnostics()
                }
            }
        }
        onOpened: Qt.callLater(function() {
            const target = page.findDescendant(diagnosticsDialog.contentItem, "closeDiagnostics")
            if (target)
                target.forceActiveFocus()
        })
        onClosed: {
            if (!page.suppressDiagnosticsClose && page.diagnostics.open)
                page.controller.closeDiagnostics()
        }
        onRejected: {
            if (!page.suppressDiagnosticsClose)
                page.controller.closeDiagnostics()
        }
    }


    Connections {
        target: page.controller
        function onDiagnosticsChanged() { page.syncDiagnostics() }
    }
    Component.onCompleted: syncDiagnostics()
    onDiagnosticsChanged: syncDiagnostics()

    ColumnLayout {
        width: page.availableWidth
        spacing: Math.round(14 * page.theme.scale)

        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: page.pageMargin
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            spacing: 5
            Text {
                Layout.fillWidth: true
                text: page.tr("设置与支持")
                color: page.theme.text
                font.pixelSize: Math.round(25 * page.theme.scale)
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                text: page.tr("管理外观、下载、程序更新与社区支持。")
                color: page.theme.muted
                font.pixelSize: Math.round(13 * page.theme.scale)
                wrapMode: Text.Wrap
            }
        }
        Rectangle {
            objectName: "appearanceSection"
            Layout.fillWidth: true
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            Layout.preferredHeight: appearanceContent.implicitHeight + Math.round(30 * page.theme.scale)
            color: page.theme.surface
            radius: Math.round(9 * page.theme.scale)
            border.color: page.theme.line

            ColumnLayout {
                id: appearanceContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(15 * page.theme.scale)
                spacing: Math.round(13 * page.theme.scale)

                Text {
                    Layout.fillWidth: true
                    text: page.tr("外观与语言")
                    color: page.theme.text
                    font.pixelSize: Math.round(18 * page.theme.scale)
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: page.tr("界面语言在下次启动时完整生效；主题、材质、缩放与动效会随设置更新。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    wrapMode: Text.Wrap
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: page.narrow ? 1 : 2
                    columnSpacing: Math.round(24 * page.theme.scale)
                    rowSpacing: Math.round(13 * page.theme.scale)

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            text: page.tr("界面主题")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.theme.scale)
                            ChoiceChip {
                                objectName: "settingsThemeLight"
                                tokens: page.theme
                                text: page.tr("浅色")
                                selected: String(page.setting("theme", "dark")) === "light"
                                onClicked: page.changeSetting("theme", "light")
                            }
                            ChoiceChip {
                                objectName: "settingsThemeDark"
                                tokens: page.theme
                                text: page.tr("深色")
                                selected: String(page.setting("theme", "dark")) === "dark"
                                onClicked: page.changeSetting("theme", "dark")
                            }
                            ChoiceChip {
                                objectName: "settingsThemeSystem"
                                tokens: page.theme
                                text: page.tr("跟随系统")
                                selected: String(page.setting("theme", "dark")) === "system"
                                onClicked: page.changeSetting("theme", "system")
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            text: page.tr("界面材质")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.theme.scale)
                            ChoiceChip {
                                objectName: "settingsMaterialSolid"
                                tokens: page.theme
                                text: page.tr("纯色阅读")
                                selected: String(page.setting("material", "solid")) === "solid"
                                onClicked: page.changeSetting("material", "solid")
                            }
                            ChoiceChip {
                                objectName: "settingsMaterialGlass"
                                tokens: page.theme
                                text: page.tr("月光玻璃")
                                selected: String(page.setting("material", "solid")) === "glass"
                                onClicked: page.changeSetting("material", "glass")
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            text: page.tr("应用缩放")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.theme.scale)
                            Repeater {
                                model: [100, 115, 130]
                                ChoiceChip {
                                    required property int modelData
                                    objectName: "settingsScale" + modelData
                                    tokens: page.theme
                                    text: modelData + "%"
                                    selected: Number(page.setting("ui_scale", 100)) === modelData
                                    onClicked: page.changeSetting("ui_scale", modelData)
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            text: page.tr("界面语言")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: Math.round(6 * page.theme.scale)
                            ChoiceChip {
                                objectName: "settingsLanguageZh"
                                tokens: page.theme
                                text: "简体中文"
                                selected: String(page.setting("ui_language", "zh-CN")) === "zh-CN"
                                onClicked: page.changeSetting("ui_language", "zh-CN")
                            }
                            ChoiceChip {
                                objectName: "settingsLanguageEn"
                                tokens: page.theme
                                text: "English"
                                selected: String(page.setting("ui_language", "zh-CN")) === "en"
                                onClicked: page.changeSetting("ui_language", "en")
                            }
                            ChoiceChip {
                                objectName: "settingsLanguageJa"
                                tokens: page.theme
                                text: "日本語"
                                selected: String(page.setting("ui_language", "zh-CN")) === "ja"
                                onClicked: page.changeSetting("ui_language", "ja")
                            }
                        }
                    }
                }

                MotionChoice {
                    objectName: "settingsReduceMotion"
                    Layout.fillWidth: true
                    tokens: page.theme
                    text: page.tr("减少动效")
                    detail: page.tr("关闭非必要过渡和持续动画，保留操作反馈。")
                    selected: page.setting("reduce_motion", false) === true
                    onClicked: page.changeSetting("reduce_motion", !(page.setting("reduce_motion", false) === true))
                }
            }
        }
        Rectangle {
            objectName: "downloadSection"
            Layout.fillWidth: true
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            Layout.preferredHeight: downloadContent.implicitHeight + Math.round(30 * page.theme.scale)
            color: page.theme.surface
            radius: Math.round(9 * page.theme.scale)
            border.color: page.theme.line

            ColumnLayout {
                id: downloadContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(15 * page.theme.scale)
                spacing: Math.round(12 * page.theme.scale)

                Text {
                    Layout.fillWidth: true
                    text: page.tr("下载与存储")
                    color: page.theme.text
                    font.pixelSize: Math.round(18 * page.theme.scale)
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: page.tr("下载来源同时用于组件和程序更新。指定镜像只重写对应来源的公开文件，安装前仍会校验完整性。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    wrapMode: Text.Wrap
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: page.narrow ? 1 : 2
                    columnSpacing: Math.round(20 * page.theme.scale)
                    rowSpacing: Math.round(12 * page.theme.scale)

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            text: page.tr("下载方式")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        UiComboBox {
                            id: sourceCombo
                            objectName: "downloadSourceChoice"
                            Layout.fillWidth: true
                            theme: page.theme
                            model: page.controller.downloadSources || []
                            currentIndex: page.indexOfValue(model, page.setting("download_source", "auto"))
                            onActivated: index => page.changeSetting("download_source", currentValue)
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            text: page.tr("并行下载数")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                        }
                        UiComboBox {
                            id: workersCombo
                            objectName: "downloadWorkersChoice"
                            Layout.fillWidth: true
                            theme: page.theme
                            model: [
                                { label: "1", value: 1 },
                                { label: "2", value: 2 },
                                { label: "3", value: 3 },
                                { label: "4", value: 4 },
                                { label: "5", value: 5 },
                                { label: "6", value: 6 },
                                { label: "7", value: 7 },
                                { label: "8", value: 8 }
                            ]
                            currentIndex: page.indexOfValue(model, page.setting("download_workers", 4))
                            onActivated: index => page.changeSetting("download_workers", Number(currentValue))
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: page.theme.line
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                        text: page.tr("任务输出目录")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.theme.scale)
                        Text {
                            id: outputPath
                            objectName: "outputDirectory"
                            Layout.fillWidth: true
                            text: String(page.setting("output_directory", "")).length > 0
                                ? String(page.setting("output_directory", ""))
                                : page.tr("使用系统默认目录")
                            color: String(page.setting("output_directory", "")).length > 0 ? page.theme.text : page.theme.muted
                            font.pixelSize: Math.round(13 * page.theme.scale)
                            elide: Text.ElideMiddle
                            textFormat: Text.PlainText
                            ToolTip.visible: outputHover.hovered && truncated
                            ToolTip.text: String(page.setting("output_directory", ""))
                            HoverHandler { id: outputHover }
                        }
                        UiButton {
                            objectName: "chooseOutputDirectory"
                            theme: page.theme
                            text: page.tr("选择目录")
                            onClicked: outputDialog.open()
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("用于后续准备、调音和导出任务的共同目录。单个任务仍可保留自己的结果位置。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
        Rectangle {
            objectName: "updateSection"
            Layout.fillWidth: true
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            Layout.preferredHeight: updateContent.implicitHeight + Math.round(30 * page.theme.scale)
            color: page.theme.surface
            radius: Math.round(9 * page.theme.scale)
            border.color: page.theme.line

            ColumnLayout {
                id: updateContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(15 * page.theme.scale)
                spacing: Math.round(11 * page.theme.scale)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(9 * page.theme.scale)
                    Text {
                        Layout.fillWidth: true
                        text: page.tr("程序更新")
                        color: page.theme.text
                        font.pixelSize: Math.round(18 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }
                    Rectangle {
                        width: Math.round(8 * page.theme.scale)
                        height: width
                        radius: width / 2
                        color: page.updateStateColor(page.currentUpdateState)
                    }
                    Text {
                        objectName: "updateState"
                        text: page.updateStateLabel(page.currentUpdateState)
                        color: page.updateStateColor(page.currentUpdateState)
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: page.tr("发现新版本会先展示变更说明。你可以立即处理、稍后再看，或忽略这个版本。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    wrapMode: Text.Wrap
                }

                MotionChoice {
                    objectName: "settingsStartupUpdate"
                    Layout.fillWidth: true
                    tokens: page.theme
                    text: page.tr("启动时检查更新")
                    detail: page.tr("在后台检查，有新版本时显示更新内容。")
                    selected: page.setting("check_for_updates", true) === true
                    onClicked: page.changeSetting("check_for_updates", !selected)
                }
                MotionChoice {
                    objectName: "settingsAutoRestart"
                    Layout.fillWidth: true
                    visible: page.controller.canApplyProgramUpdates
                    tokens: page.theme
                    text: page.tr("下载完成后自动重启安装")
                    detail: page.tr("仅在你已确认下载时生效。")
                    selected: page.setting("auto_restart_updates", true) === true
                    onClicked: page.changeSetting("auto_restart_updates", !selected)
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(14 * page.theme.scale)
                    Text {
                        text: page.tr("当前版本") + "  " + page.formatVersion(page.controller.currentVersion)
                        color: page.theme.text
                        font.pixelSize: Math.round(13 * page.theme.scale)
                    }
                    Text {
                        visible: String(page.updateInfo.version || "").length > 0
                        text: page.tr("可用版本") + "  " + page.formatVersion(page.updateInfo.version)
                        color: page.theme.accent
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }
                }

                Text {
                    objectName: "updateMessage"
                    Layout.fillWidth: true
                    visible: String(page.updateInfo.message || "").length > 0
                    text: String(page.updateInfo.message || "")
                    color: page.currentUpdateState === "failed" || page.currentUpdateState === "error"
                        ? page.theme.error : page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                }

                ProgressBar {
                    id: updateProgress
                    objectName: "updateProgress"
                    Layout.fillWidth: true
                    visible: page.downloading || Number(page.updateInfo.total || 0) > 0
                    indeterminate: page.downloading && Number(page.updateInfo.total || 0) <= 0 && !page.theme.reduceMotion
                    value: Number(page.updateInfo.total || 0) > 0
                        ? Math.max(0, Math.min(1, Number(page.updateInfo.done || 0) / Number(page.updateInfo.total)))
                        : 0
                    background: Rectangle {
                        implicitHeight: Math.max(5, Math.round(6 * page.theme.scale))
                        radius: height / 2
                        color: page.theme.base
                    }
                    contentItem: Item {
                        Rectangle {
                            width: parent.width * Math.max(0, Math.min(1, updateProgress.visualPosition))
                            height: parent.height
                            radius: height / 2
                            color: page.currentUpdateState === "failed" || page.currentUpdateState === "error"
                                ? page.theme.error : page.theme.accent
                        }
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(7 * page.theme.scale)
                    UiButton {
                        objectName: "updateCheck"
                        theme: page.theme
                        text: page.tr("检查更新")
                        enabled: !page.downloading && !page.applying
                        onClicked: page.controller.checkForUpdates()
                    }
                    UiButton {
                        objectName: "updatePrimary"
                        theme: page.theme
                        primary: true
                        text: page.updatePrimaryLabel()
                        enabled: !page.downloading && !page.applying
                            && !(page.workBusy && page.updateInfo.apply_ready)
                        onClicked: page.updatePrimaryAction()
                    }
                    UiButton {
                        objectName: "updateCancel"
                        theme: page.theme
                        quiet: true
                        visible: page.downloading && page.updateInfo.can_cancel
                        text: page.tr("取消下载")
                        onClicked: page.controller.cancelUpdate()
                    }
                    UiButton {
                        objectName: "updateResetReminder"
                        theme: page.theme
                        quiet: true
                        text: page.tr("恢复已忽略提醒")
                        onClicked: page.controller.resetUpdateReminder()
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: page.updateCanDefer()
                    spacing: 5
                    Text {
                        text: page.tr("延后提醒")
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.theme.scale)
                        UiComboBox {
                            id: settingsDelay
                            objectName: "updateDeferDelay"
                            Layout.fillWidth: true
                            Layout.maximumWidth: Math.round(220 * page.theme.scale)
                            theme: page.theme
                            model: page.delayOptions()
                            currentIndex: 0
                        }
                        UiButton {
                            objectName: "updateDefer"
                            theme: page.theme
                            quiet: true
                            text: page.tr("按所选时长延后")
                            onClicked: page.controller.deferUpdate(Number(settingsDelay.currentValue))
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: page.workBusy && page.updateInfo.apply_ready
                    text: page.tr("当前任务运行中。更新包会保留，任务结束后再重启安装。")
                    color: page.theme.accent
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    wrapMode: Text.Wrap
                }
            }
        }
        Rectangle {
            objectName: "supportSection"
            Layout.fillWidth: true
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            Layout.bottomMargin: page.pageMargin
            Layout.preferredHeight: supportContent.implicitHeight + Math.round(30 * page.theme.scale)
            color: page.theme.surface
            radius: Math.round(9 * page.theme.scale)
            border.color: page.theme.line

            ColumnLayout {
                id: supportContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Math.round(15 * page.theme.scale)
                spacing: Math.round(10 * page.theme.scale)

                Text {
                    Layout.fillWidth: true
                    text: page.tr("帮助与社区")
                    color: page.theme.text
                    font.pixelSize: Math.round(18 * page.theme.scale)
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: page.tr("遇到安装、组件或工作流问题时，可以通过 GitHub 提交可复现反馈；日常交流和版本消息在用户群。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(8 * page.theme.scale)
                    Text {
                        objectName: "qqGroup"
                        text: page.tr("QQ 群") + "  " + page.communityNumber
                        color: page.theme.text
                        font.pixelSize: Math.round(14 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }
                    Item { Layout.fillWidth: true }
                    UiButton {
                        objectName: "copyQQGroup"
                        theme: page.theme
                        text: page.tr("复制群号")
                        onClicked: page.controller.copyCommunity()
                    }
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(7 * page.theme.scale)
                    UiButton {
                        objectName: "openFeedback"
                        theme: page.theme
                        text: page.tr("GitHub 反馈")
                        onClicked: page.controller.openFeedback()
                    }
                    UiButton {
                        objectName: "showDiagnostics"
                        theme: page.theme
                        text: page.tr("本机诊断")
                        onClicked: page.controller.showDiagnostics()
                    }
                    UiButton {
                        objectName: "replayTutorial"
                        theme: page.theme
                        quiet: true
                        text: page.tr("重新查看教程")
                        onClicked: page.controller.showTutorial()
                    }
                }
            }
        }
    }

    component ChoiceChip: Button {
        id: chip
        required property QtObject tokens
        property bool selected: false

        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        checkable: false
        font.pixelSize: Math.round(12 * tokens.scale)
        font.weight: selected ? Font.DemiBold : Font.Normal
        leftPadding: Math.round(10 * tokens.scale)
        rightPadding: Math.round(10 * tokens.scale)
        topPadding: Math.round(6 * tokens.scale)
        bottomPadding: Math.round(6 * tokens.scale)
        implicitHeight: Math.max(Math.round(32 * tokens.scale), implicitContentHeight + Math.round(12 * tokens.scale))
        Accessible.name: text
        Accessible.role: Accessible.RadioButton
        Accessible.checked: selected

        background: Rectangle {
            radius: Math.round(16 * tokens.scale)
            color: chip.selected
                ? tokens.accent
                : chip.hovered ? Qt.lighter(tokens.surface, 1.12) : tokens.base
            border.width: 1
            border.color: chip.visualFocus || chip.selected ? tokens.accent : tokens.line
            Behavior on color {
                enabled: !tokens.reduceMotion && !chip.down
                ColorAnimation { duration: 90 }
            }
        }
        contentItem: Text {
            text: chip.text
            font: chip.font
            color: chip.selected ? tokens.onAccent : tokens.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            textFormat: Text.PlainText
        }
    }

    component MotionChoice: Button {
        id: motion
        required property QtObject tokens
        property bool selected: false
        property string detail: ""

        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        leftPadding: Math.round(11 * tokens.scale)
        rightPadding: Math.round(11 * tokens.scale)
        topPadding: Math.round(9 * tokens.scale)
        bottomPadding: Math.round(9 * tokens.scale)
        implicitHeight: Math.max(Math.round(46 * tokens.scale), contentRow.implicitHeight + Math.round(18 * tokens.scale))
        Accessible.name: text
        Accessible.role: Accessible.CheckBox
        Accessible.checked: selected

        background: Rectangle {
            radius: Math.round(7 * tokens.scale)
            color: motion.hovered ? Qt.lighter(tokens.base, 1.08) : tokens.base
            border.color: motion.visualFocus ? tokens.accent : tokens.line
        }
        contentItem: RowLayout {
            id: contentRow
            spacing: Math.round(10 * tokens.scale)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    Layout.fillWidth: true
                    text: motion.text
                    color: tokens.text
                    font.pixelSize: Math.round(13 * tokens.scale)
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: motion.detail
                    color: tokens.muted
                    font.pixelSize: Math.round(11 * tokens.scale)
                    wrapMode: Text.Wrap
                }
            }
            Rectangle {
                width: Math.round(34 * tokens.scale)
                height: Math.round(19 * tokens.scale)
                radius: height / 2
                color: motion.selected ? tokens.accent : tokens.line
                Rectangle {
                    width: Math.round(15 * tokens.scale)
                    height: width
                    radius: width / 2
                    y: Math.round(2 * tokens.scale)
                    x: motion.selected ? parent.width - width - Math.round(2 * tokens.scale) : Math.round(2 * tokens.scale)
                    color: motion.selected ? tokens.base : tokens.muted
                    Behavior on x {
                        enabled: !tokens.reduceMotion
                        NumberAnimation { duration: 110; easing.type: Easing.OutCubic }
                    }
                }
            }
        }
    }

}
