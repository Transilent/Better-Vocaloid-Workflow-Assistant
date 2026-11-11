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
    property bool closingAfterTask: false
    property real entryProgress: 1

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

    NumberAnimation {
        id: entrance
        target: window
        property: "entryProgress"
        from: 0
        to: 1
        duration: 155
        easing.type: Easing.OutCubic
    }

    onClosing: function(event) {
        if (controller.workBusy && !closingAfterTask) {
            event.accepted = false
            exitDialog.open()
        }
    }

    Dialog {
        id: exitDialog
        title: window.controller.text("任务正在运行")
        modal: true
        anchors.centerIn: parent
        width: Math.min(parent.width - 36, 390)
        contentItem: Text {
            text: window.controller.text("可以继续等待，或取消操作并在收尾后退出。")
            wrapMode: Text.Wrap
            color: theme.text
            font.pixelSize: 14 * theme.scale
        }
        footer: DialogButtonBox {
            UiButton {
                theme: window.tokens
                text: window.controller.text("继续等待")
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
                onClicked: exitDialog.close()
            }
            UiButton {
                theme: window.tokens
                text: window.controller.text("取消并退出")
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
                onClicked: {
                    exitDialog.close()
                    window.controller.cancelAndClose()
                }
            }
        }
    }

    Connections {
        target: window.controller
        function onCloseReady() {
            window.closingAfterTask = true
            window.close()
        }
        function onNavigationChanged() {
            entrance.stop()
            window.entryProgress = 1
            if (!theme.reduceMotion)
                entrance.start()
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

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: window.compact ? 8 : 12
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
                        onClicked: window.controller.navigate(modelData.pageId)
                        ToolTip.visible: hovered && window.compact
                        ToolTip.text: modelData.title
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
            opacity: 0.82 + 0.18 * window.entryProgress
            sourceComponent: {
                switch (window.controller.route) {
                case "components": return componentsPage
                case "results": return resultsPage
                case "updates": return settingsPage
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
            PreparationPage { theme: window.tokens; controller: window.controller }
        }
        Component {
            id: resultsPage
            ResultsPage { theme: window.tokens; controller: window.controller }
        }
        Component {
            id: settingsPage
            SettingsPage { theme: window.tokens; controller: window.controller.support }
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

    StartupDialogs {
        theme: window.tokens
        controller: window.controller.support
        navigationApi: window.controller
    }
}
