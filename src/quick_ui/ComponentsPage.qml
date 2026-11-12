import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "componentsPage"
    required property QtObject theme
    required property QtObject controller
    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    readonly property bool stacked: availableWidth < (theme.scale > 1.2 ? 1050 : 875)
    readonly property var component: controller.selected || ({})
    readonly property var health: component.health || ({})
    readonly property var volume: health.volume || ({})
    readonly property var lastCheck: component.last_check || ({})
    readonly property var taskPlan: controller.componentTaskPlan || ({})
    readonly property var missingIds: taskPlan.missingIds || []
    readonly property var planComponents: taskPlan.components || []
    readonly property bool planLoaded: taskPlan.ready === true
    readonly property bool planComplete: planLoaded && missingIds.length === 0
    readonly property bool builtinFallbackAvailable: planLoaded && taskPlan.builtinReady === true
        && missingIds.indexOf("roformer") >= 0
    readonly property real reserveBytes: typeof health.reserve_bytes === "number" && health.reserve_bytes >= 0
        ? health.reserve_bytes : 0
    readonly property real freeBytes: typeof volume.free_bytes === "number" ? volume.free_bytes : -1
    readonly property bool lowSpace: reserveBytes > 0 && freeBytes >= 0 && freeBytes < reserveBytes
    readonly property bool usesPlannedRoformer: planComponents.indexOf("roformer") >= 0
    readonly property bool idle: !controller.workBusy && !controller.loading
    readonly property string planBadgeText: !planLoaded
        ? controller.text(controller.loading ? "目录状态获取中" : "目录状态未知")
        : planComplete ? controller.text("当前方案组件已齐备") : controller.text("当前方案缺 {0} 项").replace("{0}", missingIds.length)
    readonly property color planBadgeColor: !planLoaded ? theme.muted
        : planComplete ? theme.success : theme.attention

    property bool showCheckIssues: false

    function sizeLabel(bytes) {
        if (typeof bytes !== "number" || !isFinite(bytes) || bytes < 0)
            return controller.text("未知")
        const units = ["B", "KiB", "MiB", "GiB"]
        let unit = 0
        while (bytes >= 1024 && unit < units.length - 1) {
            bytes /= 1024
            unit += 1
        }
        return bytes.toFixed(unit === 0 ? 0 : 2) + " " + units[unit]
    }

    function componentTitle(componentId) {
        return componentId === "builtin" ? controller.text("内置 Karaoke 2")
            : componentId === "roformer" ? "BS-RoFormer"
            : componentId === "chinese" ? controller.text("中文歌词识别")
            : componentId === "expression" ? controller.text("参考演唱音色")
            : componentId
    }

    function groupLabel(group) {
        return group === "missing" ? controller.text("当前方案缺项")
            : group === "ready" ? controller.text("已就绪")
            : controller.text("其他可选")
    }

    function healthText() {
        const rows = []
        if (health.runtime)
            rows.push(controller.text("已装运行库") + "  " + health.runtime.toUpperCase())
        if (component.installed_bytes > 0)
            rows.push(controller.text(health.installed_estimate ? "安装体积（清单估算）" : "已安装")
                      + "  " + sizeLabel(component.installed_bytes))
        if (health.expected_files > 0)
            rows.push(controller.text("完整文件") + "  " + health.complete_files + " / " + health.expected_files)
        if (health.source)
            rows.push(controller.text("来源依据") + "  " + controller.text(health.source)
                      + (health.version ? " · " + health.version : ""))
        if (health.scope)
            rows.push(controller.text(health.scope))
        return rows.join("\n")
    }

    function lastCheckText() {
        const parts = []
        if (lastCheck.message)
            parts.push(lastCheck.message)
        if (typeof lastCheck.checked === "number") {
            parts.push(typeof lastCheck.expected === "number" && lastCheck.expected > 0
                ? controller.text("已核对 {0} / {1} 个文件").replace("{0}", lastCheck.checked).replace("{1}", lastCheck.expected)
                : controller.text("已核对 {0} 个文件").replace("{0}", lastCheck.checked))
        }
        if (lastCheck.verified === true)
            parts.push(controller.text("结果：通过"))
        else if (lastCheck.state === "cancelled")
            parts.push(controller.text("校验已取消。"))
        else if (lastCheck.state)
            parts.push(controller.text("结果：未通过"))
        return parts.length > 0 ? parts.join(" · ") : controller.text("尚未执行完整校验")
    }

    function installActionText() {
        if (component.status === "ready")
            return controller.text("修复 / 重新安装")
        if (component.status === "damaged")
            return controller.text("修复安装")
        if (component.status === "partial")
            return controller.text("继续下载 / 修复")
        return controller.text("下载并安装")
    }

    component PlanMetric: ColumnLayout {
        required property string label
        required property string value
        property color valueColor: page.theme.text
        spacing: Math.round(4 * page.theme.scale)
        Text {
            Layout.fillWidth: true
            text: parent.label
            font.pixelSize: Math.round(11 * page.theme.scale)
            color: page.theme.muted
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        Text {
            Layout.fillWidth: true
            text: parent.value
            font.pixelSize: Math.round(19 * page.theme.scale)
            font.weight: Font.DemiBold
            color: parent.valueColor
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
    }

    component CatalogEntry: ItemDelegate {
        id: entry
        required property string componentId
        required property string title
        required property string summary
        required property string status
        required property string statusText
        required property string componentGroup

        objectName: "component_" + componentId
        width: ListView.view ? ListView.view.width : 0
        height: Math.round(50 * page.theme.scale)
        Accessible.name: title + " · " + page.groupLabel(componentGroup) + " · " + statusText
        highlighted: page.controller.selectedId === componentId
        focusPolicy: Qt.StrongFocus
        hoverEnabled: true
        padding: 0
        onClicked: page.controller.selectComponent(componentId)

        background: Rectangle {
            color: entry.highlighted ? page.theme.selected : entry.hovered ? page.theme.hover : "transparent"
            border.color: entry.visualFocus ? page.theme.accent : "transparent"
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: entry.highlighted ? Math.round(3 * page.theme.scale) : 0
                color: page.theme.accent
            }
            Rectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: page.theme.line }
        }

        contentItem: Item {
            RowLayout {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.leftMargin: Math.round(12 * page.theme.scale)
                anchors.rightMargin: Math.round(12 * page.theme.scale)
                spacing: Math.round(8 * page.theme.scale)
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(2 * page.theme.scale)
                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text(entry.title)
                        color: page.theme.text
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text(entry.summary)
                        color: page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                }
                Rectangle {
                    Layout.preferredWidth: Math.round(6 * page.theme.scale)
                    Layout.preferredHeight: Layout.preferredWidth
                    radius: width / 2
                    color: entry.status === "ready" ? page.theme.success
                        : entry.status === "error" ? page.theme.error : page.theme.attention
                }
                Text {
                    text: page.controller.text(entry.statusText)
                    color: page.theme.muted
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    textFormat: Text.PlainText
                }
            }
        }
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 22
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("模型与组件")
                font.pixelSize: Math.round(25 * page.theme.scale)
                font.weight: Font.DemiBold
                color: page.theme.text
                wrapMode: Text.Wrap
            }
            UiButton {
                objectName: "returnToPreparation"
                theme: page.theme
                text: page.controller.text("返回准备")
                onClicked: page.controller.returnToPreparation()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            spacing: 8
            TextField {
                id: search
                objectName: "componentSearch"
                Layout.fillWidth: true
                Layout.maximumWidth: 440
                Layout.preferredHeight: Math.max(36, implicitHeight)
                placeholderText: page.controller.text("查找组件")
                text: page.controller.query
                font.pixelSize: Math.round(13 * page.theme.scale)
                color: page.theme.text
                placeholderTextColor: page.theme.muted
                Accessible.name: placeholderText
                selectByMouse: true
                onTextEdited: page.controller.setQuery(text)
                background: Rectangle {
                    radius: 7
                    color: page.theme.surface
                    border.color: search.activeFocus ? page.theme.accent : page.theme.line
                }
            }
            UiButton {
                objectName: "refreshComponents"
                theme: page.theme
                text: page.controller.text(page.controller.loading ? "正在检查…" : "刷新状态")
                enabled: page.idle
                onClicked: page.controller.refresh()
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            Layout.bottomMargin: 20
            columns: page.stacked ? 1 : 2
            columnSpacing: 14
            rowSpacing: 12

            Rectangle {
                objectName: "componentTable"
                Layout.fillWidth: page.stacked
                Layout.preferredWidth: Math.round(284 * page.theme.scale)
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: Math.round(370 * page.theme.scale)
                color: page.theme.surface
                radius: 8
                border.color: page.theme.line
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(11 * page.theme.scale)
                    spacing: Math.round(7 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.theme.scale)
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("目录")
                            color: page.theme.text
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        Rectangle {
                            Layout.preferredHeight: Math.round(25 * page.theme.scale)
                            Layout.preferredWidth: planBadge.width + Math.round(16 * page.theme.scale)
                            radius: height / 2
                            color: Qt.tint(page.planBadgeColor, Qt.rgba(0, 0, 0, 0.88))
                            border.color: page.planBadgeColor
                            Text {
                                id: planBadge
                                anchors.centerIn: parent
                                text: page.planBadgeText
                                color: page.planBadgeColor
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }
                    }

                    Text {
                        objectName: "componentTaskPlan"
                        Layout.fillWidth: true
                        text: page.planLoaded
                            ? page.controller.text("当前准备") + " · " + (page.taskPlan.summary || page.controller.text("未选择组件"))
                              + (page.missingIds.length > 0
                                 ? "  ·  " + page.controller.text("缺项") + " "
                                   + page.missingIds.map(page.componentTitle).join(" · ")
                                 : "")
                            : page.controller.text("当前准备") + " · " + page.controller.text("目录读取完成后显示缺项")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }

                    ListView {
                        id: catalogList
                        objectName: "componentList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.controller.componentModel
                        delegate: CatalogEntry {}
                        section.property: "componentGroup"
                        section.delegate: Rectangle {
                            required property string section
                            width: catalogList.width
                            height: Math.round(25 * page.theme.scale)
                            color: page.theme.raised
                            Text {
                                anchors.left: parent.left
                                anchors.leftMargin: Math.round(12 * page.theme.scale)
                                anchors.verticalCenter: parent.verticalCenter
                                text: page.groupLabel(parent.section)
                                color: parent.section === "missing" ? page.theme.attention : page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                font.weight: Font.DemiBold
                            }
                        }
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        Text {
                            anchors.centerIn: parent
                            width: Math.max(0, parent.width - 24)
                            visible: catalogList.count === 0
                            text: page.controller.text(page.controller.loading ? "正在读取组件状态…" : "没有匹配的组件")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }

            Rectangle {
                objectName: "componentDetail"
                Layout.fillWidth: true
                Layout.preferredWidth: Math.round(525 * page.theme.scale)
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: details.implicitHeight + Math.round(30 * page.theme.scale)
                color: page.theme.surface
                radius: 8
                border.color: page.theme.line

                ColumnLayout {
                    id: details
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: Math.round(15 * page.theme.scale)
                    spacing: Math.round(9 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(8 * page.theme.scale)
                        Text {
                            Layout.fillWidth: true
                            text: page.component.title || page.controller.text("选择组件")
                            color: page.theme.text
                            font.pixelSize: Math.round(18 * page.theme.scale)
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                        Rectangle {
                            visible: !!page.component.id
                            readonly property color statusColor: page.component.status === "ready"
                                ? page.theme.success : page.component.status === "error"
                                    ? page.theme.error : page.theme.attention
                            Layout.preferredHeight: Math.round(25 * page.theme.scale)
                            Layout.preferredWidth: selectedBadge.width + Math.round(16 * page.theme.scale)
                            radius: height / 2
                            color: Qt.tint(statusColor, Qt.rgba(0, 0, 0, 0.88))
                            border.color: statusColor
                            Text {
                                id: selectedBadge
                                anchors.centerIn: parent
                                text: page.controller.text(page.component.status_text || "")
                                color: parent.statusColor
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: text.length > 0
                        text: page.component.detail || ""
                        color: page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !!page.component.id && page.usesPlannedRoformer
                        text: page.controller.text("缺项与已就绪分组按当前准备方案；安装和校验仍以右侧 CPU/CUDA 档为准。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Rectangle {
                        objectName: "componentPlan"
                        Layout.fillWidth: true
                        Layout.preferredHeight: planFacts.implicitHeight + Math.round(24 * page.theme.scale)
                        radius: Math.round(7 * page.theme.scale)
                        color: page.theme.raised
                        border.color: page.theme.line
                        ColumnLayout {
                            id: planFacts
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Math.round(12 * page.theme.scale)
                            spacing: Math.round(10 * page.theme.scale)

                            Text {
                                Layout.fillWidth: true
                                text: page.controller.text(!page.component.id ? "从左侧目录选择一个组件查看计划"
                                    : page.volume.path ? "安装计划" : "本机状态")
                                color: page.theme.text
                                font.pixelSize: Math.round(13 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: !!page.volume.path
                                spacing: Math.round(12 * page.theme.scale)
                                PlanMetric {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    label: page.controller.text("完整下载大小")
                                    value: page.component.download_bytes > 0
                                        ? page.sizeLabel(page.component.download_bytes) : page.controller.text("未知")
                                }
                                PlanMetric {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    label: page.controller.text("建议预留")
                                    value: page.reserveBytes > 0
                                        ? page.sizeLabel(page.reserveBytes) : page.controller.text("未知")
                                }
                                PlanMetric {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    label: page.controller.text("卷可用空间")
                                    value: page.sizeLabel(page.freeBytes)
                                    valueColor: page.lowSpace ? page.theme.attention : page.theme.text
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: !!page.component.summary
                                text: page.controller.text(page.component.summary || "")
                                color: page.theme.text
                                font.pixelSize: Math.round(12 * page.theme.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: !!page.volume.path
                                text: page.controller.text("组件根") + " · " + (page.volume.path || "")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                elide: Text.ElideMiddle
                                textFormat: Text.PlainText
                                ToolTip.visible: volumeHover.hovered && !!page.volume.path
                                ToolTip.text: page.volume.path || ""
                                HoverHandler { id: volumeHover }
                            }
                            Text {
                                Layout.fillWidth: true
                                visible: typeof page.health.cache_bytes === "number"
                                text: page.controller.text("可复用下载缓存") + " · " + page.sizeLabel(page.health.cache_bytes)
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                textFormat: Text.PlainText
                            }
                        }
                    }

                    Text {
                        objectName: "componentSpaceWarning"
                        Layout.fillWidth: true
                        visible: page.lowSpace
                        text: page.controller.text("可用空间低于建议预留；安装前会重新检查。")
                        color: page.theme.attention
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.component.id === "roformer" || !!page.component.can_install
                        spacing: Math.round(12 * page.theme.scale)

                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: page.component.id === "roformer"
                            Text {
                                text: page.controller.text("运行库")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                            }
                            UiComboBox {
                                objectName: "runtimeChoice"
                                Layout.fillWidth: true
                                theme: page.theme
                                enabled: page.idle
                                Accessible.name: page.controller.text("运行库")
                                model: [{ label: page.controller.text("仅 CPU"), value: "cpu" },
                                        { label: page.controller.text("NVIDIA GPU / CUDA"), value: "cuda" }]
                                currentIndex: page.controller.runtime === "cuda" ? 1 : 0
                                onActivated: page.controller.setRuntime(currentValue)
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: !!page.component.can_install
                            Text {
                                text: page.controller.text("下载来源")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                            }
                            UiComboBox {
                                objectName: "downloadSourceChoice"
                                Layout.fillWidth: true
                                theme: page.theme
                                enabled: page.idle
                                Accessible.name: page.controller.text("下载来源")
                                model: page.controller.downloadSources
                                currentIndex: page.controller.downloadSources.findIndex(option => option.value === page.controller.downloadSource)
                                onActivated: page.controller.setDownloadSource(currentValue)
                            }
                        }
                        Item { Layout.fillWidth: true; Layout.preferredWidth: 80 }
                    }

                    Text {
                        objectName: "componentHealth"
                        Layout.fillWidth: true
                        visible: !!page.component.id && text.length > 0
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                        text: page.healthText()
                    }

                    Text {
                        objectName: "componentLastVerification"
                        Layout.fillWidth: true
                        visible: !!page.component.id
                        text: page.controller.text("最近一次完整校验") + " · " + page.lastCheckText()
                        color: page.lastCheck.verified === true ? page.theme.success
                            : (page.lastCheck.message || page.lastCheck.state) ? page.theme.attention : page.theme.muted
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    UiButton {
                        objectName: "componentVerificationIssues"
                        theme: page.theme
                        quiet: true
                        visible: page.lastCheck.issue_count > 0
                        text: page.controller.text(page.showCheckIssues ? "收起检查详情" : "查看检查详情")
                            + " (" + (page.lastCheck.issue_count || 0) + ")"
                        onClicked: page.showCheckIssues = !page.showCheckIssues
                    }

                    Repeater {
                        model: page.showCheckIssues ? page.lastCheck.issues || [] : []
                        delegate: Text {
                            required property string modelData
                            Layout.fillWidth: true
                            text: modelData
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.WrapAnywhere
                            textFormat: Text.PlainText
                        }
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: Math.round(6 * page.theme.scale)
                        UiButton {
                            objectName: "installComponent"
                            theme: page.theme
                            visible: !!page.component.can_install
                            enabled: page.idle
                            primary: !page.component.can_use
                            text: page.installActionText()
                            onClicked: page.controller.installSelected()
                        }
                        UiButton {
                            objectName: "verifyComponent"
                            theme: page.theme
                            visible: !!page.component.can_verify
                            enabled: page.idle
                            text: page.controller.text("完整校验")
                            onClicked: page.controller.verifySelected()
                        }
                        UiButton {
                            objectName: "useComponent"
                            theme: page.theme
                            visible: !!page.component.model && !!page.component.can_use
                            enabled: page.idle
                            primary: true
                            text: page.controller.text("使用此方案")
                            onClicked: page.controller.useComponent()
                        }
                    }

                    Rectangle {
                        objectName: "builtinFallback"
                        Layout.fillWidth: true
                        visible: page.builtinFallbackAvailable
                        Layout.preferredHeight: builtinFallbackBody.implicitHeight + Math.round(20 * page.theme.scale)
                        radius: Math.round(7 * page.theme.scale)
                        color: Qt.rgba(page.theme.success.r, page.theme.success.g, page.theme.success.b, 0.07)
                        border.color: page.theme.line
                        RowLayout {
                            id: builtinFallbackBody
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Math.round(10 * page.theme.scale)
                            spacing: Math.round(7 * page.theme.scale)
                            Text {
                                Layout.fillWidth: true
                                text: page.missingIds.length === 1
                                    ? page.controller.text("可改用内置 Karaoke 2，无需安装 BS-RoFormer。")
                                    : page.controller.text("可改用内置 Karaoke 2；其他缺项仍需处理。")
                                color: page.theme.text
                                font.pixelSize: Math.round(12 * page.theme.scale)
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            UiButton {
                                objectName: "useBuiltinComponent"
                                theme: page.theme
                                visible: page.builtinFallbackAvailable
                                enabled: page.idle
                                text: page.controller.text("改用内置方案")
                                onClicked: page.controller.useBuiltinComponent()
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.controller.operation.state !== "idle"
                        spacing: Math.round(8 * page.theme.scale)
                        Text {
                            Layout.fillWidth: true
                            text: (page.controller.operation.component ? page.controller.operation.component + " · " : "")
                                + page.controller.text(page.controller.operation.message || "组件任务进行中")
                                + (page.controller.operation.total > 0
                                   && page.controller.busy
                                   ? " · " + page.controller.operation.done + " / " + page.controller.operation.total : "")
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        UiButton {
                            objectName: "componentPlanCancel"
                            theme: page.theme
                            quiet: true
                            visible: page.controller.busy
                            enabled: page.controller.operation.state === "running"
                            text: page.controller.text("取消")
                            onClicked: page.controller.cancelOperation()
                        }
                    }
                }
            }
        }
    }
}
