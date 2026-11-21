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
        anchors.margins: Math.round(22 * page.unit)
        spacing: Math.round(15 * page.unit)

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: page.label("调音工坊")
                font.pixelSize: Math.round(27 * page.unit)
                font.weight: Font.DemiBold
                color: page.theme.text
            }
            Item { Layout.fillWidth: true }
            UiButton {
                objectName: "workshopReturnTuning"
                theme: page.theme
                quiet: true
                text: page.label("返回调音")
                onClicked: page.controller.navigate("tuning")
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: tabs.implicitHeight + Math.round(8 * page.unit)
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: page.theme.line
            }
            RowLayout {
                id: tabs
                spacing: Math.round(8 * page.unit)
                Repeater {
                    model: [{key: "style", text: "风格制作"}, {key: "calibration", text: "声库拟合"},
                            {key: "presets", text: "预设库"}]
                    UiButton {
                        required property var modelData
                        objectName: "workshopTab_" + modelData.key
                        theme: page.theme
                        quiet: true
                        selected: page.workshop.pane === modelData.key
                        text: page.label(modelData.text)
                        Accessible.checkable: true
                        Accessible.checked: selected
                        onClicked: page.workshop.setPane(modelData.key)
                    }
                }
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
