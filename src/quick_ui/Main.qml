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
    readonly property bool mixAway: controller.route !== "audio_mixdown" && controller.mixdown.view.playing
    readonly property bool chorusAway: controller.route !== "chorus" && controller.chorus.view.playing
    readonly property bool previewLoading: controller.mixdown.view.loading
    property int previewWait: 0
    readonly property var operations: controller.activeOperations
    readonly property var issue: controller.latestOperationIssue
    readonly property var primaryOperation: operations.length > 0 ? operations[0] : ({})
    property bool closingAfterTask: false
    property bool discardChorusOnExit: false

    onPreviewLoadingChanged: previewWait = 0
    Timer {
        interval: 1000
        repeat: true
        running: window.previewLoading
        onTriggered: window.previewWait += 1
    }

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
    palette.highlightedText: theme.accentInk

    QtObject {
        id: theme
        readonly property bool dark: window.preferences.theme === "dark"
            || (window.preferences.theme === "system" && window.systemDark)
        readonly property real scale: window.initialScale || (window.preferences.ui_scale || 100) / 100
        readonly property bool reduceMotion: window.initialReducedMotion || !!window.preferences.reduce_motion
        readonly property int panelMotionMs: reduceMotion ? 0 : 140
        // Refined Apple-HIG / Linear surface elevation ladder (eye-friendly matte paper in Light Mode)
        readonly property color base: dark ? "#0e1016" : "#e4e7ef"
        readonly property color surface: dark ? "#171a24" : "#f2f3f7"
        readonly property color raised: dark ? "#202433" : "#e7eaf2"
        readonly property color sidebar: dark ? "#13151e" : "#dbdfe9"
        readonly property color text: dark ? "#f0f1f8" : "#141724"
        readonly property color muted: dark ? "#989cb2" : "#485066"
        readonly property color line: dark ? "#252938" : "#c0c6d6"
        readonly property color lineSubtle: dark ? "#1d202d" : "#d0d5e2"
        readonly property color highlightTop: dark ? "#14ffffff" : "#70ffffff"
        readonly property color disabledBg: dark ? "#232634" : "#d5dae6"
        // Brand moonlight-lavender accent + high-contrast semantic tones
        // NOTE: Never name a property `onAccent` in Qt 6 QML when `accent` exists (it collides with signal handler parsing!)
        readonly property color accent: dark ? "#b99cff" : "#5a3bc2"
        readonly property color accentHover: dark ? "#c8b0ff" : "#4c2fb0"
        readonly property color accentInk: dark ? "#141022" : "#ffffff"
        readonly property color accentPressed: dark ? "#9f7ee8" : "#3e2494"
        readonly property color selected: dark ? "#2b2440" : "#dbd2f6"
        readonly property color selectedLine: dark ? "#5a4685" : "#7a5ed4"
        readonly property color hover: dark ? "#272c3d" : "#dce0ec"
        readonly property color pressed: dark ? "#30364a" : "#cfd5e4"
        readonly property color success: dark ? "#56d39a" : "#16794b"
        readonly property color error: dark ? "#f87171" : "#c42b3b"
        readonly property color attention: dark ? "#f5b841" : "#a65808"
        // Unified radius & 5-step Apple typography scale
        readonly property int radiusSm: Math.round(6 * scale)
        readonly property int radiusMd: Math.round(8 * scale)
        readonly property int radiusLg: Math.round(11 * scale)
        readonly property int radiusXl: Math.round(14 * scale)
        readonly property int fontXs: Math.round(11 * scale)
        readonly property int fontSm: Math.round(12 * scale)
        readonly property int fontBody: Math.round(13 * scale)
        readonly property int fontTitle: Math.round(15 * scale)
        readonly property int fontDisplay: Math.round(19 * scale)
        readonly property string monoFont: "Consolas"
    }

    onClosing: function(event) {
        if ((controller.hasActiveOperations || window.previewLoading) && !closingAfterTask) {
            event.accepted = false
            exitDialog.open()
        } else if (controller.chorus.dirty && !discardChorusOnExit) {
            event.accepted = false
            chorusExitDialog.open()
        }
    }

    UiDialog {
        id: chorusExitDialog
        theme: window.tokens
        title: window.controller.text("修音工程有未保存修改")
        standardButtons: Dialog.Discard | Dialog.Cancel
        Label {
            text: window.controller.text("请先保存工程，或放弃当前修改后继续。")
            color: theme.text
            font.pixelSize: theme.fontBody
        }
        onDiscarded: {
            window.discardChorusOnExit = true
            close()
            window.close()
        }
        onRejected: window.closingAfterTask = false
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
        width: window.compact ? Math.round(66 * Math.min(1.15, theme.scale)) : Math.round(198 * Math.min(1.18, theme.scale))
        color: theme.sidebar
        clip: true

        function routeSectionTitle(pageId) {
            if (pageId === "processing") return window.controller.text("前期与人声")
            if (pageId === "audio_mixdown") return window.controller.text("后期与交付")
            if (pageId === "project_convert") return window.controller.text("实用工具")
            if (pageId === "components") return window.controller.text("系统")
            return ""
        }
        function routeHasOperation(pageId) {
            for (let i = 0; i < window.operations.length; ++i) {
                if (window.operations[i] && window.operations[i].route === pageId)
                    return true
            }
            return false
        }
        function routePlaying(pageId) {
            if (pageId === "chorus") return !!window.controller.chorus.view.playing
            if (pageId === "audio_mixdown") return !!window.controller.mixdown.view.playing
            return false
        }

        Image {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: Math.min(320, parent.height * 0.42)
            visible: window.useArtwork
            source: window.assets + "moonlight-background.png"
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: 480
            opacity: theme.dark ? 0.18 : 0.14
        }
        Rectangle {
            anchors.fill: parent
            visible: window.useArtwork
            gradient: Gradient {
                GradientStop { position: 0.62; color: theme.sidebar }
                GradientStop { position: 1; color: Qt.rgba(theme.sidebar.r, theme.sidebar.g, theme.sidebar.b, 0.30) }
            }
        }
        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: theme.lineSubtle }

        Flickable {
            id: navScroll
            objectName: "workspaceNavigationViewport"
            anchors.fill: parent
            anchors.margins: window.compact ? 7 : 10
            contentWidth: width
            contentHeight: navColumn.height
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            ScrollBar.vertical: UiScrollBar { theme: window.tokens; policy: ScrollBar.AsNeeded }

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
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 40
                Layout.leftMargin: window.compact ? 0 : 4
                spacing: 9
                Image {
                    Layout.alignment: window.compact ? Qt.AlignHCenter : Qt.AlignVCenter
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    source: window.assets + "app.png"
                    sourceSize: Qt.size(64, 64)
                }
                Text {
                    visible: !window.compact
                    text: "VWA"
                    color: theme.text
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.6
                }
                Item { visible: !window.compact; Layout.fillWidth: true }
            }
            Rectangle {
                objectName: "sidebarCommandTrigger"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round((window.compact ? 30 : 30) * Math.min(1.15, theme.scale))
                Layout.bottomMargin: 4
                radius: theme.radiusMd
                color: cmdTriggerHover.containsMouse ? theme.hover : theme.raised
                border.width: 1
                border.color: cmdTriggerHover.containsMouse ? theme.selectedLine : theme.lineSubtle
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: window.compact ? 0 : 9
                    anchors.rightMargin: window.compact ? 0 : 7
                    spacing: 6
                    UiIcon {
                        Layout.alignment: window.compact ? Qt.AlignHCenter : Qt.AlignVCenter
                        Layout.preferredWidth: 13
                        Layout.preferredHeight: 13
                        glyph: "target"
                        tint: theme.muted
                    }
                    Text {
                        visible: !window.compact
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        text: window.controller.text("快捷命令")
                        color: theme.muted
                        font.pixelSize: Math.round(12 * Math.min(1.12, theme.scale))
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Rectangle {
                        objectName: "sidebarCommandShortcutBadge"
                        visible: !window.compact
                        radius: 4
                        color: theme.surface
                        border.color: theme.line
                        Layout.preferredWidth: kbdTxt.implicitWidth + 10
                        Layout.minimumWidth: Layout.preferredWidth
                        Layout.preferredHeight: kbdTxt.implicitHeight + 4
                        implicitWidth: Layout.preferredWidth
                        implicitHeight: Layout.preferredHeight
                        Text {
                            id: kbdTxt
                            anchors.centerIn: parent
                            text: "Ctrl+K"
                            color: theme.muted
                            font.pixelSize: Math.round(10 * Math.min(1.12, theme.scale))
                            font.family: theme.monoFont
                        }
                    }
                }
                MouseArea {
                    id: cmdTriggerHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: commandPalette.open()
                }
                ToolTip.visible: cmdTriggerHover.containsMouse
                ToolTip.text: window.controller.text("打开全局快捷命令面板 (Ctrl+K)")
            }
            Repeater {
                model: window.controller.routes
                delegate: ColumnLayout {
                    required property var modelData
                    readonly property string sectionLabel: sidebar.routeSectionTitle(modelData.pageId)
                    readonly property bool isBusyRoute: sidebar.routeHasOperation(modelData.pageId)
                    readonly property bool isPlayingRoute: sidebar.routePlaying(modelData.pageId)
                    readonly property int chorusReviewCount: modelData.pageId === "chorus"
                        ? ((window.controller.chorus.view.issues || []).length) : 0
                    readonly property bool chorusHasHigh: modelData.pageId === "chorus"
                        && ((window.controller.chorus.view.highIssueCount || 0) > 0)
                    readonly property bool isDirtyRoute: modelData.pageId === "chorus" && !!window.controller.chorus.dirty
                    Layout.fillWidth: true
                    Layout.fillHeight: modelData.pageId === "project_convert"
                    spacing: 3
                    Item { visible: modelData.pageId === "project_convert"; Layout.fillHeight: true; Layout.minimumHeight: 8 }
                    Rectangle {
                        visible: sectionLabel.length > 0 && modelData.pageId !== "processing"
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        Layout.bottomMargin: window.compact ? 4 : 2
                        Layout.leftMargin: window.compact ? 8 : 6
                        Layout.rightMargin: window.compact ? 8 : 6
                        height: 1
                        color: theme.lineSubtle
                    }
                    Text {
                        visible: !window.compact && sectionLabel.length > 0
                        Layout.fillWidth: true
                        Layout.leftMargin: 10
                        Layout.topMargin: modelData.pageId === "processing" ? 2 : 3
                        Layout.bottomMargin: 2
                        text: sectionLabel
                        color: theme.muted
                        opacity: 0.78
                        font.pixelSize: Math.round(10.5 * Math.min(1.15, theme.scale))
                        font.weight: Font.DemiBold
                        font.letterSpacing: 0.5
                        textFormat: Text.PlainText
                    }
                    UiButton {
                        id: navButton
                        objectName: "nav_" + modelData.pageId
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.round((window.compact ? 44 : 36) * Math.min(1.15, theme.scale))
                        theme: window.tokens
                        text: window.compact ? modelData.short : modelData.title
                        leadingGlyph: modelData.icon || ""
                        leadingIconSize: window.compact ? 16 : 16
                        stackedIcon: window.compact
                        Accessible.name: modelData.title
                        leftPadding: window.compact ? 3 : 11
                        rightPadding: window.compact ? 3 : 10
                        topPadding: window.compact ? 4 : Math.round(6 * theme.scale)
                        bottomPadding: topPadding
                        leftAligned: !window.compact
                        quiet: true
                        borderlessSelected: true
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
                            width: 3
                            height: Math.round(16 * theme.scale)
                            radius: width / 2
                            color: theme.accent
                            opacity: navButton.selected ? 1 : 0
                            Behavior on opacity {
                                enabled: !theme.reduceMotion
                                NumberAnimation { duration: 100; easing.type: Easing.OutCubic }
                            }
                        }
                        // Live route activity badges (playing / running / pending review / unsaved)
                        Row {
                            anchors.right: parent.right
                            anchors.rightMargin: window.compact ? 4 : 8
                            anchors.verticalCenter: window.compact ? undefined : parent.verticalCenter
                            anchors.top: window.compact ? parent.top : undefined
                            anchors.topMargin: window.compact ? 4 : 0
                            spacing: 4
                            Rectangle {
                                visible: isPlayingRoute
                                width: 7
                                height: 7
                                radius: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: theme.success
                            }
                            Rectangle {
                                visible: isBusyRoute
                                width: 7
                                height: 7
                                radius: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: theme.accent
                            }
                            Rectangle {
                                visible: !window.compact && chorusReviewCount > 0
                                radius: 8
                                color: chorusHasHigh ? theme.error : theme.attention
                                implicitWidth: Math.max(16, badgeTxt.implicitWidth + 8)
                                implicitHeight: 16
                                Text {
                                    id: badgeTxt
                                    anchors.centerIn: parent
                                    text: chorusReviewCount > 99 ? "99+" : String(chorusReviewCount)
                                    color: "#111318"
                                    font.pixelSize: 10
                                    font.weight: Font.Bold
                                }
                            }
                            Rectangle {
                                visible: isDirtyRoute && chorusReviewCount === 0 && !isBusyRoute && !isPlayingRoute
                                width: 6
                                height: 6
                                radius: 3
                                anchors.verticalCenter: parent.verticalCenter
                                color: theme.attention
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

        Item {
            id: workspaceStage
            Layout.fillWidth: true
            Layout.fillHeight: true

            WorkspaceStage {
                id: pageLoader
                anchors.fill: parent
                route: window.controller.route
                reduceMotion: window.tokens.reduceMotion
                pageComponents: ({
                    components: componentsPage,
                    results: resultsPage,
                    updates: settingsPage,
                    tuning: tuningPage,
                    tuning_workshop: workshopPage,
                    chorus: chorusPage,
                    audio_mixdown: mixdownPage,
                    video_mixdown: videoPage,
                    publishing: publicationPage,
                    project_convert: convertPage,
                    processing: preparationPage
                })
            }

            // Floating Activity Island (overlays bottom edge without compressing editor canvas height)
            Column {
                id: floatingStack
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Math.round(14 * theme.scale)
                width: Math.min(parent.width - Math.round(32 * theme.scale), Math.round(620 * theme.scale))
                spacing: Math.round(8 * theme.scale)
                z: 40

                Rectangle {
                    objectName: "backgroundStudioActivity"
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(parent.width, Math.max(Math.round(320 * theme.scale), studioBar.implicitWidth + Math.round(28 * theme.scale)))
                    height: studioBar.implicitHeight + Math.round(12 * theme.scale)
                    visible: window.mixAway
                    radius: Math.round(14 * theme.scale)
                    color: theme.surface
                    border.width: 1
                    border.color: theme.selectedLine
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.leftMargin: parent.radius; anchors.rightMargin: parent.radius
                        height: 1; color: theme.highlightTop
                    }
                    RowLayout {
                        id: studioBar
                        anchors.fill: parent
                        anchors.leftMargin: Math.round(14 * theme.scale)
                        anchors.rightMargin: Math.round(10 * theme.scale)
                        spacing: Math.round(8 * theme.scale)
                        Rectangle { width: 8; height: 8; radius: 4; color: theme.success }
                        Text {
                            Layout.fillWidth: true
                            color: theme.text
                            font.pixelSize: Math.round(13 * theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                            text: window.controller.text("简易混音 · 后台试听")
                        }
                        UiButton {
                            objectName: "pauseBackgroundMix"
                            visible: window.mixAway && window.controller.mixdown.view.playing
                            theme: window.tokens
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text("暂停")
                            onClicked: window.controller.mixdown.togglePlay()
                        }
                        UiButton {
                            objectName: "returnBackgroundStudio"
                            theme: window.tokens
                            primary: true
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text("返回混音")
                            onClicked: window.controller.navigate("audio_mixdown")
                        }
                    }
                }

                Rectangle {
                    objectName: "backgroundChorusActivity"
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(parent.width, Math.max(Math.round(320 * theme.scale), chorusBar.implicitWidth + Math.round(28 * theme.scale)))
                    height: chorusBar.implicitHeight + Math.round(12 * theme.scale)
                    visible: window.chorusAway
                    radius: Math.round(14 * theme.scale)
                    color: theme.surface
                    border.width: 1
                    border.color: theme.selectedLine
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.leftMargin: parent.radius; anchors.rightMargin: parent.radius
                        height: 1; color: theme.highlightTop
                    }
                    RowLayout {
                        id: chorusBar
                        anchors.fill: parent
                        anchors.leftMargin: Math.round(14 * theme.scale)
                        anchors.rightMargin: Math.round(10 * theme.scale)
                        spacing: Math.round(8 * theme.scale)
                        Rectangle { width: 8; height: 8; radius: 4; color: theme.success }
                        Text {
                            Layout.fillWidth: true
                            color: theme.text
                            font.pixelSize: Math.round(13 * theme.scale)
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            text: window.controller.text("修音工作台 · 后台试听")
                        }
                        UiButton {
                            theme: window.tokens
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text("暂停")
                            onClicked: window.controller.chorus.togglePlay()
                        }
                        UiButton {
                            theme: window.tokens
                            primary: true
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text("返回修音")
                            onClicked: window.controller.navigate("chorus")
                        }
                    }
                }

                Rectangle {
                    objectName: "workspaceActivity"
                    width: parent.width
                    height: footer.implicitHeight + Math.round(18 * theme.scale)
                    visible: window.operations.length > 0 || !!window.issue.id || window.previewLoading
                        || window.controller.notice.length > 0
                    radius: Math.round(14 * theme.scale)
                    color: theme.surface
                    border.width: 1
                    border.color: (!window.operations.length && !window.previewLoading && !!window.issue.id)
                        ? theme.error : theme.line
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        anchors.leftMargin: parent.radius; anchors.rightMargin: parent.radius
                        height: 1; color: theme.highlightTop
                    }
                    RowLayout {
                        id: footer
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Math.round(14 * theme.scale)
                        anchors.rightMargin: Math.round(12 * theme.scale)
                        spacing: Math.round(10 * theme.scale)
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text {
                                objectName: "operationHeadline"
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                text: window.operations.length ? window.primaryOperation.detail
                                    : window.previewLoading ? window.controller.text("正在准备连续试听…")
                                    : window.issue.title || window.controller.notice
                                color: window.operations.length || window.previewLoading || !window.issue.id ? theme.text : theme.error
                                font.pixelSize: 13 * theme.scale
                                font.weight: Font.DemiBold
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                ToolTip.visible: activityHover.hovered && (truncated || !!window.issue.details)
                                ToolTip.text: window.operations.length || window.previewLoading ? text
                                    : window.issue.details || text
                                HoverHandler { id: activityHover }
                            }
                            Text {
                                objectName: "operationTiming"
                                Layout.fillWidth: true
                                visible: text.length > 0
                                text: window.operations.length ? [window.primaryOperation.timing,
                                      window.primaryOperation.count].filter(s => s).join(" · ")
                                    : window.previewLoading ? window.controller.text("已等待 {0}")
                                        .replace("{0}", Math.floor(window.previewWait / 60).toString().padStart(2, "0")
                                            + ":" + (window.previewWait % 60).toString().padStart(2, "0"))
                                    : window.issue.hint || ""
                                font.pixelSize: 11 * theme.scale
                                color: theme.muted
                                elide: Text.ElideRight
                                textFormat: Text.PlainText
                            }
                            UiProgressBar {
                                objectName: "workspaceOperationProgress"
                                theme: window.tokens
                                Layout.fillWidth: true
                                visible: (window.operations.length || window.previewLoading)
                                    && (window.primaryOperation.fraction !== undefined || !theme.reduceMotion)
                                indeterminate: window.primaryOperation.fraction === undefined && !theme.reduceMotion
                                value: window.primaryOperation.fraction === undefined ? 0 : window.primaryOperation.fraction
                                Accessible.name: window.controller.text("当前操作进度")
                            }
                        }
                        UiButton {
                            objectName: "openOperationWorkspace"
                            visible: window.operations.length
                                ? (window.primaryOperation.route !== window.controller.route
                                    || (String(window.primaryOperation.kind).indexOf("calibration-") === 0
                                        && window.controller.workshop.pane !== "calibration"))
                                : window.previewLoading ? window.controller.route !== "audio_mixdown"
                                : !!window.issue.route && window.issue.route !== window.controller.route
                            theme: window.tokens
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text(window.operations.length ? "查看进度"
                                : window.previewLoading ? "返回混音" : "查看工作区")
                            onClicked: {
                                const entry = window.operations.length ? window.primaryOperation
                                    : window.previewLoading ? {kind: "mixdown-preview", route: "audio_mixdown"}
                                    : window.issue
                                if (String(entry.kind).indexOf("calibration-") === 0)
                                    window.controller.openTuningWorkshop("calibration")
                                else
                                    window.controller.navigate(entry.route)
                            }
                        }
                        UiButton {
                            objectName: "activityDiagnostics"
                            theme: window.tokens
                            quiet: true
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.operations.length > 1
                                ? window.controller.text("{0} 项操作").replace("{0}", window.operations.length)
                                : window.controller.text("本机诊断")
                            onClicked: window.controller.support.showDiagnostics()
                        }
                        UiButton {
                            objectName: "dismissOperationIssue"
                            visible: !window.operations.length && !window.previewLoading
                                && (!!window.issue.id || window.controller.notice.length > 0)
                            theme: window.tokens
                            quiet: true
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text("关闭")
                            onClicked: window.controller.dismissOperationIssue(window.issue.id || "")
                        }
                        UiButton {
                            objectName: "cancelWorkspaceOperation"
                            visible: !!window.primaryOperation.canCancel
                            enabled: !window.primaryOperation.cancelling
                            theme: window.tokens
                            implicitHeight: Math.round(28 * theme.scale)
                            text: window.controller.text("取消")
                            onClicked: window.controller.cancelActiveOperation(window.primaryOperation.id)
                        }
                    }
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
                onWorkshopRequested: pane => window.controller.openTuningWorkshop(pane)
            }
        }
        Component {
            id: workshopPage
            TuningWorkshopPage { theme: window.tokens; controller: window.controller }
        }
        Component {
            id: chorusPage
            ChorusPage { theme: window.tokens; controller: window.controller.chorus }
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
        Component {
            id: convertPage
            ProjectConvertPage {
                theme: window.tokens
                controller: window.controller.converter
            }
        }
    }

    function navigateByRouteIndex(index) {
        if (commandPalette.visible)
            return
        const routes = window.controller.routes || []
        if (index >= 0 && index < routes.length && routes[index] && routes[index].pageId)
            window.controller.navigate(routes[index].pageId)
    }

    Shortcut { objectName: "globalCommandPaletteShortcut"; sequence: "Ctrl+K"; context: Qt.WindowShortcut; onActivated: commandPalette.visible ? commandPalette.close() : commandPalette.open() }
    Shortcut { objectName: "globalShortcutSheet"; sequence: "Ctrl+/"; context: Qt.WindowShortcut; onActivated: shortcutSheet.visible ? shortcutSheet.close() : shortcutSheet.open() }
    Shortcut { sequence: "F1"; context: Qt.WindowShortcut; onActivated: shortcutSheet.visible ? shortcutSheet.close() : shortcutSheet.open() }
    Shortcut { sequence: "Ctrl+1"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(0) }
    Shortcut { sequence: "Ctrl+2"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(1) }
    Shortcut { sequence: "Ctrl+3"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(2) }
    Shortcut { sequence: "Ctrl+4"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(3) }
    Shortcut { sequence: "Ctrl+5"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(4) }
    Shortcut { sequence: "Ctrl+6"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(5) }
    Shortcut { sequence: "Ctrl+7"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(6) }
    Shortcut { sequence: "Ctrl+8"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(7) }
    Shortcut { sequence: "Ctrl+9"; context: Qt.WindowShortcut; onActivated: window.navigateByRouteIndex(8) }

    UiCommandPalette { id: commandPalette; theme: window.tokens; controller: window.controller; onShortcutSheetRequested: shortcutSheet.open() }
    ShortcutSheetDialog { id: shortcutSheet; theme: window.tokens; controller: window.controller.support }
    DiagnosticsDialog { theme: window.tokens; controller: window.controller.support }

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
