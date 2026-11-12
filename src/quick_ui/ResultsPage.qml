import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Rectangle {
    id: page
    objectName: "resultsPage"
    required property QtObject theme
    required property QtObject controller
    color: theme.base
    clip: true

    readonly property bool idle: !controller.taskLoading && !controller.workBusy
    readonly property bool mutationIdle: !controller.taskLoading && !controller.taskMutationBusy
    readonly property var detail: controller.taskDetail
    readonly property int railWidth: Math.round(280 * theme.scale)
    readonly property int inspectorWidth: Math.round(320 * theme.scale)
    readonly property real bodyGap: Math.round(14 * theme.scale)
    readonly property var selectedArtifact: {
        const row = page.controller.selectedTaskArtifact
        return row && String(row.id || "").length > 0 ? row : ({})
    }
    readonly property var selectedSource: {
        const key = String(page.selectedArtifact.id || "")
        const all = page.detail.artifactDetails
        return key.length > 0 && all && all[key] ? all[key] : ({})
    }
    readonly property var deliveryIds: {
        const value = page.detail.delivery ? page.detail.delivery.artifactIds : []
        return value || []
    }
    readonly property string deliveryArtifactId: {
        const delivery = page.detail.delivery
        if (delivery && String(delivery.openArtifactId || "").length > 0)
            return String(delivery.openArtifactId)
        for (let index = 0; index < page.deliveryIds.length; ++index) {
            if (String(page.deliveryIds[index] || "").length > 0)
                return String(page.deliveryIds[index])
        }
        return ""
    }
    readonly property var currentDeliveryArtifact: page.artifactById(page.deliveryArtifactId)
    readonly property var primaryAction: page.controller.taskPrimaryAction || ({})
    readonly property string primaryKind: String(page.primaryAction.kind || "")
    readonly property bool hasHistoricalArtifacts: {
        const rows = page.detail.artifacts || []
        for (let index = 0; index < rows.length; ++index) {
            if (String(rows[index].state || "") === "historical")
                return true
        }
        return false
    }
    property bool requestExpanded: false
    property string expandedArtifactId: ""

    component StatusTag: Rectangle {
        id: tag
        property string tagText: ""
        property color tagColor: page.theme.accent
        implicitWidth: Math.max(Math.round(34 * page.theme.scale),
                                tagLabel.implicitWidth + Math.round(14 * page.theme.scale))
        implicitHeight: tagLabel.implicitHeight + Math.round(8 * page.theme.scale)
        radius: Math.round(5 * page.theme.scale)
        color: page.theme.raised

        Text {
            id: tagLabel
            anchors.centerIn: parent
            text: tag.tagText
            color: tag.tagColor
            font.pixelSize: Math.round(11 * page.theme.scale)
            textFormat: Text.PlainText
        }
    }

    component FactRow: RowLayout {
        id: fact
        property string factName: ""
        property string factValue: ""
        property string factTip: ""
        Layout.fillWidth: true
        spacing: Math.round(8 * page.theme.scale)

        Text {
            Layout.preferredWidth: Math.round(70 * page.theme.scale)
            text: fact.factName
            color: page.theme.muted
            font.pixelSize: Math.round(11 * page.theme.scale)
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        Text {
            Layout.fillWidth: true
            text: fact.factValue.length > 0 ? fact.factValue : page.controller.text("未记录")
            color: page.theme.text
            font.pixelSize: Math.round(11 * page.theme.scale)
            elide: Text.ElideMiddle
            textFormat: Text.PlainText
            ToolTip.visible: factHover.hovered && fact.factTip.length > 0
            ToolTip.text: fact.factTip
            HoverHandler { id: factHover }
        }
    }

    function statusColor(available) {
        return available ? page.theme.success : page.theme.error
    }

    function artifactStatusColor(row) {
        if (!row)
            return page.theme.muted
        if (String(row.state || "") === "historical")
            return page.theme.muted
        return row.available ? page.theme.success : page.theme.error
    }

    function shortRevision(value) {
        const text = String(value || "")
        return text.length > 12 ? text.substring(0, 12) + "…" : text
    }

    function optionIndex(options, value) {
        for (let index = 0; index < options.length; ++index) {
            if (String(options[index].value) === String(value))
                return index
        }
        return 0
    }

    function taskFilterOptions() {
        return [
            { label: page.controller.text("全部状态"), value: "all" },
            { label: page.controller.text("已完成"), value: "done" },
            { label: page.controller.text("待处理 / 中断"), value: "pending" },
            { label: page.controller.text("失败"), value: "failed" },
            { label: page.controller.text("处理中"), value: "running" }
        ]
    }

    function taskSortOptions() {
        return [
            { label: page.controller.text("最近更新"), value: "recent" },
            { label: page.controller.text("名称 A–Z"), value: "title" }
        ]
    }

    function artifactById(value) {
        const key = String(value || "")
        const rows = page.detail.artifacts || []
        for (let index = 0; index < rows.length; ++index) {
            if (String(rows[index].id || "") === key)
                return rows[index]
        }
        return {}
    }

    function isDeliveryArtifact(row) {
        if (!row)
            return false
        const key = String(row.id || "")
        for (let index = 0; index < page.deliveryIds.length; ++index) {
            if (String(page.deliveryIds[index] || "") === key)
                return true
        }
        return false
    }

    function primaryActionText(kind) {
        if (kind === "progress")
            return page.controller.text("查看进度与错误")
        if (kind === "retry")
            return page.controller.text("继续 / 重试")
        if (kind === "tuning")
            return page.controller.text("调音或查看工程")
        if (kind === "audio_mixdown")
            return page.controller.text("分轨送入简易混音")
        if (kind === "video_mixdown")
            return page.controller.text("合成视频")
        if (kind === "publishing")
            return page.controller.text("准备发布")
        if (kind === "delivery")
            return page.controller.text("打开当前产物")
        return page.controller.text("检查文件与依赖")
    }

    function bindingInfo(kind) {
        const all = page.detail.bindings
        return all && all[kind] ? all[kind] : null
    }

    function bindingRows() {
        const result = []
        const kinds = ["project", "mixdown", "video"]
        for (let index = 0; index < kinds.length; ++index) {
            const row = page.bindingInfo(kinds[index])
            if (row && String(row.id || "").length > 0)
                result.push(row)
        }
        return result
    }

    function bindingId(kind) {
        const row = page.bindingInfo(kind)
        return row ? String(row.id || "") : ""
    }

    function bindingRevision(kind) {
        const row = page.bindingInfo(kind)
        return row ? String(row.revision || "") : ""
    }

    function beginArtifactRegistration(kind) {
        const current = page.bindingInfo(kind)
        registerArtifactDialog.expectedBindingJobId = page.controller.selectedTaskId
        registerArtifactDialog.expectedBindingTitle = page.detail.title || ""
        registerArtifactDialog.expectedBindingKind = kind
        registerArtifactDialog.expectedBindingId = current ? String(current.id || "") : ""
        registerArtifactDialog.expectedBindingRevision = current ? String(current.revision || "") : ""
        registerArtifactDialog.expectedOldPath = current ? String(current.path || "") : ""
        registerArtifactDialog.pendingArtifactUrl = ""
        registerArtifactFileDialog.bindingKind = kind
        registerArtifactFileDialog.open()
    }

    function beginArtifactUnlink(row) {
        unlinkBindingDialog.expectedBindingJobId = page.controller.selectedTaskId
        unlinkBindingDialog.expectedBindingTitle = page.detail.title || ""
        unlinkBindingDialog.expectedBindingKind = String(row.kind || "")
        unlinkBindingDialog.expectedBindingId = String(row.id || "")
        unlinkBindingDialog.expectedBindingRevision = String(row.revision || "")
        unlinkBindingDialog.expectedBindingPath = String(row.path || "")
        unlinkBindingDialog.hasRelatedPublication = !!page.detail.hasPublication
        unlinkBindingDialog.open()
    }

    function beginArtifactRebuild() {
        rebuildArtifactsDialog.expectedRebuildJobId = page.controller.selectedTaskId
        rebuildArtifactsDialog.expectedRebuildTitle = page.detail.title || ""
        rebuildArtifactsDialog.expectedIndexRevision = String(page.detail.artifactIndexRevision || "")
        rebuildArtifactsDialog.open()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(68 * page.theme.scale)
            Layout.leftMargin: Math.round(22 * page.theme.scale)
            Layout.rightMargin: Math.round(22 * page.theme.scale)
            spacing: Math.round(14 * page.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Math.round(2 * page.theme.scale)
                Text {
                    Layout.fillWidth: true
                    text: page.controller.text("任务与结果")
                    color: page.theme.text
                    font.pixelSize: Math.round(24 * page.theme.scale)
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: page.controller.text("选择任务，查看产物并继续制作。")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    elide: Text.ElideRight
                }
            }

            UiButton {
                objectName: "openTaskManagement"
                theme: page.theme
                text: page.controller.text("任务管理…")
                enabled: !!page.controller.selectedTaskId
                onClicked: taskManagementDrawer.open()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: page.theme.line
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: Math.round(22 * page.theme.scale)
            Layout.rightMargin: Math.round(22 * page.theme.scale)
            Layout.topMargin: Math.round(12 * page.theme.scale)
            Layout.bottomMargin: Math.round(12 * page.theme.scale)
            spacing: page.bodyGap

            Rectangle {
                objectName: "taskListPanel"
                Layout.preferredWidth: page.railWidth
                Layout.minimumWidth: page.railWidth
                Layout.maximumWidth: page.railWidth
                Layout.fillHeight: true
                color: page.theme.surface
                radius: Math.round(9 * page.theme.scale)
                border.color: page.theme.line
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(10 * page.theme.scale)
                    spacing: Math.round(7 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.theme.scale)
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("任务")
                            color: page.theme.text
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            objectName: "taskCount"
                            text: taskList.count.toString()
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                        }
                        UiButton {
                            objectName: "refreshTasks"
                            theme: page.theme
                            quiet: true
                            text: page.controller.text(page.controller.taskLoading ? "正在检查…" : "刷新")
                            enabled: !page.controller.taskLoading && !page.controller.workBusy
                            onClicked: page.controller.refreshTasks()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(6 * page.theme.scale)
                        TextField {
                            id: search
                            objectName: "taskSearch"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.max(Math.round(36 * page.theme.scale), implicitHeight)
                            placeholderText: page.controller.text("搜索歌名、任务名、BV 号或保存目录")
                            text: page.controller.taskQuery
                            font.pixelSize: Math.round(12 * page.theme.scale)
                            color: page.theme.text
                            placeholderTextColor: page.theme.muted
                            selectByMouse: true
                            Accessible.name: placeholderText
                            onTextEdited: page.controller.setTaskQuery(text)
                            onAccepted: page.controller.refreshTasks()
                            background: Rectangle {
                                radius: Math.round(7 * page.theme.scale)
                                color: page.theme.base
                                border.color: search.activeFocus ? page.theme.accent : page.theme.line
                            }
                        }
                        UiButton {
                            objectName: "clearTaskSearch"
                            visible: page.controller.taskQuery.length > 0
                            theme: page.theme
                            text: page.controller.text("清空")
                            quiet: true
                            onClicked: page.controller.setTaskQuery("")
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Math.round(6 * page.theme.scale)
                        UiComboBox {
                            objectName: "taskStateFilter"
                            Layout.fillWidth: true
                            theme: page.theme
                            model: page.taskFilterOptions()
                            currentIndex: page.optionIndex(model, page.controller.taskStateFilter)
                            onActivated: page.controller.setTaskStateFilter(currentValue)
                            Accessible.name: page.controller.text("任务状态筛选")
                        }
                        UiComboBox {
                            objectName: "taskSort"
                            Layout.fillWidth: true
                            theme: page.theme
                            model: page.taskSortOptions()
                            currentIndex: page.optionIndex(model, page.controller.taskSort)
                            onActivated: page.controller.setTaskSort(currentValue)
                            Accessible.name: page.controller.text("任务排序")
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: page.theme.line
                    }

                    ListView {
                        id: taskList
                        objectName: "taskList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: taskList.count > 0
                        clip: true
                        model: page.controller.taskModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                        delegate: ItemDelegate {
                            id: taskRow
                            required property string jobId
                            required property string title
                            required property string statusText
                            required property string summary
                            required property string sourceText
                            width: taskList.width
                            height: Math.max(Math.round(82 * page.theme.scale), taskRowContent.implicitHeight + Math.round(18 * page.theme.scale))
                            highlighted: page.controller.selectedTaskId === jobId
                            hoverEnabled: true
                            focusPolicy: Qt.StrongFocus
                            Accessible.name: title + ", " + statusText + ", " + sourceText + ", " + summary
                            onClicked: {
                                taskManagementDrawer.close()
                                page.controller.selectTask(jobId)
                            }

                            contentItem: ColumnLayout {
                                id: taskRowContent
                                spacing: Math.round(2 * page.theme.scale)

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(7 * page.theme.scale)
                                    Text {
                                        Layout.fillWidth: true
                                        text: taskRow.title
                                        color: page.theme.text
                                        font.pixelSize: Math.round(13 * page.theme.scale)
                                        font.weight: taskRow.highlighted ? Font.DemiBold : Font.Normal
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        text: taskRow.statusText
                                        color: taskRow.highlighted ? page.theme.accent : page.theme.muted
                                        font.pixelSize: Math.round(11 * page.theme.scale)
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: taskRow.sourceText
                                    color: taskRow.highlighted ? page.theme.text : page.theme.muted
                                    font.pixelSize: Math.round(11 * page.theme.scale)
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: taskRow.summary
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }

                            background: Rectangle {
                                color: taskRow.highlighted ? page.theme.selected
                                    : taskRow.hovered ? page.theme.hover : "transparent"
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: taskRow.highlighted ? 2 : 0
                                    color: page.theme.accent
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: taskList.count === 0
                        spacing: Math.round(8 * page.theme.scale)
                        Item { Layout.fillHeight: true }
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.taskLoading
                                ? page.controller.text("正在读取任务…")
                                : page.controller.taskQuery.length > 0
                                    ? page.controller.text("没有匹配的任务，可清空搜索后刷新。")
                                    : page.controller.taskStateFilter !== "all"
                                        ? page.controller.text("当前状态下没有任务，可切换状态筛选后刷新。")
                                        : page.controller.text("还没有任务。完成首次准备后会显示在这里。")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                            wrapMode: Text.Wrap
                            horizontalAlignment: Text.AlignHCenter
                        }
                        UiButton {
                            objectName: "emptyResultsGoPrepare"
                            Layout.alignment: Qt.AlignHCenter
                            theme: page.theme
                            text: page.controller.text("去准备")
                            visible: !page.controller.taskLoading
                            onClicked: page.controller.navigate("processing")
                        }
                        Item { Layout.fillHeight: true }
                    }
                }
            }

            Item {
                objectName: "taskDetailPanel"
                Layout.fillWidth: true
                Layout.minimumWidth: Math.round(390 * page.theme.scale)
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.fill: parent
                    spacing: Math.round(10 * page.theme.scale)

                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: !page.controller.selectedTaskId
                        spacing: Math.round(6 * page.theme.scale)
                        Text {
                            text: page.controller.text("选择任务")
                            color: page.theme.text
                            font.pixelSize: Math.round(18 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("从左侧选择一个任务。")
                            color: page.theme.muted
                            font.pixelSize: Math.round(13 * page.theme.scale)
                            wrapMode: Text.Wrap
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        spacing: Math.round(3 * page.theme.scale)

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)
                            Text {
                                Layout.fillWidth: true
                                text: page.detail.title || page.controller.text("任务详情")
                                color: page.theme.text
                                font.pixelSize: Math.round(20 * page.theme.scale)
                                font.weight: Font.DemiBold
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                            }
                            StatusTag {
                                tagText: page.detail.statusText || ""
                                tagColor: page.theme.accent
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Math.round(8 * page.theme.scale)
                            Text {
                                objectName: "taskFolderSummary"
                                Layout.fillWidth: true
                                text: String(page.detail.jobId || "").split(/[\\/]/).pop() || ""
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                elide: Text.ElideMiddle
                                textFormat: Text.PlainText
                                ToolTip.visible: folderHover.hovered
                                ToolTip.text: page.detail.jobId || ""
                                HoverHandler { id: folderHover }
                            }
                            Text {
                                text: page.controller.taskLoading
                                    ? page.controller.text("正在核验产物…")
                                    : page.controller.text("下一步：") + (page.detail.nextAction || page.controller.text("检查文件与依赖"))
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: !!page.detail.recovery && (page.detail.recovery.message || "").length > 0
                            text: page.detail.recovery && page.detail.recovery.message ? page.detail.recovery.message : ""
                            color: page.theme.error
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: String(page.detail.taskBlockReason || "").length > 0
                            text: String(page.detail.taskBlockReason || "")
                            color: page.theme.attention
                            font.pixelSize: Math.round(11 * page.theme.scale)
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        implicitHeight: deliveryContent.implicitHeight + Math.round(20 * page.theme.scale)
                        radius: Math.round(8 * page.theme.scale)
                        color: page.theme.raised
                        border.color: page.theme.line

                        RowLayout {
                            id: deliveryContent
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Math.round(10 * page.theme.scale)
                            spacing: Math.round(10 * page.theme.scale)

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(2 * page.theme.scale)
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(7 * page.theme.scale)
                                    Text {
                                        text: page.controller.text("当前交付")
                                        color: page.theme.text
                                        font.pixelSize: Math.round(12 * page.theme.scale)
                                        font.weight: Font.DemiBold
                                    }
                                    StatusTag {
                                        visible: !!page.currentDeliveryArtifact.id
                                        tagText: page.currentDeliveryArtifact.statusText || ""
                                        tagColor: page.statusColor(!!page.currentDeliveryArtifact.available)
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        visible: page.deliveryIds.length > 1
                                        text: page.deliveryIds.length.toString() + " " + page.controller.text("项")
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.currentDeliveryArtifact.label
                                        || page.controller.text("暂无可交接的产物")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(13 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: !!page.currentDeliveryArtifact.id
                                    text: page.currentDeliveryArtifact.displayPath || ""
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    elide: Text.ElideMiddle
                                    textFormat: Text.PlainText
                                }
                            }

                            UiButton {
                                objectName: "openCurrentDelivery"
                                visible: !!page.currentDeliveryArtifact.id && !!page.currentDeliveryArtifact.openable
                                theme: page.theme
                                text: page.controller.text("打开")
                                enabled: page.idle && !!page.currentDeliveryArtifact.openable
                                onClicked: page.controller.openArtifact(String(page.currentDeliveryArtifact.id || ""))
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        spacing: Math.round(8 * page.theme.scale)
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("产物")
                            color: page.theme.text
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        Text {
                            visible: page.hasHistoricalArtifacts
                            text: page.controller.text("历史版本在末尾")
                            color: page.theme.muted
                            font.pixelSize: Math.round(10 * page.theme.scale)
                        }
                        Text {
                            text: page.detail.artifacts ? page.detail.artifacts.length.toString() : "0"
                            color: page.theme.muted
                            font.pixelSize: Math.round(11 * page.theme.scale)
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: !!page.controller.selectedTaskId
                        clip: true

                        ListView {
                            id: taskArtifactList
                            objectName: "taskArtifactList"
                            anchors.fill: parent
                            clip: true
                            model: page.detail.artifacts || []
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                            delegate: ItemDelegate {
                                id: artifactRow
                                required property var modelData
                                readonly property string artifactId: String(modelData.id || "")
                                width: taskArtifactList.width
                                height: Math.max(Math.round(72 * page.theme.scale), artifactRowContent.implicitHeight + Math.round(16 * page.theme.scale))
                                highlighted: page.controller.selectedTaskArtifactId === artifactId
                                hoverEnabled: true
                                focusPolicy: Qt.StrongFocus
                                objectName: "taskArtifact_" + artifactId
                                Accessible.name: (modelData.label || "") + ", " + (modelData.statusText || "")
                                onClicked: page.controller.selectTaskArtifact(artifactId)

                                contentItem: ColumnLayout {
                                    id: artifactRowContent
                                    spacing: Math.round(2 * page.theme.scale)

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Math.round(7 * page.theme.scale)
                                        Text {
                                            Layout.fillWidth: true
                                            text: artifactRow.modelData.label || ""
                                            color: page.theme.text
                                            font.pixelSize: Math.round(12 * page.theme.scale)
                                            font.weight: artifactRow.highlighted ? Font.DemiBold : Font.Normal
                                            elide: Text.ElideRight
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            visible: page.isDeliveryArtifact(artifactRow.modelData)
                                            text: page.controller.text("当前交付")
                                            color: page.theme.accent
                                            font.pixelSize: Math.round(10 * page.theme.scale)
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            text: artifactRow.modelData.statusText || ""
                                            color: page.artifactStatusColor(artifactRow.modelData)
                                            font.pixelSize: Math.round(10 * page.theme.scale)
                                            textFormat: Text.PlainText
                                        }
                                        UiButton {
                                            objectName: "openArtifact_" + artifactRow.artifactId
                                            visible: !!artifactRow.modelData.available && !!artifactRow.modelData.openable
                                            theme: page.theme
                                            quiet: true
                                            text: page.controller.text("打开")
                                            enabled: page.idle && !!artifactRow.modelData.openable
                                            onClicked: page.controller.openArtifact(artifactRow.modelData.id)
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: artifactRow.modelData.displayPath || ""
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        elide: Text.ElideMiddle
                                        textFormat: Text.PlainText
                                        ToolTip.visible: artifactHover.hovered
                                        ToolTip.text: artifactRow.modelData.path || ""
                                        HoverHandler { id: artifactHover }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: page.controller.text("来源") + " · "
                                            + (page.detail.artifactDetails && page.detail.artifactDetails[artifactRow.artifactId]
                                                ? (page.detail.artifactDetails[artifactRow.artifactId].producerText || page.controller.text("未记录"))
                                                : page.controller.text("未记录"))
                                            + "  ·  " + page.controller.text("修订") + " "
                                            + (page.shortRevision(artifactRow.modelData.revision) || page.controller.text("未记录"))
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(9 * page.theme.scale)
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                }

                                background: Rectangle {
                                    color: artifactRow.highlighted ? page.theme.selected
                                        : artifactRow.hovered ? page.theme.hover : "transparent"
                                    Rectangle {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        height: 1
                                        color: page.theme.line
                                    }
                                    Rectangle {
                                        anchors.left: parent.left
                                        anchors.top: parent.top
                                        anchors.bottom: parent.bottom
                                        width: artifactRow.highlighted ? 2 : 0
                                        color: page.theme.accent
                                    }
                                }
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            width: parent.width - Math.round(24 * page.theme.scale)
                            visible: page.controller.selectedTaskId && (!page.detail.artifacts || page.detail.artifacts.length === 0)
                            text: page.controller.taskLoading
                                ? page.controller.text("正在检查文件身份…")
                                : page.controller.text("还没有可列出的产物。请按下一步继续处理或检查任务记录。")
                            color: page.theme.muted
                            font.pixelSize: Math.round(12 * page.theme.scale)
                            wrapMode: Text.Wrap
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }
            }

            Rectangle {
                objectName: "taskArtifactInspector"
                Layout.preferredWidth: page.inspectorWidth
                Layout.minimumWidth: page.inspectorWidth
                Layout.maximumWidth: page.inspectorWidth
                Layout.fillHeight: true
                color: page.theme.surface
                radius: Math.round(9 * page.theme.scale)
                border.color: page.theme.line
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Math.round(12 * page.theme.scale)
                    spacing: Math.round(8 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("选中详情")
                            color: page.theme.text
                            font.pixelSize: Math.round(14 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        StatusTag {
                            visible: !!page.selectedArtifact.id
                            tagText: String(page.selectedArtifact.state || "") === "historical"
                                ? page.controller.text("历史版本")
                                : page.isDeliveryArtifact(page.selectedArtifact)
                                    ? page.controller.text("当前交付") : page.controller.text("产物")
                            tagColor: page.theme.accent
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: page.theme.line
                    }

                    ScrollView {
                        id: inspectorScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        contentWidth: availableWidth
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                        ColumnLayout {
                            width: inspectorScroll.availableWidth
                            spacing: Math.round(7 * page.theme.scale)

                            Text {
                                Layout.fillWidth: true
                                visible: !page.selectedArtifact.id
                                text: page.controller.text("选择产物查看状态、来源和本地操作。")
                                color: page.theme.muted
                                font.pixelSize: Math.round(12 * page.theme.scale)
                                wrapMode: Text.Wrap
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: !!page.selectedArtifact.id
                                spacing: Math.round(7 * page.theme.scale)

                                Text {
                                    Layout.fillWidth: true
                                    text: page.selectedArtifact.label || page.controller.text("未命名产物")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(15 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.Wrap
                                    textFormat: Text.PlainText
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(7 * page.theme.scale)
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.selectedArtifact.statusText || ""
                                        color: page.artifactStatusColor(page.selectedArtifact)
                                        font.pixelSize: Math.round(12 * page.theme.scale)
                                        font.weight: Font.DemiBold
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        text: page.selectedArtifact.role || ""
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        textFormat: Text.PlainText
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    visible: String(page.selectedArtifact.state || "") === "historical"
                                    text: page.controller.text("历史版本只读，不参与当前交付或下一步。")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    wrapMode: Text.Wrap
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: page.theme.line
                                }

                                FactRow {
                                    factName: page.controller.text("文件位置")
                                    factValue: page.selectedArtifact.displayPath || page.selectedArtifact.path || ""
                                    factTip: page.selectedArtifact.path || ""
                                }
                                FactRow {
                                    factName: page.controller.text("修订")
                                    factValue: page.shortRevision(page.selectedArtifact.revision)
                                }
                                FactRow {
                                    factName: page.controller.text("来源")
                                    factValue: page.selectedSource.producerText || ""
                                }
                                FactRow {
                                    factName: "SHA-256"
                                    factValue: page.selectedSource.sha256Short || ""
                                    factTip: page.selectedSource.sha256 || ""
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: page.theme.line
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: page.controller.text("上游依赖")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(12 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                }

                                Text {
                                    Layout.fillWidth: true
                                    visible: !page.selectedSource.dependencies
                                        || page.selectedSource.dependencies.length === 0
                                    text: page.controller.text("没有已登记的上游依赖。")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                    wrapMode: Text.Wrap
                                }

                                Repeater {
                                    model: page.selectedSource.dependencies || []
                                    delegate: ColumnLayout {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        spacing: Math.round(1 * page.theme.scale)
                                        Text {
                                            Layout.fillWidth: true
                                            text: (modelData.label || "") + " · " + (modelData.statusText || "")
                                            color: page.theme.text
                                            font.pixelSize: Math.round(10 * page.theme.scale)
                                            elide: Text.ElideRight
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: (modelData.displayPath || "")
                                                + (String(modelData.revision || "").length > 0
                                                    ? "  ·  " + page.controller.text("修订") + " " + page.shortRevision(modelData.revision)
                                                    : "")
                                            color: page.theme.muted
                                            font.pixelSize: Math.round(9 * page.theme.scale)
                                            elide: Text.ElideMiddle
                                            textFormat: Text.PlainText
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: page.theme.line
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: Math.round(7 * page.theme.scale)
                                    UiButton {
                                        objectName: "openInspectorArtifact"
                                        theme: page.theme
                                        text: page.controller.text("打开文件")
                                        enabled: page.idle && !!page.selectedArtifact.openable
                                        onClicked: page.controller.openArtifact(String(page.selectedArtifact.id || ""))
                                    }
                                    UiButton {
                                        visible: !!page.selectedArtifact.available
                                            && (page.selectedArtifact.role === "editor_project" || page.selectedArtifact.role === "tuned_project")
                                            && String(page.selectedArtifact.path || "").toLowerCase().endsWith(".svp")
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text("调音")
                                        enabled: page.idle
                                        onClicked: page.controller.tuneArtifact(String(page.selectedArtifact.id || ""))
                                    }
                                    UiButton {
                                        objectName: "subtitleArtifact_" + String(page.selectedArtifact.id || "")
                                        visible: !!page.selectedArtifact.available && page.selectedArtifact.role === "midi"
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text("做字幕")
                                        enabled: page.idle && !!page.selectedArtifact.openable
                                        onClicked: page.controller.editSubtitleArtifact(String(page.selectedArtifact.id || ""))
                                    }
                                    UiButton {
                                        objectName: "artifactSource_" + String(page.selectedArtifact.id || "")
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text(page.expandedArtifactId === String(page.selectedArtifact.id || "")
                                            ? "收起来源" : "来源与依赖")
                                        onClicked: {
                                            const key = String(page.selectedArtifact.id || "")
                                            page.expandedArtifactId = page.expandedArtifactId === key ? "" : key
                                        }
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    visible: page.expandedArtifactId === String(page.selectedArtifact.id || "")
                                    spacing: Math.round(5 * page.theme.scale)
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.selectedArtifact.path || ""
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(9 * page.theme.scale)
                                        wrapMode: Text.WrapAnywhere
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        visible: String(page.selectedSource.sha256 || "").length > 0
                                        text: page.selectedSource.sha256 || ""
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(9 * page.theme.scale)
                                        wrapMode: Text.WrapAnywhere
                                        textFormat: Text.PlainText
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: page.controller.text("当前交付见中间栏。")
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(9 * page.theme.scale)
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: page.theme.line
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(62 * page.theme.scale)
            color: page.theme.surface

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Math.round(22 * page.theme.scale)
                anchors.rightMargin: Math.round(22 * page.theme.scale)
                spacing: Math.round(14 * page.theme.scale)

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Math.round(1 * page.theme.scale)
                    Text {
                        Layout.fillWidth: true
                        text: page.controller.selectedTaskId
                            ? (page.detail.title || page.controller.text("任务详情"))
                            : page.controller.text("选择任务")
                        color: page.theme.text
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Text {
                        Layout.fillWidth: true
                        text: {
                            if (!page.controller.selectedTaskId)
                                return page.controller.text("选择任务后显示当前交付与下一步。")
                            if (page.controller.taskLoading)
                                return page.controller.text("正在核验产物…")
                            const delivery = page.currentDeliveryArtifact.label
                                || page.controller.text("暂无可交接的产物")
                            if (page.primaryKind.length === 0)
                                return page.controller.text("当前交付") + " · " + delivery
                                    + "  ·  " + page.controller.text("下一步：")
                                    + (page.detail.nextAction || page.controller.text("检查文件与依赖"))
                            return page.controller.text("当前交付") + " · " + delivery
                        }
                        color: page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                }

                UiButton {
                    objectName: "taskPrimaryAction"
                    visible: !!page.controller.selectedTaskId && page.primaryKind.length > 0
                    theme: page.theme
                    primary: true
                    text: page.primaryActionText(page.primaryKind)
                    enabled: !!page.primaryAction.enabled
                    onClicked: page.controller.runTaskPrimaryAction()
                }
            }
        }
    }

    Drawer {
        id: taskManagementDrawer
        objectName: "taskManagementDrawer"
        parent: Overlay.overlay
        edge: Qt.RightEdge
        modal: true
        interactive: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        width: Math.min(parent ? parent.width - Math.round(28 * page.theme.scale) : Math.round(420 * page.theme.scale),
                        Math.round(430 * page.theme.scale))

        contentItem: Rectangle {
            color: page.theme.surface

            ScrollView {
                id: managementScroll
                anchors.fill: parent
                contentWidth: availableWidth
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: managementScroll.availableWidth - Math.round(28 * page.theme.scale)
                    x: Math.round(14 * page.theme.scale)
                    spacing: Math.round(9 * page.theme.scale)

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("任务管理")
                            color: page.theme.text
                            font.pixelSize: Math.round(18 * page.theme.scale)
                            font.weight: Font.DemiBold
                        }
                        UiButton {
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("关闭")
                            onClicked: taskManagementDrawer.close()
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.detail.title || page.controller.text("未命名任务")
                        color: page.theme.text
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: page.theme.line }

                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text("恢复与交接")
                        color: page.theme.text
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.theme.scale)
                        UiButton {
                            objectName: "resumeTask"
                            visible: !!page.detail.canResume
                            theme: page.theme
                            text: page.controller.text("继续 / 重试")
                            enabled: page.idle && !!page.detail.canResume
                            onClicked: page.controller.resumeSelectedTask()
                        }
                        UiButton {
                            objectName: "inspectTaskProgress"
                            theme: page.theme
                            text: page.controller.text("查看进度与错误")
                            enabled: page.idle || (page.controller.preparationBusy
                                && page.controller.preparationTask.job === page.controller.selectedTaskId)
                            onClicked: page.controller.inspectTaskProgress(page.controller.selectedTaskId)
                        }
                        UiButton {
                            objectName: "reprocessTask"
                            theme: page.theme
                            text: page.controller.text("按当前设置重做…")
                            enabled: page.idle && !!page.detail.canReprocess
                            onClicked: {
                                reprocessConfirm.job = page.controller.selectedTaskId
                                reprocessConfirm.taskTitle = page.detail.title
                                reprocessConfirm.open()
                            }
                        }
                        UiButton {
                            objectName: "handoffTask"
                            visible: !!page.detail.canDispatch
                            theme: page.theme
                            text: page.controller.text(page.detail.handoff === "audio_ready" ? "分轨送入简易混音"
                                : page.detail.handoff === "awaiting_tuning"
                                    ? "调音或查看工程"
                                : page.detail.handoff === "mixdown_ready" ? "合成视频"
                                : page.detail.handoff === "video_ready" ? "准备发布" : "打开发布资料")
                            enabled: page.idle
                            onClicked: page.controller.dispatchTaskHandoff()
                        }
                        UiButton {
                            objectName: "mixTaskStems"
                            visible: page.detail.handoff !== "audio_ready" && (page.detail.mixArtifactIds || []).length > 0
                            theme: page.theme
                            text: page.controller.text("分轨送入简易混音")
                            enabled: page.idle
                            onClicked: page.controller.mixTaskStems()
                        }
                        UiButton {
                            objectName: "composeTaskVideo"
                            visible: page.detail.handoff !== "mixdown_ready" && !!page.detail.studioTargets
                                     && (page.detail.studioTargets.video_mixdown || []).length > 0
                            theme: page.theme
                            text: page.controller.text("从混音合成视频")
                            enabled: page.idle
                            onClicked: page.controller.composeTaskVideo()
                        }
                        UiButton {
                            objectName: "prepareTaskPublication"
                            visible: !page.detail.canDispatch && !!page.detail.studioTargets
                                     && (page.detail.studioTargets.publishing || []).length > 0
                            theme: page.theme
                            text: page.controller.text("重新准备发布")
                            enabled: page.idle
                            onClicked: page.controller.prepareTaskPublication()
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: page.theme.line }

                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text("任务资料")
                        color: page.theme.text
                        font.pixelSize: Math.round(13 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }

                    Text {
                        objectName: "taskFolder"
                        Layout.fillWidth: true
                        text: page.detail.jobId || ""
                        color: page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        elide: Text.ElideMiddle
                        textFormat: Text.PlainText
                        ToolTip.visible: managementFolderHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: managementFolderHover }
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.theme.scale)
                        UiButton {
                            objectName: "openTaskFolder"
                            theme: page.theme
                            text: page.controller.text("打开任务目录")
                            glyph: "folder"
                            enabled: page.idle
                            onClicked: page.controller.openSelectedTaskFolder()
                        }
                        UiButton {
                            objectName: "openTaskLog"
                            visible: !!page.detail.hasLog
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("打开任务日志")
                            onClicked: page.controller.openSelectedTaskLog()
                        }
                        UiButton {
                            objectName: "registerExistingVideo"
                            visible: !page.detail.hasVideoBinding && !page.detail.hasPublication
                            theme: page.theme
                            text: page.controller.text("登记已有视频…")
                            enabled: page.mutationIdle && !!page.detail.canRegisterVideo
                            onClicked: {
                                registerVideoDialog.expectedVideoJobId = page.controller.selectedTaskId
                                registerVideoDialog.expectedVideoTitle = page.detail.title || ""
                                existingVideoDialog.open()
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        implicitHeight: requestBox.implicitHeight + Math.round(18 * page.theme.scale)
                        radius: Math.round(6 * page.theme.scale)
                        color: page.theme.base
                        border.color: page.theme.line

                        ColumnLayout {
                            id: requestBox
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Math.round(9 * page.theme.scale)
                            spacing: Math.round(7 * page.theme.scale)

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(8 * page.theme.scale)
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: Math.round(2 * page.theme.scale)
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.controller.text("请求摘要")
                                        color: page.theme.text
                                        font.pixelSize: Math.round(12 * page.theme.scale)
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.detail.requestPreview || page.controller.text("任务未记录可展示的请求摘要。")
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                        ToolTip.visible: requestPreviewHover.hovered && truncated
                                        ToolTip.text: text
                                        HoverHandler { id: requestPreviewHover }
                                    }
                                }
                                UiButton {
                                    objectName: "toggleRequestSummary"
                                    theme: page.theme
                                    quiet: true
                                    text: page.controller.text(page.requestExpanded ? "收起" : "展开")
                                    onClicked: page.requestExpanded = !page.requestExpanded
                                }
                            }

                            Repeater {
                                model: page.requestExpanded ? (page.detail.requestSummary || []) : []
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: Math.round(8 * page.theme.scale)
                                    Text {
                                        Layout.preferredWidth: Math.round(92 * page.theme.scale)
                                        text: modelData.label || ""
                                        color: page.theme.muted
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.value || ""
                                        color: page.theme.text
                                        font.pixelSize: Math.round(10 * page.theme.scale)
                                        wrapMode: Text.Wrap
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        objectName: "manualBindingsPanel"
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        implicitHeight: manualBindings.implicitHeight + Math.round(18 * page.theme.scale)
                        radius: Math.round(6 * page.theme.scale)
                        color: page.theme.base
                        border.color: page.theme.line

                        ColumnLayout {
                            id: manualBindings
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Math.round(9 * page.theme.scale)
                            spacing: Math.round(7 * page.theme.scale)

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Math.round(8 * page.theme.scale)
                                Text {
                                    Layout.fillWidth: true
                                    text: page.controller.text("人工登记")
                                    color: page.theme.text
                                    font.pixelSize: Math.round(12 * page.theme.scale)
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    text: page.bindingRows().length.toString()
                                    color: page.theme.muted
                                    font.pixelSize: Math.round(10 * page.theme.scale)
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: page.controller.text("登记后会核验文件与当前上游修订。取消登记和重建只修改任务记录，外部工程、音频或视频保留在原位置。")
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                wrapMode: Text.Wrap
                            }

                            Flow {
                                Layout.fillWidth: true
                                spacing: Math.round(7 * page.theme.scale)
                                UiButton {
                                    objectName: "registerTaskProject"
                                    theme: page.theme
                                    text: page.controller.text(page.bindingId("project").length > 0
                                        ? "替换编辑器工程…" : "登记编辑器工程…")
                                    enabled: page.mutationIdle && !!page.detail.canRegisterProject
                                    onClicked: page.beginArtifactRegistration("project")
                                }
                                UiButton {
                                    objectName: "registerTaskMixdown"
                                    theme: page.theme
                                    text: page.controller.text(page.bindingId("mixdown").length > 0
                                        ? "替换 Mixdown…" : "登记 Mixdown…")
                                    enabled: page.mutationIdle && !!page.detail.canRegisterMixdown
                                    onClicked: page.beginArtifactRegistration("mixdown")
                                }
                                UiButton {
                                    objectName: "rebuildTaskArtifacts"
                                    theme: page.theme
                                    quiet: true
                                    text: page.controller.text("重建文件清单…")
                                    enabled: page.mutationIdle && !!page.detail.canRebuildArtifacts
                                    onClicked: page.beginArtifactRebuild()
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                visible: page.bindingRows().length === 0
                                text: page.controller.text("此任务还没有人工登记。自动生成并核验通过的产物仍会显示在产物列表。")
                                color: page.theme.muted
                                font.pixelSize: Math.round(10 * page.theme.scale)
                                wrapMode: Text.Wrap
                            }

                            Repeater {
                                model: page.bindingRows()
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: Math.round(7 * page.theme.scale)

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: Math.round(1 * page.theme.scale)
                                        Text {
                                            Layout.fillWidth: true
                                            text: (modelData.label || "") + " · " + (modelData.fileName || "")
                                            color: page.theme.text
                                            font.pixelSize: Math.round(10 * page.theme.scale)
                                            elide: Text.ElideRight
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: (modelData.displayPath || "") + "  ·  "
                                                + page.controller.text("修订") + " " + page.shortRevision(modelData.revision)
                                            color: page.theme.muted
                                            font.pixelSize: Math.round(9 * page.theme.scale)
                                            elide: Text.ElideMiddle
                                            textFormat: Text.PlainText
                                            ToolTip.visible: bindingHover.hovered && truncated
                                            ToolTip.text: modelData.path || text
                                            HoverHandler { id: bindingHover }
                                        }
                                    }
                                    Text {
                                        text: modelData.statusText || ""
                                        color: page.statusColor(!!modelData.available)
                                        font.pixelSize: Math.round(9 * page.theme.scale)
                                        textFormat: Text.PlainText
                                    }
                                    UiButton {
                                        objectName: "unlinkTaskBinding_" + String(modelData.kind || "")
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text("取消登记…")
                                        enabled: page.mutationIdle && (modelData.kind === "project"
                                            ? !!page.detail.canUnbindProject
                                            : modelData.kind === "mixdown"
                                                ? !!page.detail.canUnbindMixdown
                                                : !!page.detail.canUnbindVideo)
                                        onClicked: page.beginArtifactUnlink(modelData)
                                    }
                                }
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: page.theme.line }

                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text("危险操作")
                        color: page.theme.error
                        font.pixelSize: Math.round(12 * page.theme.scale)
                        font.weight: Font.DemiBold
                    }

                    Text {
                        Layout.fillWidth: true
                        text: page.controller.text("重命名只修改任务名称；删除会移除整个任务目录，并继续受现有引用与忙状态保护。")
                        color: page.theme.muted
                        font.pixelSize: Math.round(10 * page.theme.scale)
                        wrapMode: Text.Wrap
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: Math.round(7 * page.theme.scale)
                        UiButton {
                            objectName: "renameTask"
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("重命名")
                            enabled: page.mutationIdle && !!page.detail.canRename
                            onClicked: renameDialog.open()
                        }
                        UiButton {
                            objectName: "deleteTask"
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("删除…")
                            enabled: page.mutationIdle && !!page.detail.canDelete
                            onClicked: deleteDialog.open()
                        }
                    }

                    Item { Layout.preferredHeight: Math.round(12 * page.theme.scale) }
                }
            }
        }
    }


    Dialog {
        id: reprocessConfirm
        objectName: "reprocessTaskConfirm"
        property string job: ""
        property string taskTitle: ""
        title: page.controller.text("用当前准备设置另建任务")
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        width: Math.min(parent.width - 40, 470 * page.theme.scale)
        standardButtons: Dialog.Cancel | Dialog.Ok
        onAccepted: page.controller.reprocessSelectedTask(job)
        contentItem: Text {
            text: reprocessConfirm.taskTitle + "\n" + page.controller.text("复用可用的原素材，按当前准备页设置处理；原任务及产物保留。")
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: page.theme.text
            font.pixelSize: 13 * page.theme.scale
        }
    }

    FileDialog {
        id: registerArtifactFileDialog
        objectName: "registerTaskArtifactFileDialog"
        property string bindingKind: "project"
        title: page.controller.text(bindingKind === "project" ? "选择编辑器工程" : "选择 Mixdown 音频")
        fileMode: FileDialog.OpenFile
        nameFilters: bindingKind === "project"
            ? [page.controller.text("编辑器工程 (*.svp *.ustx *.vsqx)")]
            : [page.controller.text("音频 (*.wav *.flac *.ogg *.mp3 *.m4a)")]
        onAccepted: {
            registerArtifactDialog.pendingArtifactUrl = selectedFile
            registerArtifactDialog.open()
        }
    }

    Dialog {
        id: registerArtifactDialog
        objectName: "registerTaskArtifactDialog"
        property string expectedBindingJobId: ""
        property string expectedBindingTitle: ""
        property string expectedBindingKind: "project"
        property string expectedBindingId: ""
        property string expectedBindingRevision: ""
        property string expectedOldPath: ""
        property url pendingArtifactUrl

        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 520, 600)
        padding: 18

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text(registerArtifactDialog.expectedBindingKind === "project"
                    ? "登记外部编辑器工程" : "登记外部 Mixdown")
                color: page.theme.text
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: registerArtifactDialog.expectedBindingTitle || page.controller.text("未命名任务")
                color: page.theme.text
                font.pixelSize: 13 * page.theme.scale
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: String(registerArtifactDialog.pendingArtifactUrl)
                color: page.theme.text
                font.pixelSize: 11 * page.theme.scale
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                visible: registerArtifactDialog.expectedBindingId.length > 0
                text: page.controller.text("这会替换当前任务中的同类人工登记：")
                    + "\n" + (registerArtifactDialog.expectedOldPath || "")
                color: page.theme.accent
                font.pixelSize: 11 * page.theme.scale
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("登记时会完整核验文件内容和可用的上游修订；不会移动或改写所选文件。替换时旧登记会失效，但旧文件仍保留在原位置。")
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    text: page.controller.text("取消")
                    quiet: true
                    onClicked: registerArtifactDialog.close()
                }
                UiButton {
                    objectName: "confirmRegisterTaskArtifact"
                    theme: page.theme
                    primary: true
                    text: page.controller.text("核验并登记")
                    enabled: page.mutationIdle
                        && registerArtifactDialog.expectedBindingJobId === page.controller.selectedTaskId
                        && (registerArtifactDialog.expectedBindingKind === "project"
                            ? !!page.detail.canRegisterProject : !!page.detail.canRegisterMixdown)
                        && registerArtifactDialog.expectedBindingId
                            === page.bindingId(registerArtifactDialog.expectedBindingKind)
                        && registerArtifactDialog.expectedBindingRevision
                            === page.bindingRevision(registerArtifactDialog.expectedBindingKind)
                        && String(registerArtifactDialog.pendingArtifactUrl).length > 0
                    onClicked: {
                        page.controller.registerSelectedTaskFile(
                            registerArtifactDialog.expectedBindingJobId,
                            registerArtifactDialog.expectedBindingKind,
                            registerArtifactDialog.pendingArtifactUrl)
                        registerArtifactDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: unlinkBindingDialog
        objectName: "unlinkTaskBindingDialog"
        property string expectedBindingJobId: ""
        property string expectedBindingTitle: ""
        property string expectedBindingKind: ""
        property string expectedBindingId: ""
        property string expectedBindingRevision: ""
        property string expectedBindingPath: ""
        property bool hasRelatedPublication: false

        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 500, 580)
        padding: 18

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("取消人工登记")
                color: page.theme.text
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: unlinkBindingDialog.expectedBindingTitle || page.controller.text("未命名任务")
                color: page.theme.text
                font.pixelSize: 13 * page.theme.scale
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: unlinkBindingDialog.expectedBindingPath
                color: page.theme.muted
                font.pixelSize: 11 * page.theme.scale
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("只会从此任务移除当前登记的引用，不会删除、移动或改写外部文件。自动产出的文件和其他登记不受影响。")
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                visible: unlinkBindingDialog.expectedBindingKind === "video"
                    && unlinkBindingDialog.hasRelatedPublication
                text: page.controller.text("此任务还关联发布准备资料；取消成品视频登记时也会取消该发布关联。发布包中的文件仍保留。")
                color: page.theme.accent
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    text: page.controller.text("保留登记")
                    quiet: true
                    onClicked: unlinkBindingDialog.close()
                }
                UiButton {
                    objectName: "confirmUnlinkTaskBinding"
                    theme: page.theme
                    text: page.controller.text("取消此项登记")
                    enabled: page.mutationIdle
                        && unlinkBindingDialog.expectedBindingJobId === page.controller.selectedTaskId
                        && unlinkBindingDialog.expectedBindingKind.length > 0
                        && unlinkBindingDialog.expectedBindingId
                            === page.bindingId(unlinkBindingDialog.expectedBindingKind)
                        && unlinkBindingDialog.expectedBindingRevision
                            === page.bindingRevision(unlinkBindingDialog.expectedBindingKind)
                    onClicked: {
                        page.controller.unlinkSelectedTaskBinding(
                            unlinkBindingDialog.expectedBindingJobId,
                            unlinkBindingDialog.expectedBindingKind,
                            unlinkBindingDialog.expectedBindingId,
                            unlinkBindingDialog.expectedBindingRevision)
                        unlinkBindingDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: rebuildArtifactsDialog
        objectName: "rebuildTaskArtifactsDialog"
        property string expectedRebuildJobId: ""
        property string expectedRebuildTitle: ""
        property string expectedIndexRevision: ""

        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 500, 580)
        padding: 18

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("重建任务文件清单")
                color: page.theme.text
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: rebuildArtifactsDialog.expectedRebuildTitle || page.controller.text("未命名任务")
                color: page.theme.text
                font.pixelSize: 13 * page.theme.scale
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("将根据现有处理记录重新核验文件清单，并保留旧清单备份。人工登记的工程、Mixdown、视频和发布选择会从任务记录恢复。旧任务缺少校验记录的文件会按当前内容登记。")
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("重建不会删除或改写任务内外的音视频、工程或其他用户文件。")
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    text: page.controller.text("取消")
                    quiet: true
                    onClicked: rebuildArtifactsDialog.close()
                }
                UiButton {
                    objectName: "confirmRebuildTaskArtifacts"
                    theme: page.theme
                    primary: true
                    text: page.controller.text("重建清单")
                    enabled: page.mutationIdle && !!page.detail.canRebuildArtifacts
                        && rebuildArtifactsDialog.expectedRebuildJobId === page.controller.selectedTaskId
                        && rebuildArtifactsDialog.expectedIndexRevision
                            === String(page.detail.artifactIndexRevision || "")
                    onClicked: {
                        page.controller.rebuildSelectedTaskArtifacts(
                            rebuildArtifactsDialog.expectedRebuildJobId,
                            rebuildArtifactsDialog.expectedIndexRevision)
                        rebuildArtifactsDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: renameDialog
        objectName: "renameTaskDialog"
        property string expectedJobId: ""
        property string expectedTitle: ""
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 420, 440)
        padding: 18

        onOpened: {
            expectedJobId = page.detail.jobId || ""
            expectedTitle = page.detail.title || ""
            renameField.text = expectedTitle
            renameField.forceActiveFocus()
            renameField.selectAll()
        }

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("重命名任务")
                color: page.theme.text
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("只修改任务名称，不移动或改写任务目录和产物。")
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            TextField {
                id: renameField
                objectName: "renameTaskField"
                Layout.fillWidth: true
                placeholderText: page.controller.text("任务名称")
                selectByMouse: true
                maximumLength: 120
                Accessible.name: placeholderText
                onAccepted: {
                    if (page.mutationIdle && text.trim().length > 0) {
                        page.controller.renameSelectedTask(text.trim(), renameDialog.expectedJobId)
                        renameDialog.close()
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    text: page.controller.text("取消")
                    quiet: true
                    onClicked: renameDialog.close()
                }
                UiButton {
                    objectName: "confirmRenameTask"
                    theme: page.theme
                    primary: true
                    text: page.controller.text("保存名称")
                    enabled: page.mutationIdle && renameDialog.expectedJobId.length > 0 && renameField.text.trim().length > 0
                    onClicked: {
                        page.controller.renameSelectedTask(renameField.text.trim(), renameDialog.expectedJobId)
                        renameDialog.close()
                    }
                }
            }
        }
    }

    Dialog {
        id: deleteDialog
        objectName: "deleteTaskDialog"
        property string expectedJobId: ""
        property string expectedTitle: ""
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 440, 500)
        padding: 18

        onOpened: {
            expectedJobId = page.detail.jobId || ""
            expectedTitle = page.detail.title || ""
        }

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("删除任务及整个目录")
                color: page.theme.error
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: deleteDialog.expectedTitle || page.controller.text("未命名任务")
                color: page.theme.text
                font.pixelSize: 14 * page.theme.scale
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: deleteDialog.expectedJobId
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("该目录中的音频、MIDI、工程、发布资料和其他输出都会永久删除。此操作无法撤销，其他任务仍在引用时删除会被拒绝。")
                color: page.theme.error
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    text: page.controller.text("取消")
                    quiet: true
                    onClicked: deleteDialog.close()
                }
                UiButton {
                    objectName: "confirmDeleteTask"
                    theme: page.theme
                    text: page.controller.text("永久删除")
                    enabled: page.mutationIdle && !!page.detail.canDelete && deleteDialog.expectedJobId.length > 0
                    onClicked: {
                        page.controller.deleteSelectedTask(deleteDialog.expectedJobId)
                        deleteDialog.close()
                    }
                }
            }
        }
    }

    FileDialog {
        id: existingVideoDialog
        objectName: "existingVideoDialog"
        title: page.controller.text("选择已有本地视频")
        fileMode: FileDialog.OpenFile
        nameFilters: [page.controller.text("视频 (*.mp4 *.mkv *.mov *.webm)")]
        onAccepted: {
            registerVideoDialog.pendingVideoUrl = selectedFile
            registerVideoDialog.open()
        }
    }

    Dialog {
        id: registerVideoDialog
        objectName: "registerExistingVideoDialog"
        property string expectedVideoJobId: ""
        property string expectedVideoTitle: ""
        property url pendingVideoUrl

        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        x: parent ? Math.max(18, (parent.width - width) / 2) : 18
        y: parent ? Math.max(18, (parent.height - height) / 2) : 18
        width: Math.min(parent ? parent.width - 36 : 500, 560)
        padding: 18

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: page.controller.text("登记已有成品视频")
                color: page.theme.text
                font.pixelSize: 17 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: registerVideoDialog.expectedVideoTitle || page.controller.text("未命名任务")
                color: page.theme.text
                font.pixelSize: 13 * page.theme.scale
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: String(registerVideoDialog.pendingVideoUrl)
                color: page.theme.muted
                font.pixelSize: 11 * page.theme.scale
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("该入口只把所选视频显式登记到当前来源任务，依赖列表保持为空；不会推断 Mixdown、调音工程或其他上游，也不会覆盖任务已有的视频或发布资料。")
                color: page.theme.muted
                font.pixelSize: 12 * page.theme.scale
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UiButton {
                    theme: page.theme
                    text: page.controller.text("取消")
                    quiet: true
                    onClicked: registerVideoDialog.close()
                }
                UiButton {
                    objectName: "confirmRegisterExistingVideo"
                    theme: page.theme
                    primary: true
                    text: page.controller.text("登记到当前任务")
                    enabled: page.mutationIdle
                        && !!page.detail.canRegisterVideo
                        && registerVideoDialog.expectedVideoJobId === page.controller.selectedTaskId
                        && String(registerVideoDialog.pendingVideoUrl).length > 0
                    onClicked: {
                        page.controller.registerSelectedTaskVideo(
                            registerVideoDialog.expectedVideoJobId,
                            registerVideoDialog.pendingVideoUrl)
                        registerVideoDialog.close()
                    }
                }
            }
        }
    }
}
