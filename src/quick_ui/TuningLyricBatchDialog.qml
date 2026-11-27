import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

UiDialog {
    id: dialog
    objectName: "tuningLyricBatchDialog"
    required property QtObject controller
    required property string sourceKey
    signal noteRequested(var note)
    readonly property real uiScale: theme.scale || 1
    readonly property var review: controller.lyricBatch || ({})
    property var slots: []
    property bool reviewed: false
    property string previewText: ""
    property bool peeking: false
    readonly property bool current: reviewed && previewText === words.text

    function label(text) { return controller.text(text) }
    function refresh() {
        const result = controller.previewLyricBatch(words.text)
        slots = result.rows || []
        previewText = words.text
        reviewed = true
    }

    title: label("整句贴词")
    modal: true
    focus: true
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, Math.round(710 * uiScale))
    height: Math.min(parent.height - 56, Math.round(540 * uiScale))
    closePolicy: Popup.CloseOnEscape
    onSourceKeyChanged: {
        peeking = false
        if (visible) close()
    }
    onOpened: {
        if (peeking) {
            peeking = false
            return
        }
        words.text = ""
        slots = []
        reviewed = false
        previewText = ""
        controller.previewLyricBatch("")
        words.forceActiveFocus()
    }
    onClosed: {
        if (!peeking) {
            controller.dismissLyricBatch()
            slots = []
        }
    }

    contentItem: ColumnLayout {
        spacing: Math.round(7 * dialog.uiScale)
        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: String(dialog.review.track || "")
                color: dialog.theme.text
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            UiButton {
                objectName: "tuningLyricBatchPreview"
                theme: dialog.theme
                text: dialog.label("核对配对")
                onClicked: dialog.refresh()
            }
        }

        ScrollView {
            id: inputScroll
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(72 * dialog.uiScale)
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            background: Rectangle {
                color: dialog.theme.surface
                border.color: words.activeFocus ? dialog.theme.accent : dialog.theme.line
                radius: Math.round(6 * dialog.uiScale)
            }
            TextArea {
                id: words
                objectName: "tuningLyricBatchText"
                width: inputScroll.availableWidth
                placeholderText: dialog.label("粘贴这一句。可在下方逐个调整。")
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                selectByMouse: true
                padding: Math.round(8 * dialog.uiScale)
                color: dialog.theme.text
                font.pixelSize: Math.round(14 * dialog.uiScale)
                background: null
            }
        }

        Text {
            Layout.fillWidth: true
            color: dialog.review.reason || (dialog.review.extra || []).length
                   ? dialog.theme.error : dialog.theme.muted
            font.pixelSize: Math.round(12 * dialog.uiScale)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            text: dialog.review.reason || (
                dialog.reviewed && !dialog.current ? dialog.label("输入已修改，请重新核对配对。")
                : dialog.current && (dialog.review.extra || []).length
                    ? dialog.label("多出的歌词") + " · " + dialog.review.extra.join(" ")
                : dialog.current && dialog.review.blocked
                    ? dialog.label("请处理标出的问题，或保留对应音符的原词。")
                : dialog.current ? dialog.label("拟修改音符") + " · " + String(dialog.review.changes || 0)
                : dialog.label("按住 Shift 框选可只选区。延音和换气保留原样。"))
        }

        ListView {
            id: list
            objectName: "tuningLyricBatchRows"
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: dialog.slots
            clip: true
            spacing: Math.round(4 * dialog.uiScale)
            enabled: dialog.current
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                id: slot
                required property var modelData
                required property int index
                readonly property var currentRow: (dialog.review.rows || [])[index] || modelData
                width: ListView.view.width - Math.round(12 * dialog.uiScale)
                height: rowLayout.implicitHeight + Math.round(8 * dialog.uiScale)
                radius: Math.round(5 * dialog.uiScale)
                color: dialog.theme.base
                border.width: 1
                border.color: currentRow.blocked ? dialog.theme.error : dialog.theme.line
                RowLayout {
                    id: rowLayout
                    anchors.fill: parent
                    anchors.margins: Math.round(4 * dialog.uiScale)
                    spacing: Math.round(6 * dialog.uiScale)
                    UiButton {
                        objectName: "tuningLyricBatchLocate_" + slot.index
                        theme: dialog.theme
                        text: Number(slot.modelData.start).toFixed(2) + " s"
                        quiet: true
                        onClicked: {
                            dialog.peeking = true
                            dialog.close()
                            dialog.noteRequested(slot.modelData)
                        }
                    }
                    Text {
                        Layout.preferredWidth: Math.round(58 * dialog.uiScale)
                        text: String(slot.modelData.before || "—")
                        color: dialog.theme.text
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                    }
                    Text {
                        text: "→"
                        color: dialog.theme.muted
                        Accessible.ignored: true
                    }
                    TextField {
                        id: replacement
                        objectName: "tuningLyricBatchSlot_" + slot.index
                        Layout.preferredWidth: Math.round(105 * dialog.uiScale)
                        Layout.minimumWidth: Math.round(75 * dialog.uiScale)
                        text: String(slot.currentRow.after || "")
                        readOnly: !!slot.currentRow.preserved
                        selectByMouse: true
                        maximumLength: 300
                        Accessible.name: dialog.label("替换歌词")
                        onTextEdited: dialog.controller.reviseLyricBatch(slot.modelData.id, text)
                        background: Rectangle {
                            color: dialog.theme.surface
                            border.color: replacement.activeFocus ? dialog.theme.accent : dialog.theme.line
                            radius: Math.round(4 * dialog.uiScale)
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: slot.currentRow.blocked ? dialog.theme.error : dialog.theme.muted
                        text: [
                            slot.currentRow.reason || "",
                            slot.currentRow.overlap ? dialog.label("同轨音符重叠") : "",
                            (slot.currentRow.readings || []).length > 1
                                ? dialog.label("多音字") + " · " + slot.currentRow.readings.join(" / ") : ""
                        ].filter(Boolean).join("\n")
                    }
                    UiButton {
                        theme: dialog.theme
                        text: dialog.label("保留")
                        quiet: true
                        visible: !slot.currentRow.preserved
                        enabled: String(slot.currentRow.after) !== String(slot.currentRow.before)
                        onClicked: dialog.controller.reviseLyricBatch(slot.modelData.id, slot.modelData.before)
                    }
                }
            }
        }
    }
    footer: DialogButtonBox {
        alignment: Qt.AlignRight
        UiButton {
            theme: dialog.theme
            text: dialog.label("取消")
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            onClicked: dialog.close()
        }
        UiButton {
            objectName: "tuningLyricBatchApply"
            theme: dialog.theme
            primary: true
            text: dialog.label("应用整句")
            enabled: dialog.current && !!dialog.review.canApply
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: if (dialog.controller.applyLyricBatch()) dialog.close()
        }
    }
}
