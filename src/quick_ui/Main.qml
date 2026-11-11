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
    required property bool showWindow
    required property bool useArtwork
    readonly property QtObject tokens: theme
    readonly property bool compact: width < 1110 || (theme.scale > 1.2 && width < 1280)
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
    palette.highlightedText: "#211930"

    QtObject {
        id: theme
        readonly property real scale: window.initialScale
        readonly property bool reduceMotion: window.initialReducedMotion
        readonly property color base: "#13141a"
        readonly property color surface: "#191b23"
        readonly property color raised: "#20222b"
        readonly property color text: "#eeeef5"
        readonly property color muted: "#a6a9ba"
        readonly property color line: "#30323d"
        readonly property color accent: "#c2a8ff"
        readonly property color success: "#91cdbb"
        readonly property color error: "#efabac"
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
        if (controller.busy && !closingAfterTask) {
            event.accepted = false
            exitDialog.open()
        }
    }

    Dialog {
        id: exitDialog
        title: window.controller.text("组件任务正在运行")
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
        color: "#171822"
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
            opacity: 0.22
        }
        Rectangle {
            anchors.fill: parent
            visible: window.useArtwork
            gradient: Gradient {
                GradientStop { position: 0.64; color: "#ff171822" }
                GradientStop { position: 1; color: "#47171822" }
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
                        Layout.preferredHeight: 41
                        theme: window.tokens
                        text: window.compact ? window.controller.text(modelData.pageId === "processing" ? "准备" : "组件") : modelData.title
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
            sourceComponent: window.controller.route === "components" ? componentsPage : preparationPage
        }
        Component {
            id: componentsPage
            ComponentsPage { theme: window.tokens; controller: window.controller }
        }
        Component {
            id: preparationPage
            PreparationPage { theme: window.tokens; controller: window.controller }
        }

        Rectangle {
            objectName: "workspaceActivity"
            Layout.fillWidth: true
            Layout.preferredHeight: footer.implicitHeight + 16
            visible: window.controller.operation.state !== "idle" || window.controller.notice.length > 0
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
                        text: window.controller.notice || window.controller.operation.message || ""
                        color: window.controller.operation.state === "failed" ? theme.error : theme.text
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
}
