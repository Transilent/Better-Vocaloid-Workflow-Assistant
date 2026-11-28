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
    ScrollBar.vertical: UiScrollBar { theme: page.theme }

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
        && (missingIds.indexOf("roformer") >= 0 || missingIds.indexOf("roformer_onnx") >= 0)
    readonly property real reserveBytes: typeof health.reserve_bytes === "number" && health.reserve_bytes >= 0
        ? health.reserve_bytes : 0
    readonly property real freeBytes: typeof volume.free_bytes === "number" ? volume.free_bytes : -1
    readonly property bool lowSpace: reserveBytes > 0 && freeBytes >= 0 && freeBytes < reserveBytes
    readonly property bool usesPlannedRoformer: planComponents.indexOf("roformer") >= 0
    readonly property bool activeInPlan: !!component.id && planComponents.indexOf(component.id) >= 0
    readonly property bool idle: !controller.workBusy && !controller.loading
    readonly property var sourceChoices: controller.downloadSources
    readonly property string sourceHint: controller.downloadSource === "direct" ? ""
        : component.id === "roformer" ? controller.text(
            controller.downloadSource === "modelscope" || controller.downloadSource === "github-release"
            ? "所选方案不经过 Hugging Face；Python 和 PyTorch 依赖由各自的镜像提供。"
            : "PyPI 只用于普通依赖，SJTU 只用于 PyTorch。连不上 Hugging Face 时可直接选魔搭或 GitHub。")
        : component.id === "roformer_onnx" ? controller.text(
            "ONNX 权重支持直连或加速源下载；隔离 onnxruntime-webgpu 1.27.0 仍单独保存。")
        : component.id === "chinese" ? controller.text(
            "HF-Mirror 可能跳回 Hugging Face；暂无核验通过的独立来源。")
        : ""
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
            : componentId === "roformer_onnx" ? controller.text("BS-RoFormer ONNX（实验性）")
            : componentId === "chinese" ? controller.text("中文 / 英文歌词识别")
            : componentId === "expression" ? controller.text("参考演唱音色")
            : componentId
    }

    function groupLabel(group) {
        return group === "missing" ? controller.text("当前方案缺项")
            : group === "ready" ? controller.text("已就绪")
            : controller.text("其他可选")
    }

    function targetTierSuffix() {
        if (component.id === "roformer")
            return controller.runtime === "cuda" ? " · GPU (CUDA)" : " · CPU"
        if (component.id === "roformer_onnx")
            return controller.onnxPrecision === "fp16" ? " · 20s FP16" : " · 10s FP16"
        return ""
    }
    function healthText() {
        const rows = []
        const installedList = health.installed_runtimes
        const installedPrecs = health.installed_precisions
        if (Array.isArray(installedList) && installedList.length > 0) {
            rows.push(controller.text("已装运行库") + "  " + installedList.map(rt => rt === "cuda" ? "GPU (CUDA)" : "CPU").join(" + "))
        } else if (Array.isArray(installedPrecs) && installedPrecs.length > 0) {
            rows.push(controller.text("已装档位") + "  " + installedPrecs.map(p => p === "fp16" ? "20s FP16" : "10s FP16").join(" + "))
        } else if (health.runtime) {
            rows.push(controller.text("已装运行库") + "  " + health.runtime.toUpperCase())
        }
        if (component.installed_bytes > 0)
            rows.push(controller.text(health.installed_estimate ? "安装体积（清单估算）" : "已安装") + "  " + sizeLabel(component.installed_bytes))
        if (health.expected_files > 0)
            rows.push(controller.text("完整文件") + "  " + health.complete_files + " / " + health.expected_files)
        if (health.source)
            rows.push(controller.text("来源依据") + "  " + controller.text(health.source) + (health.version ? " · " + health.version : ""))
        if (health.scope)
            rows.push(controller.text(health.scope))
        return rows.join("\n")
    }
    function lastCheckText() {
        const parts = []
        if (lastCheck.message) parts.push(lastCheck.message)
        if (typeof lastCheck.checked === "number")
            parts.push(typeof lastCheck.expected === "number" && lastCheck.expected > 0
                ? controller.text("已核对 {0} / {1} 个文件").replace("{0}", lastCheck.checked).replace("{1}", lastCheck.expected)
                : controller.text("已核对 {0} 个文件").replace("{0}", lastCheck.checked))
        if (lastCheck.verified === true) parts.push(controller.text("结果：通过"))
        else if (lastCheck.state === "cancelled") parts.push(controller.text("校验已取消。"))
        else if (lastCheck.state) parts.push(controller.text("结果：未通过"))
        return parts.length > 0 ? parts.join(" · ") : controller.text("尚未执行完整校验")
    }
    function installActionText() {
        const base = component.status === "ready" ? "修复 / 重新安装"
            : component.status === "damaged" ? "修复安装"
            : component.status === "partial" ? "继续下载 / 修复" : "下载并安装"
        return controller.text(base) + targetTierSuffix()
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
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(5 * page.theme.scale)
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text(entry.title)
                            color: page.theme.text
                            font.pixelSize: Math.round(13 * page.theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        Rectangle {
                            visible: page.planComponents.indexOf(entry.componentId) >= 0
                            Layout.preferredHeight: Math.round(17 * page.theme.scale)
                            Layout.preferredWidth: inUseText.implicitWidth + Math.round(8 * page.theme.scale)
                            radius: height / 2
                            color: Qt.rgba(page.theme.accent.r, page.theme.accent.g, page.theme.accent.b, page.theme.dark ? 0.20 : 0.14)
                            Text {
                                id: inUseText
                                anchors.centerIn: parent
                                text: page.controller.text("使用中")
                                color: page.theme.accent
                                font.pixelSize: Math.round(9 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }
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
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 14
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            spacing: 8
            Text {
                text: page.controller.text("模型与组件")
                font.pixelSize: Math.round(18 * page.theme.scale)
                font.weight: Font.DemiBold
                color: page.theme.text
            }
            TextField {
                id: search
                objectName: "componentSearch"
                Layout.fillWidth: true
                Layout.maximumWidth: 340
                Layout.preferredHeight: Math.round(30 * page.theme.scale)
                placeholderText: page.controller.text("查找组件…")
                text: page.controller.query
                font.pixelSize: Math.round(12 * page.theme.scale)
                color: page.theme.text
                placeholderTextColor: page.theme.muted
                Accessible.name: placeholderText
                selectByMouse: true
                onTextEdited: page.controller.setQuery(text)
                background: Rectangle {
                    radius: 6
                    color: page.theme.surface
                    border.color: search.activeFocus ? page.theme.accent : page.theme.line
                }
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
            UiButton {
                objectName: "refreshComponents"
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                quiet: true
                text: page.controller.text(page.controller.loading ? "正在检查…" : "刷新状态")
                leadingGlyph: "refresh"
                leadingIconSize: 14
                enabled: page.idle
                onClicked: page.controller.refresh()
            }
            UiButton {
                objectName: "returnToPreparation"
                theme: page.theme
                implicitHeight: Math.round(30 * page.theme.scale)
                text: page.controller.text("返回准备")
                leadingGlyph: "arrow_left"
                leadingIconSize: 14
                onClicked: page.controller.returnToPreparation()
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            Layout.bottomMargin: 16
            columns: page.stacked ? 1 : 2
            columnSpacing: 14
            rowSpacing: 12

            Rectangle {
                objectName: "componentTable"
                Layout.fillWidth: page.stacked
                Layout.preferredWidth: Math.round(284 * page.theme.scale)
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: Math.round(440 * page.theme.scale)
                color: page.theme.surface
                radius: page.theme.radiusLg !== undefined ? page.theme.radiusLg : Math.round(11 * page.theme.scale)
                border.color: page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line
                clip: true

                Rectangle {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.leftMargin: parent.radius; anchors.rightMargin: parent.radius
                    height: 1; color: page.theme.highlightTop !== undefined ? page.theme.highlightTop : "transparent"
                }

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
                            color: Qt.rgba(page.planBadgeColor.r, page.planBadgeColor.g, page.planBadgeColor.b, page.theme.dark ? 0.18 : 0.13)
                            border.color: Qt.rgba(page.planBadgeColor.r, page.planBadgeColor.g, page.planBadgeColor.b, page.theme.dark ? 0.55 : 0.45)
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
                        ScrollBar.vertical: UiScrollBar { theme: page.theme }
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
                radius: page.theme.radiusLg !== undefined ? page.theme.radiusLg : Math.round(11 * page.theme.scale)
                border.color: page.theme.lineSubtle !== undefined ? page.theme.lineSubtle : page.theme.line

                Rectangle {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.leftMargin: parent.radius; anchors.rightMargin: parent.radius
                    height: 1; color: page.theme.highlightTop !== undefined ? page.theme.highlightTop : "transparent"
                }

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
                            visible: page.activeInPlan
                            Layout.preferredHeight: Math.round(25 * page.theme.scale)
                            Layout.preferredWidth: activeBadge.width + Math.round(14 * page.theme.scale)
                            radius: height / 2
                            color: Qt.rgba(page.theme.accent.r, page.theme.accent.g, page.theme.accent.b, page.theme.dark ? 0.18 : 0.12)
                            border.color: Qt.rgba(page.theme.accent.r, page.theme.accent.g, page.theme.accent.b, page.theme.dark ? 0.55 : 0.45)
                            Text {
                                id: activeBadge
                                anchors.centerIn: parent
                                text: page.controller.text("当前已选用")
                                color: page.theme.accent
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                            }
                        }
                        Rectangle {
                            visible: !!page.component.id
                            readonly property color statusColor: page.component.status === "ready"
                                ? page.theme.success : page.component.status === "error"
                                    ? page.theme.error : page.theme.attention
                            Layout.preferredHeight: Math.round(25 * page.theme.scale)
                            Layout.preferredWidth: selectedBadge.width + Math.round(16 * page.theme.scale)
                            radius: height / 2
                            color: Qt.rgba(statusColor.r, statusColor.g, statusColor.b, page.theme.dark ? 0.18 : 0.12)
                            border.color: Qt.rgba(statusColor.r, statusColor.g, statusColor.b, page.theme.dark ? 0.55 : 0.45)
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

                    RoformerRuntimeBar {
                        visible: page.component.id === "roformer"
                        theme: page.theme
                        controller: page.controller
                        health: page.health
                        idle: page.idle
                    }

                    RoformerOnnxBar {
                        visible: page.component.id === "roformer_onnx"
                        theme: page.theme
                        controller: page.controller
                        health: page.health
                        idle: page.idle
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.component.id === "roformer" && page.usesPlannedRoformer
                        text: page.controller.text("缺项与已就绪分组按当前准备方案；安装和校验仍以右侧 CPU/CUDA 档为准。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    ComponentPlanCard {
                        theme: page.theme
                        controller: page.controller
                        component: page.component
                        health: page.health
                        volume: page.volume
                        reserveBytes: page.reserveBytes
                        freeBytes: page.freeBytes
                        lowSpace: page.lowSpace
                        sizeLabelFn: page.sizeLabel
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
                                text: page.controller.text("目标运行库（GPU / CPU 独立共存）")
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
                            visible: page.sourceChoices.length > 0
                            Text {
                                text: page.controller.text("下载来源")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                            }
                            UiComboBox {
                                id: sourceCombo
                                objectName: "downloadSourceChoice"
                                Layout.fillWidth: true
                                theme: page.theme
                                enabled: page.idle
                                Accessible.name: page.controller.text("下载来源")
                                readonly property var sourceModel: page.sourceChoices
                                model: sourceModel
                                currentIndex: sourceModel.findIndex(option => option.value === page.controller.downloadSource)
                                onActivated: page.controller.setDownloadSource(currentValue)
                            }
                        }
                        Item { Layout.fillWidth: true; Layout.preferredWidth: 80 }
                    }

                    Text {
                        objectName: "componentSourceHint"
                        Layout.fillWidth: true
                        visible: page.sourceChoices.length > 0 && text.length > 0
                        text: page.sourceHint
                        color: page.theme.muted
                        font.pixelSize: Math.round(11 * page.theme.scale)
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
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
                            text: page.controller.text("完整校验") + page.targetTierSuffix()
                            onClicked: page.controller.verifySelected()
                        }
                        UiButton {
                            objectName: "useComponent"
                            theme: page.theme
                            visible: !!page.component.model && !!page.component.can_use
                            enabled: page.idle && !page.activeInPlan
                            primary: !page.activeInPlan
                            text: page.controller.text(page.activeInPlan ? "当前已选用" : "使用此方案")
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

                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: page.controller.operation.state !== "idle"
                        spacing: Math.round(5 * page.theme.scale)
                        UiProgressBar {
                            Layout.fillWidth: true
                            theme: page.theme
                            visible: page.controller.busy
                            indeterminate: !(page.controller.operation.total > 0)
                            value: page.controller.operation.total > 0
                                ? Math.min(1, page.controller.operation.done / page.controller.operation.total) : 0
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)
                            Text {
                                Layout.fillWidth: true
                                text: (page.controller.operation.component ? page.controller.operation.component + " · " : "")
                                    + page.controller.text(page.controller.operation.message || "组件任务进行中")
                                    + (page.controller.operation.total > 0 && page.controller.busy
                                       ? " · " + (page.controller.operation.total > 100000
                                           ? page.sizeLabel(page.controller.operation.done) + " / " + page.sizeLabel(page.controller.operation.total)
                                           : page.controller.operation.done + " / " + page.controller.operation.total) : "")
                                color: page.theme.muted
                                font.pixelSize: Math.round(11 * page.theme.scale)
                                wrapMode: page.controller.operation.state === "failed" ? Text.WrapAnywhere : Text.NoWrap
                                elide: page.controller.operation.state === "failed" ? Text.ElideNone : Text.ElideRight
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
}
