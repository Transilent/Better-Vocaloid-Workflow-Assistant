import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "tuningWorkshopPage"
    required property QtObject theme
    required property QtObject controller
    readonly property var workshop: controller.workshop
    readonly property real unit: theme.scale

    function label(text) { return controller.text(text) }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Math.round(12 * page.unit)
        spacing: Math.round(8 * page.unit)

        // Unified single-row studio header with segmented tab pill
        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(10 * page.unit)
            Text {
                text: page.label("调音工坊")
                font.pixelSize: Math.round(17 * page.unit)
                font.weight: Font.DemiBold
                color: page.theme.text
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                radius: Math.round(7 * page.unit)
                color: page.theme.raised
                border.color: page.theme.line
                Layout.preferredWidth: tabs.implicitWidth + 4
                Layout.minimumWidth: Layout.preferredWidth
                Layout.preferredHeight: tabs.implicitHeight + 4
                implicitWidth: Layout.preferredWidth
                implicitHeight: Layout.preferredHeight
                RowLayout {
                    id: tabs
                    anchors.fill: parent
                    anchors.margins: 2
                    spacing: 2
                    Repeater {
                        model: [{key: "style", text: "风格制作", icon: "nav_workshop"},
                                {key: "calibration", text: "声库拟合", icon: "target"},
                                {key: "presets", text: "预设库", icon: "layers"}]
                        UiButton {
                            required property var modelData
                            objectName: "workshopTab_" + modelData.key
                            theme: page.theme
                            implicitHeight: Math.round(26 * page.unit)
                            quiet: !selected
                            selected: page.workshop.pane === modelData.key
                            text: page.label(modelData.text)
                            leadingGlyph: modelData.icon
                            leadingIconSize: 14
                            Accessible.checkable: true
                            Accessible.checked: selected
                            onClicked: page.workshop.setPane(modelData.key)
                        }
                    }
                }
            }
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "workshopReturnTuning"
                theme: page.theme
                implicitHeight: Math.round(28 * page.unit)
                quiet: true
                text: page.label("返回调音")
                leadingGlyph: "arrow_left"
                leadingIconSize: 14
                onClicked: page.controller.navigate("tuning")
            }
        }

        Loader {
            id: paneLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: page.workshop.pane === "calibration" ? calibration
                : page.workshop.pane === "presets" ? presets : author
            onLoaded: {
                if (!page.theme.reduceMotion && page.visible)
                    reveal.restart()
            }
            NumberAnimation { id: reveal; target: paneLoader; property: "opacity"; from: .86; to: 1; duration: 145 }
        }

        Component {
            id: author
            WorkshopStylePane {
                theme: page.theme
                controller: page.workshop
                onTargetRequested: page.controller.navigate("tuning")
                onPreparationRequested: page.controller.openWorkshopSeparation()
                onPreparedVocalRequested: page.controller.chooseWorkshopVocal(false)
            }
        }
        Component {
            id: calibration
            WorkshopCalibrationPane {
                theme: page.theme
                controller: page.controller.calibration
                onBindingRequested: page.controller.navigate("tuning")
                onPresetsRequested: page.workshop.setPane("presets")
                onComponentsRequested: page.controller.navigate("components")
            }
        }
        Component {
            id: presets
            WorkshopPresetPane {
                theme: page.theme
                controller: page.workshop
                support: page.controller.support
                onTargetRequested: page.controller.navigate("tuning")
            }
        }
    }
}
