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
    implicitHeight: Math.max(Math.round(36 * theme.scale), implicitContentHeight + Math.round(14 * theme.scale))
    focusPolicy: Qt.StrongFocus
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
    indicator: Text {
        x: combo.width - width - Math.round(10 * combo.theme.scale)
        y: Math.round((combo.height - height) / 2)
        text: "▾"
        color: combo.theme.muted
        font.pixelSize: Math.round(13 * combo.theme.scale)
    }
    background: Rectangle {
        radius: Math.round(7 * combo.theme.scale)
        color: combo.enabled ? combo.theme.surface : combo.theme.base
        border.color: combo.activeFocus || combo.popup.visible ? combo.theme.accent : combo.theme.line
    }
    popup: Popup {
        y: combo.height + 3
        width: combo.width
        implicitHeight: Math.min(contentItem.implicitHeight + 2, Math.round(250 * combo.theme.scale))
        padding: 1
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
            radius: Math.round(7 * combo.theme.scale)
        }
    }
    delegate: ItemDelegate {
        id: option
        required property int index
        width: combo.width - 2
        height: Math.max(Math.round(34 * combo.theme.scale), implicitHeight)
        highlighted: combo.highlightedIndex === index
        text: combo.textAt(index)
        contentItem: Text {
            text: option.text
            color: combo.theme.text
            font: combo.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            textFormat: Text.PlainText
        }
        background: Rectangle {
            color: option.highlighted ? combo.theme.selected : option.hovered ? combo.theme.hover : "transparent"
        }
    }
}
