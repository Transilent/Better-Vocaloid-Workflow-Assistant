import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    required property var page
    objectName: "updateSection"
    visible: page.settingsCategory === "update"
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

        Rectangle {
            objectName: "justUpdatedBanner"
            Layout.fillWidth: true
            visible: String(page.updateInfo.just_updated_version || "").length > 0
            implicitHeight: justUpdatedRow.implicitHeight + Math.round(16 * page.theme.scale)
            radius: Math.round(8 * page.theme.scale)
            color: page.theme.base
            border.color: page.theme.success
            RowLayout {
                id: justUpdatedRow
                anchors.fill: parent
                anchors.margins: Math.round(8 * page.theme.scale)
                spacing: Math.round(8 * page.theme.scale)
                Text {
                    Layout.fillWidth: true
                    text: page.tr("已成功更新至 ") + page.formatVersion(page.updateInfo.just_updated_version)
                        + page.tr("，旧版暂存文件已自动清理。")
                    color: page.theme.success
                    font.pixelSize: Math.round(12 * page.theme.scale)
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
                UiButton {
                    objectName: "ackJustUpdated"
                    theme: page.theme
                    quiet: true
                    text: page.tr("我知道了")
                    onClicked: page.controller.acknowledgeJustUpdated()
                }
            }
        }

        SettingsMotionChoice {
            objectName: "settingsStartupUpdate"
            Layout.fillWidth: true
            tokens: page.theme
            text: page.tr("启动时检查更新")
            detail: page.tr("在后台检查，有新版本时显示更新内容。")
            selected: page.setting("check_for_updates", true) === true
            onClicked: page.changeSetting("check_for_updates", !selected)
        }
        SettingsMotionChoice {
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
        Text {
            objectName: "updateOperationBlockers"
            Layout.fillWidth: true
            visible: page.operationBlockerText.length > 0
            text: page.tr("正在运行") + "  " + page.operationBlockerText
            color: page.theme.accent
            font.pixelSize: Math.round(11 * page.theme.scale)
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: String(page.updateInfo.notes || "").length > 0
            spacing: Math.round(4 * page.theme.scale)
            Text {
                text: page.tr("更新说明")
                color: page.theme.text
                font.pixelSize: Math.round(12 * page.theme.scale)
                font.weight: Font.DemiBold
            }
            ScrollView {
                id: updateReleaseNotesScroll
                objectName: "updateReleaseNotesScroll"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(150 * page.theme.scale)
                clip: true
                contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                background: Rectangle {
                    color: page.theme.base
                    radius: Math.round(7 * page.theme.scale)
                    border.color: page.theme.line
                }
                TextArea {
                    objectName: "updateReleaseNotes"
                    width: updateReleaseNotesScroll.availableWidth
                    text: String(page.updateInfo.notes || "")
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    color: page.theme.text
                    selectionColor: page.theme.accent
                    selectedTextColor: page.theme.base
                    font.pixelSize: Math.round(11 * page.theme.scale)
                    background: Rectangle { color: "transparent" }
                    Accessible.name: page.tr("更新说明")
                }
            }
        }

        UiProgressBar {
            id: updateProgress
            objectName: "updateProgress"
            theme: page.theme
            Layout.fillWidth: true
            visible: page.downloading || Number(page.updateInfo.total || 0) > 0
            indeterminate: page.downloading && Number(page.updateInfo.total || 0) <= 0 && !page.theme.reduceMotion
            fillColor: page.currentUpdateState === "failed" || page.currentUpdateState === "error"
                ? page.theme.error : page.theme.accent
            value: Number(page.updateInfo.total || 0) > 0
                ? Math.max(0, Math.min(1, Number(page.updateInfo.done || 0) / Number(page.updateInfo.total)))
                : 0
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
                onClicked: page.updatePrimaryAction()
            }
            UiButton {
                objectName: "updateOpenReleasePage"
                theme: page.theme
                quiet: true
                visible: !!page.updateInfo.can_full_migrate && String(page.updateInfo.url || "").length > 0
                text: page.tr("打开发布页")
                onClicked: page.controller.openRelease()
            }
            UiButton {
                objectName: "updateCancel"
                theme: page.theme
                quiet: true
                visible: (page.downloading || page.updateInfo.auto_restart_active)
                    && page.updateInfo.can_cancel
                text: page.updateInfo.auto_restart_active ? page.tr("暂缓安装") : page.tr("取消下载")
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
