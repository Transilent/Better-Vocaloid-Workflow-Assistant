import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: root
    objectName: "quickCommandPalette"
    required property QtObject theme
    required property QtObject controller
    property var customCommands: []
    property string query: ""
    property int currentIndex: 0

    parent: Overlay.overlay
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? Math.round(Math.max(36, Math.min(parent.height * 0.14, parent.height - height - 36))) : 60
    width: parent ? Math.min(parent.width - Math.round(32 * theme.scale), Math.round(560 * theme.scale)) : Math.round(560 * theme.scale)
    height: Math.min(parent ? parent.height - Math.round(80 * theme.scale) : 500, bodyCol.implicitHeight + Math.round(16 * theme.scale))
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    Overlay.modal: Rectangle {
        color: root.theme.dark ? "#8007090f" : "#4418162a"
    }

    function tr(msg) {
        return controller && typeof controller.text === "function" ? controller.text(msg) : msg
    }

    function allCommands() {
        const list = []
        const routeHints = {
            processing: "zhunbei preparation split vocal stem 分离 准备",
            tuning: "tiaoyin svp ustx vpr acep piano roll 调音",
            tuning_workshop: "gongfang style calibration preset 风格 工坊 校准",
            chorus: "xiuyin pitch timing midi vocal 修音 合唱 音高 节奏",
            audio_mixdown: "hunyin eq reverb compressor mixer 混音 均衡 混响",
            video_mixdown: "shipin mp4 video render 视频 合成",
            publishing: "fabu bilibili xiaohongshu publish 发布 B站 小红书",
            components: "zujian model download rmvpe 组件 模型",
            results: "renwu jobs history 任务 历史 结果",
            updates: "shezhi theme dark light settings 设置 主题 外观"
        }
        const routes = controller.routes || []
        for (let i = 0; i < routes.length; ++i) {
            const r = routes[i]
            list.push({
                id: "nav:" + r.pageId,
                title: r.title,
                subtitle: tr("切换工作区"),
                icon: r.icon || "chevron",
                shortcut: i < 9 ? ("Ctrl+" + (i + 1)) : "",
                keywords: (r.title + " " + r.short + " " + (routeHints[r.pageId] || "")).toLowerCase(),
                action: () => controller.navigate(r.pageId)
            })
        }
        const chorus = controller.chorus
        if (chorus) {
            const cView = chorus.view || ({})
            if (cView.score && cView.vocals > 0) {
                list.push({
                    id: "chorus:autotune-both",
                    title: tr("修音工作台 · 对照 MIDI 批量对齐节奏并修音"),
                    subtitle: tr("使用背景 MIDI 一次性修整全组人声音高与节奏"),
                    icon: "pitch_curve",
                    shortcut: "",
                    keywords: "midi autotune pitch timing chorus 修音 对齐 节奏 音高 批量",
                    action: () => {
                        controller.navigate("chorus")
                        chorus.autoTuneToScore("all", "both")
                    }
                })
                list.push({
                    id: "chorus:autotune-pitch",
                    title: tr("修音工作台 · 对照 MIDI 仅修音（本轨）"),
                    subtitle: tr("对照背景 MIDI 音高自动修整当前选中人声轨"),
                    icon: "music_note",
                    shortcut: "",
                    keywords: "midi pitch chorus 修音 单轨 音高",
                    action: () => {
                        controller.navigate("chorus")
                        chorus.autoTuneToScore("track", "pitch")
                    }
                })
            }
            if ((cView.issues || []).length > 0) {
                list.push({
                    id: "chorus:next-issue",
                    title: tr("修音工作台 · 跳转下一处待核对") + " (" + cView.issues.length + ")",
                    subtitle: tr("定位并聚焦最高优先级的待核对唱段或音符"),
                    icon: "issues",
                    shortcut: "N",
                    keywords: "review issue jump 待核对 跳转 偏调",
                    action: () => {
                        controller.navigate("chorus")
                        if (!cView.inspector) chorus.toggleInspector()
                        chorus.inspectorSection("batch")
                        chorus.focusNextReviewNote()
                    }
                })
            }
            if (cView.vocals > 0) {
                list.push({
                    id: "chorus:toggle-play",
                    title: cView.playing ? tr("修音工作台 · 暂停试听") : tr("修音工作台 · 播放试听"),
                    subtitle: tr("控制修音工作台音频传输"),
                    icon: cView.playing ? "pause" : "play",
                    shortcut: "Space",
                    keywords: "play pause audio chorus 播放 暂停 试听",
                    action: () => chorus.togglePlay()
                })
            }
        }
        const isDark = root.theme.dark
        list.push({
            id: "global:toggle-theme",
            title: isDark ? tr("切换为浅色主题") : tr("切换为深色主题"),
            subtitle: tr("立即切换工作台外观主题与窗口标题栏"),
            icon: "palette",
            shortcut: "",
            keywords: "theme dark light appearance 主题 浅色 深色 切换 外观",
            action: () => controller.support.setSetting("theme", isDark ? "light" : "dark")
        })
        list.push({
            id: "global:diagnostics",
            title: tr("打开本机诊断摘要"),
            subtitle: tr("查看当前操作状态与本机运行日志"),
            icon: "info_circle",
            shortcut: "",
            keywords: "diagnostics log debug 诊断 日志 排查",
            action: () => controller.support.showDiagnostics()
        })
        if (customCommands && customCommands.length) {
            for (let j = 0; j < customCommands.length; ++j)
                list.push(customCommands[j])
        }
        return list
    }

    readonly property var filteredCommands: {
        const all = root.allCommands()
        const q = root.query.trim().toLowerCase()
        if (!q.length)
            return all
        const terms = q.split(/\s+/)
        return all.filter(item => {
            const hay = (item.title + " " + item.subtitle + " " + (item.keywords || "")).toLowerCase()
            for (let i = 0; i < terms.length; ++i) {
                if (hay.indexOf(terms[i]) < 0)
                    return false
            }
            return true
        })
    }

    function executeIndex(idx) {
        const list = root.filteredCommands
        if (idx < 0 || idx >= list.length)
            return
        const cmd = list[idx]
        root.close()
        if (cmd && typeof cmd.action === "function")
            cmd.action()
    }

    onOpened: {
        query = ""
        currentIndex = 0
        searchField.text = ""
        searchField.forceActiveFocus()
    }
    onQueryChanged: currentIndex = 0

    enter: Transition {
        enabled: !root.theme.reduceMotion
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.97; to: 1.0; duration: 110; easing.type: Easing.OutCubic }
        }
    }

    background: Rectangle {
        radius: root.theme.radiusXl !== undefined ? root.theme.radiusXl : Math.round(14 * root.theme.scale)
        color: root.theme.surface
        border.width: 1
        border.color: root.theme.line
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: parent.radius
            anchors.rightMargin: parent.radius
            height: 1
            color: root.theme.highlightTop !== undefined ? root.theme.highlightTop : (root.theme.dark ? "#14ffffff" : "#99ffffff")
        }
    }

    contentItem: ColumnLayout {
        id: bodyCol
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Math.round(12 * root.theme.scale)
            spacing: Math.round(10 * root.theme.scale)

            UiIcon {
                Layout.preferredWidth: Math.round(17 * root.theme.scale)
                Layout.preferredHeight: Layout.preferredWidth
                glyph: "target"
                tint: root.theme.accent
            }

            TextField {
                id: searchField
                objectName: "commandPaletteSearch"
                Layout.fillWidth: true
                placeholderText: root.tr("输入工作区、命令或关键词（如：修音 / MIDI / 混音 / 浅色）…")
                color: root.theme.text
                placeholderTextColor: root.theme.muted
                font.pixelSize: Math.round(14 * root.theme.scale)
                selectByMouse: true
                background: null
                padding: Math.round(4 * root.theme.scale)
                onTextChanged: root.query = text
                Keys.onDownPressed: {
                    if (root.filteredCommands.length > 0)
                        root.currentIndex = (root.currentIndex + 1) % root.filteredCommands.length
                }
                Keys.onUpPressed: {
                    if (root.filteredCommands.length > 0)
                        root.currentIndex = (root.currentIndex - 1 + root.filteredCommands.length) % root.filteredCommands.length
                }
                Keys.onReturnPressed: root.executeIndex(root.currentIndex)
                Keys.onEnterPressed: root.executeIndex(root.currentIndex)
            }

            Rectangle {
                radius: Math.round(5 * root.theme.scale)
                color: root.theme.raised
                border.color: root.theme.lineSubtle !== undefined ? root.theme.lineSubtle : root.theme.line
                implicitWidth: escTxt.implicitWidth + Math.round(10 * root.theme.scale)
                implicitHeight: escTxt.implicitHeight + Math.round(4 * root.theme.scale)
                Text {
                    id: escTxt
                    anchors.centerIn: parent
                    text: "Esc"
                    color: root.theme.muted
                    font.pixelSize: Math.round(10 * root.theme.scale)
                    font.family: root.theme.monoFont || "Consolas"
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: root.theme.lineSubtle !== undefined ? root.theme.lineSubtle : root.theme.line
        }

        ListView {
            id: cmdList
            objectName: "commandPaletteList"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(
                Math.round(360 * root.theme.scale),
                Math.max(Math.round(56 * root.theme.scale),
                         root.filteredCommands.length * Math.round(46 * root.theme.scale) + Math.round(14 * root.theme.scale)))
            topMargin: Math.round(6 * root.theme.scale)
            bottomMargin: Math.round(6 * root.theme.scale)
            leftMargin: Math.round(6 * root.theme.scale)
            rightMargin: Math.round(6 * root.theme.scale)
            spacing: 2
            clip: true
            model: root.filteredCommands
            currentIndex: root.currentIndex
            ScrollBar.vertical: UiScrollBar { theme: root.theme; policy: ScrollBar.AsNeeded }

            delegate: Rectangle {
                id: rowItem
                required property var modelData
                required property int index
                readonly property bool isActive: root.currentIndex === index
                width: cmdList.width - Math.round(12 * root.theme.scale)
                height: Math.round(44 * root.theme.scale)
                radius: root.theme.radiusMd !== undefined ? root.theme.radiusMd : Math.round(8 * root.theme.scale)
                color: isActive ? root.theme.selected : rowHover.containsMouse ? root.theme.hover : "transparent"

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Math.round(10 * root.theme.scale)
                    anchors.rightMargin: Math.round(10 * root.theme.scale)
                    spacing: Math.round(10 * root.theme.scale)

                    UiIcon {
                        Layout.preferredWidth: Math.round(16 * root.theme.scale)
                        Layout.preferredHeight: Layout.preferredWidth
                        glyph: rowItem.modelData.icon || "chevron"
                        tint: rowItem.isActive ? root.theme.accent : root.theme.muted
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            Layout.fillWidth: true
                            text: rowItem.modelData.title
                            color: root.theme.text
                            font.pixelSize: Math.round(13 * root.theme.scale)
                            font.weight: rowItem.isActive ? Font.DemiBold : Font.Normal
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: !!(rowItem.modelData.subtitle)
                            text: rowItem.modelData.subtitle || ""
                            color: root.theme.muted
                            font.pixelSize: Math.round(11 * root.theme.scale)
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                        }
                    }

                    Rectangle {
                        visible: !!(rowItem.modelData.shortcut)
                        radius: Math.round(5 * root.theme.scale)
                        color: root.theme.raised
                        border.color: root.theme.line
                        implicitWidth: keyTxt.implicitWidth + Math.round(10 * root.theme.scale)
                        implicitHeight: keyTxt.implicitHeight + Math.round(4 * root.theme.scale)
                        Text {
                            id: keyTxt
                            anchors.centerIn: parent
                            text: rowItem.modelData.shortcut || ""
                            color: root.theme.muted
                            font.pixelSize: Math.round(10 * root.theme.scale)
                            font.family: root.theme.monoFont || "Consolas"
                        }
                    }
                }

                MouseArea {
                    id: rowHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onEntered: root.currentIndex = rowItem.index
                    onClicked: root.executeIndex(rowItem.index)
                }
            }

            Text {
                anchors.centerIn: parent
                visible: root.filteredCommands.length === 0
                text: root.tr("未找到匹配的命令")
                color: root.theme.muted
                font.pixelSize: Math.round(13 * root.theme.scale)
            }
        }
    }
}
