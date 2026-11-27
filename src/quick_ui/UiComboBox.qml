import QtQuick
import QtQuick.Controls

ComboBox {
    id: combo
    required property QtObject theme
    property string fieldLabel: ""

    textRole: "label"
    valueRole: "value"
    leftPadding: Math.round(11 * theme.scale)
    rightPadding: Math.round(31 * theme.scale)
    font.pixelSize: Math.round(13 * theme.scale)
    implicitHeight: Math.max(Math.round(34 * theme.scale), implicitContentHeight + Math.round(12 * theme.scale))
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    Accessible.name: fieldLabel || currentText
    Accessible.description: fieldLabel ? currentText : ""

    contentItem: Text {
        leftPadding: combo.leftPadding
        rightPadding: combo.rightPadding
        text: combo.displayText
        font: combo.font
        color: combo.enabled ? combo.theme.text : combo.theme.muted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        textFormat: Text.PlainText
    }
    indicator: UiIcon {
        x: combo.width - width - Math.round(10 * combo.theme.scale)
        y: Math.round((combo.height - height) / 2)
        width: Math.round(13 * combo.theme.scale)
        height: width
        glyph: "chevron"
        rotation: combo.popup.visible ? -90 : 90
        tint: combo.hovered || combo.popup.visible ? combo.theme.text : combo.theme.muted
        Behavior on rotation {
            enabled: !combo.theme.reduceMotion
            NumberAnimation { duration: 110; easing.type: Easing.OutCubic }
        }
    }
    background: Rectangle {
        radius: combo.theme.radiusMd !== undefined ? combo.theme.radiusMd : Math.round(8 * combo.theme.scale)
        color: !combo.enabled ? combo.theme.base : combo.hovered || combo.popup.visible ? combo.theme.hover : combo.theme.raised
        border.color: combo.activeFocus || combo.popup.visible ? combo.theme.accent : combo.theme.line
        Behavior on color {
            enabled: !combo.theme.reduceMotion
            ColorAnimation { duration: 90 }
        }
        Rectangle {
            visible: combo.enabled
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: combo.theme.highlightTop || "transparent"
        }
    }
    popup: Popup {
        y: combo.height + 4
        width: combo.width
        implicitHeight: Math.min(contentItem.implicitHeight + Math.round(10 * combo.theme.scale), Math.round(256 * combo.theme.scale))
        padding: Math.round(4 * combo.theme.scale)
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: combo.popup.visible ? combo.delegateModel : null
            currentIndex: combo.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            color: combo.theme.surface
            border.color: combo.theme.line
            radius: combo.theme.radiusMd !== undefined ? combo.theme.radiusMd : Math.round(8 * combo.theme.scale)
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: parent.radius
                anchors.rightMargin: parent.radius
                height: 1
                color: combo.theme.highlightTop || "transparent"
            }
        }
    }
    delegate: ItemDelegate {
        id: option
        required property int index
        width: combo.width - Math.round(8 * combo.theme.scale)
        height: Math.max(Math.round(32 * combo.theme.scale), implicitHeight)
        leftPadding: Math.round(10 * combo.theme.scale)
        rightPadding: leftPadding
        highlighted: combo.highlightedIndex === index
        text: combo.textAt(index)
        contentItem: Text {
            text: option.text
            color: option.highlighted ? combo.theme.accent : combo.theme.text
            font.pixelSize: combo.font.pixelSize
            font.weight: option.highlighted ? Font.DemiBold : Font.Normal
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        background: Rectangle {
            radius: combo.theme.radiusSm !== undefined ? combo.theme.radiusSm : Math.round(6 * combo.theme.scale)
            color: option.highlighted ? combo.theme.selected : option.hovered ? combo.theme.hover : "transparent"
        }
    }
}
