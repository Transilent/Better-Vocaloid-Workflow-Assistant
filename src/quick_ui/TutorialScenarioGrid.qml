import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    id: grid
    objectName: "tutorialScenarioGrid"
    required property QtObject theme
    required property QtObject controller
    required property var cards
    signal scenarioPicked(string pageId)

    Layout.fillWidth: true
    columns: 2
    columnSpacing: Math.round(10 * theme.scale)
    rowSpacing: Math.round(10 * theme.scale)

    function tr(msg) {
        return controller && typeof controller.text === "function" ? controller.text(msg) : msg
    }

    Repeater {
        model: grid.cards
        Rectangle {
            objectName: "scenarioCard_" + String(modelData.id || index)
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(132 * grid.theme.scale)
            radius: Math.round(10 * grid.theme.scale)
            color: cardHover.hovered ? grid.theme.hover : grid.theme.base
            border.color: cardHover.hovered ? grid.theme.accent : grid.theme.line

            HoverHandler { id: cardHover }
            TapHandler {
                onTapped: grid.scenarioPicked(String(modelData.pageId || "processing"))
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Math.round(12 * grid.theme.scale)
                spacing: Math.round(5 * grid.theme.scale)
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: grid.tr(String(modelData.title || ""))
                        color: grid.theme.text
                        font.pixelSize: Math.round(14 * grid.theme.scale)
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    Rectangle {
                        radius: Math.round(4 * grid.theme.scale)
                        color: grid.theme.selected
                        implicitWidth: badgeTxt.implicitWidth + Math.round(10 * grid.theme.scale)
                        implicitHeight: badgeTxt.implicitHeight + Math.round(4 * grid.theme.scale)
                        Text {
                            id: badgeTxt
                            anchors.centerIn: parent
                            text: grid.tr(String(modelData.badge || ""))
                            color: grid.theme.accent
                            font.pixelSize: Math.round(10 * grid.theme.scale)
                            font.weight: Font.DemiBold
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    text: grid.tr(String(modelData.summary || ""))
                    color: grid.theme.muted
                    font.pixelSize: Math.round(12 * grid.theme.scale)
                    lineHeight: 1.3
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}
