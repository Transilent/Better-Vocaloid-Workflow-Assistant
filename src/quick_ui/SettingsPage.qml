import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Rectangle {
    id: page
    function vwaTr(message) {
        return typeof quickTranslation !== "undefined" && quickTranslation ? quickTranslation.text(message) : message
    }
    objectName: "settingsPage"
    required property QtObject theme
    required property QtObject controller

    clip: true
    color: theme.base
    focus: true

    readonly property var values: controller.settings || ({})
    readonly property var updateInfo: controller.startup || ({})
    readonly property var storageView: controller.storage || ({})
    readonly property var presetView: controller.presetLibrary || ({})
    readonly property bool narrow: width < (theme.scale > 1.2 ? 840 : 680)
    readonly property bool showCategoryRail: width >= (theme.scale > 1.2 ? 960 : 820)
    readonly property int categoryRailWidth: Math.round(184 * theme.scale)
    readonly property bool storageSideBySide: showCategoryRail
        && width >= Math.round(1480 * theme.scale)
    readonly property string supportLogGuideUrl: "https://github.com/Transilent/Better-Vocaloid-Workflow-Assistant/blob/main/.github/SUPPORT.md"
    readonly property var categoryIds: ["appearance", "update", "storage", "presets", "support", "about"]
    property string settingsCategory: "appearance"
    readonly property int pageMargin: Math.round((narrow ? 16 : 24) * theme.scale)
    readonly property string communityNumber: controller.qqGroup || "490619616"
    readonly property string currentUpdateState: String(updateInfo.update_state || "idle").toLowerCase()
    readonly property bool workBusy: !!controller.workBusy
    readonly property bool downloading: currentUpdateState === "downloading"
    readonly property bool applying: currentUpdateState === "applying"
    readonly property bool restartPending: !!updateInfo.restart_pending
    readonly property string operationBlockerText: (controller.operationBlockers || []).join("、")
    property var cacheSelection: ({})
    property int cacheGeneration: -1
    property string presetQuery: ""
    property string presetType: "all"
    property var selectedPreset: ({})
    property var presetExportEntry: ({})
    property var presetRemoveEntry: ({})
    property var presetTextEntry: ({})
    property string presetTextMode: ""
    readonly property var filteredPresets: {
        var query = presetQuery.trim().toLowerCase()
        var wanted = presetType
        var rows = (presetView.entries || []).filter(function(entry) {
            if (wanted !== "all" && String(entry.type || "") !== wanted)
                return false
            if (!query.length)
                return true
            return (String(entry.name || "") + " " + String(entry.id || "") + " "
                    + String(entry.version || "")).toLowerCase().indexOf(query) >= 0
        })
        rows.sort(function(left, right) {
            if (!!left.favorite !== !!right.favorite)
                return left.favorite ? -1 : 1
            var typeOrder = String(left.type || "").localeCompare(String(right.type || ""))
            if (typeOrder !== 0)
                return typeOrder
            return String(left.name || "").localeCompare(String(right.name || ""))
        })
        return rows
    }

    function tr(source) {
        return controller && typeof controller.text === "function" ? controller.text(source) : source
    }

    function categoryTitle(category) {
        switch (String(category || "")) {
        case "appearance": return tr("外观与交互")
        case "update": return tr("更新")
        case "storage": return tr("下载与存储")
        case "presets": return tr("预设库")
        case "support": return tr("支持与日志")
        case "about": return tr("关于")
        default: return String(category || "")
        }
    }

    function categoryIcon(category) {
        switch (String(category || "")) {
        case "appearance": return "palette"
        case "update": return "refresh"
        case "storage": return "storage"
        case "presets": return "layers"
        case "support": return "help_circle"
        case "about": return "info_circle"
        default: return ""
        }
    }

    function selectCategory(category) {
        settingsCategory = String(category || "appearance")
        if (settingsFlick) {
            settingsFlick.cancelFlick()
            settingsFlick.returnToBounds()
            settingsFlick.contentY = 0
        }
        Qt.callLater(function() {
            if (settingsFlick && settingsFlick.visible)
                settingsFlick.forceActiveFocus()
        })
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

    function typeLabel(type) {
        switch (String(type || "")) {
        case "preparation": return tr("准备")
        case "mixdown": return tr("简易混音")
        case "tuning": return tr("调音")
        case "style_recipe": return tr("风格配方")
        case "voicebank_calibration": return tr("声库校准")
        default: return String(type || tr("未知类型"))
        }
    }

    function presetKey(entry) {
        if (!entry)
            return ""
        return String(entry.id || "") + "|" + String(entry.version || "")
    }

    function isPresetSelected(entry) {
        return presetKey(entry).length > 0 && presetKey(entry) === presetKey(selectedPreset)
    }

    function selectPreset(entry) {
        selectedPreset = entry && typeof entry === "object" ? entry : ({})
        var key = presetKey(selectedPreset)
        if (key.length > 0 && controller && typeof controller.inspectPreset === "function")
            controller.inspectPreset(selectedPreset.id, selectedPreset.version)
    }

    function syncPresetSelection() {
        var preferred = presetKey(selectedPreset)
        if (!preferred.length)
            preferred = String(presetView.last_entry_key || "")
        var entries = presetView.entries || []
        for (var index = 0; index < entries.length; ++index) {
            if (presetKey(entries[index]) === preferred) {
                selectedPreset = entries[index]
                if (!presetView.inspect_busy && String(presetView.inspect_key || "") !== preferred
                        && controller && typeof controller.inspectPreset === "function")
                    controller.inspectPreset(selectedPreset.id, selectedPreset.version)
                return
            }
        }
        selectedPreset = ({})
    }

    function toggleCache(path, selected) {
        var next = Object.assign(({}), cacheSelection)
        var key = String(path || "")
        if (!key.length)
            return
        if (selected)
            next[key] = true
        else
            delete next[key]
        cacheSelection = next
    }

    function cachePathSelected(path) {
        return !!cacheSelection[String(path || "")]
    }

    function selectedCacheRows() {
        var rows = storageView.rows || []
        return rows.filter(function(row) {
            return !row.protected && cachePathSelected(row.path)
        })
    }

    function selectedCachePaths() {
        return selectedCacheRows().map(function(row) { return String(row.path) })
    }

    function selectedCacheBytes() {
        var rows = selectedCacheRows()
        var total = 0
        for (var index = 0; index < rows.length; ++index)
            total += Number(rows[index].bytes || 0)
        return total
    }

    function sizeLabel(bytes) {
        var value = Number(bytes || 0)
        var units = ["B", "KiB", "MiB", "GiB"]
        var unit = 0
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024
            unit += 1
        }
        return value.toFixed(unit === 0 ? 0 : 2) + " " + units[unit]
    }

    function openPresetText(mode) {
        if (!selectedPreset || !Object.keys(selectedPreset).length)
            return
        presetTextMode = mode
        presetTextEntry = selectedPreset
        if (mode === "copy")
            presetNameField.text = String(selectedPreset.name || "") + tr(" 副本")
        else
            presetNameField.text = String(selectedPreset.alias || "")
        presetTextDialog.open()
        Qt.callLater(function() {
            presetNameField.forceActiveFocus()
            presetNameField.selectAll()
        })
    }

    function confirmPresetText() {
        var value = presetNameField.text.trim()
        if (!presetTextEntry || !presetKey(presetTextEntry).length)
            return
        if (presetTextMode === "copy") {
            if (!value.length)
                return
            if (controller.copyPreset(presetTextEntry.id, presetTextEntry.version, value))
                presetTextDialog.close()
        } else if (controller.renamePreset(presetTextEntry.id, presetTextEntry.version, value)) {
            presetTextDialog.close()
        }
    }

    function confirmPresetImport() {
        var preview = presetView.import_preview || ({})
        if (!preview.path)
            return
        if (controller.importPreset(!!preview.copy_required || presetImportCopy.checked))
            presetImportPreviewDialog.close()
    }

    function confirmPresetRemove() {
        if (!presetRemoveEntry || !presetKey(presetRemoveEntry).length)
            return
        if (controller.removePreset(presetRemoveEntry.id, presetRemoveEntry.version))
            presetRemoveDialog.close()
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
        if (downloading || applying)
            return updateStateLabel(currentUpdateState)
        if (updateInfo.apply_ready)
            return tr("重启并安装")
        if (updateInfo.can_update)
            return tr("立即更新")
        if (updateInfo.can_full_migrate)
            return tr("一键下载并自动迁移")
        if (String(updateInfo.url || "").length > 0)
            return tr("打开发布页")
        return tr("立即更新")
    }

    function updatePrimaryAction() {
        if (downloading || applying)
            return
        if (updateInfo.apply_ready)
            controller.applyUpdate()
        else if (updateInfo.can_update)
            controller.downloadUpdate()
        else if (updateInfo.can_full_migrate)
            controller.startFullMigration()
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


    property alias settingsFlick: settingsFlick
    property alias outputDialog: settingsDialogs.outputDialog
    property alias presetImportFileDialog: settingsDialogs.presetImportFileDialog
    property alias presetExportFileDialog: settingsDialogs.presetExportFileDialog
    property alias storageCleanDialog: settingsDialogs.storageCleanDialog
    property alias presetImportPreviewDialog: settingsDialogs.presetImportPreviewDialog
    property alias presetImportCopy: settingsDialogs.presetImportCopy
    property alias presetConflictDialog: settingsDialogs.presetConflictDialog
    property alias presetTextDialog: settingsDialogs.presetTextDialog
    property alias presetNameField: settingsDialogs.presetNameField
    property alias presetRemoveDialog: settingsDialogs.presetRemoveDialog
    property alias presetRecoverDialog: settingsDialogs.presetRecoverDialog

    SettingsDialogs {
        id: settingsDialogs
        page: page
    }

    Connections {
        target: page.controller
        function onStorageChanged() {
            if (page.storageView.generation !== page.cacheGeneration) {
                page.cacheGeneration = page.storageView.generation
                page.cacheSelection = ({})
            }
        }
        function onPresetLibraryChanged() {
            page.syncPresetSelection()
            if (page.presetView.conflict && !presetConflictDialog.visible)
                presetConflictDialog.open()
        }
    }
    Timer {
        objectName: "supportActivityMonitor"
        interval: 500
        repeat: true
        running: true
        onTriggered: controller.refreshActivityNow()
    }

    Component.onCompleted: {
        controller.setMonitorActive(true)
    }
    Component.onDestruction: controller.setMonitorActive(false)

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: page.pageMargin
            Layout.leftMargin: page.pageMargin
            Layout.rightMargin: page.pageMargin
            Layout.bottomMargin: Math.round(15 * page.theme.scale)
            spacing: Math.round(16 * page.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
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
                    text: page.tr("按分类管理本机外观、更新、下载与存储、预设库和支持。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(13 * page.theme.scale)
                    wrapMode: Text.Wrap
                }
            }

            UiButton {
                objectName: "topSupportEntry"
                theme: page.theme
                quiet: true
                visible: page.settingsCategory !== "support"
                leadingGlyph: "help_circle"
                leadingIconSize: 15
                text: page.tr("支持与日志")
                onClicked: page.selectCategory("support")
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(1, Math.round(1 * page.theme.scale))
            color: page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: page.showCategoryRail ? 2 : 1
            columnSpacing: 0
            rowSpacing: 0

            Rectangle {
                id: categoryPanel
                objectName: "settingsCategoryNavigation"
                Layout.row: 0
                Layout.column: 0
                Layout.fillWidth: !page.showCategoryRail
                Layout.fillHeight: page.showCategoryRail
                Layout.preferredWidth: page.showCategoryRail ? page.categoryRailWidth : -1
                Layout.preferredHeight: page.showCategoryRail ? -1
                    : categoryFlow.implicitHeight + Math.round(20 * page.theme.scale)
                color: page.theme.base
                clip: true

                Rectangle {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    visible: page.showCategoryRail
                    width: 1
                    color: page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line
                }
                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    visible: !page.showCategoryRail
                    height: 1
                    color: page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line
                }

                ColumnLayout {
                    id: categoryContent
                    anchors.fill: parent
                    anchors.leftMargin: Math.round(10 * page.theme.scale)
                    anchors.rightMargin: Math.round(10 * page.theme.scale)
                    anchors.topMargin: Math.round(12 * page.theme.scale)
                    anchors.bottomMargin: Math.round(10 * page.theme.scale)
                    spacing: Math.round(4 * page.theme.scale)

                    Flow {
                        id: categoryFlow
                        Layout.fillWidth: true
                        spacing: Math.round(4 * page.theme.scale)

                        Repeater {
                            model: page.categoryIds
                            delegate: UiButton {
                                required property string modelData
                                objectName: "settingsCategory_" + modelData
                                width: page.showCategoryRail ? categoryFlow.width : implicitWidth
                                theme: page.theme
                                text: page.categoryTitle(modelData)
                                leadingGlyph: page.categoryIcon(modelData)
                                leadingIconSize: 16
                                quiet: true
                                borderlessSelected: true
                                selected: page.settingsCategory === modelData
                                leftAligned: true
                                checkable: false
                                Accessible.name: text
                                Accessible.role: Accessible.RadioButton
                                Accessible.checked: selected
                                onClicked: page.selectCategory(modelData)
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: page.showCategoryRail
                    }
                }
            }

            ScrollView {
                id: settingsScroll
                objectName: "settingsCategoryContent"
                Layout.row: page.showCategoryRail ? 0 : 1
                Layout.column: page.showCategoryRail ? 1 : 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                focus: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical: UiScrollBar { theme: page.theme; policy: ScrollBar.AsNeeded }
                background: Rectangle { color: "transparent" }

                Flickable {
                    id: settingsFlick
                    objectName: "settingsContentFlickable"
                    contentWidth: width
                    contentHeight: settingsContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ColumnLayout {
                        id: settingsContent
                        width: settingsFlick.width
                        spacing: Math.round(14 * page.theme.scale)

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round(10 * page.theme.scale)
                    }


                    SettingsAppearanceSection { page: page }
                    SettingsStorageSection { page: page }
                    SettingsPresetsSection { page: page }
                    SettingsUpdateSection { page: page }
                    SettingsSupportAboutSection { page: page }


                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: page.pageMargin
                    }
                    }
                }
            }
        }
    }
}
