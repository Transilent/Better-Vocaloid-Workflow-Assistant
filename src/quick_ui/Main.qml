import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    objectName: "quickWorkspace"
    required property QtObject controller
    required property url assets
    required property int initialWidth
    required property int initialHeight
    required property real initialScale
    required property bool initialReducedMotion
    required property bool systemDark
    required property bool showWindow
    readonly property QtObject tokens: theme
    readonly property bool compact: width < 1110 || (theme.scale > 1.2 && width < 1280)
    readonly property bool useArtwork: preferences.material === "glass"
    readonly property var preferences: controller.support.settings
    readonly property bool preparationAway: controller.preparationBusy && controller.route !== "processing"
    readonly property bool tuningAway: controller.operationKind.indexOf("tuning-") === 0 && controller.route !== "tuning"
    readonly property bool mixAway: controller.route !== "audio_mixdown"
        && (controller.mixdown.view.playing || controller.operationKind.indexOf("mixdown-") === 0)
    readonly property bool videoAway: controller.operationKind.indexOf("video-") === 0 && controller.route !== "video_mixdown"
    readonly property bool publicationAway: controller.operationKind.indexOf("publication-") === 0 && controller.route !== "publishing"
    readonly property string studioRoute: videoAway ? "video_mixdown" : publicationAway ? "publishing"
        : tuningAway ? "tuning" : mixAway ? "audio_mixdown" : ""
    property bool closingAfterTask: false

    width: initialWidth
    height: initialHeight
    visible: showWindow
    title: "VWA"
    color: theme.base
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowMinimizeButtonHint
        | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint
    palette.window: theme.base
    palette.windowText: theme.text
    palette.text: theme.text
    palette.base: theme.surface
    palette.button: theme.surface
    palette.buttonText: theme.text
    palette.highlight: theme.accent
    palette.highlightedText: theme.onAccent

    QtObject {
        id: theme
        readonly property bool dark: window.preferences.theme === "dark"
            || (window.preferences.theme === "system" && window.systemDark)
        readonly property real scale: window.initialScale || (window.preferences.ui_scale || 100) / 100
        readonly property bool reduceMotion: window.initialReducedMotion || !!window.preferences.reduce_motion
        readonly property int panelMotionMs: reduceMotion ? 0 : 140
        readonly property color base: dark ? "#13141a" : "#f5f4f9"
        readonly property color surface: dark ? "#191b23" : "#ffffff"
        readonly property color raised: dark ? "#20222b" : "#eeebf5"
        readonly property color sidebar: dark ? "#171822" : "#eae7f2"
        readonly property color text: dark ? "#eeeef5" : "#242233"
        readonly property color muted: dark ? "#a6a9ba" : "#5c576d"
        readonly property color line: dark ? "#30323d" : "#d8d3e3"
        readonly property color accent: dark ? "#c2a8ff" : "#6544a5"
        readonly property color onAccent: dark ? "#211930" : "#ffffff"
        readonly property color accentPressed: dark ? "#a48ad9" : "#503289"
        readonly property color selected: dark ? "#312940" : "#e1d8f5"
        readonly property color selectedLine: dark ? "#564369" : "#b6a4d5"
        readonly property color hover: dark ? "#262833" : "#e8e3f2"
        readonly property color pressed: dark ? "#30313d" : "#dcd4e9"
        readonly property color success: dark ? "#91cdbb" : "#286a53"
        readonly property color error: dark ? "#efabac" : "#9f3545"
        readonly property color attention: dark ? "#d4ad80" : "#825320"
    }

    onClosing: function(event) {
        if ((controller.workBusy || controller.calibration.view.busy) && !closingAfterTask) {
            event.accepted = false
            exitDialog.open()
        }
    }

    Popup {
        id: exitDialog
        objectName: "activeTaskExitDialog"
        parent: Overlay.overlay
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        anchors.centerIn: parent
        width: Math.min(parent.width - 36, Math.round(410 * theme.scale))
        height: exitBody.implicitHeight + topPadding + bottomPadding
        padding: Math.round(16 * theme.scale)
        background: Rectangle {
            color: theme.surface
            radius: Math.round(10 * theme.scale)
            border.color: theme.line
        }
        contentItem: Column {
            id: exitBody
            spacing: Math.round(12 * theme.scale)
            Text {
                width: exitDialog.availableWidth
                text: window.controller.text("任务正在运行")
                color: theme.text
                font.pixelSize: Math.round(16 * theme.scale)
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Text {
                width: exitDialog.availableWidth
                text: window.controller.text("可以继续等待，或取消操作并在收尾后退出。")
                wrapMode: Text.WordWrap
                color: theme.text
                font.pixelSize: Math.round(14 * theme.scale)
            }
            RowLayout {
                width: exitDialog.availableWidth
                Item { Layout.fillWidth: true }
                UiButton {
                    objectName: "activeTaskCancelAndExit"
                    theme: window.tokens
                    text: window.controller.text("取消并退出")
                    onClicked: {
                        exitDialog.close()
                        window.controller.cancelAndClose()
                    }
                }
                UiButton {
                    id: keepRunning
                    objectName: "activeTaskKeepRunning"
                    theme: window.tokens
                    primary: true
                    text: window.controller.text("继续等待")
                    onClicked: exitDialog.close()
                }
            }
        }
        onOpened: keepRunning.forceActiveFocus()
    }

    Connections {
        target: window.controller
        function onCloseReady() {
            window.closingAfterTask = true
            window.close()
        }
    }

    Rectangle {
        id: sidebar
        objectName: "workspaceNavigation"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        width: window.compact ? 66 : 176
        color: theme.sidebar
        clip: true

        Image {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: Math.min(320, parent.height * 0.42)
            visible: window.useArtwork
            source: window.assets + "moonlight-background.png"
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: 480
            opacity: theme.dark ? 0.22 : 0.18
        }
        Rectangle {
            anchors.fill: parent
            visible: window.useArtwork
            gradient: Gradient {
                GradientStop { position: 0.64; color: theme.sidebar }
                GradientStop { position: 1; color: Qt.rgba(theme.sidebar.r, theme.sidebar.g, theme.sidebar.b, 0.28) }
            }
        }
        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.line }

        Flickable {
            id: navScroll
            objectName: "workspaceNavigationViewport"
            anchors.fill: parent
            anchors.margins: window.compact ? 8 : 12
            contentWidth: width
            contentHeight: navColumn.height
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            function reveal(item) {
                if (!item || contentHeight <= height)
                    return;
                const y = item.mapToItem(navColumn, 0, 0).y;
                const target = y < contentY ? y
                    : y + item.height > contentY + height ? y + item.height - height : contentY;
                contentY = Math.max(0, Math.min(contentHeight - height, target));
            }

          ColumnLayout {
            id: navColumn
            width: navScroll.width
            height: Math.max(implicitHeight, navScroll.height)
            spacing: 9
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 48
                spacing: 10
                Image {
                    Layout.alignment: window.compact ? Qt.AlignHCenter : Qt.AlignVCenter
                    Layout.preferredWidth: 33
                    Layout.preferredHeight: 33
                    source: window.assets + "app.png"
                    sourceSize: Qt.size(66, 66)
                }
                Text {
                    visible: !window.compact
                    text: "VWA"
                    color: theme.text
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                }
                Item { visible: !window.compact; Layout.fillWidth: true }
            }
            Repeater {
                model: window.controller.routes
                delegate: ColumnLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.fillHeight: modelData.pageId === "components"
                    Item { visible: modelData.pageId === "components"; Layout.fillHeight: true }
                    UiButton {
                        id: navButton
                        objectName: "nav_" + modelData.pageId
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round(41 * Math.min(1.15, theme.scale))
                        theme: window.tokens
                        text: window.compact ? modelData.short : modelData.title
                        Accessible.name: modelData.title
                        leftPadding: window.compact ? 3 : 10
                        rightPadding: leftPadding
                        leftAligned: !window.compact
                        quiet: true
                        selected: window.controller.route === modelData.pageId
                        Accessible.checkable: true
                        Accessible.checked: selected
                        onClicked: window.controller.navigate(modelData.pageId)
                        onActiveFocusChanged: {
                            if (activeFocus)
                                navScroll.reveal(navButton);
                        }
                        onSelectedChanged: {
                            if (selected)
                                navScroll.reveal(navButton);
                        }
                        ToolTip.visible: hovered && window.compact
                        ToolTip.text: modelData.title
                        Rectangle {
                            anchors.left: parent.left
                            anchors.leftMargin: 2
                            anchors.verticalCenter: parent.verticalCenter
                            width: 2
                            height: Math.round(16 * theme.scale)
                            radius: width / 2
                            color: theme.accent
                            opacity: navButton.selected ? 1 : 0
                            Behavior on opacity {
                                enabled: !theme.reduceMotion
                                NumberAnimation { duration: 90; easing.type: Easing.OutCubic }
                            }
                        }
                    }
                }
            }
          }
        }
    }

    ColumnLayout {
        anchors.left: sidebar.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        spacing: 0

        Loader {
            id: pageLoader
            objectName: "pageLoader"
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: {
                switch (window.controller.route) {
                case "components": return componentsPage
                case "results": return resultsPage
                case "updates": return settingsPage
                case "tuning": return tuningPage
                case "audio_mixdown": return mixdownPage
                case "video_mixdown": return videoPage
                case "publishing": return publicationPage
                default: return preparationPage
                }
            }
        }
        Component {
            id: componentsPage
            ComponentsPage { theme: window.tokens; controller: window.controller }
        }
        Component {
            id: preparationPage
            PreparationPage {
                theme: window.tokens
                controller: window.controller
                onSubtitleRequested: subtitleDialog.openLocal()
            }
        }
        Component {
            id: resultsPage
            ResultsPage { theme: window.tokens; controller: window.controller }
        }
        Component {
            id: settingsPage
            SettingsPage { theme: window.tokens; controller: window.controller.support }
        }
        Component {
            id: tuningPage
            TuningPage {
                theme: window.tokens
                controller: window.controller.tuning
                onCalibrationRequested: voiceCalibration.open()
            }
        }
        Component {
            id: mixdownPage
            MixdownPage { theme: window.tokens; controller: window.controller.mixdown }
        }
        Component {
            id: videoPage
            VideoPage { theme: window.tokens; controller: window.controller.video }
        }
        Component {
            id: publicationPage
            PublicationPage {
                theme: window.tokens
                controller: window.controller.publication
                onTaskVideoRequested: window.controller.navigate("results")
            }
        }

        Rectangle {
            objectName: "backgroundCalibration"
            Layout.fillWidth: true
            Layout.preferredHeight: calibrationBar.implicitHeight + 10
            visible: !voiceCalibration.visible && window.controller.calibration.view.busy
            color: theme.surface
            Rectangle { width: parent.width; height: 1; color: theme.line }
            RowLayout {
                id: calibrationBar
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                Text {
                    Layout.fillWidth: true
                    text: window.controller.text("声库校准") + " · " + window.controller.calibration.view.message
                    elide: Text.ElideRight
                    color: theme.text
                    font.pixelSize: 13 * theme.scale
                    textFormat: Text.PlainText
                }
                UiButton {
                    objectName: "returnBackgroundCalibration"
                    theme: window.tokens
                    text: window.controller.text("查看校准")
                    onClicked: voiceCalibration.open()
                }
            }
        }

        Rectangle {
            objectName: "backgroundStudioActivity"
            Layout.fillWidth: true
            Layout.preferredHeight: studioBar.implicitHeight + 12
            visible: window.studioRoute.length > 0
            color: theme.surface
            Rectangle { width: parent.width; height: 1; color: theme.line }
            RowLayout {
                id: studioBar
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                Text {
                    Layout.fillWidth: true
                    color: theme.text
                    font.pixelSize: 13 * theme.scale
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                    text: window.videoAway ? window.controller.video.view.message
                        : window.publicationAway ? window.controller.publication.view.message
                        : window.tuningAway ? window.controller.tuning.view.message
                        : window.controller.mixdown.view.playing ? window.controller.text("简易混音 · 后台试听")
                        : window.controller.mixdown.view.message || window.controller.text("简易混音")
                }
                UiButton {
                    objectName: "pauseBackgroundMix"
                    visible: window.mixAway && window.controller.mixdown.view.playing
                    theme: window.tokens
                    text: window.controller.text("暂停")
                    onClicked: window.controller.mixdown.togglePlay()
                }
                UiButton {
                    objectName: "returnBackgroundStudio"
                    theme: window.tokens
                    text: window.controller.text(window.videoAway ? "返回视频" : window.publicationAway ? "返回发布"
                                                 : window.tuningAway ? "返回调音" : "返回混音")
                    onClicked: window.controller.navigate(window.studioRoute)
                }
            }
        }

        Rectangle {
            objectName: "workspaceActivity"
            Layout.fillWidth: true
            Layout.preferredHeight: footer.implicitHeight + 16
            visible: window.preparationAway || window.controller.busy || window.controller.notice.length > 0
                || (window.controller.route === "components" && window.controller.operation.state !== "idle")
            color: theme.surface
            Rectangle { width: parent.width; height: 1; color: theme.line }
            RowLayout {
                id: footer
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                        Layout.fillWidth: true
                        text: window.controller.notice || (window.preparationAway
                            ? window.controller.preparationTask.message
                            : window.controller.operation.message) || ""
                        color: !window.preparationAway && window.controller.operation.state === "failed" ? theme.error : theme.text
                        font.pixelSize: 13 * theme.scale
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        ToolTip.visible: activityHover.hovered && truncated
                        ToolTip.text: text
                        HoverHandler { id: activityHover }
                    }
                    ProgressBar {
                        objectName: "componentProgress"
                        Layout.fillWidth: true
                        visible: window.controller.busy
                        indeterminate: !window.controller.operation.total && !theme.reduceMotion
                        value: window.controller.operation.total ? Math.min(1, window.controller.operation.done / window.controller.operation.total) : 0
                        Accessible.name: window.controller.text("组件任务进度")
                    }
                    ProgressBar {
                        objectName: "backgroundPreparationProgress"
                        Layout.fillWidth: true
                        visible: window.preparationAway
                        value: window.controller.preparationTask.total > 0
                            ? window.controller.preparationTask.completed / window.controller.preparationTask.total : 0
                        Accessible.name: window.controller.text("准备任务进度")
                    }
                }
                UiButton {
                    objectName: "viewPreparationProgress"
                    visible: window.preparationAway
                    theme: window.tokens
                    text: window.controller.text("查看进度")
                    onClicked: window.controller.navigate("processing")
                }
                UiButton {
                    objectName: "cancelComponent"
                    visible: window.controller.busy
                    enabled: window.controller.operation.state === "running"
                    theme: window.tokens
                    text: window.controller.text("取消")
                    onClicked: window.controller.cancelOperation()
                }
            }
        }
    }

    CalibrationDialog {
        id: voiceCalibration
        theme: window.tokens
        controller: window.controller.calibration
        onBindingRequested: window.controller.navigate("tuning")
    }

    SubtitleDialog {
        id: subtitleDialog
        theme: window.tokens
        controller: window.controller.subtitles
        onNeteaseRequested: subtitleSources.openPane("lyrics")
    }

    PreparationSourcesDialog {
        id: subtitleSources
        theme: window.tokens
        controller: window.controller.preparationSources
        lyricPurpose: "subtitles"
    }

    Connections {
        target: window.controller
        function onSubtitlesRequested() { subtitleDialog.openLocal() }
    }

    StartupDialogs {
        theme: window.tokens
        controller: window.controller.support
        navigationApi: window.controller
    }
}
