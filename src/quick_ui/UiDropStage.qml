import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: stage
    required property QtObject theme
    property string iconGlyph: "waveform"
    property string title: ""
    property string subtitle: ""
    property string dropHint: ""
    property var formatTags: []
    property string primaryText: ""
    property string primaryIcon: ""
    property bool primaryEnabled: true
    property string secondaryText: ""
    property string secondaryIcon: ""
    property bool secondaryEnabled: true
    property string tertiaryText: ""
    property string tertiaryIcon: ""
    property bool tertiaryEnabled: true
    property bool acceptDrop: true
    property bool dragActive: false

    signal primaryClicked()
    signal secondaryClicked()
    signal tertiaryClicked()
    signal filesDropped(var urls)

    DropArea {
        id: dropArea
        anchors.fill: parent
        enabled: stage.acceptDrop
        onEntered: drag => {
            if (drag.hasUrls) {
                drag.accept(Qt.CopyAction)
                stage.dragActive = true
            }
        }
        onExited: stage.dragActive = false
        onDropped: drop => {
            stage.dragActive = false
            if (drop.hasUrls && drop.urls && drop.urls.length > 0) {
                drop.accept(Qt.CopyAction)
                stage.filesDropped(drop.urls)
            }
        }
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(parent.width - Math.round(32 * stage.theme.scale), Math.round(540 * stage.theme.scale))
        implicitHeight: contentCol.implicitHeight + Math.round(48 * stage.theme.scale)
        height: Math.min(parent.height - Math.round(24 * stage.theme.scale), implicitHeight)
        radius: stage.theme.radiusXl !== undefined ? stage.theme.radiusXl : Math.round(14 * stage.theme.scale)
        color: stage.dragActive ? stage.theme.selected : stage.theme.surface
        border.width: stage.dragActive ? 2 : 1
        border.color: stage.dragActive ? stage.theme.accent
            : (stage.theme.lineSubtle !== undefined ? stage.theme.lineSubtle : stage.theme.line)
        scale: stage.dragActive && !stage.theme.reduceMotion ? 1.012 : 1.0
        Behavior on color {
            enabled: !stage.theme.reduceMotion
            ColorAnimation { duration: 120 }
        }
        Behavior on scale {
            enabled: !stage.theme.reduceMotion
            NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: stage.theme.highlightTop !== undefined ? stage.theme.highlightTop : (stage.theme.dark ? "#14ffffff" : "#99ffffff")
        }

        // Subtle inner dashed-feel magnetic landing frame
        Rectangle {
            anchors.fill: parent
            anchors.margins: Math.round(10 * stage.theme.scale)
            radius: Math.max(4, parent.radius - Math.round(5 * stage.theme.scale))
            color: "transparent"
            border.width: 1
            border.color: stage.dragActive ? stage.theme.accent : stage.theme.line
            opacity: stage.dragActive ? 0.85 : 0.42
        }

        ColumnLayout {
            id: contentCol
            anchors.centerIn: parent
            width: Math.max(120, parent.width - Math.round(56 * stage.theme.scale))
            spacing: Math.round(12 * stage.theme.scale)

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: Math.round(56 * stage.theme.scale)
                height: width
                radius: width / 2
                color: stage.dragActive ? stage.theme.accent : stage.theme.selected
                border.width: 1
                border.color: stage.dragActive ? stage.theme.accent : stage.theme.selectedLine
                UiIcon {
                    anchors.centerIn: parent
                    width: Math.round(26 * stage.theme.scale)
                    height: width
                    glyph: stage.iconGlyph
                    tint: stage.dragActive ? (stage.theme.accentInk !== undefined ? stage.theme.accentInk : "#ffffff") : stage.theme.accent
                }
            }

            Text {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                text: stage.dragActive && stage.dropHint.length > 0 ? stage.dropHint : stage.title
                color: stage.theme.text
                font.pixelSize: Math.round(17 * stage.theme.scale)
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            Text {
                Layout.fillWidth: true
                visible: stage.subtitle.length > 0
                horizontalAlignment: Text.AlignHCenter
                text: stage.subtitle
                color: stage.theme.muted
                font.pixelSize: Math.round(13 * stage.theme.scale)
                lineHeight: 1.3
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }

            Flow {
                Layout.alignment: Qt.AlignHCenter
                Layout.maximumWidth: parent.width
                visible: (stage.formatTags || []).length > 0
                spacing: Math.round(6 * stage.theme.scale)
                Repeater {
                    model: stage.formatTags || []
                    delegate: Rectangle {
                        required property string modelData
                        radius: Math.round(5 * stage.theme.scale)
                        color: stage.theme.raised
                        border.width: 1
                        border.color: stage.theme.lineSubtle !== undefined ? stage.theme.lineSubtle : stage.theme.line
                        implicitWidth: tagLabel.implicitWidth + Math.round(14 * stage.theme.scale)
                        implicitHeight: tagLabel.implicitHeight + Math.round(6 * stage.theme.scale)
                        Text {
                            id: tagLabel
                            anchors.centerIn: parent
                            text: modelData
                            color: stage.theme.muted
                            font.pixelSize: Math.round(11 * stage.theme.scale)
                            font.family: stage.theme.monoFont || "Consolas"
                            textFormat: Text.PlainText
                        }
                    }
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Math.round(4 * stage.theme.scale)
                spacing: Math.round(8 * stage.theme.scale)
                UiButton {
                    visible: stage.primaryText.length > 0
                    theme: stage.theme
                    primary: true
                    enabled: stage.primaryEnabled
                    text: stage.primaryText
                    leadingGlyph: stage.primaryIcon
                    leadingIconSize: 15
                    leftPadding: Math.round(16 * stage.theme.scale)
                    rightPadding: leftPadding
                    onClicked: stage.primaryClicked()
                }
                UiButton {
                    visible: stage.secondaryText.length > 0
                    theme: stage.theme
                    enabled: stage.secondaryEnabled
                    text: stage.secondaryText
                    leadingGlyph: stage.secondaryIcon
                    leadingIconSize: 15
                    leftPadding: Math.round(14 * stage.theme.scale)
                    rightPadding: leftPadding
                    onClicked: stage.secondaryClicked()
                }
                UiButton {
                    visible: stage.tertiaryText.length > 0
                    theme: stage.theme
                    quiet: true
                    enabled: stage.tertiaryEnabled
                    text: stage.tertiaryText
                    leadingGlyph: stage.tertiaryIcon
                    leadingIconSize: 14
                    onClicked: stage.tertiaryClicked()
                }
            }
        }
    }
}
