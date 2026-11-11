import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "resultsPage"
    required property QtObject theme
    required property QtObject controller
    contentWidth: availableWidth
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    readonly property bool stacked: availableWidth < (theme.scale > 1.2 ? 980 : 840)
    readonly property bool idle: !controller.taskLoading && !controller.workBusy
    readonly property var detail: controller.taskDetail
    readonly property real wideCardHeight: Math.max(430, availableHeight * 0.66)
    property bool requestExpanded: false
    property string expandedArtifactId: ""

    function statusColor(available) {
        return available ? theme.success : theme.error
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
            { label: controller.text("全部状态"), value: "all" },
            { label: controller.text("已完成"), value: "done" },
            { label: controller.text("待处理 / 中断"), value: "pending" },
            { label: controller.text("失败"), value: "failed" },
            { label: controller.text("处理中"), value: "running" }
        ]
    }

    function taskSortOptions() {
        return [
            { label: controller.text("最近更新"), value: "recent" },
            { label: controller.text("名称 A–Z"), value: "title" }
        ]
    }

    ColumnLayout {
        width: page.availableWidth
        spacing: 12

        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: 22
            Layout.leftMargin: 22
            Layout.rightMargin: 22
            spacing: 5
            Text {
                Layout.fillWidth: true
                text: page.controller.text("任务与结果")
                color: page.theme.text
                font.pixelSize: 25 * page.theme.scale
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: page.controller.text("查找已有任务，核验产物，并继续真实可用的下一步。")
                color: page.theme.muted
                font.pixelSize: 13 * page.theme.scale
                wrapMode: Text.Wrap
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 22
            Layout.rightMargin: 22
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                TextField {
                    id: search
                    objectName: "taskSearch"
                    Layout.fillWidth: true
                    Layout.maximumWidth: 480
                    Layout.preferredHeight: Math.max(38, implicitHeight)
                    placeholderText: page.controller.text("搜索歌名、任务名、BV 号或保存目录")
                    text: page.controller.taskQuery
                    font.pixelSize: 13 * page.theme.scale
                    color: page.theme.text
                    placeholderTextColor: page.theme.muted
                    selectByMouse: true
                    Accessible.name: placeholderText
                    onTextEdited: page.controller.setTaskQuery(text)
                    onAccepted: page.controller.refreshTasks()
                    background: Rectangle {
                        radius: 7
                        color: page.theme.surface
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
                Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                UiComboBox {
                    objectName: "taskStateFilter"
                    Layout.preferredWidth: Math.round(170 * page.theme.scale)
                    Layout.maximumWidth: Math.round(210 * page.theme.scale)
                    theme: page.theme
                    model: page.taskFilterOptions()
                    currentIndex: page.optionIndex(model, page.controller.taskStateFilter)
                    onActivated: page.controller.setTaskStateFilter(currentValue)
                    Accessible.name: page.controller.text("任务状态筛选")
                }
                UiComboBox {
                    objectName: "taskSort"
                    Layout.preferredWidth: Math.round(145 * page.theme.scale)
                    Layout.maximumWidth: Math.round(180 * page.theme.scale)
                    theme: page.theme
                    model: page.taskSortOptions()
                    currentIndex: page.optionIndex(model, page.controller.taskSort)
                    onActivated: page.controller.setTaskSort(currentValue)
                    Accessible.name: page.controller.text("任务排序")
                }
                Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
                UiButton {
                    objectName: "refreshTasks"
                    theme: page.theme
                    text: page.controller.text(page.controller.taskLoading ? "正在检查…" : "刷新任务")
                    enabled: !page.controller.taskLoading && !page.controller.workBusy
                    onClicked: page.controller.refreshTasks()
                }
            }
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 22
            Layout.rightMargin: 22
            Layout.bottomMargin: 22
            columns: page.stacked ? 1 : 2
            columnSpacing: 14
            rowSpacing: 12

            Rectangle {
                objectName: "taskListPanel"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: page.stacked ? Math.max(280, Math.min(350, page.availableHeight * 0.42)) : page.wideCardHeight
                color: page.theme.surface
                radius: 9
                border.color: page.theme.line
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 1
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round(42 * page.theme.scale)
                        Layout.leftMargin: 14
                        Layout.rightMargin: 12
                        spacing: 8
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("任务")
                            color: page.theme.text
                            font.pixelSize: 14 * page.theme.scale
                            font.weight: Font.DemiBold
                        }
                        Text {
                            objectName: "taskCount"
                            text: taskList.count.toString()
                            color: page.theme.muted
                            font.pixelSize: 12 * page.theme.scale
                        }
                    }

                    Rectangle { Layout.fillWidth: true; height: 1; color: page.theme.line }

                    ListView {
                        id: taskList
                        objectName: "taskList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.controller.taskModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}

                        delegate: ItemDelegate {
                            id: taskRow
                            required property string jobId
                            required property string title
                            required property string statusText
                            required property string summary
                            width: taskList.width
                            height: Math.max(64, taskRowContent.implicitHeight + 18)
                            highlighted: page.controller.selectedTaskId === jobId
                            hoverEnabled: true
                            focusPolicy: Qt.StrongFocus
                            Accessible.name: title + ", " + statusText + ", " + summary
                            onClicked: page.controller.selectTask(jobId)

                            contentItem: ColumnLayout {
                                id: taskRowContent
                                spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        Layout.fillWidth: true
                                        text: taskRow.title
                                        color: page.theme.text
                                        font.pixelSize: 14 * page.theme.scale
                                        font.weight: taskRow.highlighted ? Font.DemiBold : Font.Normal
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        text: taskRow.statusText
                                        color: taskRow.highlighted ? page.theme.accent : page.theme.muted
                                        font.pixelSize: 12 * page.theme.scale
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: taskRow.summary
                                    color: page.theme.muted
                                    font.pixelSize: 12 * page.theme.scale
                                    elide: Text.ElideRight
                                    textFormat: Text.PlainText
                                }
                            }

                            background: Rectangle {
                                color: taskRow.highlighted ? "#312940"
                                    : taskRow.hovered ? page.theme.raised : "transparent"
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

                    Text {
                        Layout.fillWidth: true
                        Layout.margins: 16
                        visible: page.controller.taskLoading || page.controller.taskModel.count === 0
                        horizontalAlignment: Text.AlignHCenter
                        text: page.controller.taskLoading
                            ? page.controller.text("正在读取任务…")
                            : page.controller.taskQuery.length > 0
                                ? page.controller.text("没有匹配的任务，可清空搜索后刷新。")
                                : page.controller.taskStateFilter !== "all"
                                    ? page.controller.text("当前状态下没有任务，可切换状态筛选后刷新。")
                                    : page.controller.text("还没有任务。完成首次准备后会显示在这里。")
                        color: page.theme.muted
                        font.pixelSize: 13 * page.theme.scale
                        wrapMode: Text.Wrap
                    }
                }
            }

            Rectangle {
                objectName: "taskDetailPanel"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredHeight: page.stacked
                    ? detailContent.implicitHeight + 32
                    : Math.max(page.wideCardHeight, detailContent.implicitHeight + 32)
                color: page.theme.surface
                radius: 9
                border.color: page.theme.line
                clip: true

                ColumnLayout {
                    id: detailContent
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 16
                    spacing: 11

                    Text {
                        Layout.fillWidth: true
                        visible: !page.controller.selectedTaskId
                        text: page.controller.text("选择任务")
                        color: page.theme.text
                        font.pixelSize: 18 * page.theme.scale
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: !page.controller.selectedTaskId
                        text: page.controller.text("从左侧或上方列表选择任务，查看状态、产物和下一步。")
                        color: page.theme.muted
                        font.pixelSize: 13 * page.theme.scale
                        wrapMode: Text.Wrap
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        text: page.detail.title || page.controller.text("任务详情")
                        color: page.theme.text
                        font.pixelSize: 19 * page.theme.scale
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        spacing: 8
                        Rectangle {
                            radius: 5
                            color: page.theme.raised
                            implicitHeight: statusLabel.implicitHeight + 8
                            implicitWidth: statusLabel.implicitWidth + 14
                            Text {
                                id: statusLabel
                                anchors.centerIn: parent
                                text: page.detail.statusText || ""
                                color: page.theme.accent
                                font.pixelSize: 12 * page.theme.scale
                                textFormat: Text.PlainText
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.taskLoading
                                ? page.controller.text("正在核验产物…")
                                : page.controller.text("下一步：") + (page.detail.nextAction || page.controller.text("检查文件与依赖"))
                            color: page.theme.muted
                            font.pixelSize: 13 * page.theme.scale
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        implicitHeight: requestBox.implicitHeight + 18
                        radius: 6
                        color: page.theme.base
                        border.color: page.theme.line

                        ColumnLayout {
                            id: requestBox
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 9
                            spacing: 7

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.controller.text("请求摘要")
                                        color: page.theme.text
                                        font.pixelSize: 12 * page.theme.scale
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.detail.requestPreview || page.controller.text("任务未记录可展示的请求摘要。")
                                        color: page.theme.muted
                                        font.pixelSize: 11 * page.theme.scale
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
                                    spacing: 8
                                    Text {
                                        Layout.preferredWidth: Math.round(92 * page.theme.scale)
                                        text: modelData.label || ""
                                        color: page.theme.muted
                                        font.pixelSize: 11 * page.theme.scale
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.value || ""
                                        color: page.theme.text
                                        font.pixelSize: 11 * page.theme.scale
                                        wrapMode: Text.Wrap
                                        textFormat: Text.PlainText
                                    }
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        visible: !!page.controller.selectedTaskId
                        color: page.theme.line
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        text: page.controller.text("任务目录")
                        color: page.theme.muted
                        font.pixelSize: 12 * page.theme.scale
                    }
                    Text {
                        objectName: "taskFolder"
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        text: page.detail.jobId || ""
                        color: page.theme.text
                        font.pixelSize: 12 * page.theme.scale
                        elide: Text.ElideMiddle
                        wrapMode: Text.NoWrap
                        textFormat: Text.PlainText
                        ToolTip.visible: folderHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: folderHover }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !!page.detail.recovery && (page.detail.recovery.message || "").length > 0
                        text: page.detail.recovery && page.detail.recovery.message ? page.detail.recovery.message : ""
                        color: page.theme.error
                        font.pixelSize: 12 * page.theme.scale
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                    }

                    Flow {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        spacing: 7
                        UiButton {
                            objectName: "resumeTask"
                            visible: !!page.detail.canResume
                            theme: page.theme
                            primary: true
                            text: page.controller.text("继续 / 重试")
                            enabled: page.idle && !!page.detail.canResume
                            onClicked: page.controller.resumeSelectedTask()
                        }
                        UiButton {
                            objectName: "handoffTask"
                            visible: !!page.detail.canDispatch
                            theme: page.theme
                            primary: true
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
                            enabled: page.idle && !!page.detail.canRegisterVideo
                            onClicked: {
                                registerVideoDialog.expectedVideoJobId = page.controller.selectedTaskId
                                registerVideoDialog.expectedVideoTitle = page.detail.title || ""
                                existingVideoDialog.open()
                            }
                        }
                        UiButton {
                            objectName: "renameTask"
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("重命名")
                            enabled: page.idle && !!page.detail.canRename
                            onClicked: renameDialog.open()
                        }
                        UiButton {
                            objectName: "deleteTask"
                            theme: page.theme
                            quiet: true
                            text: page.controller.text("删除…")
                            enabled: page.idle && !!page.detail.canDelete
                            onClicked: deleteDialog.open()
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        visible: !!page.controller.selectedTaskId
                        color: page.theme.line
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                        Text {
                            Layout.fillWidth: true
                            text: page.controller.text("产物")
                            color: page.theme.text
                            font.pixelSize: 14 * page.theme.scale
                            font.weight: Font.DemiBold
                        }
                        Text {
                            text: page.detail.artifacts ? page.detail.artifacts.length.toString() : "0"
                            color: page.theme.muted
                            font.pixelSize: 12 * page.theme.scale
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: !!page.controller.selectedTaskId
                            && (!page.detail.artifacts || page.detail.artifacts.length === 0)
                        text: page.controller.taskLoading
                            ? page.controller.text("正在检查文件身份…")
                            : page.controller.text("还没有可列出的产物。请按下一步继续处理或检查任务记录。")
                        color: page.theme.muted
                        font.pixelSize: 12 * page.theme.scale
                        wrapMode: Text.Wrap
                    }

                    Repeater {
                        model: page.detail.artifacts || []
                        delegate: Rectangle {
                            id: artifactRow
                            required property var modelData
                            readonly property var sourceInfo: page.detail.artifactDetails
                                ? (page.detail.artifactDetails[modelData.id] || ({})) : ({})
                            readonly property bool sourceExpanded: page.expandedArtifactId === modelData.id
                            Layout.fillWidth: true
                            implicitHeight: artifactContent.implicitHeight + 16
                            radius: 6
                            color: page.theme.raised

                            ColumnLayout {
                                id: artifactContent
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 8
                                spacing: 6

                                RowLayout {
                                    id: artifactLayout
                                    Layout.fillWidth: true
                                    spacing: 8

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Text {
                                            Layout.fillWidth: true
                                            text: artifactRow.modelData.label || ""
                                            color: page.theme.text
                                            font.pixelSize: 12 * page.theme.scale
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                            textFormat: Text.PlainText
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: artifactRow.modelData.displayPath || ""
                                            color: page.theme.muted
                                            font.pixelSize: 11 * page.theme.scale
                                            elide: Text.ElideMiddle
                                            textFormat: Text.PlainText
                                            ToolTip.visible: artifactHover.hovered && (truncated || text !== artifactRow.modelData.path)
                                            ToolTip.text: artifactRow.modelData.path || ""
                                            HoverHandler { id: artifactHover }
                                        }
                                    }
                                    Text {
                                        text: artifactRow.modelData.statusText || ""
                                        color: page.statusColor(!!artifactRow.modelData.available)
                                        font.pixelSize: 11 * page.theme.scale
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    UiButton {
                                        visible: !!artifactRow.modelData.available
                                            && (artifactRow.modelData.role === "editor_project" || artifactRow.modelData.role === "tuned_project")
                                            && String(artifactRow.modelData.path || "").toLowerCase().endsWith(".svp")
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text("调音")
                                        enabled: page.idle
                                        onClicked: page.controller.tuneArtifact(artifactRow.modelData.id)
                                    }
                                    UiButton {
                                        objectName: "subtitleArtifact_" + (artifactRow.modelData.id || "")
                                        visible: !!artifactRow.modelData.available && artifactRow.modelData.role === "midi"
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text("做字幕")
                                        enabled: page.idle && !!artifactRow.modelData.openable
                                        onClicked: page.controller.editSubtitleArtifact(artifactRow.modelData.id)
                                    }
                                    UiButton {
                                        objectName: "openArtifact_" + (artifactRow.modelData.id || "")
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text("打开")
                                        enabled: page.idle && !!artifactRow.modelData.openable
                                        onClicked: page.controller.openArtifact(artifactRow.modelData.id)
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        Layout.fillWidth: true
                                        text: page.controller.text("来源") + " · "
                                            + (artifactRow.sourceInfo.producerText || page.controller.text("未记录"))
                                            + "  ·  " + page.controller.text("修订") + " "
                                            + (page.shortRevision(artifactRow.modelData.revision) || page.controller.text("未记录"))
                                        color: page.theme.muted
                                        font.pixelSize: 10 * page.theme.scale
                                        elide: Text.ElideRight
                                        textFormat: Text.PlainText
                                    }
                                    UiButton {
                                        objectName: "artifactSource_" + (artifactRow.modelData.id || "")
                                        theme: page.theme
                                        quiet: true
                                        text: page.controller.text(artifactRow.sourceExpanded ? "收起来源" : "来源与依赖")
                                        onClicked: {
                                            page.expandedArtifactId = artifactRow.sourceExpanded ? "" : artifactRow.modelData.id
                                        }
                                    }
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    visible: artifactRow.sourceExpanded
                                    spacing: 4

                                    Text {
                                        Layout.fillWidth: true
                                        visible: String(artifactRow.sourceInfo.sha256Short || "").length > 0
                                        text: page.controller.text("内容 SHA-256") + " · " + (artifactRow.sourceInfo.sha256Short || "")
                                        color: page.theme.muted
                                        font.pixelSize: 10 * page.theme.scale
                                        elide: Text.ElideMiddle
                                        textFormat: Text.PlainText
                                        ToolTip.visible: artifactHashHover.hovered && truncated
                                        ToolTip.text: artifactRow.sourceInfo.sha256 || text
                                        HoverHandler { id: artifactHashHover }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        visible: !artifactRow.sourceInfo.dependencies
                                            || artifactRow.sourceInfo.dependencies.length === 0
                                        text: page.controller.text("没有已登记的上游依赖。")
                                        color: page.theme.muted
                                        font.pixelSize: 10 * page.theme.scale
                                        textFormat: Text.PlainText
                                    }

                                    Repeater {
                                        model: artifactRow.sourceExpanded
                                            ? (artifactRow.sourceInfo.dependencies || []) : []
                                        delegate: RowLayout {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            spacing: 7
                                            Text {
                                                Layout.preferredWidth: Math.round(112 * page.theme.scale)
                                                text: modelData.label || ""
                                                color: page.theme.text
                                                font.pixelSize: 10 * page.theme.scale
                                                elide: Text.ElideRight
                                                textFormat: Text.PlainText
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                text: (modelData.displayPath || "") + " · " + (modelData.statusText || "")
                                                color: page.theme.muted
                                                font.pixelSize: 10 * page.theme.scale
                                                elide: Text.ElideMiddle
                                                textFormat: Text.PlainText
                                                ToolTip.visible: dependencyHover.hovered && truncated
                                                ToolTip.text: text
                                                HoverHandler { id: dependencyHover }
                                            }
                                            Text {
                                                text: page.shortRevision(modelData.revision)
                                                color: page.theme.muted
                                                font.pixelSize: 10 * page.theme.scale
                                                textFormat: Text.PlainText
                                            }
                                        }
                                    }
                                }
                            }
                        }
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
                    if (page.idle && text.trim().length > 0) {
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
                    enabled: page.idle && renameDialog.expectedJobId.length > 0 && renameField.text.trim().length > 0
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
                    enabled: page.idle && !!page.detail.canDelete && deleteDialog.expectedJobId.length > 0
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
                    enabled: page.idle
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
