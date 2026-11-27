import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    component PanelLabel: Text {
        id: label
        required property QtObject theme
        color: label.theme.text
        font.pixelSize: Math.round(13 * label.theme.scale)
        font.weight: Font.DemiBold
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    component HelperText: Text {
        id: helper
        required property QtObject theme
        color: helper.theme.muted
        font.pixelSize: Math.round(12 * helper.theme.scale)
        lineHeight: 1.18
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    component Divider: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: page.theme.line
    }

    component ThemedTextField: TextField {
        id: input
        required property QtObject theme

        implicitHeight: Math.round(38 * input.theme.scale)
        leftPadding: Math.round(11 * input.theme.scale)
        rightPadding: leftPadding
        topPadding: Math.round(8 * input.theme.scale)
        bottomPadding: topPadding
        color: input.theme.text
        placeholderTextColor: input.theme.muted
        font.pixelSize: Math.round(13 * input.theme.scale)
        selectByMouse: true
        focusPolicy: Qt.StrongFocus
        selectionColor: input.theme.accent
        selectedTextColor: input.theme.base
        Accessible.name: placeholderText

        background: Rectangle {
            radius: Math.round(7 * input.theme.scale)
            color: input.enabled ? input.theme.surface : input.theme.base
            border.width: 1
            border.color: input.activeFocus ? input.theme.accent : input.theme.line
            opacity: input.enabled ? 1 : 0.58
        }
    }

    component EditorButton: UiButton {
        implicitHeight: Math.round(30 * theme.scale)
        leftPadding: Math.round(7 * theme.scale)
        rightPadding: leftPadding
        topPadding: 0
        bottomPadding: 0
    }

    component RecipeButton: EditorButton {
        id: recipe
        required property color tint
        required property string fullLabel
        Accessible.name: fullLabel
        Accessible.checkable: true
        Accessible.checked: selected
        ToolTip.visible: hovered || visualFocus
        ToolTip.text: fullLabel
        background: Rectangle {
            radius: Math.round(6 * recipe.theme.scale)
            color: Qt.rgba(recipe.tint.r, recipe.tint.g, recipe.tint.b,
                           recipe.selected ? 0.52 : recipe.hovered ? 0.30 : 0.17)
            border.color: recipe.selected || recipe.visualFocus ? recipe.tint : recipe.theme.line
            border.width: recipe.selected || recipe.visualFocus ? 2 : 1
            Rectangle {
                anchors.left: parent.left
                anchors.leftMargin: 5
                anchors.verticalCenter: parent.verticalCenter
                width: 5; height: 5; radius: 2.5
                color: recipe.tint
            }
        }
    }

    component StrengthRow: RowLayout {
        id: strengthRow
        required property string label
        required property string sliderName
        required property string field
        required property real amount
        property string valueName: ""
        Layout.fillWidth: true
        spacing: page.editorGap
        HelperText {
            theme: page.theme
            Layout.minimumWidth: Math.round(46 * page.scale)
            text: strengthRow.label
        }
        PercentSlider {
            id: strengthControl
            property string dragRegion: ""
            objectName: strengthRow.sliderName
            Layout.fillWidth: true
            strength: strengthRow.amount
            enabled: !page.busy && page.hasSource && strengthRow.enabled
            Accessible.name: strengthRow.label
            onMoved: {
                if (!pressed)
                    page.controller.setBrushValue(page.styleRegionId, strengthRow.field, value / 100)
            }
            onPressedChanged: {
                if (pressed)
                    dragRegion = page.styleRegionId
                else if (dragRegion === page.styleRegionId && value !== percent)
                    page.controller.setBrushValue(dragRegion, strengthRow.field, value / 100)
            }
        }
        HelperText {
            objectName: strengthRow.valueName
            theme: page.theme
            Layout.minimumWidth: Math.round(33 * page.scale)
            horizontalAlignment: Text.AlignRight
            text: page.strengthText(strengthControl.value / 100)
        }
    }

    component PercentSlider: Slider {
        id: slider
        required property real strength
        readonly property int percent: Math.round(Math.max(0, Math.min(1, strength)) * 100)

        implicitHeight: Math.round(30 * page.scale)
        from: 0
        to: 100
        value: percent
        stepSize: 1
        snapMode: Slider.SnapAlways
        focusPolicy: Qt.StrongFocus
        wheelEnabled: false
        Accessible.description: page.vwaTr("范围 0% 到 100%，每次调整 1%")

        background: Rectangle {
            x: slider.leftPadding + slider.handle.width / 2
            y: Math.round((slider.height - height) / 2)
            width: Math.max(0, slider.availableWidth - slider.handle.width)
            height: Math.round(4 * page.scale)
            radius: height / 2
            color: page.theme.line
            Rectangle {
                width: parent.width * slider.visualPosition
                height: parent.height
                radius: parent.radius
                color: page.theme.accent
            }
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: Math.round((slider.height - height) / 2)
            width: Math.round(15 * page.scale)
            height: width
            radius: width / 2
            color: slider.visualFocus ? page.theme.accent : page.theme.text
            border.width: slider.visualFocus ? 2 : 1
            border.color: slider.visualFocus ? page.theme.text : page.theme.accent
            opacity: slider.enabled ? 1 : 0.5
        }
    }

}
