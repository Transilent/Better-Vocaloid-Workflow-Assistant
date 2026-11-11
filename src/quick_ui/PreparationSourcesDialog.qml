import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "preparationSourcesDialog"
    required property QtObject theme
    required property QtObject controller
    signal componentsRequested()
    property string pane: "lyrics"
    property string selectedSong: ""
    property string selectedJob: ""
    readonly property var view: controller.view
    readonly property var network: view.network

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    title: ({lyrics:"核对网易云版本", download:"B站下载与账号", check:"检查准备组件"})[pane]
    width: Math.min(parent.width - 42, Math.round(720 * theme.scale))
    height: Math.min(parent.height - 48, Math.round((pane === "lyrics" ? 610 : pane === "download" ? 510 : 340) * theme.scale))
    padding: Math.round(15 * theme.scale)

    function openPane(name) {
        pane = name
        open()
        if (name === "download")
            controller.refreshDownloadSettings()
        else if (name === "check")
            controller.checkComponents()
    }
    component Body: Text {
        Layout.fillWidth: true
        font.pixelSize: 13 * dialog.theme.scale
        color: dialog.theme.muted
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    contentItem: ColumnLayout {
        spacing: 9 * dialog.theme.scale
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: visible
            visible: dialog.pane === "lyrics"
            RowLayout {
                Layout.fillWidth: true
                TextField {
                    id: keyword
                    objectName: "neteaseSearchQuery"
                    Layout.fillWidth: true
                    placeholderText: "歌名、歌手或歌曲链接"
                    enabled: !dialog.view.busy
                    selectByMouse: true
                    onAccepted: dialog.controller.searchSongs(text)
                }
                UiButton {
                    objectName: "searchNeteaseVersions"
                    theme: dialog.theme
                    text: "查找"
                    enabled: !dialog.view.busy && !!keyword.text.trim()
                    onClicked: {
                        dialog.selectedSong = ""
                        dialog.controller.searchSongs(keyword.text)
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                UiComboBox {
                    id: versions
                    objectName: "neteaseVersionChoice"
                    theme: dialog.theme
                    Layout.fillWidth: true
                    model: dialog.view.songs
                    valueRole: "id"
                    currentIndex: model.findIndex(row => row.id === dialog.selectedSong)
                    enabled: model.length > 0 && !dialog.view.busy
                    onActivated: dialog.selectedSong = currentValue
                }
                UiButton {
                    objectName: "previewNeteaseLyrics"
                    theme: dialog.theme
                    text: "预览歌词"
                    enabled: versions.currentIndex >= 0 && !dialog.view.busy
                    onClicked: dialog.controller.previewSong(dialog.selectedSong)
                }
                UiButton {
                    theme: dialog.theme
                    text: "打开歌曲试听"
                    enabled: !!(dialog.selectedSong || dialog.view.previewId) && !dialog.view.busy
                    onClicked: dialog.controller.auditionSong(dialog.selectedSong || dialog.view.previewId)
                }
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TextArea {
                    objectName: "neteaseLyricsPreview"
                    text: dialog.view.preview
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    placeholderText: "选择版本后查看实际原文歌词"
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: visible
            visible: dialog.pane === "download"
            Body { text: "可用同一 BV 的本地原视频，或按此处的超时、重试和登录设置下载。" }
            CheckBox {
                id: useLogin
                text: "下载使用 B站登录状态"
                checked: dialog.network.use_login
                enabled: !dialog.view.busy && dialog.view.networkLoaded
            }
            RowLayout {
                Body { text: "连接超时（秒）"; Layout.fillWidth: false }
                SpinBox { id: timeout; from: 5; to: 120; value: dialog.network.timeout; editable: true }
                Body { text: "重试"; Layout.fillWidth: false }
                SpinBox { id: retries; from: 0; to: 10; value: dialog.network.retries; editable: true }
                UiButton {
                    objectName: "saveBilibiliDownloadSettings"
                    theme: dialog.theme
                    text: "保存下载设置"
                    enabled: !dialog.view.busy && dialog.view.networkLoaded
                    onClicked: dialog.controller.saveDownloadSettings(useLogin.checked, timeout.value, retries.value)
                }
            }
            RowLayout {
                UiButton {
                    objectName: "openBilibiliDownloadLogin"
                    theme: dialog.theme
                    text: "登录 / 换账号"
                    enabled: !dialog.view.busy
                    onClicked: dialog.controller.loginDownload()
                }
                UiButton {
                    theme: dialog.theme
                    text: "刷新登录状态"
                    enabled: !dialog.view.busy
                    onClicked: dialog.controller.refreshDownloadSettings()
                }
            }
            Body { text: dialog.view.login }
            Body { text: dialog.view.selectedDownload ? "已选复用：" + dialog.view.selectedDownload : "当前未选择复用素材。" }
            RowLayout {
                Layout.fillWidth: true
                UiButton {
                    objectName: "findReusableBilibiliDownload"
                    theme: dialog.theme
                    text: "查找同 BV 素材"
                    enabled: !dialog.view.busy
                    onClicked: { dialog.selectedJob = ""; dialog.controller.findDownloads() }
                }
                UiComboBox {
                    theme: dialog.theme
                    Layout.fillWidth: true
                    model: dialog.view.downloads
                    valueRole: "job"
                    currentIndex: model.findIndex(row => row.job === dialog.selectedJob)
                    enabled: model.length > 0 && !dialog.view.busy
                    onActivated: dialog.selectedJob = currentValue
                }
                UiButton {
                    theme: dialog.theme
                    text: "采用"
                    enabled: !!dialog.selectedJob && !dialog.view.busy
                    onClicked: dialog.controller.selectDownload(dialog.selectedJob)
                }
                UiButton {
                    theme: dialog.theme
                    quiet: true
                    text: "取消复用"
                    visible: !!dialog.view.selectedDownload
                    enabled: !dialog.view.busy
                    onClicked: dialog.controller.selectDownload("")
                }
            }
            Item { Layout.fillHeight: true }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: visible
            visible: dialog.pane === "check"
            Repeater {
                model: dialog.view.issues
                delegate: Body {
                    required property string modelData
                    text: modelData
                    color: dialog.theme.attention
                }
            }
            Item { Layout.fillHeight: true }
        }
        Body {
            visible: !!dialog.view.message
            color: dialog.theme.text
            text: dialog.view.message
        }
        RowLayout {
            Layout.fillWidth: true
            UiButton {
                theme: dialog.theme
                text: "关闭"
                onClicked: dialog.close()
            }
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "applyNeteaseLyrics"
                theme: dialog.theme
                text: "填入主唱歌词"
                primary: true
                visible: dialog.pane === "lyrics"
                enabled: !dialog.view.busy && dialog.view.canApply
                    && (!dialog.selectedSong || dialog.selectedSong === dialog.view.previewId)
                onClicked: { if (dialog.controller.applyLyrics()) dialog.close() }
            }
            UiButton {
                theme: dialog.theme
                text: "打开模型与组件"
                visible: dialog.pane === "check" && dialog.view.issues.length > 0
                onClicked: { dialog.close(); dialog.componentsRequested() }
            }
        }
    }
}
