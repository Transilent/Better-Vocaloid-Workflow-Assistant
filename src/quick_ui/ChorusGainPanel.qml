import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property QtObject controller
    required property QtObject theme
    required property var view
    readonly property var gain: view.syllableGain
    readonly property bool vocal: view.take.role === "vocal" && view.rangeFirst < view.rangeLast
    spacing: Math.round(5 * theme.scale)

    function t(text) { return quickTranslation.text(text) }

    component Note: Text {
        color: panel.theme.muted
        font.pixelSize: Math.round(11 * panel.theme.scale)
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        textFormat: Text.PlainText
    }

    Text {
        Layout.fillWidth: true
        text: panel.t("按音节补力度")
        color: panel.theme.text
        font.pixelSize: Math.round(14 * panel.theme.scale)
        font.weight: Font.DemiBold
    }
    Note {
        visible: panel.vocal
        text: panel.view.take.name || ""
        maximumLineCount: 1
        elide: Text.ElideRight
    }
    Note {
        text: panel.t(!panel.vocal ? "在时间轴上选中一段人声。" : !panel.gain.ready
                      ? "请先核对并确认这句歌词。" : "补偿有声部分，保留辅音、气口和句内轻重。")
        visible: !panel.gain.rows.length
    }

    UiButton {
        objectName: "chorusGainConfirmLyrics"
        Layout.fillWidth: true
        theme: panel.theme
        visible: panel.vocal && !panel.gain.ready
        text: panel.t("去核对歌词")
        onClicked: panel.controller.inspectorSection("phrase")
    }
    UiButton {
        objectName: "chorusAnalyzeGain"
        Layout.fillWidth: true
        theme: panel.theme
        visible: panel.gain.ready
        enabled: !panel.view.busy
        text: panel.t(panel.gain.rows.length ? "重新分析音节" : "分析音节")
        onClicked: panel.controller.analyzeGain()
    }
    RowLayout {
        Layout.fillWidth: true
        visible: !!panel.gain.rows.length
        spacing: Math.round(4 * panel.theme.scale)
        UiButton {
            objectName: "chorusHearCurrentGain"
            Layout.fillWidth: true
            theme: panel.theme
            text: panel.t("试听当前")
            selected: panel.gain.audition === "current"
            onClicked: panel.controller.auditionGain("current")
        }
        UiButton {
            objectName: "chorusHearSuggestedGain"
            Layout.fillWidth: true
            theme: panel.theme
            text: panel.t("试听建议")
            selected: panel.gain.audition === "suggested"
            onClicked: panel.controller.auditionGain("suggested")
        }
        UiButton {
            objectName: "chorusExitGainAudition"
            theme: panel.theme
            quiet: true
            glyph: "×"
            text: panel.t("退出独听")
            visible: !!panel.gain.audition
            onClicked: panel.controller.stopGainAudition()
        }
    }
    UiButton {
        objectName: "chorusApplyGain"
        Layout.fillWidth: true
        theme: panel.theme
        primary: true
        visible: !!panel.gain.rows.length
        enabled: !panel.view.busy && (panel.gain.applied > 0 || panel.gain.rows.some(row => row.db > 0))
        text: panel.t("应用音节力度")
        onClicked: panel.controller.applySyllableGain()
    }
    UiButton {
        objectName: "chorusClearGain"
        Layout.fillWidth: true
        theme: panel.theme
        quiet: true
        visible: panel.gain.applied > 0
        enabled: !panel.view.busy
        text: panel.t("清除选区补偿")
        onClicked: panel.controller.clearSyllableGain()
    }
    Note {
        visible: !!panel.gain.rows.length || panel.gain.applied > 0
        text: (panel.gain.route ? panel.gain.route + " · " : "")
              + panel.t("已应用 %1 个音节").replace("%1", panel.gain.applied)
    }
    Note {
        visible: !!panel.gain.rows.length
        text: panel.t("试听时独听所选人声。")
    }

    Repeater {
        model: panel.gain.rows.length
        delegate: RowLayout {
            id: syllable
            required property int index
            readonly property var row: panel.gain.rows[index] || {label: "", time: 0, level: 0, limit: 0, db: 0}
            Layout.fillWidth: true
            spacing: Math.round(5 * panel.theme.scale)

            UiButton {
                theme: panel.theme
                Layout.preferredWidth: Math.round(61 * panel.theme.scale)
                leftPadding: 3
                rightPadding: 3
                quiet: true
                text: syllable.row.label + " " + syllable.row.time.toFixed(2)
                ToolTip.visible: hovered
                ToolTip.text: panel.t("原录音实测 %1 dBFS").replace("%1", syllable.row.level.toFixed(1))
                onClicked: panel.controller.seek(syllable.row.time)
            }
            Slider {
                id: slider
                objectName: "chorusSyllableGain-" + syllable.index
                Layout.fillWidth: true
                from: 0
                to: syllable.row.limit
                value: syllable.row.db
                stepSize: 0.5
                focusPolicy: Qt.StrongFocus
                enabled: !panel.view.busy
                Accessible.name: panel.t("音节补偿") + " " + syllable.row.label
                onMoved: if (!pressed) panel.controller.setSyllableGain(syllable.index, value)
                onPressedChanged: if (!pressed) panel.controller.setSyllableGain(syllable.index, value)
            }
            Note {
                Layout.fillWidth: false
                Layout.preferredWidth: Math.round(47 * panel.theme.scale)
                horizontalAlignment: Text.AlignRight
                text: "+" + slider.value.toFixed(1) + " dB"
            }
        }
    }
}
