import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    required property var page
    objectName: "appearanceSection"
    visible: page.settingsCategory === "appearance"
    Layout.fillWidth: true
    Layout.leftMargin: page.pageMargin
    Layout.rightMargin: page.pageMargin
    Layout.preferredHeight: appearanceContent.implicitHeight + Math.round(30 * page.theme.scale)
    color: page.theme.surface
    radius: Math.round(9 * page.theme.scale)
    border.color: page.theme.line

    ColumnLayout {
        id: appearanceContent
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Math.round(15 * page.theme.scale)
        spacing: Math.round(13 * page.theme.scale)

        Text {
            Layout.fillWidth: true
            text: page.tr("外观与交互")
            color: page.theme.text
            font.pixelSize: Math.round(18 * page.theme.scale)
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: page.tr("界面语言在下次启动时完整生效；主题、材质、缩放与动效会随设置更新。")
            color: page.theme.muted
            font.pixelSize: Math.round(12 * page.theme.scale)
            wrapMode: Text.Wrap
        }

        GridLayout {
            Layout.fillWidth: true
            columns: page.narrow ? 1 : 2
            columnSpacing: Math.round(24 * page.theme.scale)
            rowSpacing: Math.round(13 * page.theme.scale)

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: page.tr("界面主题")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(6 * page.theme.scale)
                    SettingsChoiceChip {
                        objectName: "settingsThemeLight"
                        tokens: page.theme
                        text: page.tr("浅色")
                        selected: String(page.setting("theme", "dark")) === "light"
                        onClicked: page.changeSetting("theme", "light")
                    }
                    SettingsChoiceChip {
                        objectName: "settingsThemeDark"
                        tokens: page.theme
                        text: page.tr("深色")
                        selected: String(page.setting("theme", "dark")) === "dark"
                        onClicked: page.changeSetting("theme", "dark")
                    }
                    SettingsChoiceChip {
                        objectName: "settingsThemeSystem"
                        tokens: page.theme
                        text: page.tr("跟随系统")
                        selected: String(page.setting("theme", "dark")) === "system"
                        onClicked: page.changeSetting("theme", "system")
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: page.tr("界面材质")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(6 * page.theme.scale)
                    SettingsChoiceChip {
                        objectName: "settingsMaterialSolid"
                        tokens: page.theme
                        text: page.tr("纯色阅读")
                        selected: String(page.setting("material", "solid")) === "solid"
                        onClicked: page.changeSetting("material", "solid")
                    }
                    SettingsChoiceChip {
                        objectName: "settingsMaterialGlass"
                        tokens: page.theme
                        text: page.tr("月光玻璃")
                        selected: String(page.setting("material", "solid")) === "glass"
                        onClicked: page.changeSetting("material", "glass")
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: page.tr("应用缩放")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(6 * page.theme.scale)
                    Repeater {
                        model: [100, 115, 130]
                        SettingsChoiceChip {
                            required property int modelData
                            objectName: "settingsScale" + modelData
                            tokens: page.theme
                            text: modelData + "%"
                            selected: Number(page.setting("ui_scale", 100)) === modelData
                            onClicked: page.changeSetting("ui_scale", modelData)
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Text {
                    text: page.tr("界面语言")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: Math.round(6 * page.theme.scale)
                    SettingsChoiceChip {
                        objectName: "settingsLanguageZh"
                        tokens: page.theme
                        text: page.vwaTr("简体中文")
                        selected: String(page.setting("ui_language", "zh-CN")) === "zh-CN"
                        onClicked: page.changeSetting("ui_language", "zh-CN")
                    }
                    SettingsChoiceChip {
                        objectName: "settingsLanguageEn"
                        tokens: page.theme
                        text: "English"
                        selected: String(page.setting("ui_language", "zh-CN")) === "en"
                        onClicked: page.changeSetting("ui_language", "en")
                    }
                    SettingsChoiceChip {
                        objectName: "settingsLanguageJa"
                        tokens: page.theme
                        text: page.vwaTr("日本語")
                        selected: String(page.setting("ui_language", "zh-CN")) === "ja"
                        onClicked: page.changeSetting("ui_language", "ja")
                    }
                }
            }
        }

        SettingsMotionChoice {
            objectName: "settingsReduceMotion"
            Layout.fillWidth: true
            tokens: page.theme
            text: page.tr("减少动效")
            detail: page.tr("关闭非必要过渡和持续动画，保留操作反馈。")
            selected: page.setting("reduce_motion", false) === true
            onClicked: page.changeSetting("reduce_motion", !(page.setting("reduce_motion", false) === true))
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 5
            Text {
                text: page.tr("图形渲染模式（下次启动生效）")
                color: page.theme.muted
                font.pixelSize: Math.round(12 * page.theme.scale)
            }
            Flow {
                Layout.fillWidth: true
                spacing: Math.round(6 * page.theme.scale)
                SettingsChoiceChip {
                    objectName: "settingsRenderAuto"
                    tokens: page.theme
                    text: page.tr("自动（硬件加速）")
                    selected: String(page.setting("render_backend", "auto")) === "auto"
                    onClicked: page.changeSetting("render_backend", "auto")
                }
                SettingsChoiceChip {
                    objectName: "settingsRenderSoftware"
                    tokens: page.theme
                    text: page.tr("软件渲染（兼容模式）")
                    selected: String(page.setting("render_backend", "auto")) === "software"
                    onClicked: page.changeSetting("render_backend", "software")
                }
            }
        }

        ColumnLayout {
            id: audioDeviceBox
            Layout.fillWidth: true
            spacing: 5
            readonly property QtObject uxApi: typeof window !== "undefined" && window.controller ? window.controller.ux : null
            visible: !!uxApi
            RowLayout {
                Layout.fillWidth: true
                spacing: Math.round(8 * page.theme.scale)
                Text {
                    text: page.tr("音频输出设备（WASAPI）")
                    color: page.theme.muted
                    font.pixelSize: Math.round(12 * page.theme.scale)
                }
                UiButton {
                    theme: page.theme
                    quiet: true
                    implicitHeight: Math.round(24 * page.theme.scale)
                    text: page.tr("刷新设备")
                    onClicked: if (audioDeviceBox.uxApi) audioDeviceBox.uxApi.refreshAudioDevices()
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: Math.round(6 * page.theme.scale)
                Repeater {
                    model: audioDeviceBox.uxApi ? audioDeviceBox.uxApi.audioOutputDevices : []
                    SettingsChoiceChip {
                        required property var modelData
                        tokens: page.theme
                        text: modelData.label || page.tr("跟随系统默认")
                        selected: audioDeviceBox.uxApi
                            && String(audioDeviceBox.uxApi.selectedAudioOutputDevice || "") === String(modelData.name || "")
                        onClicked: if (audioDeviceBox.uxApi) audioDeviceBox.uxApi.selectAudioOutputDevice(modelData.name || "")
                    }
                }
            }
        }
    }
}
