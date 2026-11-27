import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "convertReceiptCard"
    required property QtObject theme
    required property QtObject controller
    required property var receipt
    required property real s

    readonly property var outputs: receipt.outputs || []
    readonly property var warnings: receipt.warnings || []
    readonly property bool allVerified: outputs.length > 0 && outputs.every(o => !!o.verified)

    function vwaTr(text) {
        return quickTranslation ? quickTranslation.text(text) : text
    }

    function formatBytes(n) {
        const val = Number(n || 0)
        if (val < 1024) return val + " B"
        if (val < 1024 * 1024) return (val / 1024).toFixed(1) + " KB"
        return (val / (1024 * 1024)).toFixed(2) + " MB"
    }

    implicitHeight: receiptCol.implicitHeight + Math.round(22 * card.s)
    radius: card.theme.radiusLg
    color: card.theme.raised
    border.width: 1
    border.color: card.allVerified ? card.theme.selectedLine : card.theme.attention

    ColumnLayout {
        id: receiptCol
        anchors.fill: parent
        anchors.margins: Math.round(12 * card.s)
        spacing: Math.round(8 * card.s)

        RowLayout {
            Layout.fillWidth: true
            spacing: Math.round(6 * card.s)
            UiIcon {
                width: Math.round(15 * card.s)
                height: width
                glyph: card.allVerified ? "check" : "info_circle"
                tint: card.allVerified ? card.theme.success : card.theme.attention
            }
            Text {
                objectName: "convertReceiptStatus"
                Layout.fillWidth: true
                text: card.allVerified
                    ? card.vwaTr("转换完成 · 已自动回读校验")
                    : card.vwaTr("转换完成 · 请复核")
                color: card.theme.text
                font.pixelSize: card.theme.fontBody
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
            UiButton {
                objectName: "convertOpenOutputFolder"
                theme: card.theme
                quiet: true
                leadingGlyph: "folder"
                leadingIconSize: 13
                implicitHeight: Math.round(28 * card.s)
                text: card.vwaTr("打开文件夹")
                onClicked: card.controller.openFolder(card.receipt.outputDir || "")
            }
        }

        // Receipt warnings (verification diffs, DS fallback, optional dict notices)
        Rectangle {
            objectName: "convertReceiptWarnings"
            visible: card.warnings.length > 0
            Layout.fillWidth: true
            implicitHeight: receiptWarnCol.implicitHeight + Math.round(14 * card.s)
            radius: card.theme.radiusMd
            color: card.theme.surface
            border.width: 1
            border.color: card.theme.attention

            ColumnLayout {
                id: receiptWarnCol
                anchors.fill: parent
                anchors.margins: Math.round(8 * card.s)
                spacing: Math.round(4 * card.s)
                Repeater {
                    model: card.warnings
                    delegate: Text {
                        required property string modelData
                        Layout.fillWidth: true
                        text: "• " + modelData
                        color: card.theme.attention
                        font.pixelSize: card.theme.fontXs
                        wrapMode: Text.Wrap
                        lineHeight: 1.3
                        textFormat: Text.PlainText
                    }
                }
            }
        }

        // Output file rows with one-click handoff
        Repeater {
            model: card.outputs
            delegate: Rectangle {
                required property var modelData
                required property int index
                Layout.fillWidth: true
                implicitHeight: outRow.implicitHeight + Math.round(12 * card.s)
                radius: card.theme.radiusMd
                color: card.theme.surface
                border.width: 1
                border.color: card.theme.lineSubtle

                RowLayout {
                    id: outRow
                    anchors.fill: parent
                    anchors.margins: Math.round(8 * card.s)
                    spacing: Math.round(8 * card.s)

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            Layout.fillWidth: true
                            text: modelData.fileName || ""
                            color: card.theme.text
                            font.pixelSize: card.theme.fontSm
                            font.weight: Font.DemiBold
                            elide: Text.ElideMiddle
                            textFormat: Text.PlainText
                        }
                        Text {
                            Layout.fillWidth: true
                            text: card.receipt.targetGroup === "lyrics"
                                ? ((modelData.lineCount || 0) + " " + card.vwaTr("行歌词") + " · " + card.formatBytes(modelData.sizeBytes))
                                : (card.vwaTr("源 ") + (modelData.sourceNotes || 0) + " → " + card.vwaTr("输出 ") + (modelData.outputNotes || 0) + " " + card.vwaTr("音符")
                                    + (modelData.verified ? (" · " + card.vwaTr("一致")) : (" · " + card.vwaTr("请复核")))
                                    + " · " + card.formatBytes(modelData.sizeBytes))
                            color: modelData.verified ? card.theme.success : card.theme.attention
                            font.pixelSize: card.theme.fontXs
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }

                    UiButton {
                        objectName: "convertSendToTuning_" + index
                        visible: !!card.receipt.canSendToTuning
                        theme: card.theme
                        quiet: true
                        implicitHeight: Math.round(28 * card.s)
                        text: card.vwaTr("送入调音")
                        onClicked: card.controller.sendToTuning(modelData.path || "")
                    }
                    UiButton {
                        objectName: "convertSendToChorus_" + index
                        visible: !!card.receipt.canSendToChorus
                        theme: card.theme
                        quiet: true
                        implicitHeight: Math.round(28 * card.s)
                        text: card.vwaTr("作修音参考谱")
                        onClicked: card.controller.sendToChorusScore(modelData.path || "")
                    }
                }
            }
        }

        // Preserved vs omitted transparent notes
        Text {
            Layout.fillWidth: true
            visible: (card.receipt.preserved || []).length > 0
            text: card.vwaTr("已保留：") + (card.receipt.preserved || []).join("；")
            color: card.theme.text
            font.pixelSize: card.theme.fontXs
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
        Text {
            Layout.fillWidth: true
            visible: (card.receipt.omitted || []).length > 0
            text: card.vwaTr("未迁移（格式差异）：") + (card.receipt.omitted || []).join("；")
            color: card.theme.muted
            font.pixelSize: card.theme.fontXs
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
        }
    }
}
