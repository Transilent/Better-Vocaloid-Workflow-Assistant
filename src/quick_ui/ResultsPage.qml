import QtQuick
import QtQuick.Controls
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

    function statusColor(available) {
        return available ? theme.success : theme.error
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

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 22
            Layout.rightMargin: 22
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
            UiButton {
                objectName: "refreshTasks"
                theme: page.theme
                text: page.controller.text(page.controller.taskLoading ? "正在检查…" : "刷新任务")
                enabled: !page.controller.taskLoading && !page.controller.workBusy
                onClicked: page.controller.refreshTasks()
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
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
                            text: page.controller.text(page.detail.handoff === "audio_ready" ? "打开分轨目录"
                                : page.detail.handoff === "awaiting_tuning"
                                    ? (page.detail.delivery.openArtifactId ? "打开工程" : "查看工程与 MIDI")
                                : page.detail.handoff === "mixdown_ready" ? "打开登记的混音"
                                : page.detail.handoff === "video_ready" ? "打开登记的视频" : "打开发布资料")
                            enabled: page.idle
                            onClicked: page.controller.dispatchTaskHandoff()
                        }
                        UiButton {
                            objectName: "openTaskFolder"
                            theme: page.theme
                            text: page.controller.text("打开任务目录")
                            enabled: page.idle
                            onClicked: page.controller.openSelectedTaskFolder()
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
                            Layout.fillWidth: true
                            implicitHeight: artifactLayout.implicitHeight + 16
                            radius: 6
                            color: page.theme.raised

                            RowLayout {
                                id: artifactLayout
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 8
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
                                    objectName: "openArtifact_" + (artifactRow.modelData.id || "")
                                    theme: page.theme
                                    quiet: true
                                    text: page.controller.text("打开")
                                    enabled: page.idle && !!artifactRow.modelData.openable
                                    onClicked: page.controller.openArtifact(artifactRow.modelData.id)
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
}
